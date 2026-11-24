import json
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.exceptions import ConfigEntryNotReady
from pyecoforest.exceptions import EcoforestAuthenticationRequired, EcoforestConnectionError

from .const import DOMAIN
from .coordinator import EcoforestCoordinator
from homeassistant.helpers import entity_registry as er

from .overrides.api import EcoGeoApi, is_stale_registry_entry

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.NUMBER, Platform.BUTTON, Platform.SELECT]


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ecoforest from a config entry."""

    api = EcoGeoApi(entry.data[CONF_HOST], entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    try:
        device = await api.get()
        _LOGGER.debug("Ecoforest: %s", device)
    except EcoforestAuthenticationRequired:
        _LOGGER.error("Authentication on device")
        return False
    except EcoforestConnectionError as err:
        _LOGGER.error("Error communicating with device")
        raise ConfigEntryNotReady from err

    coordinator = EcoforestCoordinator(hass, api)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    _remove_stale_entities(hass, entry)
    _register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "dump_registers"):
        return

    async def dump_registers(call: ServiceCall) -> dict:
        """Read-only scan of the heat pump registers, written to /config/ecoforest_dump.json."""
        out = {}
        for entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
            out[coordinator.data.model_name] = await coordinator.api.dump()
        path = hass.config.path("ecoforest_dump.json")
        await hass.async_add_executor_job(lambda: open(path, "w").write(json.dumps(out, indent=1)))
        _LOGGER.info("Register dump written to %s", path)
        return {"file": path, "devices": {k: {"coils": len(v["coils"]), "registers": len(v["registers"]), "errors": v["errors"]} for k, v in out.items()}}

    hass.services.async_register(DOMAIN, "dump_registers", dump_registers, supports_response=SupportsResponse.OPTIONAL)


def _remove_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop registry entries that no code creates any more (e.g. nameless sensor twins of numbers)."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if is_stale_registry_entry(entity.domain, entity.unique_id):
            _LOGGER.info("Removing stale entity %s", entity.entity_id)
            registry.async_remove(entity.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "dump_registers")

    return unload_ok
