import json
import re
from pathlib import Path

import pytest
from pyecoforest.exceptions import EcoforestConnectionError, EcoforestError

from custom_components.ecoforest_ecogeo_plus.overrides import api as api_mod
from custom_components.ecoforest_ecogeo_plus.overrides.api import EcoGeoApi, MAPPING
from custom_components.ecoforest_ecogeo_plus.overrides.api_types import DataTypes, Operations
from custom_components.ecoforest_ecogeo_plus.overrides.registers import OPTIONAL_REQUESTS, addresses_of

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
        await RawApi([["num=2", "0001"]] * 3)._load_data(1, 2, 2002)  # too short / not hex
    with pytest.raises(EcoforestError):
        await RawApi([["x", "zz", "0001"]] * 3)._load_data(1, 2, 2002)
    # -5 (busy) twice, then a good answer: the third attempt wins
    ok = await RawApi([EcoforestError("-5"), EcoforestError("-5"), ["0007"]])._load_data(9, 1, 2002)
    assert ok == {9: "0007"}


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


@pytest.mark.asyncio
async def test_power_output_follows_season_and_labels():
    base = {**regs(), 5083: f"{4365:04x}", 5185: f"{3800:04x}"}
    winter = await FakeApi(registers=base, coils={128: "1"}).get()
    assert winter.state["power_heating"] == 4365 and winter.state["power_cooling"] == 3800
    assert winter.state["power_output"] == 4365  # not 8165 anymore
    summer = await FakeApi(registers=base, coils={127: "1"}).get()
    assert summer.state["power_output"] == 3800
    # summer with HTR recovery: PF 12.0 x 480 W = 5760 W > evaporation 3800 W
    htr = await FakeApi(registers={**base, 5082: f"{480:04x}", 31: f"{120:04x}"}, coils={127: "1"}).get()
    assert htr.state["power_output"] == 5760
    # summer without recovery: PF ~ EER, within 3 % -> exact evaporation value
    nohtr = await FakeApi(registers={**base, 5082: f"{480:04x}", 31: f"{80:04x}"}, coils={127: "1"}).get()
    assert nohtr.state["power_output"] == 3800
    # winter with recovery (PF clearly above COP): 9.0 x 480 = 4320 > base 3000
    w_htr = await FakeApi(registers={**base, 5083: f"{3000:04x}", 5082: f"{480:04x}", 31: f"{90:04x}"}, coils={128: "1"}).get()
    assert w_htr.state["power_output"] == 4320
    # winter without recovery: PF x P within 3 % of the exact value -> keep the exact measurement
    w = await FakeApi(registers={**base, 5083: f"{4365:04x}", 5082: f"{480:04x}", 31: f"{91:04x}"}, coils={128: "1"}).get()
    assert w.state["power_output"] == 4365
    unknown = await FakeApi(registers=base).get()
    assert unknown.state["power_output"] == 4365
    assert winter.state["season_mode"] == "winter"


@pytest.mark.asyncio
async def test_program_mode_is_read_only_and_translated():
    api = FakeApi(registers={**regs(), 5024: "0002"})
    dev = await api.get()
    assert dev.state["program_mode"] == "auto"
    with pytest.raises(Exception):
        await api.set_raw_value("program_mode", 1)  # not writable
    assert api.writes == []
    for lang in ("en", "it"):
        tr = json.loads((BASE / "translations" / f"{lang}.json").read_text())
        assert set(tr["entity"]["sensor"]["season_mode"]["state"]) == {"none", "winter", "summer"}
        assert set(tr["entity"]["sensor"]["program_mode"]["state"]) == {"winter", "summer", "auto", "mixed", "remote"}


def test_stale_registry_entries():
    from custom_components.ecoforest_ecogeo_plus.overrides.api import is_stale_registry_entry as stale
    assert stale("sensor", "x_number_bus_pool_setpoint")          # sensor twin of a writable number
    assert stale("sensor", "x_number_surplus_activation_setpoint")
    assert stale("select", "x_select_program_mode")               # key no longer exists
    assert not stale("number", "x_number_bus_pool_setpoint")
    assert not stale("sensor", "x_number_dhw_setpoint")           # original twin is kept
    assert not stale("sensor", "x_t_outdoor")
    assert not stale("select", "x_select_bus_program")
    assert not stale("sensor", "x_program_mode")


@pytest.mark.asyncio
async def test_compressor_limits_and_surplus_numbers():
    api = FakeApi(registers={**regs(), 175: f"{400:04x}", 2000: f"{35:04x}"})
    dev = await api.get()
    assert dev.state["number_night_compressor_limit"] == 40.0
    assert dev.state["number_surplus_activation_setpoint"] == 3.5
    await api.set_numeric_value("number_night_compressor_limit", 45)
    assert api.writes[-1] == {"idOperacion": 2012, "dir": 175, "num": 1, "01c2": "01c2"}
    with pytest.raises(ValueError):
        await api.set_numeric_value("number_night_compressor_limit", 10)


@pytest.mark.asyncio
async def test_monthly_energy_and_year_totals():
    # November (month 11): heating 370, cooling 0, electric 61 kWh; other months heating 100
    r = {**regs(), 5096: f"{11:04x}", 5097: f"{26:04x}"}
    for m in range(12):
        r[5144 + m] = f"{(370 if m == 10 else 100):04x}"
        r[5156 + m] = f"{0:04x}"
        r[5168 + m] = f"{(61 if m == 10 else 20):04x}"
    dev = await FakeApi(registers=r).get()
    assert "energy_condensation_11" not in dev.state  # history is now in the attributes
    assert dev.state["energy_heating_month"] == 370
    assert dev.state["energy_cooling_month"] == 0
    assert dev.state["energy_electric_month"] == 61
    assert dev.state["energy_heating_year"] == 1100 + 370
    assert dev.state["energy_electric_year"] == 61 + 220
    assert dev.state["pf_year"] == round(1470 / 281, 1)
    assert dev.state["pf_month"] == round(370 / 61, 1)
    history = dev.attributes["energy_heating_month"]
    assert list(history)[0] == "2025-12" and list(history)[-1] == "2026-11"
    assert history["2026-11"] == 370 and history["2025-12"] == 100 and len(history) == 12
    assert dev.attributes["energy_electric_month"]["2026-11"] == 61
    assert dev.attributes["pf_month"]["2026-11"] == round(370 / 61, 1)


@pytest.mark.asyncio
async def test_night_band():
    r = {**regs(), 5127: "0017", 5128: "0000", 5129: "0009", 5130: "001e"}
    dev = await FakeApi(registers=r, coils={104: "1"}).get()
    assert dev.state["night_band"] == "23:00-09:30"
    dev = await FakeApi(registers=r, coils={104: "0"}).get()
    assert dev.state["night_band"] == "off"


@pytest.mark.asyncio
async def test_dump_is_read_only_and_tolerates_errors(monkeypatch):
    monkeypatch.setattr(api_mod, "RETRY_PAUSE", 0)
    api = FakeApi(registers={**regs(), 175: "0190", 5082: "01e0"}, coils={53: "1"}, fail={31})
    out = await api.dump(ranges={DataTypes.Register: [(1, 60), (175, 1), (5082, 1)], DataTypes.Coil: [(50, 5)]}, chunk=30, pause=0)
    assert out["registers"][175] == {"hex": "0190", "value": 400}
    assert out["registers"][5082]["value"] == 480
    assert out["coils"][53] == "1"
    assert any("reg 31/30" in e for e in out["errors"])
    assert api.writes == []


@pytest.mark.asyncio
async def test_alarm_status_from_coil_50():
    ok = await FakeApi(registers=regs(), coils={50: "1"}).get()
    bad = await FakeApi(registers=regs(), coils={50: "0"}).get()
    assert ok.state["alarm_status"] == "ok" and bad.state["alarm_status"] == "alarm"


def test_whole_domestic_table_is_mapped():
    """Every address of the 2023 domestic table (pp. 8-10) is read by some entity."""
    analog = [*range(1, 9), 11, 13, 14, 15, 29, 30, 31, 94, 132, 133, *range(134, 144), *range(176, 180),
              *range(186, 190), 194, 195, 196, *range(198, 204), 2000, 2001]
    integer = [5002, 5004, 5005, *range(5080, 5084), *range(5093, 5098), *range(5144, 5180), 5185,
               *range(5220, 5228), 5281, 5282, 5285, 5286, 5287, 7002, 7003]
    boolean = [50, 53, 127, 128]
    regs_mapped, coils_mapped = set(), set()
    for d in MAPPING.values():
        for dt, addrs in addresses_of(d):
            (regs_mapped if dt == DataTypes.Register else coils_mapped).update(addrs)
    assert set(analog + integer) <= regs_mapped, sorted(set(analog + integer) - regs_mapped)
    assert set(boolean) <= coils_mapped, sorted(set(boolean) - coils_mapped)


@pytest.mark.asyncio
async def test_slow_blocks_follow_slow_every():
    api = FakeApi(registers=regs())
    api.slow_every = 3
    for _ in range(6):
        await api.get()
    assert sum(1 for _, a in api.reads if a == 5144) == 2  # polls 1 and 4


@pytest.mark.asyncio
async def test_live_values_are_read_every_poll():
    api = FakeApi(registers=regs())
    api.slow_every = 100
    for _ in range(3):
        await api.get()
    for addr in (5002, 94, 132, 202, 5220):  # compressor rpm, condensation T, superheat, EER, state
        assert sum(1 for _, a in api.reads if a == addr) == 3, addr
    assert sum(1 for _, a in api.reads if a == 5144) == 1  # monthly energies: slow


@pytest.mark.asyncio
async def test_requests_to_the_gateway_are_serialized(monkeypatch):
    import asyncio
    from pyecoforest.api import EcoforestApi
    running = {"now": 0, "max": 0}

    async def slow(self, data=None):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0.01)
        running["now"] -= 1
        return ["0000"]

    monkeypatch.setattr(EcoforestApi, "_request", slow)
    api = EcoGeoApi("http://x/", "u", "p")
    await asyncio.gather(*(api._request({"a": i}) for i in range(5)))
    assert running["max"] == 1


class BusyApi(FakeApi):
    """FakeApi whose register block at `busy` answers -5 while `busy_left` > 0."""

    def __init__(self, *a, busy=None, **kw):
        super().__init__(*a, **kw)
        self.busy = busy
        self.busy_left = 0

    async def _request(self, data=None):
        if data["dir"] == self.busy and data["idOperacion"] == 2002 and self.busy_left > 0:
            self.busy_left -= 1
            raise EcoforestError("bad response: error_geo_get_reg=-5")
        return await super()._request(data)


@pytest.mark.asyncio
async def test_required_block_busy_keeps_last_values(monkeypatch):
    monkeypatch.setattr(api_mod, "RETRY_PAUSE", 0)
    api = BusyApi(registers={**regs(), 200: "0160"}, busy=194)
    assert (await api.get()).state["t_heating"] == 35.2
    api.registers[200] = "0170"
    api.busy_left = api_mod.RETRIES  # one whole poll fails
    assert (await api.get()).state["t_heating"] == 35.2  # last value kept, no error
    assert (await api.get()).state["t_heating"] == 36.8  # next poll reads again
    api.busy_left = api_mod.RETRIES * (api_mod.MAX_STALE_POLLS + 1)
    for _ in range(api_mod.MAX_STALE_POLLS):
        await api.get()
    with pytest.raises(EcoforestError):
        await api.get()


@pytest.mark.asyncio
async def test_required_block_busy_without_previous_values_fails(monkeypatch):
    monkeypatch.setattr(api_mod, "RETRY_PAUSE", 0)
    api = BusyApi(registers=regs(), busy=194)
    api.busy_left = api_mod.RETRIES
    with pytest.raises(EcoforestError):
        await api.get()


@pytest.mark.asyncio
async def test_registers_found_with_the_dumps():
    api = FakeApi(registers={
        **regs(), 5062: "0003", 5065: "0136", 5066: "00fd", 214: "01a4", 215: "0105", 58: "0019",
        97: "0105", 5033: "01c4", 5034: "01e2", 5210: "03d4", 5280: "000f", 5296: "0064", 5138: "8840",
    })
    s = (await api.get()).state
    assert s["compressor_state"] == "running"
    assert s["t_buffer_heating_stop"] == 31.0
    assert s["t_buffer_heating_setpoint"] == 26.1
    assert s["buffer_heating_start_dt"] == 2.5
    assert s["t_dg1_heating_setpoint"] == 26.1
    assert s["t_sg2_heating_setpoint"] == 25.3
    assert s["sg2_regulation"] == 98.0
    assert s["t_dhw_setpoint_effective"] == 42.0
    assert s["pump_brine"] == 45.2
    assert s["pump_production"] == 48.2
    assert s["startup_countdown"] == 15
    assert s["ground_ratio"] == 100
    assert s["status_bits"] == -30656
    for key in ("compressor_state", "t_buffer_heating_setpoint", "pump_brine", "sg2_regulation"):
        assert MAPPING[key]["enabled_default"] is True
    api.registers[5062] = "0002"  # never seen: unknown state -> None, not an error
    assert (await api.get()).state["compressor_state"] is None


@pytest.mark.asyncio
async def test_working_mode():
    async def mode(state, coils):
        return (await FakeApi(registers={**regs(), 5062: f"{state:04x}"}, coils=coils).get()).state["working_mode"]
    assert await mode(0, {58: "1"}) == "off"
    assert await mode(1, {}) == "starting"
    assert await mode(3, {58: "1", 128: "1"}) == "dhw"
    assert await mode(3, {128: "1"}) == "heating"
    assert await mode(3, {127: "1"}) == "cooling"

