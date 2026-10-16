import os

# Force mock mode before the app (and its cached settings) is imported.
os.environ["USE_MOCKS"] = "true"
