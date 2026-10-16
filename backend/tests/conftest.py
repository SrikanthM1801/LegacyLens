import os
import tempfile

# Force mock mode and isolate runtime state before the app (and its cached settings) is imported.
os.environ["USE_MOCKS"] = "true"
_tmp = tempfile.mkdtemp(prefix="legacylens-test-")
os.environ["GRAPH_PATH"] = os.path.join(_tmp, "graph.json")
os.environ["STATE_DIR"] = os.path.join(_tmp, "state")
