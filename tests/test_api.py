import json
import re
from pathlib import Path

import pytest
from pyecoforest.exceptions import EcoforestConnectionError, EcoforestError

from custom_components.ecoforest_ecogeo_plus.overrides import api as api_mod
from custom_components.ecoforest_ecogeo_plus.overrides.api import EcoGeoApi, MAPPING
from custom_components.ecoforest_ecogeo_plus.overrides.api_types import DataTypes, Operations
from custom_components.ecoforest_ecogeo_plus.overrides.registers import OPTIONAL_REQUESTS

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "ecoforest_ecogeo_plus"


class FakeApi(EcoGeoApi):
    """EcoGeoApi with the HTTP layer replaced by a dict of registers/coils."""

    def __init__(self, registers=None, coils=None, fail=()):
        super().__init__("http://x/", "u", "p")
        self.registers = registers or {}
        self.coils = coils or {}
        self.fail = set(fail)
        self.writes = []
        self.reads = []

    async def _request(self, data=None):
        op, addr, num = data["idOperacion"], data["dir"], data["num"]
        if op in (Operations.Get[DataTypes.Coil], Operations.Get[DataTypes.Register]):
            if addr in self.fail:
                raise EcoforestConnectionError("boom")
            self.reads.append((op, addr))
            src, default = (self.coils, "0") if op == 2001 else (self.registers, "0000")
            return [src.get(a, default) for a in range(addr, addr + num)]
        self.writes.append(data)
        return []


def regs(**kw):
    r = {5323 + i: f"{v:04x}" for i, v in enumerate([2, 3, 4, 5, 6, 7])}
    r.update({k: v for k, v in kw.items()})
    return r


@pytest.mark.asyncio
async def test_existing_entities_unchanged():
    api = FakeApi(registers={**regs(), 200: "0160", 5082: "0258"})
    dev = await api.get()
    assert dev.state["t_heating"] == 35.2
    assert dev.state["power_electric"] == 600


@pytest.mark.asyncio
async def test_new_registers_and_state_texts():
    api = FakeApi(
        registers={**regs(), 5220: "0002", 5002: "0bb8", 4: "00c8", 5093: "000e", 5094: "001e", 5095: "0018", 5096: "000b", 5097: "001a"},
        coils={128: "1", 127: "0", 53: "1"},
    )
    dev = await api.get()
    assert dev.state["hp_state"] == "on"
    assert dev.state["compressor_rpm"] == 3000
    assert dev.state["t_production_in"] == 20.0
    assert dev.state["season_mode"] == "winter"
    assert dev.state["switch_bms_onoff"] is True
    assert dev.state["device_clock"] == "2026-11-24 14:30"
    assert dev.state["select_demand_dg1"] == 0


@pytest.mark.asyncio
async def test_unreadable_optional_block_is_skipped_then_given_up():
    api = FakeApi(registers=regs(), fail={5220})
    dev = await api.get()
    assert dev.state["hp_state"] is None
    assert dev.state["power_electric"] == 0
    for _ in range(5):
        await api.get()
    # tried MAX_OPTIONAL_FAILURES times, then not asked again
    assert api._failures[(DataTypes.Register, 5220)] == api_mod.MAX_OPTIONAL_FAILURES


@pytest.mark.asyncio
async def test_only_blocks_of_enabled_entities_are_read():
    api = FakeApi(registers=regs())
    api.wanted = {"hp_state", "power_electric"}
    await api.get()
    asked = {a for _, a in api.reads}
    assert 5220 in asked and 134 not in asked and 2000 not in asked and 7002 not in asked and 5144 not in asked
    api2 = FakeApi(registers=regs())
    api2.wanted = {"number_bus_pool_setpoint", "season_mode"}
    await api2.get()
    asked2 = {a for _, a in api2.reads}
    assert 134 in asked2 and 127 in asked2 and 5220 not in asked2


def test_parse_variants_and_errors():
    api = FakeApi()
    assert api._parse("error_geo_get_reg=0\ndir=1&num=2&00ff&0100") == ["dir=1", "num=2", "00ff", "0100"]
    assert api._parse("error_geo_get_reg=0\n1&dir=1&num=2&00ff&0100")[-2:] == ["00ff", "0100"]
    with pytest.raises(EcoforestError):
        api._parse("error_geo_get_reg=-4\n-4")


class RawApi(EcoGeoApi):
    """Returns canned raw reply tokens to _load_data."""

    def __init__(self, replies):
        super().__init__("http://x/", "u", "p")
        self.replies = list(replies)

    async def _request(self, data=None):
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.mark.asyncio
async def test_load_data_validates_and_retries(monkeypatch):
    monkeypatch.setattr(api_mod, "RETRY_PAUSE", 0)
    ok = await RawApi([EcoforestError("-4"), ["dir=1", "num=2", "0001", "00ff"]])._load_data(1, 2, 2002)
    assert ok == {1: "0001", 2: "00ff"}
    with pytest.raises(EcoforestError):
        await RawApi([["num=2", "0001"], ["num=2", "0001"]])._load_data(1, 2, 2002)  # too short / not hex
    with pytest.raises(EcoforestError):
        await RawApi([["x", "zz", "0001"], ["x", "zz", "0001"]])._load_data(1, 2, 2002)


@pytest.mark.asyncio
async def test_slow_blocks_polled_every_n_cycles():
    api = FakeApi(registers=regs())
    await api.get()
    first = sum(1 for _, a in api.reads if a == 5144)
    await api.get()
    assert sum(1 for _, a in api.reads if a == 5144) == first == 1


@pytest.mark.asyncio
async def test_missing_probe_stays_none():
    api = FakeApi(registers={**regs(), 11: "f830"})  # -2000/10 -> -200.0? use -9999 -> -999.9
    api.registers[11] = f"{(-9999) & 0xFFFF:04x}"
    dev = await api.get()
    assert dev.state["t_outdoor"] is None


@pytest.mark.asyncio
async def test_writes():
    api = FakeApi(registers=regs())
    await api.turn_switch("switch_bms_onoff", True)
    assert api.writes[-1] == {"idOperacion": 2011, "dir": 53, "num": 1, 1: 1}
    await api.turn_switch("switch_surplus_control", True)
    assert api.writes[-1] == {"idOperacion": 2012, "dir": 7002, "num": 1, "0001": "0001"}
    await api.set_numeric_value("number_bus_heating_dg1_setpoint", 35)
    assert api.writes[-1] == {"idOperacion": 2012, "dir": 135, "num": 1, "015e": "015e"}
    await api.set_numeric_value("number_dhw_setpoint", 48.3)  # 48.3*10 = 482.99999 without rounding
    assert api.writes[-1]["dir"] == 17 and "01e3" in api.writes[-1]
    await api.set_raw_value("select_bus_program", 2)
    assert api.writes[-1] == {"idOperacion": 2012, "dir": 5221, "num": 1, "0002": "0002"}


@pytest.mark.asyncio
async def test_write_guards():
    api = FakeApi(registers=regs())
    with pytest.raises(ValueError):
        await api.set_numeric_value("number_bus_heating_dg1_setpoint", 90)
    with pytest.raises(ValueError):
        await api.set_raw_value("select_bus_program", 9)
    with pytest.raises(Exception):
        await api.turn_switch("t_outdoor", True)  # a sensor is not writable
    with pytest.raises(Exception):
        await api.set_numeric_value("hp_state", 1)
    assert api.writes == []


def test_negative_values():
    api = FakeApi()
    assert api.convert_to_ecoforest_int(-5.5) == "ffc9"
    assert api.parse_ecoforest_int("ffc9") == -55
    assert api.parse_ecoforest_int("8000") == -32768


def test_every_key_has_a_translation_and_unique_addresses():
    for lang in ("en", "it"):
        tr = json.loads((BASE / "translations" / f"{lang}.json").read_text())
        have = {k for p in tr["entity"].values() for k in p}
        assert set(MAPPING) <= have, (lang, set(MAPPING) - have)
    # no two read-only sensors on the same register/coil
    seen = {}
    for k, d in MAPPING.items():
        if d["type"] == "custom" or d["entity_type"] in ("switch", "select", "button") or d.get("is_number"):
            continue
        key = (d["data_type"], d["address"])
        assert key not in seen, (k, seen[key])
        seen[key] = k


def test_every_mapped_address_is_covered_by_a_request():
    covered = {DataTypes.Coil: set(), DataTypes.Register: set()}
    for dt in covered:
        for r in api_mod.REQUESTS[dt] + OPTIONAL_REQUESTS[dt]:
            covered[dt].update(range(r["address"], r["address"] + r["length"]))
    for k, d in MAPPING.items():
        if d["type"] != "custom":
            assert d["address"] in covered[d["data_type"]], k


def test_key_of_unique_id():
    from custom_components.ecoforest_ecogeo_plus.overrides.api import key_of_unique_id
    assert key_of_unique_id("ec_b_x_hp_state") == "hp_state"
    assert key_of_unique_id("ec_b_x_select_hp_state") == "select_hp_state"
    assert key_of_unique_id("alias_t_dhw") == "t_dhw"
    assert key_of_unique_id("alias_t_dhw_start") == "t_dhw_start"
    assert key_of_unique_id("nonsense") is None


def test_writable_entities_are_not_also_created_as_sensors():
    """Number/switch/select definitions with entity_category config cannot be sensors (HA refuses)."""
    from custom_components.ecoforest_ecogeo_plus import sensor  # noqa: F401  (imports with real HA)
    import inspect
    assert 'not definition.get("writable")' in inspect.getsource(sensor.async_setup_entry)
    # the original three numbers keep their historical read-only sensor twin
    for key in ("number_dhw_setpoint", "number_dhw_dt_start", "number_dhw_htr_set"):
        assert not MAPPING[key].get("writable")
