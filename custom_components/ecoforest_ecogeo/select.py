"""Select platform for Ecoforest (BMS remote control: state, program, demands)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ALIAS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EcoforestCoordinator
from .entity import EcoforestEntity
from .overrides.api import MAPPING


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ecoforest select platform."""
    coordinator: EcoforestCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    device_alias = config_entry.data[CONF_ALIAS] if CONF_ALIAS in config_entry.data else None
    async_add_entities(
        EcoforestSelectEntity(coordinator, key, definition, device_alias)
        for key, definition in MAPPING.items()
        if definition["entity_type"] == "select"
    )


class EcoforestSelectEntity(EcoforestEntity, SelectEntity):
    """A register with a fixed set of values."""

    @property
    def options(self) -> list[str]:
        return list(self._definition["options"].values())

    @property
    def current_option(self) -> str | None:
        value = self.data.state.get(self.entity_description.key)
        return self._definition["options"].get(value)

    async def async_select_option(self, option: str) -> None:
        raw = next(k for k, v in self._definition["options"].items() if v == option)
        await self.coordinator.api.set_raw_value(self.entity_description.key, raw)
        await self.coordinator.async_request_refresh()
