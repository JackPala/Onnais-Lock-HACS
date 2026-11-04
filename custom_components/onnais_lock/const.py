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
# (observed on this project's hardware as "MH_safe_008").
LOCAL_NAME_MATCH = "MH_safe*"

CONF_SECRET = "secret"

COMMAND_TIMEOUT = 12
CONNECT_TIMEOUT = 30
POLL_INTERVAL_SECONDS = 60

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
