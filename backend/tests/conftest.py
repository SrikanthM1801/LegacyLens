import os
import tempfile

# Force mock mode and isolate runtime state before the app (and its cached settings) is imported.
os.environ["USE_MOCKS"] = "true"
# Run the Teams bot anonymously in tests, whatever .env holds.
for _k in ("BOT_APP_ID", "BOT_APP_PASSWORD", "BOT_APP_TENANT_ID"):
    os.environ[_k] = ""
# Admin/dashboard settings come from the test, never from the developer's .env.
for _k in ("ADMIN_API_KEY", "PROJECT_ACCOUNTS"):
    os.environ[_k] = ""
os.environ["DASHBOARD_DEMO"] = "false"
os.environ["ORG_NAME"] = "Organisation"
os.environ["DEFAULT_ACCOUNT_NAME"] = "Default account"
os.environ["UI_DIST_DIR"] = os.path.join(os.path.dirname(__file__), "no-ui-build-in-tests")  # a local frontend/dist must not change test results
# Never touch real Graph / Azure Tables from tests.
os.environ["LIVE_M365"] = "false"
os.environ["USE_SAMPLE_DATA"] = "true"
for _k in ("ADMIN_API_KEY", "SHAREPOINT_SITE_URL"):
    os.environ[_k] = ""
_tmp = tempfile.mkdtemp(prefix="legacylens-test-")
os.environ["GRAPH_PATH"] = os.path.join(_tmp, "graph.json")
os.environ["STATE_DIR"] = os.path.join(_tmp, "state")
