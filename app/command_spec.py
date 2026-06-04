"""Canonical command catalog per asset type, transcribed from the gateway
spec (Handoff §10 and Field Spec §15).

Exposed to the frontend via GET /api/assets/{asset_id}/commands so the Command
Panel can render the right buttons/inputs dynamically instead of hard-coding.

Each entry: (command, label, value_required, value_type, unit, category)
  value_type : null | "number" | "integer"
  category   : "read" | "control" | "setpoint"
"""

# (command, label, value_required, value_type, unit, category)
BMS_COMMANDS = [
    ("READ_BMS_ALL",          "Read BMS State",        False, None,      "",   "read"),
    ("READ_BMS_ALARMS",       "Read Alarms",           False, None,      "",   "read"),
    ("START_BMS_PRECHARGE",   "Start Precharge",       False, None,      "",   "control"),
    ("STOP_BMS_PRECHARGE",    "Stop Precharge",        False, None,      "",   "control"),
    ("START_INSULATION_TEST", "Start Insulation Test", False, None,      "",   "control"),
    ("STOP_INSULATION_TEST",  "Stop Insulation Test",  False, None,      "",   "control"),
    ("BMS_FAN_AUTO",          "Fan Auto",              False, None,      "",   "control"),
    ("BMS_FAN_ON",            "Fan On",                False, None,      "",   "control"),
    ("BMS_FAN_OFF",           "Fan Off",               False, None,      "",   "control"),
    ("RESET_BMS",             "Reset BCU/BMS",         False, None,      "",   "control"),
    ("BMS_RESET_FAULT",       "Reset Fault",           False, None,      "",   "control"),
]

PCS_COMMANDS = [
    ("PCS_STATUS",             "Read PCS Status",      False, None,      "",     "read"),
    ("PCS_POWER_ON",           "Power On",             False, None,      "",     "control"),
    ("PCS_POWER_OFF",          "Power Off",            False, None,      "",     "control"),
    ("PCS_STANDBY",            "Standby",              False, None,      "",     "control"),
    ("PCS_SET_ACTIVE_POWER",   "Set Active Power",     True,  "number",  "kW",   "setpoint"),
    ("PCS_SET_REACTIVE_POWER", "Set Reactive Power",   True,  "number",  "kVAr", "setpoint"),
    ("PCS_RESET_FAULT",        "Reset Fault",          False, None,      "",     "control"),
    ("PCS_HEARTBEAT",          "Send Heartbeat",       False, None,      "",     "control"),
]

CHILLER_COMMANDS = [
    ("READ_ALL",      "Read All",        False, None,      "",  "read"),
    ("READ_TEMP",     "Read Temperature",False, None,      "",  "read"),
    ("READ_ONOFF",    "Read On/Off",     False, None,      "",  "read"),
    ("READ_MODE",     "Read Mode",       False, None,      "",  "read"),
    ("READ_SETTINGS", "Read Settings",   False, None,      "",  "read"),
    ("CHILLER_ON",    "Turn On",         False, None,      "",  "control"),
    ("CHILLER_OFF",   "Turn Off",        False, None,      "",  "control"),
    ("SET_TEMP",      "Set Temperature", True,  "number",  "C", "setpoint"),
    ("SET_MODE",      "Set Mode",        True,  "integer", "",  "setpoint"),
]

COMMAND_SPEC = {"bms": BMS_COMMANDS, "pcs": PCS_COMMANDS, "chiller": CHILLER_COMMANDS}


def commands_for(asset_type: str) -> list[dict]:
    out = []
    for cmd, label, value_required, value_type, unit, category in COMMAND_SPEC.get(asset_type, []):
        out.append({
            "command": cmd,
            "label": label,
            "value_required": value_required,
            "value_type": value_type,
            "unit": unit or None,
            "category": category,
        })
    return out
