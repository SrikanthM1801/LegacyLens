"""
Run the ingestion pipeline from the CLI (same code path as POST /ingest).

    .venv/bin/python scripts/ingest.py [--source data/sample] [--keep]

Respects USE_MOCKS from .env. --keep skips the index/graph reset (only safe for new artifacts).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.ingestion.pipeline import IngestSourceError, run_ingest  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", help="directory under data/ (default: data/sample)")
    ap.add_argument("--keep", action="store_true", help="do not reset the index and graph first")
    args = ap.parse_args()
    try:
        resp = run_ingest(args.source, reset=not args.keep)
    except IngestSourceError as e:
        sys.exit(f"error: {e}")
    print(json.dumps(resp.model_dump(), indent=2))
    sys.exit(1 if resp.warnings else 0)


if __name__ == "__main__":
    main()
