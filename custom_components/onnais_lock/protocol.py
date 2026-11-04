"""Original ONNAIS packet codec derived from observed vendor-app behavior."""


def encode(kind, action, payload=b""):
    if not 0 <= kind <= 255 or not 0 <= action <= 255 or len(payload) > 253:
        raise ValueError("invalid command or payload size")
    prefix = bytes((0xAA, 0x55, len(payload) + 2, kind, action)) + bytes(payload)
    return prefix + bytes((sum(prefix) & 0xFF, 0x5A))


def decode(frame):
    if len(frame) < 7 or frame[:2] != b"\xaa\x55" or frame[-1] != 0x5A:
        raise ValueError("invalid framing")
    if frame[2] < 2 or frame[2] + 5 != len(frame):
        raise ValueError("invalid length")
    if sum(frame[:-2]) & 0xFF != frame[-2]:
        raise ValueError("invalid checksum")
    return frame[3], frame[4], bytes(frame[5:-2])


class FrameStream:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while True:
            start = self.buffer.find(b"\xaa\x55")
            if start < 0:
                self.buffer[:] = self.buffer[-1:] if self.buffer[-1:] == b"\xaa" else b""
                break
            del self.buffer[:start]
            if len(self.buffer) < 3:
                break
            size = self.buffer[2] + 5
            if size < 7:
                del self.buffer[0]
                continue
            if len(self.buffer) < size:
                break
            try:
                frame = decode(self.buffer[:size])
            except ValueError:
                del self.buffer[0]
                continue
            del self.buffer[:size]
            frames.append(frame)
        return frames


def status_from_payload(payload):
    if len(payload) < 2:
        raise ValueError("truncated status response")
    return {"locked": not bool(payload[0] & 1), "battery": payload[1]}


def parse_full_status(payload):
    """Transliteration of the vendor app's getDeviceStatus() hex-string
    parser (app.pretty.js:16210-16239), operating on the same payload bytes
    our FrameStream already hands back from a kind=6 action=3 reply."""
    if len(payload) < 20:
        raise ValueError("truncated full status response")
    hex_string = payload.hex()
    flags = bin(int(hex_string[0:2], 16))[2:].zfill(8)
    result = {
        "locked": flags[-1] == "0",
        "buzzer": flags[-2] == "1",
        "keypad_light": flags[-3] == "1",
        "battery": int(hex_string[2:4], 16),
        "temp_passwords": [],
        "fingerprints": [],
        "key_fobs": [],
    }
    password_bits = bin(int(hex_string[4:8], 16))[2:].zfill(16)
    for slot in range(10):
        if password_bits[len(password_bits) - 1 - slot] == "1":
            result["temp_passwords"].append(slot)
    fingerprint_bits = bin(int(hex_string[8:40], 16))[2:].zfill(128)
    index = len(fingerprint_bits) - 9
    for slot in range(8, 88):
        if 0 <= index < len(fingerprint_bits) and fingerprint_bits[index] == "1":
            result["fingerprints"].append(slot)
        index -= 1
    remainder = hex_string[40:]
    if remainder.strip("0"):
        fob_bits = bin(int(remainder, 16))[2:]
        for slot in range(len(fob_bits)):
            if fob_bits[len(fob_bits) - 1 - slot] == "1":
                result["key_fobs"].append(slot)
    return result


_TEMP_PASSWORD_SLOT_SYMBOLS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "A"]


def temp_password_slot_byte(slot):
    if not 0 <= slot <= 9:
        raise ValueError("temp password slot must be 0-9")
    return int("B" + _TEMP_PASSWORD_SLOT_SYMBOLS[slot], 16)


def encode_set_temp_password(slot, digits):
    if not digits.isdigit() or not 1 <= len(digits) <= 9:
        raise ValueError("passcode must be 1-9 decimal digits")
    return bytes([temp_password_slot_byte(slot), len(digits)]) + bytes(int(d) for d in digits)


def encode_delete_temp_password(slot):
    return bytes([temp_password_slot_byte(slot)])


def temp_password_reply_ok(slot, content):
    return content == bytes([temp_password_slot_byte(slot), 0x00])


