"""Load the api layer without importing Home Assistant (package __init__ is skipped)."""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for name, path in (
    ("custom_components", ROOT / "custom_components"),
    ("custom_components.ecoforest_ecogeo_plus", ROOT / "custom_components" / "ecoforest_ecogeo_plus"),
):
    mod = types.ModuleType(name)
    mod.__path__ = [str(path)]
    sys.modules[name] = mod
