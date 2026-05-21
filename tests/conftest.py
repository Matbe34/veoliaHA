"""Make `custom_components.veolia_water` importable directly as a package."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
