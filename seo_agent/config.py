import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
DATA_DIR = Path(os.getenv("SEO_DATA_DIR", str(ROOT / "data"))).resolve()
MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
API_KEY = os.getenv("OPENAI_API_KEY", "")
USER_AGENT = "SEOAgent/1.0"
