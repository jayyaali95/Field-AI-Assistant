import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://copilot:copilot@localhost:5432/copilot")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
OCR_LANG = os.environ.get("OCR_LANG", "eng")

# The asset master. Questions that name an asset get its model and serial added to the search.
FLEET_REGISTER = "corpus/data/fleet_register.csv"
