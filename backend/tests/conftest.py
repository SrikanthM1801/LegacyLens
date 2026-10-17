import os
import tempfile

# Force mock mode and isolate runtime state before the app (and its cached settings) is imported.
os.environ["USE_MOCKS"] = "true"
# Run the Teams bot anonymously in tests, whatever .env holds.
for _k in ("BOT_APP_ID", "BOT_APP_PASSWORD", "BOT_APP_TENANT_ID"):
    os.environ[_k] = ""
_tmp = tempfile.mkdtemp(prefix="legacylens-test-")
os.environ["GRAPH_PATH"] = os.path.join(_tmp, "graph.json")
os.environ["STATE_DIR"] = os.path.join(_tmp, "state")
