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
"""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import (
    COMMAND_TIMEOUT,
    CONNECT_TIMEOUT,
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


class OnnaisAuthError(Exception):
    """Raised when the lock rejects the configured secret."""


class OnnaisCommandError(Exception):
    """Raised when the lock rejects or does not answer a command."""


class OnnaisLockClient:
    """One-shot connect / authenticate / command / disconnect session."""

    def __init__(self, ble_device: BLEDevice, secret: bytes):
        self._ble_device = ble_device
        self._secret = bytes(secret)
        self._client: BleakClient | None = None
        self._stream = FrameStream()
        self._replies: asyncio.Queue = asyncio.Queue()

    def update_ble_device(self, ble_device: BLEDevice) -> None:
        self._ble_device = ble_device

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

    async def _connect(self) -> BleakClient:
        client = await establish_connection(
            BleakClientWithServiceCache,
            self._ble_device,
            self._ble_device.address,
            timeout=CONNECT_TIMEOUT,
        )
        if client.services.get_service(SERVICE_UUID) is None:
            await client.disconnect()
            raise OnnaisCommandError("Expected ONNAIS GATT service not found on this device")
        self._stream = FrameStream()
        await client.start_notify(NOTIFY_CHAR_UUID, self._on_notify)
        await asyncio.sleep(0.3)
        return client

    async def _authenticate(self, client: BleakClient, secret: bytes) -> None:
        result = await self._request(client, 1, 2, bytes([len(secret)]) + secret)
        if result != b"\x00":
            raise OnnaisAuthError("Lock rejected the configured secret")

    async def run(self, work):
        """Connect, authenticate with the stored secret, run work(client), disconnect."""
        client = await self._connect()
        try:
            await self._authenticate(client, self._secret)
            return await work(client)
        finally:
            await client.disconnect()

    # -- high level operations, each a self-contained connect/auth/act/disconnect cycle --

    async def get_status(self) -> dict:
        async def work(client):
            content = await self._request(client, 6, 3)
            try:
                return parse_full_status(content)
            except ValueError:
                return status_from_payload(content)
        return await self.run(work)

    async def set_locked(self, locked: bool) -> None:
        async def work(client):
            result = await self._request(client, 6, 2 if locked else 1)
            if result != b"\x00":
                raise OnnaisCommandError("Lock rejected the lock/unlock command")
        await self.run(work)

    async def set_buzzer(self, on: bool) -> None:
        async def work(client):
            result = await self._request(client, 5, 1, bytes([1 if on else 0]))
            if result != b"\x00":
                raise OnnaisCommandError("Lock rejected the buzzer toggle")
        await self.run(work)

    async def set_keypad_light(self, on: bool) -> None:
        async def work(client):
            result = await self._request(client, 5, 2, bytes([1 if on else 0]))
            if result != b"\x00":
                raise OnnaisCommandError("Lock rejected the keypad light toggle")
        await self.run(work)

    async def set_temp_password(self, slot: int, digits: str) -> None:
        async def work(client):
            content = await self._request(client, 2, 1, encode_set_temp_password(slot, digits))
            if not temp_password_reply_ok(slot, content):
                raise OnnaisCommandError(f"Lock rejected temp passcode for slot {slot}")
        await self.run(work)

    async def delete_temp_password(self, slot: int) -> None:
        async def work(client):
            content = await self._request(client, 2, 2, encode_delete_temp_password(slot))
            if not temp_password_reply_ok(slot, content):
                raise OnnaisCommandError(f"Lock rejected deleting temp passcode slot {slot}")
        await self.run(work)

    async def delete_fingerprint(self, slot: int) -> None:
        async def work(client):
            result = await self._request(client, 2, 4, bytes([slot]))
            if result != b"\x00":
                raise OnnaisCommandError(f"Lock rejected deleting fingerprint slot {slot}")
        await self.run(work)

    async def begin_add_key_fob(self) -> None:
        async def work(client):
            await self._request(client, 2, 7)
        await self.run(work)

    async def delete_key_fob(self, slot: int) -> None:
        async def work(client):
            result = await self._request(client, 2, 8, bytes([slot]))
            if result != b"\x00":
                raise OnnaisCommandError(f"Lock rejected deleting key fob slot {slot}")
        await self.run(work)

    async def cancel_operation(self) -> None:
        async def work(client):
            await self._request(client, 2, 9)
        await self.run(work)

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
        client = await probe._connect()
        try:
            await probe._authenticate(client, old_secret)
            result = await probe._request(client, 1, 1, bytes([len(new_secret)]) + new_secret)
            if result != b"\x00":
                raise OnnaisCommandError("Lock rejected the new owner secret")
            await probe._authenticate(client, new_secret)
        finally:
            await client.disconnect()
