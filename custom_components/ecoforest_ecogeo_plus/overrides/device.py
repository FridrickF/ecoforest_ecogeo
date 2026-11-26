from dataclasses import dataclass, field


@dataclass
class EcoGeoDevice:
    is_supported: bool
    model_name: str

    state: dict[str, any] | None = None
    attributes: dict[str, dict] = field(default_factory=dict)  # extra state attributes per entity key

    @classmethod
    def build(cls, model_name: str, data: dict[str, any], attributes: dict[str, dict] | None = None):  # -> EcoGeoDevice:

        return EcoGeoDevice(
            is_supported=True,
            model_name=model_name,
            state=data,
            attributes=attributes or {},
        )
