"""Extra registers of the ecoGEO+ *domestic* BMS/Modbus table (guide 2023, v02, pages 8-10).

Everything defined here is additive: the original entities stay in ``api.MAPPING``
untouched (same keys, same unique ids). 

Entry format (same shape as ``api.MAPPING``) plus:

* ``enabled_default``: ``False`` -> entity is created disabled (none at the moment)
* ``group``: ``"fast"`` (read every poll) or ``"slow"`` (read every SLOW_EVERY polls)
* ``writable``: entity may write to the device (numbers / selects / switches)
* ``options``: for selects/text sensors, ``{raw value: option key}``
* ``diagnostic``: entity category diagnostic

Limits (min/max) marked ``# inferred`` are not in the Modbus guide: they come from the
installer-menu ranges listed in ``Registers.csv`` or are conservative guesses.
"""

from __future__ import annotations

from typing import Any

from .api_types import DataTypes

SLOW_EVERY = 10  # slow groups are read every 10th poll (~5 min with a 30 s interval)

# Optional request blocks: failures are tolerated (older firmware may not expose them).
OPTIONAL_REQUESTS: dict[int, list[dict[str, Any]]] = {
    DataTypes.Coil: [
        {"address": 50, "length": 4, "group": "fast"},     # 50 alarm, 51-52, 53 BMS on/off
        {"address": 127, "length": 2, "group": "fast"},    # 127 summer, 128 winter
    ],
    DataTypes.Register: [
        {"address": 5024, "length": 1, "group": "fast"},   # program mode (winter/summer/auto/mixed/remote)
        {"address": 5220, "length": 8, "group": "fast"},   # state, program, demands
        {"address": 134, "length": 10, "group": "fast"},   # BUS setpoints
        {"address": 176, "length": 4, "group": "fast"},    # BUS thermostat setpoints
        {"address": 7002, "length": 2, "group": "fast"},   # surplus / consumption limit enable
        {"address": 2000, "length": 2, "group": "fast"},   # surplus / consumption limit setpoints
        {"address": 74, "length": 8, "group": "fast"},     # compressor capacity min/max per service (inferred, CSV)
        {"address": 175, "length": 1, "group": "fast"},    # night time compressor limitation (inferred, CSV)
        {"address": 94, "length": 1, "group": "slow"},
        {"address": 132, "length": 2, "group": "slow"},
        {"address": 186, "length": 8, "group": "slow"},
        {"address": 202, "length": 2, "group": "slow"},
        {"address": 5002, "length": 4, "group": "slow"},
        {"address": 5093, "length": 5, "group": "slow"},
        {"address": 5144, "length": 36, "group": "slow"},
        {"address": 5281, "length": 7, "group": "slow"},
    ],
}
R = DataTypes.Register
C = DataTypes.Coil

HP_STATE = {0: "keyboard", 1: "off", 2: "on", 3: "emergency"}
BUS_PROGRAM = {0: "keyboard", 1: "winter", 2: "summer", 3: "mixed"}
SEASON = {0: "none", 1: "winter", 2: "summer"}
# 5024 is NOT in the official domestic table: it comes from Registers.csv (community list) -> inferred
PROGRAM_MODE = {0: "winter", 1: "summer", 2: "auto", 3: "mixed", 4: "remote"}
DEMAND_ONOFF = {0: "none", 1: "demand"}
DEMAND_HEAT_COOL = {0: "none", 1: "heating", 2: "cooling"}

MONTHS = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]


def _ro(address, entity_type, *, type="float", slow=True, enabled=True, data_type=R, **extra):
    d = {
        "data_type": data_type,
        "type": type,
        "address": address,
        "entity_type": entity_type,
        "optional": True,
        "group": "slow" if slow else "fast",
        "enabled_default": enabled,
    }
    d.update(extra)
    return d


EXTRA_MAPPING: dict[str, dict[str, Any]] = {
    # ---- ANALOG, read only (value / 10) -------------------------------------------
    "t_production_in": _ro(4, "temperature", slow=False),
    "t_compressor_suction": _ro(5, "temperature", slow=False),
    "p_compressor_suction": _ro(6, "pressure", slow=False),
    "p_compressor_discharge": _ro(7, "pressure", slow=False),
    "t_airunit_out": _ro(29, "temperature", slow=False),
    "t_condensation": _ro(94, "temperature"),
    "superheat": _ro(132, "kelvin"),
    "expansion_valve": _ro(133, "percentage"),
    "t_dhw_start": _ro(198, "temperature", slow=False),
    "t_evaporation": _ro(199, "temperature", slow=False),
    "eer": _ro(202, "measurement", enabled=True),
    "t_compressor_discharge": _ro(203, "temperature"),
    "t_bus_thermostat_dg1": _ro(186, "temperature"),
    "t_bus_thermostat_sg2": _ro(187, "temperature"),
    "t_bus_thermostat_sg3": _ro(188, "temperature"),
    "t_bus_thermostat_sg4": _ro(189, "temperature"),
    # ---- INTEGER, read only -------------------------------------------------------
    "compressor_rpm": _ro(5002, "rpm", type="int", enabled=True),
    "t_scroll": _ro(5004, "temperature", type="int"),
    "t_inverter": _ro(5005, "temperature", type="int"),
    "working_hours_l": _ro(5080, "raw", type="int", slow=False, diagnostic=True),
    "working_hours_h": _ro(5081, "raw", type="int", slow=False, diagnostic=True),
    "starts_counter": _ro(5281, "counter", type="int", diagnostic=True),
    "starts_counter_x1000": _ro(5282, "counter", type="int", diagnostic=True),
    "software_version_1": _ro(5285, "raw", type="int", diagnostic=True),
    "software_version_2": _ro(5286, "raw", type="int", diagnostic=True),
    "software_version_3": _ro(5287, "raw", type="int", diagnostic=True),
    # 5220 heat pump state as a human readable sensor (read works without BMS control)
    "hp_state": _ro(5220, "text", type="int", slow=False, enabled=True, options=HP_STATE),
    # ---- COILS, read only -----------------------------------------------------------
    "season_mode": {
        "data_type": R,
        "type": "custom",
        "entity_type": "text",
        "optional": True,
        "group": "fast",
        "enabled_default": True,
        "reads": (C, (127, 128)),
        "options": SEASON,
        "value_fn": lambda data, raw: _season(raw),
    },
    "device_clock": {
        "data_type": R,
        "type": "custom",
        "entity_type": "text",
        "optional": True,
        "group": "slow",
        "enabled_default": True,
        "diagnostic": True,
        "reads": (R, tuple(range(5093, 5098))),
        "value_fn": lambda data, raw: _clock(raw),
    },
    # ---- BMS remote control (all disabled by default, need "BMS remote control") -----
    "switch_bms_onoff": {
        "data_type": C, "type": "boolean", "address": 53, "entity_type": "switch",
        "optional": True, "group": "fast", "enabled_default": True, "writable": True,
    },
    "switch_surplus_control": {
        "data_type": R, "type": "bool_register", "address": 7002, "entity_type": "switch",
        "optional": True, "group": "fast", "enabled_default": True, "writable": True,
    },
    "switch_consumption_limit_control": {
        "data_type": R, "type": "bool_register", "address": 7003, "entity_type": "switch",
        "optional": True, "group": "fast", "enabled_default": True, "writable": True,
    },
    "select_hp_state": {
        "data_type": R, "type": "int", "address": 5220, "entity_type": "select",
        "optional": True, "group": "fast", "enabled_default": True, "writable": True,
        "options": HP_STATE,
    },
    # read only: what the keyboard/monitor program is set to (5024, inferred, not in the official guide)
    "program_mode": _ro(5024, "text", type="int", slow=False, diagnostic=True, options=PROGRAM_MODE),
    "select_bus_program": {
        "data_type": R, "type": "int", "address": 5221, "entity_type": "select",
        "optional": True, "group": "fast", "enabled_default": True, "writable": True,
        "options": BUS_PROGRAM,
    },
    "select_demand_dhw": {
        "data_type": R, "type": "int", "address": 5222, "entity_type": "select",
        "optional": True, "group": "fast", "enabled_default": True, "writable": True,
        "options": DEMAND_ONOFF,
    },
    "select_demand_pool": {
        "data_type": R, "type": "int", "address": 5223, "entity_type": "select",
        "optional": True, "group": "fast", "enabled_default": True, "writable": True,
        "options": DEMAND_ONOFF,
    },
    **{
        f"select_demand_{z.lower()}": {
            "data_type": R, "type": "int", "address": 5224 + i, "entity_type": "select",
            "optional": True, "group": "fast", "enabled_default": True, "writable": True,
            "options": DEMAND_HEAT_COOL,
        }
        for i, z in enumerate(["dg1", "sg2", "sg3", "sg4"])
    },
}


def _number(address, entity_type, lo, hi, step=0.1, **extra):
    d = {
        "data_type": R, "type": "float", "address": address, "entity_type": entity_type,
        "is_number": True, "min": lo, "max": hi, "step": step,
        "optional": True, "group": "fast", "enabled_default": True, "writable": True,
    }
    d.update(extra)
    return d


# ---- ANALOG read/write (BUS setpoints). Remember: demand AND setpoint must be sent. ----
# 2000/2001: surplus / consumption limit setpoints (guide 2023). The HP24 list has year/month at the same
# addresses, so on older software they may mean something else: read them first, do not write blindly.
EXTRA_MAPPING.update({
    "number_bus_dhw_setpoint": _number(134, "temperature", 0, 65),                # inferred
    "number_bus_heating_dg1_setpoint": _number(135, "temperature", 20, 55),       # inferred
    "number_bus_heating_sg2_setpoint": _number(136, "temperature", 20, 55),       # inferred
    "number_bus_heating_sg3_setpoint": _number(137, "temperature", 20, 55),       # inferred
    "number_bus_heating_sg4_setpoint": _number(138, "temperature", 20, 55),       # inferred
    "number_bus_cooling_dg1_setpoint": _number(139, "temperature", 7, 55),        # inferred
    "number_bus_cooling_sg2_setpoint": _number(140, "temperature", 7, 55),        # inferred
    "number_bus_cooling_sg3_setpoint": _number(141, "temperature", 7, 55),        # inferred
    "number_bus_cooling_sg4_setpoint": _number(142, "temperature", 7, 55),        # inferred
    "number_bus_pool_setpoint": _number(143, "temperature", 15, 55),              # inferred
    "number_bus_thermostat_dg1_setpoint": _number(176, "temperature", 15, 30),    # inferred
    "number_bus_thermostat_sg2_setpoint": _number(177, "temperature", 15, 30),    # inferred
    "number_bus_thermostat_sg3_setpoint": _number(178, "temperature", 15, 30),    # inferred
    "number_bus_thermostat_sg4_setpoint": _number(179, "temperature", 15, 30),    # inferred
    "number_surplus_activation_setpoint": _number(2000, "power_kw", 0, 30),       # inferred
    "number_consumption_limit_setpoint": _number(2001, "power_kw", 0, 30),        # inferred
    # compressor capacity limits in % (Registers.csv / community, not in the official guide) -> inferred
    "number_compressor_dhw_min": _number(74, "percentage", 25, 100, 1),
    "number_compressor_dhw_max": _number(75, "percentage", 25, 100, 1),
    "number_compressor_heating_min": _number(76, "percentage", 25, 100, 1),
    "number_compressor_heating_max": _number(77, "percentage", 25, 100, 1),
    "number_compressor_cooling_min": _number(78, "percentage", 25, 100, 1),
    "number_compressor_cooling_max": _number(79, "percentage", 25, 100, 1),
    "number_compressor_pool_min": _number(80, "percentage", 25, 100, 1),
    "number_compressor_pool_max": _number(81, "percentage", 25, 100, 1),
    "number_night_compressor_limit": _number(175, "percentage", 25, 100, 1),
})

# ---- INTEGER monthly energies 5144-5179 (unit not specified by the guide: Wh/kWh/MWh) ----
for _i, _m in enumerate(MONTHS):
    for _name, _base in (("condensation", 5144), ("evaporation", 5156), ("electric", 5168)):
        EXTRA_MAPPING[f"energy_{_name}_{_m}"] = _ro(
            _base + _i, "energy", type="int", diagnostic=True
        )


def _season(raw: dict) -> str | None:
    coils = raw[C]
    summer, winter = coils.get(127), coils.get(128)
    if summer is None or winter is None:
        return None
    if summer == "1":
        return "summer"
    if winter == "1":
        return "winter"
    return "none"


def _clock(raw: dict) -> str | None:
    regs = raw[R]
    try:
        h, mi, d, mo, y = (int(regs[a], 16) for a in range(5093, 5098))
    except (KeyError, ValueError):
        return None
    return f"20{y:02d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}"


def addresses_of(definition: dict) -> tuple[int, frozenset[int]] | None:
    """(data type, addresses) a definition reads, None if it needs nothing optional."""
    if "address" in definition:
        return definition["data_type"], frozenset({definition["address"]})
    if "reads" in definition:
        dt, addrs = definition["reads"]
        return dt, frozenset(addrs)
    return None


# ---- derived from the monthly counters (5144-5179, kWh, current month from the device clock 5096) ----
_COUNTER_BASE = {"heating": 5144, "cooling": 5156, "electric": 5168}  # condensation / evaporation / electric


def _counter(raw, kind, month):
    return int(raw[R][_COUNTER_BASE[kind] + month - 1], 16)


def _month(raw):
    month = int(raw[R][5096], 16)
    return month if 1 <= month <= 12 else None


def _current(kind):
    def fn(data, raw):
        m = _month(raw)
        return None if m is None else _counter(raw, kind, m)
    return fn


def _year(kind):
    def fn(data, raw):
        return sum(_counter(raw, kind, m) for m in range(1, 13))
    return fn


def _pf_year(data, raw):
    heat, cool, el = (_year(k)(data, raw) for k in ("heating", "cooling", "electric"))
    return round((heat + cool) / el, 1) if el else None


_READS = (R, (5096, *range(5144, 5180)))
for _kind in _COUNTER_BASE:
    EXTRA_MAPPING[f"energy_{_kind}_month"] = {
        "data_type": R, "type": "custom", "entity_type": "energy_total", "optional": True,
        "group": "slow", "enabled_default": True, "reads": _READS, "value_fn": _current(_kind),
    }
    EXTRA_MAPPING[f"energy_{_kind}_year"] = {
        "data_type": R, "type": "custom", "entity_type": "energy_total", "optional": True,
        "group": "slow", "enabled_default": True, "reads": _READS, "value_fn": _year(_kind),
    }
EXTRA_MAPPING["pf_year"] = {
    "data_type": R, "type": "custom", "entity_type": "measurement", "optional": True,
    "group": "slow", "enabled_default": True, "reads": _READS, "value_fn": _pf_year,
}
