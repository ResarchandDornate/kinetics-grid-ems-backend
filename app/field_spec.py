"""Canonical telemetry field dictionary for every asset type.

This is the single source of truth for field NAMES, TYPES, UNITS, GROUPS,
history policy and event-trigger flags, transcribed directly from the gateway
team's "EMS Gateway Web API Field Specification" (BMS 29 keys, PCS 41 keys,
Chiller ~25 keys).

Why this file exists
--------------------
* The gateway's /telemetry/keys endpoint only returns field names that appear
  in the LATEST packet. When an asset is offline (the spec notes the chiller
  returned keys_count=0 during capture), we'd otherwise have no field metadata.
  This predefined spec is the fallback the spec §20 asks for.
* The same definitions are reused by the mock gateway, so local dev data uses
  the EXACT production field names/units — no drift between mock and real.

Each entry: (field_key, data_type, unit, group, store_history, event_trigger)
  data_type   : number | integer | boolean | string | datetime | array
  unit        : engineering unit or ""
  group       : display group, matching the gateway's keys[].groups buckets
  store_history: write to ems_telemetry_samples? (False = latest-state only,
                 e.g. last_update timestamps and error text)
  event_trigger: spec marks this field as an event/alarm trigger (a change
                 should raise an ems_asset_events row). Wired for later use.
"""

# (field_key, data_type, unit, group, store_history, event_trigger)
BMS_FIELDS = [
    ("communication_status",              "string",   "",     "alarms_status",         True,  True),
    ("soc_percent",                       "number",   "%",    "stack_level",           True,  False),
    ("soh_percent",                       "number",   "%",    "stack_level",           True,  False),
    ("rack_inner_soc_percent",            "number",   "%",    "stack_level",           True,  False),
    ("rack_voltage_v",                    "number",   "V",    "voltage_current_power", True,  False),
    ("rack_current_a",                    "number",   "A",    "voltage_current_power", True,  False),
    ("power_kw",                          "number",   "kW",   "voltage_current_power", True,  False),
    ("max_allowed_charge_current_a",      "number",   "A",    "voltage_current_power", True,  False),
    ("max_allowed_discharge_current_a",   "number",   "A",    "voltage_current_power", True,  False),
    ("max_cell_voltage_mv",               "number",   "mV",   "voltage_current_power", True,  False),
    ("min_cell_voltage_mv",               "number",   "mV",   "voltage_current_power", True,  False),
    ("avg_cell_voltage_mv",               "number",   "mV",   "voltage_current_power", True,  False),
    ("cell_voltage_diff_mv",              "number",   "mV",   "voltage_current_power", True,  False),
    ("max_cell_temp_c",                   "number",   "C",    "temperature",           True,  False),
    ("min_cell_temp_c",                   "number",   "C",    "temperature",           True,  False),
    ("avg_temp_c",                        "number",   "C",    "temperature",           True,  False),
    ("insulation_resistance_kohm",        "number",   "kOhm", "voltage_current_power", True,  False),
    ("positive_insulation_resistance_kohm","number",  "kOhm", "voltage_current_power", True,  False),
    ("negative_insulation_resistance_kohm","number",  "kOhm", "voltage_current_power", True,  False),
    ("precharge_stage",                   "string",   "",     "alarms_status",         True,  True),
    ("bcu_state",                         "string",   "",     "alarms_status",         True,  True),
    ("current_state",                     "string",   "",     "alarms_status",         True,  True),
    ("positive_contactor_closed",         "boolean",  "",     "alarms_status",         True,  True),
    ("precharge_contactor_closed",        "boolean",  "",     "alarms_status",         True,  True),
    ("negative_contactor_closed",         "boolean",  "",     "alarms_status",         True,  True),
    ("active_alarms",                     "array",    "",     "alarms_status",         True,  True),
    ("alarm_count",                       "integer",  "count","alarms_status",         True,  True),
    ("contactor_active_flags",            "array",    "",     "alarms_status",         True,  False),
    ("last_error",                        "string",   "",     "alarms_status",         False, False),
]

PCS_FIELDS = [
    ("comm_status",                       "string",   "",     "status_faults", True,  True),
    ("last_update_ts",                    "datetime", "",     "status_faults", False, False),
    ("error",                             "string",   "",     "status_faults", False, False),
    ("ab_voltage_v",                      "number",   "V",    "ac_side",       True,  False),
    ("bc_voltage_v",                      "number",   "V",    "ac_side",       True,  False),
    ("ca_voltage_v",                      "number",   "V",    "ac_side",       True,  False),
    ("phase_a_voltage_v",                 "number",   "V",    "ac_side",       True,  False),
    ("phase_b_voltage_v",                 "number",   "V",    "ac_side",       True,  False),
    ("phase_c_voltage_v",                 "number",   "V",    "ac_side",       True,  False),
    ("phase_a_current_a",                 "number",   "A",    "ac_side",       True,  False),
    ("phase_b_current_a",                 "number",   "A",    "ac_side",       True,  False),
    ("phase_c_current_a",                 "number",   "A",    "ac_side",       True,  False),
    ("frequency_hz",                      "number",   "Hz",   "ac_side",       True,  False),
    ("active_power_kw",                   "number",   "kW",   "power_energy",  True,  False),
    ("reactive_power_kvar",               "number",   "kVAr", "power_energy",  True,  False),
    ("apparent_power_kva",                "number",   "kVA",  "power_energy",  True,  False),
    ("power_factor",                      "number",   "ratio","power_energy",  True,  False),
    ("bus_voltage_v",                     "number",   "V",    "dc_side",       True,  False),
    ("battery_voltage_v",                 "number",   "V",    "dc_side",       True,  False),
    ("battery_current_a",                 "number",   "A",    "dc_side",       True,  False),
    ("dc_power_kw",                       "number",   "kW",   "dc_side",       True,  False),
    ("dc_total_current_a",                "number",   "A",    "dc_side",       True,  False),
    ("operating_status_raw",              "integer",  "raw",  "status_faults", True,  False),
    ("operating_status",                  "string",   "",     "status_faults", True,  True),
    ("grid_offgrid_status_raw",           "integer",  "raw",  "status_faults", True,  False),
    ("grid_offgrid_status",               "string",   "",     "status_faults", True,  True),
    ("fault_status",                      "boolean",  "",     "status_faults", True,  True),
    ("igbt_temperature_c",                "number",   "C",    "thermal",       True,  False),
    ("ambient_temperature_c",             "number",   "C",    "thermal",       True,  False),
    ("inductance_temperature_c",          "number",   "C",    "thermal",       True,  False),
]

CHILLER_FIELDS = [
    ("last_poll_time",        "datetime", "",     "settings",     False, False),
    ("status",                "string",   "",     "status",       True,  True),
    ("error",                 "string",   "",     "status",       False, False),
    ("communication_status",  "string",   "",     "status",       True,  True),
    ("water_pump",            "string",   "",     "status",       True,  False),
    ("compressor1",           "string",   "",     "status",       True,  False),
    ("compressor2",           "string",   "",     "status",       True,  False),
    ("electric_heater",       "string",   "",     "status",       True,  False),
    ("condensate_fan",        "string",   "",     "status",       True,  False),
    ("makeup_pump",           "string",   "",     "status",       True,  False),
    ("outlet_water_temp",     "number",   "C",    "temperatures", True,  False),
    ("return_water_temp",     "number",   "C",    "temperatures", True,  False),
    ("ambient_temp",          "number",   "C",    "temperatures", True,  False),
    ("outlet_water_pressure", "number",   "bar",  "pressures",    True,  False),
    ("return_water_pressure", "number",   "bar",  "pressures",    True,  False),
    ("fault_code",            "integer",  "code", "settings",     True,  True),
    ("control_mode",          "integer",  "",     "settings",     True,  False),
    ("set_temperature",       "number",   "C",    "settings",     True,  False),
    ("last_update_time",      "datetime", "",     "settings",     False, False),
]

FIELD_SPEC = {"bms": BMS_FIELDS, "pcs": PCS_FIELDS, "chiller": CHILLER_FIELDS}


def display_name(field_key: str) -> str:
    """Human label from a snake_case key, e.g. 'soc_percent' -> 'Soc Percent'.
    Kept simple; the frontend can override with its own copy."""
    return field_key.replace("_", " ").title()


def iter_fields():
    """Yield dicts ready for upsert into ems_telemetry_field_dictionary."""
    for asset_type, fields in FIELD_SPEC.items():
        for key, dtype, unit, group, history, event in fields:
            yield {
                "asset_type": asset_type,
                "field_key": key,
                "display_name": display_name(key),
                "data_type": dtype,
                "unit": unit or None,
                "group_name": group,
                "store_history": history,
                "event_trigger": event,
            }
