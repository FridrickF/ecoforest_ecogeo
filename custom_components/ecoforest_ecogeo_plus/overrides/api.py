import asyncio, re, string, logging
from dataclasses import dataclass

import httpx
from pyecoforest.api import EcoforestApi

from pyecoforest.exceptions import EcoforestConnectionError, EcoforestError

from .device import EcoGeoDevice
from .api_types import DataTypes, Operations
from .registers import (
    EXTRA_MAPPING,
    OPTIONAL_REQUESTS,
    SLOW_EVERY,
    addresses_of,
)

_LOGGER = logging.getLogger(__name__)
_HEX = re.compile(r"[0-9a-fA-F]{1,4}")

MODEL_ADDRESS = 5323
MODEL_LENGTH = 6
MAX_OPTIONAL_FAILURES = 3

# ranges scanned by the dump_registers service (read only)
DUMP_RANGES = {
    DataTypes.Coil: [(1, 300)],
    DataTypes.Register: [(1, 300), (2000, 40), (5000, 330), (7000, 10)],
}
RETRY_PAUSE = 0.3

OP_TYPE_GET_SWITCH = 2001
OP_TYPE_SET_SWITCH = 2011
OP_TYPE_GET_REGISTER = 2002
OP_TYPE_SET_REGISTER = 2012

REQUESTS = {
    DataTypes.Coil : [
        {"address": 1, "length": 41},
        {"address": 57, "length": 27},
        {"address": 105, "length": 3},
        {"address": 212, "length": 15},
    ],

    DataTypes.Register: [
        {"address": 1, "length": 31},
        {"address": 59, "length": 1},
        {"address": 194, "length": 8},
        {"address": 5066, "length": 18},
        {"address": 5185, "length": 1},

        {"address": MODEL_ADDRESS, "length": MODEL_LENGTH, "op": OP_TYPE_GET_REGISTER},
    ]
}

MAPPING = {
    "t_heating": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 200,
        "entity_type": "temperature"
    },
    "t_cooling": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 201,
        "entity_type": "temperature"
    },
    "t_dhw": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 8,
        "entity_type": "temperature"
    },
    "t_dg1_h": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 3,
        "entity_type": "temperature"
    },
    "t_dg1_c": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 197,
        "entity_type": "temperature"
    },
    "t_sg2": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 194,
        "entity_type": "temperature"
    },
    "t_sg3": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 195,
        "entity_type": "temperature"
    },
    "t_sg4": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 196,
        "entity_type": "temperature"
    },
    "t_outdoor": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 11,
        "entity_type": "temperature"
    },
    "power_heating": {
        "data_type": DataTypes.Register,
        "type": "int",
        "address": 5083,
        "entity_type": "power"
    },
    "power_cooling": {
        "data_type": DataTypes.Register,
        "type": "int",
        "address": 5185,
        "entity_type": "power"
    },
    "power_electric": {
        "data_type": DataTypes.Register,
        "type": "int",
        "address": 5082,
        "entity_type": "power"
    },
    "power_output": {
        "data_type": DataTypes.Register,
        "type": "custom",
        "entity_type": "power",
        "reads": (DataTypes.Coil, (127, 128)),
        "value_fn": lambda data, raw: _useful_power(data, raw)
    },
    "t_brine_in": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 2,
        "entity_type": "temperature"
    },
    "t_brine_out": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 1,
        "entity_type": "temperature"
    },
    "p_brine": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 13,
        "entity_type": "pressure"
    },
    "p_output": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 14,
        "entity_type": "pressure"
    },
    "cop": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 30,
        "entity_type": "measurement"
    },
    "pf": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 31,
        "entity_type": "measurement"
    },
    "switch_heating": {
        "data_type": DataTypes.Coil,
        "type": "boolean",
        "address": 105,
        "entity_type": "switch"
    },
    "switch_cooling": {
        "data_type": DataTypes.Coil,
        "type": "boolean",
        "address": 107,
        "entity_type": "switch"
    },
    "switch_dhw": {
        "data_type": DataTypes.Coil,
        "type": "boolean",
        "address": 106,
        "entity_type": "switch"
    },
    "switch_dg1_output": {
        "data_type": DataTypes.Coil,
        "type": "boolean",
        "address": 60,
        "entity_type": "switch"
    },
    "switch_sg2_output": {
        "data_type": DataTypes.Coil,
        "type": "boolean",
        "address": 57,
        "entity_type": "switch"
    },
    "switch_pool_output": {
        "data_type": DataTypes.Coil,
        "type": "boolean",
        "address": 65,
        "entity_type": "switch"
    },
    "switch_pool_device_output": {
        "data_type": DataTypes.Coil,
        "type": "boolean",
        "address": 61,
        "entity_type": "switch"
    },
    "button_reset_alarms": {
        "data_type": DataTypes.Coil,
        "type": "boolean",
        "address": 83,
        "entity_type": "button"
    },
    "number_dhw_setpoint": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 17,
        "entity_type": "temperature",
        "is_number": True,
        "min": 0,
        "max": 65,
        "step": 0.1
    },
    "number_dhw_dt_start": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 15,
        "entity_type": "temperature",
        "is_number": True,
        "min": 2,
        "max": 25,
        "step": 0.1
    },
    "number_dhw_htr_set": {
        "data_type": DataTypes.Register,
        "type": "float",
        "address": 59,
        "entity_type": "temperature",
        "is_number": True,
        "min": 0,
        "max": 70,
        "step": 0.1
    },
    "alarm": {
        "data_type": DataTypes.Coil,
        "type": "custom",
        "entity_type": "enum",
        "value_fn": lambda data, raw_data: EcoGeoApi.get_alarm(raw_data)
    }
}

MAPPING.update(EXTRA_MAPPING)


def _useful_power(data, raw):
    """Thermal power delivered to the building.

    Base value: condensation capacity in winter/unknown, evaporation capacity in summer. With HTR heat
    recovery the unit also delivers the recovered heat (DHW) or both cooling and heating at once; the
    manufacturer's PF is "thermal power delivered / electric power" and includes it, so when PF x electric
    power clearly exceeds the base value (3 %, PF has 0.1 resolution) that is used instead.
    """
    base = data["power_cooling"] if _season_of(raw) == "summer" else data["power_heating"]
    pf, p_el = data.get("pf"), data.get("power_electric")
    if pf and p_el:
        total = round(pf * p_el)
        if total > base * 1.03:
            return total
    return base


def _season_of(raw) -> str | None:
    coils = raw[DataTypes.Coil]
    if coils.get(127) == "1":
        return "summer"
    if coils.get(128) == "1":
        return "winter"
    return None


def key_of_unique_id(unique_id: str) -> str | None:
    """Entity unique ids are '<device>_<key>': longest matching key wins (hp_state vs select_hp_state)."""
    best = None
    for key in MAPPING:
        if unique_id.endswith(f"_{key}") and (best is None or len(key) > len(best)):
            best = key
    return best


def _domains_of(definition: dict) -> set[str]:
    """Home Assistant domains in which the integration creates an entity for this definition."""
    if definition.get("is_number"):
        # the three original numbers keep a read-only sensor twin (historical behaviour)
        return {"number"} if definition.get("writable") else {"number", "sensor"}
    kind = definition["entity_type"]
    return {kind} if kind in ("select", "switch", "button") else {"sensor"}


def is_stale_registry_entry(domain: str, unique_id: str) -> bool:
    """Registry leftovers of earlier test builds (unknown keys, nameless sensor twins, wrong domain)."""
    key = key_of_unique_id(unique_id)
    return key is None or domain not in _domains_of(MAPPING[key])


class EcoGeoApi(EcoforestApi):
    def __init__(
        self,
        host: str,
        user: str,
        password: str
    ) -> None:
        super().__init__(host, httpx.BasicAuth(user, password))
        self._state = {DataTypes.Coil: {}, DataTypes.Register: {}}
        self._cycle = 0
        self._failures: dict[tuple[int, int], int] = {}
        # keys of the entities that are enabled (set by the coordinator); None = read everything
        self.wanted: set[str] | None = None

    def _block_wanted(self, dt: int, request: dict) -> bool:
        if self.wanted is None:
            return True
        span = set(range(request["address"], request["address"] + request["length"]))
        for key in self.wanted:
            needs = addresses_of(MAPPING.get(key, {}))
            if needs and needs[0] == dt and needs[1] & span:
                return True
        return False

    async def get(self) -> EcoGeoDevice:
        state = self._state

        # required blocks: any failure is an error, as before
        for dt in [DataTypes.Coil, DataTypes.Register]:
            for request in REQUESTS[dt]:
                state[dt].update(await self._load_data(request["address"], request["length"], Operations.Get[dt]))

        # optional blocks: only if an enabled entity needs them; tolerated on failure;
        # "slow" ones are refreshed every SLOW_EVERY polls
        refresh_slow = self._cycle % SLOW_EVERY == 0
        self._cycle += 1
        for dt in [DataTypes.Coil, DataTypes.Register]:
            for request in OPTIONAL_REQUESTS[dt]:
                key = (dt, request["address"])
                if self._failures.get(key, 0) >= MAX_OPTIONAL_FAILURES:
                    continue
                if request["group"] == "slow" and not refresh_slow and key in self._failures:
                    continue
                if not self._block_wanted(dt, request):
                    continue
                try:
                    state[dt].update(await self._load_data(request["address"], request["length"], Operations.Get[dt]))
                    self._failures[key] = 0
                except Exception as err:
                    self._failures[key] = self._failures.get(key, 0) + 1
                    _LOGGER.debug(
                        "optional block %s/%s/%s not readable (%s), attempt %s/%s",
                        dt, request["address"], request["length"], err,
                        self._failures[key], MAX_OPTIONAL_FAILURES,
                    )

        device_info = {}
        for name, definition in MAPPING.items():
            if definition["type"] == "custom":
                continue

            raw = state[definition["data_type"]].get(definition["address"])
            if raw is None:
                if not definition.get("optional"):
                    raise EcoforestError(f"missing value for {name}")
                device_info[name] = None
                continue

            match definition["type"]:
                case "int":
                    value = self.parse_ecoforest_int(raw)
                case "float":
                    value = self.parse_ecoforest_float(raw)
                case "boolean":
                    value = self.parse_ecoforest_bool(raw)
                case "bool_register":
                    value = self.parse_ecoforest_int(raw) != 0
                case _:
                    _LOGGER.error("unknown entity type for %s", name)
                    continue

            if definition["entity_type"] == "text" and "options" in definition:
                value = definition["options"].get(value)  # unknown raw value -> None (enum sensors reject strangers)

            device_info[name] = value

        for name, definition in MAPPING.items():
            if definition["entity_type"] == "temperature":
                if device_info.get(name) == -999.9:
                    device_info[name] = None

            if definition["type"] != "custom":
                continue
            try:
                device_info[name] = definition["value_fn"](device_info, state)
            except (KeyError, TypeError):
                if not definition.get("optional"):
                    raise
                device_info[name] = None

        _LOGGER.debug(device_info)
        _LOGGER.debug(state)
        return EcoGeoDevice.build(self.parse_model_name(state), device_info)

    async def _load_data(self, address, length, op_type) -> dict[int, str]:
        last_error: Exception | None = None
        for attempt in range(2):  # the gateway sometimes answers -4 / garbage when busy
            try:
                response = await self._request(
                    data={"idOperacion": op_type, "dir": address, "num": length}
                )
                values = response[-length:]
                if len(response) < length or not all(_HEX.fullmatch(v) for v in values):
                    raise EcoforestError(f"unexpected reply for {address}/{length}: {response}")
                return dict(zip(range(address, address + length), values))
            except EcoforestConnectionError as err:
                raise err
            except EcoforestError as err:
                last_error = err
                _LOGGER.debug("read %s/%s failed (%s), attempt %s", address, length, err, attempt + 1)
                await asyncio.sleep(RETRY_PAUSE)
        raise last_error

    async def dump(self, ranges: dict[int, list[tuple[int, int]]] | None = None, chunk: int = 30, pause: float = 0.3) -> dict:
        """Read-only scan of address ranges, to discover registers that nobody documented.

        Returns {"coils": {addr: "0"/"1"}, "registers": {addr: {"hex": .., "value": signed int}}, "errors": [...]}.
        """
        ranges = ranges or DUMP_RANGES
        result: dict = {"coils": {}, "registers": {}, "errors": []}
        for dt, spans in ranges.items():
            for start, length in spans:
                for address in range(start, start + length, chunk):
                    num = min(chunk, start + length - address)
                    try:
                        values = await self._load_data(address, num, Operations.Get[dt])
                    except EcoforestError as err:
                        result["errors"].append(f"{'coil' if dt == DataTypes.Coil else 'reg'} {address}/{num}: {err}")
                        await asyncio.sleep(pause)
                        continue
                    for a_, v in values.items():
                        if dt == DataTypes.Coil:
                            result["coils"][a_] = v
                        else:
                            result["registers"][a_] = {"hex": v, "value": self.parse_ecoforest_int(v)}
                    await asyncio.sleep(pause)
        return result

    def _writable(self, name) -> dict:
        definition = MAPPING.get(name)
        if definition is None or not definition.get("writable", definition["entity_type"] in ("switch", "button") or definition.get("is_number", False)):
            raise Exception(f"{name} is not writable")
        return definition

    async def _write(self, data_type: int, address: int, value: int) -> None:
        if data_type == DataTypes.Coil:
            await self._request(
                data={"idOperacion": OP_TYPE_SET_SWITCH, "dir": address, "num": 1, int(bool(value)): int(bool(value))}
            )
        else:
            raw = self.convert_raw_to_ecoforest(value)
            await self._request(
                data={"idOperacion": OP_TYPE_SET_REGISTER, "dir": address, "num": 1, raw: raw}
            )

    async def turn_switch(self, name, on: bool | None = False) -> EcoGeoDevice:
        definition = self._writable(name)
        await self._write(definition["data_type"], definition["address"], 1 if on else 0)
        return await self.get()

    async def set_numeric_value(self, name, value: float) -> EcoGeoDevice:
        definition = self._writable(name)
        if "min" in definition and not definition["min"] <= value <= definition["max"]:
            raise ValueError(f"{value} out of range [{definition['min']}, {definition['max']}] for {name}")

        await self._write(DataTypes.Register, definition["address"], round(value * 10))
        return await self.get()

    async def set_raw_value(self, name, value: int) -> EcoGeoDevice:
        """Write an integer register as is (no /10 scaling)."""
        definition = self._writable(name)
        if "options" in definition and value not in definition["options"]:
            raise ValueError(f"{value} is not a valid option for {name}")

        await self._write(DataTypes.Register, definition["address"], int(value))
        return await self.get()

    def _parse(self, response: str) -> list[str]:
        lines = response.split('\n')

        a, _, b = lines[0].strip().partition('=')
        if a not in ["error_geo_get_reg", "error_geo_get_bit", "error_geo_set_reg", "error_geo_set_bit"] or b.strip() != "0":
            raise EcoforestError("bad response: {}".format(response.strip()))

        # header fields vary (dir=..&num=..), callers take the last `length` values
        return lines[1].strip().split('&') if len(lines) > 1 else []

    def parse_model_name(self, data):
        model_dictionary = ["--"] + [*string.digits] + [*string.ascii_uppercase]

        result = ''
        for address in range(MODEL_ADDRESS, MODEL_ADDRESS + MODEL_LENGTH):
            result += model_dictionary[self.parse_ecoforest_int(data[DataTypes.Register][address])]

        return result

    def convert_to_ecoforest_int(self, value):
        return self.convert_raw_to_ecoforest(round(value * 10))

    def convert_raw_to_ecoforest(self, value: int) -> str:
        """16 bit two's complement as 4 hex digits."""
        return f"{int(value) & 0xFFFF:04x}"

    def parse_ecoforest_int(self, value):
        result = int(value, 16)
        return result if result < 32768 else result - 65536

    def parse_ecoforest_bool(self, value):
        return bool(int(value))

    def parse_ecoforest_float(self, value):
        return self.parse_ecoforest_int(value) / 10

    def get_alarm(data):
        alarm_registers = [
            1,	#Clock Board fault or not connected
            2,	#Extended memory fault
            3,	#Low outdoor temp. & Low ground temp.
            7,	#AI3 Probe failure. Compressor discharge pressure
            8,	#AI4 Probe failure. Brine outlet temperature
            9,	#AI5 Probe failure. Brine return temperature
            10,	#AI6 Probe failure. Brine circuit pressure
            11,	#AI7 Probe failure. Heating outlet temperature
            12,	#AI8 Probe failure. Heating inlet temperature
            13,	#AI9 Probe failure. Heating circuit pressure
            14,	#AI10 Probe failure. Tank temperature 1 (DHW)
            15,	#AI11 Probe failure. Outdoor temperature probe
            16,	#AI12 Probe fault
            17,	#Low brine inlet temperature
            18,	#High discharge pressure
            19,	#High discharge temperature
            20,	#Inverter temperature
            21,	#Low brine outlet temperature
            24,	#Ecogeo internal probes fault
            25,	#Low pressure brine circuit
            26,	#Low pressure Heating circuit
            33,	#Evaporation temperature
            34,	#Low suction pressure
            36,	#AI2 Probe failure Compressor suction Pressure
            37,	#AI1 Probe failure. Compressor suction temperature
            38,	#Low superheat (lowSH)
            39,	#Low evaporation temperature (LOP)
            40,	#High evaporation temperature (MOP)
            41,	#Low suction temperature
            212,	#Inverter comms fault
            213,	#High brine temperature
            214,	#pCOe number:AI13 Analog input probe on channel 1 disconnected or broken
            215,	#pCOe number:AI14 Analog input probe on channel 2 disconnected or broken
            216,	#pCOe number:AI15 Analog input probe on channel 3 disconnected or broken
            217,	#pCOe number:AI16 Analog input probe on channel 4 disconnected or broken
            218,	#pCOe number: pCOe offline
            219,	#th-T 1 Error (thermostat for DG1) **
            220,	#th-T 1 offline (thermostat for DG1) **
            221,	#th-T 2 Error (thermostat for SG2) **
            222,	#th-T 2 offline (thermostat for SG2) **
            223,	#th-T 3 Error (thermostat for SG3) **
            224,	#th-T 3 offline (thermostat for SG3) **
            225,	#th-T 4 Error (thermostat for SG4) **
            226,	#th-T 4 offline (thermostat for SG4) **
        ]

        for address in alarm_registers:
            if data[DataTypes.Coil][address] == "0":
                continue
            return address

        return 0
