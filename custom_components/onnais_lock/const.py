"""Constants for the ONNAIS RV lock integration."""

DOMAIN = "onnais_lock"

SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
WRITE_CHAR_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"

# Vendor onboarding default. Only ever used against a factory-fresh /
# unbound lock during the initial claim step; real operation always uses
# a per-device secret generated locally during that claim.
FACTORY_DEFAULT_SECRET = b"12345678"

# The official app advertises/looks for names starting with this prefix
# (observed on this project's hardware as "MH_safe_008"). This name only
# appears when a scanner does active scanning and merges in the scan
# response (e.g. BlueZ talking to the lock directly) -- advertisements
# relayed by some Bluetooth proxies carry only the bare primary packet,
# which has no name field at all. Manufacturer ID is present in every
# observed packet from this lock regardless of scanner, so it's the
# primary, reliable match; local name matching is kept as a secondary path.
LOCAL_NAME_MATCH = "MH_safe*"
MANUFACTURER_ID = 54976  # 0xD6C0, non-SIG-registered vendor value

CONF_SECRET = "secret"

COMMAND_TIMEOUT = 12

ATTR_BATTERY = "battery"
ATTR_BUZZER = "buzzer"
ATTR_KEYPAD_LIGHT = "keypad_light"
ATTR_TEMP_PASSWORDS = "temp_passwords"
ATTR_FINGERPRINTS = "fingerprints"
ATTR_KEY_FOBS = "key_fobs"

SERVICE_SET_TEMP_PASSWORD = "set_temp_password"
SERVICE_DELETE_TEMP_PASSWORD = "delete_temp_password"
SERVICE_DELETE_FINGERPRINT = "delete_fingerprint"
SERVICE_BEGIN_ADD_KEY_FOB = "begin_add_key_fob"
SERVICE_DELETE_KEY_FOB = "delete_key_fob"
SERVICE_CANCEL_OPERATION = "cancel_operation"
SERVICE_UNBIND = "unbind"

ATTR_SLOT = "slot"
ATTR_PASSCODE = "passcode"
