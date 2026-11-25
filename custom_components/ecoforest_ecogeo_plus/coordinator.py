"""The ecoforest coordinator."""

import logging
from datetime import timedelta

from pyecoforest.exceptions import EcoforestError

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .overrides.api import EcoGeoApi, MAPPING, key_of_unique_id
from .overrides.device import EcoGeoDevice
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, SLOW_REFRESH_SECONDS

_LOGGER = logging.getLogger(__name__)


class EcoforestCoordinator(DataUpdateCoordinator[EcoGeoDevice]):
    """DataUpdateCoordinator to gather data from ecoforest device."""

    def __init__(self, hass: HomeAssistant, api: EcoGeoApi) -> None:
        """Initialize DataUpdateCoordinator."""

        seconds = DEFAULT_SCAN_INTERVAL
        super().__init__(
            hass,
            _LOGGER,
            name="ecoforest_ecogeo_plus",
            update_interval=timedelta(seconds=seconds),
        )
        if self.config_entry is not None:
            seconds = int(self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
            self.update_interval = timedelta(seconds=seconds)
        self.api = api
        self.api.slow_every = max(1, round(SLOW_REFRESH_SECONDS / seconds))

    def _enabled_keys(self) -> set[str]:
        """Keys of the entities that are enabled (registry) or enabled by default (not yet registered)."""
        registry = er.async_get(self.hass)
        state: dict[str, bool] = {}
        for entry in er.async_entries_for_config_entry(registry, self.config_entry.entry_id):
            key = key_of_unique_id(entry.unique_id)
            if key:
                state[key] = state.get(key, False) or entry.disabled_by is None
        return {k for k, d in MAPPING.items() if state.get(k, d.get("enabled_default", True))}

    async def _async_update_data(self) -> EcoGeoDevice:
        """Fetch all device and sensor data from api."""
        self.api.wanted = self._enabled_keys()
        try:
            data = await self.api.get()
        except EcoforestError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

        _LOGGER.debug("Ecoforest data: %s", data)
        return data
