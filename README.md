<img src="assets/logo.png" alt="RV Smart Lock Project logo" height="180">

# ONNAIS RV Lock for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

A local, cloud-free Home Assistant integration for the **ONNAIS Guard Smart
RV Door Lock** (model `W008` / "Guard Max"). After a one-time setup, Home
Assistant talks to the lock directly over Bluetooth LE — no ONNAIS account
or internet connection needed for day-to-day use.

---

## What it gives you

| Entity | Type | What it does |
|---|---|---|
| `lock.<name>` | Lock | Lock / unlock, verified against the lock's own reported state |
| `sensor.<name>_battery` | Sensor | Battery percentage |
| `sensor.<name>_programmed_codes` | Sensor | Count and slot numbers of programmed passcodes, fingerprints, and key fobs |
| `switch.<name>_buzzer` | Switch | Audible beep on/off |
| `switch.<name>_keypad_light` | Switch | Keypad backlight on/off |

Plus services: `set_temp_password`, `delete_temp_password`,
`delete_fingerprint`, `begin_add_key_fob`, `delete_key_fob`,
`cancel_operation`, and `unbind` (destructive, requires `confirm: true`).

---

## Installation (HACS)

1. Add this repository in HACS as a **Custom repository** (category:
   Integration).
2. Install **ONNAIS RV Lock**, restart Home Assistant.
3. Go to **Settings → Devices & Services**. Home Assistant's Bluetooth
   integration will auto-discover the lock if it's in range, or use
   **Add Integration** and search for "ONNAIS".

A Bluetooth adapter (local or an [ESPHome Bluetooth
proxy](https://esphome.io/components/bluetooth_proxy.html)) needs to be
within range of the lock.

### Claiming the lock (one-time)

When the setup card appears, put the lock in pairing mode (press **3**,
**3**, then **✓**) and submit the form. Home Assistant authenticates with
the factory-default secret (only valid on an unbound lock), generates a new
random secret, and sets it as the lock's owner secret — verified before the
flow completes and stored only in your Home Assistant config entry. That's
the only time pairing mode is needed; normal operation afterward doesn't
require it.

If the lock is already claimed (by this project's own tooling, or a
previous HA setup), the manual setup step accepts an existing hex secret
instead, importing it without repeating pairing mode.

---

## How it works

- **GATT layout**: service `0000fff0-...`, notify `0000fff1-...`, write
  `0000fff2-...`.
- **Frame format**: `AA 55 [len] [kind] [action] [payload...] [checksum] 5A`.
- **Commands** are `(kind, action)` pairs: `kind=1` identity/binding,
  `kind=6` lock control, `kind=2` passcodes/fingerprints/fobs, `kind=5`
  buzzer/keypad light.
- **Claiming**: the `kind=1 action=1` ("bind") command lets any
  authenticated session set a new owner secret. This integration uses that
  during setup to become the lock's owner directly, rather than depending
  on a cloud-issued credential.
- **Full status**: a single `kind=6 action=3` request returns lock state,
  buzzer, keypad light, battery, and bitmaps of every programmed passcode/
  fingerprint/fob slot in one packet (`custom_components/onnais_lock/
  protocol.py`, unit-tested against synthetic payloads).
- **Connection model**: every operation connects, acts, and disconnects
  rather than holding one long-lived session, which proved more reliable
  against this hardware's BLE radio during development. All BLE activity
  for a lock is serialized through one coordinator.

---

## Security notes

- The owner secret is stored in Home Assistant's config entry storage, the
  same place any other integration's credentials live.
- This integration never talks to any ONNAIS server — no cloud fallback,
  no telemetry, no account requirement.
- `unbind` is destructive and requires explicit `confirm: true`.
- Independent project, not affiliated with or endorsed by ONNAIS / Hanmai
  Technology. Use at your own risk for a lock securing physical access to
  your vehicle.

## Known limitations

- `begin_add_key_fob` starts enrollment mode but doesn't report which slot
  a subsequently-pressed fob lands in — check the "Programmed codes"
  sensor's attributes afterward.
- Fingerprint *enrollment* isn't exposed (still done from the lock's
  keypad or official app); deletion is.
- Developed and tested against the `W008` / "Guard Max" model.
