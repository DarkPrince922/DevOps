import os, sys
from pathlib import Path

if not os.environ.get("TELEGRAM_TOKEN"):
    print("missing env: TELEGRAM_TOKEN")
    sys.exit(1)

if not (os.environ.get("AI_API_KEY")
        or os.environ.get("ROUTERAI_API_KEY")
        or os.environ.get("CODEX_API_KEY")
        or os.environ.get("OPENAI_API_KEY")):
    print("missing env: AI_API_KEY")
    sys.exit(1)

data_dir = Path(os.environ.get("DATA_DIR", "/app/data"))
data_dir.mkdir(parents=True, exist_ok=True)
test = data_dir / ".healthcheck"
test.write_text("ok")
test.unlink(missing_ok=True)
import app.state  # noqa
print("ok")
