"""Every test runs against an isolated RJS_HOME / RJS_DATA_DIR holding the
backend-ai-eu example profile, so nothing reads the developer's real config."""

import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "src" / "remote_jobs_digest" / "examples" / "backend-ai-eu.yaml"

_tmp = Path(tempfile.mkdtemp(prefix="rjs-test-"))
(_tmp / "home").mkdir()
shutil.copyfile(EXAMPLE, _tmp / "home" / "config.yaml")
os.environ["RJS_HOME"] = str(_tmp / "home")
os.environ["RJS_DATA_DIR"] = str(_tmp / "data")
for var in ("RJS_CONFIG", "RJS_OUTPUT_DIR", "RJS_LLM_API_KEY", "GROQ_API_KEY",
            "GEMINI_API_KEY", "RJS_LLM_BASE_URL", "TELEGRAM_BOT_TOKEN"):
    os.environ.pop(var, None)
