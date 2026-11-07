"""Async BLE client for a single ONNAIS lock.

This wraps the protocol codec in ``protocol.py`` (identical, independently
unit-tested logic to the standalone project tooling this integration was
developed from) around a ``bleak`` connection established through Home
Assistant's shared Bluetooth stack via ``bleak-retry-connector``.

Every operation here connects fresh, performs its exchange, and disconnects.
That mirrors the pattern proven reliable against the real hardware during
development: this lock's onboard radio is a known-flaky BCM-family chip, and
holding a long-lived GATT connection open across HA's whole polling interval
was never validated. A future revision could move to a persistent connection
with disconnect callbacks once that is proven reliable for a given board.

Connections never use a cached GATT services table (``use_services_cache``
is explicitly disabled below). That costs a little time on every connect,
but it's deliberate: an interrupted connection (observed live -- e.g. someone
using the lock's physical key or key fob at the same moment Home Assistant
was mid-connect) can leave a *stale* cached services list behind, which then
makes every subsequent attempt fail with ``BleakCharacteristicNotFoundError``
forever, since the device genuinely has that characteristic but the cache
says otherwise. For a lock reachable through an ESPHome Bluetooth proxy,
that cache lives inside ``bleak_esphome``'s own internal store, which has no
safe public "clear" call reachable from here -- so instead of chasing that
cache, every connection here always does a full, honest GATT discovery.

``run()`` additionally retries the whole connect/authenticate/act sequence
on transient transport failures (a dropped link, a lost reply, etc.) instead
of giving up on the first hiccup -- that single-attempt, no-retry design was
the direct cause of locks getting stuck "unavailable" until Home Assistant
was restarted.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakNotFoundError,
    BleakOutOfConnectionSlotsError,
    establish_connection,
)

from .const import (
    COMMAND_TIMEOUT,
    FACTORY_DEFAULT_SECRET,
    NOTIFY_CHAR_UUID,
    SERVICE_UUID,
    WRITE_CHAR_UUID,
)
from .protocol import (
    FrameStream,
    encode,
    encode_delete_temp_password,
    encode_set_temp_password,
    parse_full_status,
    status_from_payload,
    temp_password_reply_ok,
)

_LOGGER = logging.getLogger(__name__)

# Transient failures *after* a connection is up (a dropped link mid-
# exchange, a lost notify reply) that are worth one more full try.
# Deliberately excludes OnnaisAuthError/OnnaisCommandError (the lock's own
# deliberate rejections) and generic programming errors like AttributeError,
# which must surface as bugs rather than be silently retried.
TRANSIENT_ERRORS = (BleakError, TimeoutError, OSError, EOFError, BrokenPipeError)
# Whole connect+authenticate+act sequence attempts. Connection-establishment
# retries happen *inside* establish_connection (CONNECT_ATTEMPTS below); this
# outer retry only covers a link that came up and then failed mid-exchange.
MAX_ATTEMPTS = 2
# bleak-retry-connector hard-codes each individual connect attempt to 20s.
# This lock only accepts a connection when it next advertises, and its
# advertising gap is on the same order as that 20s, so a single attempt is a
# coin flip (observed live: a successful connect landing at 19.5s). Several
# back-to-back attempts give a ~60s window to catch an advertisement, using
# the library's supported knob instead of patching a global HA-wide constant.
CONNECT_ATTEMPTS = 3
# Hard ceiling on one full attempt so no single hung bleak/proxy call
# (write_gatt_char, start_notify, ...) can hold the per-lock lock forever.
# Background status checks (startup / health check) get a single 20s connect
# try: they must never monopolise the proxy while a user is waiting.
BACKGROUND_CONNECT_ATTEMPTS = 1
PER_ATTEMPT_TIMEOUT = 100
DISCONNECT_TIMEOUT = 10

# Shared by every ONNAIS lock in this Home Assistant instance. A Bluetooth LE
# controller can only have one outgoing connection being established at a
# time, so two locks connecting through the same ESPHome proxy at once just
# take turns on its single initiator -- observed live as an unreachable rear
# lock's attempts overlapping (and starving) the front lock's. Serializing
# connections here gives each attempt the radio's full attention.
_RADIO_LOCK = asyncio.Lock()


class OnnaisAuthError(Exception):
    """Raised when the lock rejects the configured secret."""


class OnnaisCommandError(Exception):
    """Raised when the lock rejects or does not answer a command."""


class OnnaisLockClient:
    """Connect / authenticate / act / disconnect sessions against one lock.

    All sessions are serialized through ``self._op_lock``: callers that
    arrive while another session is running wait their turn rather than
    being rejected or racing a second connect at the same radio.
    """

    def __init__(
        self,
        ble_device: BLEDevice,
        secret: bytes,
        ble_device_callback: Callable[[], BLEDevice | None] | None = None,
    ):
        self._ble_device = ble_device
        self._secret = bytes(secret)
        self._ble_device_callback = ble_device_callback
        self._stream = FrameStream()
        self._replies: asyncio.Queue = asyncio.Queue()
        self._op_lock = asyncio.Lock()

    @property
    def busy(self) -> bool:
        return self._op_lock.locked()

    def update_ble_device(self, ble_device: BLEDevice) -> None:
        self._ble_device = ble_device

    def _latest_ble_device(self) -> BLEDevice:
        """Freshest BLEDevice (i.e. best currently-reachable proxy path) for
        each connect attempt, falling back to the last one we knew about."""
        if self._ble_device_callback is not None:
            device = self._ble_device_callback()
            if device is not None:
                self._ble_device = device
        return self._ble_device

    def _on_notify(self, _characteristic, data: bytearray) -> None:
        for kind, action, payload in self._stream.feed(bytes(data)):
            self._replies.put_nowait((kind, action, payload))

    async def _request(self, client: BleakClient, kind: int, action: int, payload: bytes = b"") -> bytes:
        while not self._replies.empty():
            self._replies.get_nowait()
        await client.write_gatt_char(WRITE_CHAR_UUID, encode(kind, action, payload), response=False)
        async with asyncio.timeout(COMMAND_TIMEOUT):
            while True:
                response_kind, response_action, content = await self._replies.get()
                if response_kind == kind and response_action == (0x80 | (action & 0x0F)):
                    return content

    async def _connect(self, connect_attempts: int = CONNECT_ATTEMPTS) -> BleakClient:
        client = await establish_connection(
            BleakClientWithServiceCache,
            self._latest_ble_device(),
            self._ble_device.address,
            max_attempts=connect_attempts,
            ble_device_callback=self._latest_ble_device,
            use_services_cache=False,
        )
        try:
            if client.services.get_service(SERVICE_UUID) is None:
                raise OnnaisCommandError("Expected ONNAIS GATT service not found on this device")
            self._stream = FrameStream()
            await client.start_notify(NOTIFY_CHAR_UUID, self._on_notify)
            await asyncio.sleep(0.3)
        except BaseException:
            await self._safe_disconnect(client)
            raise
        return client

    async def _safe_disconnect(self, client: BleakClient) -> None:
        try:
            async with asyncio.timeout(DISCONNECT_TIMEOUT):
                await client.disconnect()
        except Exception as error:  # noqa: BLE001 - best-effort cleanup only
            _LOGGER.debug("ONNAIS %s: disconnect failed/timed out: %s", self._ble_device.address, error)

    async def _authenticate(self, client: BleakClient, secret: bytes) -> None:
        result = await self._request(client, 1, 2, bytes([len(secret)]) + secret)
        if result != b"\x00":
            raise OnnaisAuthError("Lock rejected the configured secret")

    async def _read_status(self, client: BleakClient) -> dict:
        content = await self._request(client, 6, 3)
        try:
            return parse_full_status(content)
        except ValueError:
            return status_from_payload(content)

    async def run(self, work, *, background: bool = False):
        """Connect, authenticate, run ``work(client)``, disconnect.

        Waits for any in-flight session on this lock first (never two
        concurrent connects to one radio). Connection establishment is
        retried inside establish_connection; a link that comes up and then
        fails mid-exchange gets one more full attempt. A lock that no
        scanner can currently see fails immediately -- nothing to connect
        through, and retrying in the same second cannot change that.
        """
        connect_attempts = BACKGROUND_CONNECT_ATTEMPTS if background else CONNECT_ATTEMPTS
        attempts = 1 if background else MAX_ATTEMPTS
        async with self._op_lock, _RADIO_LOCK:
            last_error: Exception | None = None
            for attempt in range(1, attempts + 1):
                client: BleakClient | None = None
                try:
                    async with asyncio.timeout(PER_ATTEMPT_TIMEOUT):
                        client = await self._connect(connect_attempts)
                        await self._authenticate(client, self._secret)
                        return await work(client)
                except (BleakNotFoundError, BleakOutOfConnectionSlotsError) as error:
                    raise OnnaisCommandError(f"Lock not currently reachable: {error}") from error
                except TRANSIENT_ERRORS as error:
                    last_error = error
                    _LOGGER.debug(
                        "ONNAIS %s: attempt %d/%d failed: %s",
                        self._ble_device.address, attempt, attempts, error,
                    )
                finally:
                    if client is not None:
                        await self._safe_disconnect(client)
            raise OnnaisCommandError(
                f"Lock unreachable after {attempts} attempt(s): {last_error}"
            ) from last_error

    async def _command(self, kind: int, action: int, payload: bytes, accept, what: str) -> dict | None:
        """Send one write command, then read status over the SAME connection.

        Reading status in the same session (rather than a second connection
        afterward) halves the connects per tap -- one beep, one radio
        lottery -- and means a successful command can never be followed by
        a failed separate refresh that wrongly marks the lock unavailable.
        Returns the fresh status, or None if only the post-command status
        read failed (the command itself still succeeded).
        """
        async def work(client):
            result = await self._request(client, kind, action, payload)
            if not accept(result):
                raise OnnaisCommandError(f"Lock rejected {what}")
            try:
                return await self._read_status(client)
            except TRANSIENT_ERRORS as error:
                _LOGGER.debug("ONNAIS %s: status read after %s failed: %s", self._ble_device.address, what, error)
                return None
        return await self.run(work)

    # -- high level operations, each one connect/auth/act/status/disconnect session --

    async def get_status(self, *, background: bool = False) -> dict:
        return await self.run(self._read_status, background=background)

    async def set_locked(self, locked: bool) -> dict | None:
        return await self._command(6, 2 if locked else 1, b"", lambda r: r == b"\x00",
                                   "the lock command" if locked else "the unlock command")

    async def set_buzzer(self, on: bool) -> dict | None:
        return await self._command(5, 1, bytes([1 if on else 0]), lambda r: r == b"\x00", "the buzzer toggle")

    async def set_keypad_light(self, on: bool) -> dict | None:
        return await self._command(5, 2, bytes([1 if on else 0]), lambda r: r == b"\x00", "the keypad light toggle")

    async def set_temp_password(self, slot: int, digits: str) -> dict | None:
        payload = encode_set_temp_password(slot, digits)  # validates before connecting
        return await self._command(2, 1, payload, lambda r: temp_password_reply_ok(slot, r),
                                   f"temp passcode for slot {slot}")

    async def delete_temp_password(self, slot: int) -> dict | None:
        payload = encode_delete_temp_password(slot)
        return await self._command(2, 2, payload, lambda r: temp_password_reply_ok(slot, r),
                                   f"deleting temp passcode slot {slot}")

    async def delete_fingerprint(self, slot: int) -> dict | None:
        return await self._command(2, 4, bytes([slot]), lambda r: r == b"\x00", f"deleting fingerprint slot {slot}")

    async def begin_add_key_fob(self) -> dict | None:
        return await self._command(2, 7, b"", lambda r: True, "starting key fob enrollment")

    async def delete_key_fob(self, slot: int) -> dict | None:
        return await self._command(2, 8, bytes([slot]), lambda r: r == b"\x00", f"deleting key fob slot {slot}")

    async def cancel_operation(self) -> dict | None:
        return await self._command(2, 9, b"", lambda r: True, "cancel")

    async def unbind(self) -> None:
        """Factory-resets the lock's owner secret. Destructive and
        deliberately NOT exposed as a lock/switch entity -- only as an
        explicit service call the user must invoke by name."""
        async def work(client):
            result = await self._request(client, 1, 3)
            if result != b"\x00":
                raise OnnaisCommandError("Lock rejected the unbind request")
        await self.run(work)

    @staticmethod
    async def claim(ble_device: BLEDevice, new_secret: bytes, old_secret: bytes = FACTORY_DEFAULT_SECRET) -> None:
        """One-time enrollment used by the config flow: authenticate with
        ``old_secret`` (the vendor default for a factory-fresh lock) and set
        a new, locally-generated ``new_secret``, verifying it before
        returning. Raises OnnaisAuthError if the lock is already bound to a
        different secret (e.g. still paired with the official app)."""
        probe = OnnaisLockClient(ble_device, old_secret)
        async with _RADIO_LOCK, asyncio.timeout(PER_ATTEMPT_TIMEOUT):
            client = await probe._connect()
            try:
                await probe._authenticate(client, old_secret)
                result = await probe._request(client, 1, 1, bytes([len(new_secret)]) + new_secret)
                if result != b"\x00":
                    raise OnnaisCommandError("Lock rejected the new owner secret")
                await probe._authenticate(client, new_secret)
            finally:
                await probe._safe_disconnect(client)
