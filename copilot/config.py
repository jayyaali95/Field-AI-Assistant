import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://copilot:copilot@localhost:5432/copilot")
# "<provider>:<model>", passed to LangChain's init_chat_model, e.g. "openai:gpt-5" or "anthropic:claude-opus-5-5".
# The provider reads its own key from the environment (ANTHROPIC_API_KEY, OPENAI_API_KEY, ...).
LLM_MODEL = os.environ.get("LLM_MODEL", "anthropic:claude-opus-5-5")
# Grades eval answers. Same format; point it at another provider to avoid a model grading itself.
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", LLM_MODEL)
OCR_LANG = os.environ.get("OCR_LANG", "eng")

# The asset master. Questions that name an asset get its model and serial added to the search.
FLEET_REGISTER = "corpus/data/fleet_register.csv"
