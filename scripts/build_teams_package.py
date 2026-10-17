"""
Build the LegacyLens Teams app package (manifest.json + icons, zipped) for sideloading.

Run from the repo root:  .venv/bin/python scripts/build_teams_package.py

Reads BOT_APP_ID (and optionally PUBLIC_BASE_URL) from settings / .env, fills
bot/manifest/manifest.template.json, draws the two icons if they are missing,
and writes bot/build/LegacyLens.zip. Upload that zip in Teams -> Apps ->
Manage your apps -> Upload a custom app. Bump "version" in the template
before re-uploading a changed package. Never prints secrets.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import zipfile
import zlib
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import REPO_ROOT, get_settings  # noqa: E402

MANIFEST_DIR = REPO_ROOT / "bot" / "manifest"
BUILD_DIR = REPO_ROOT / "bot" / "build"
GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")

ACCENT = (0x2B, 0x5B, 0xD7, 255)
WHITE = (255, 255, 255, 255)
CLEAR = (0, 0, 0, 0)


# --- icons (pure Python PNG, so no Pillow dependency) -----------------------
def _png(pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    h, w = len(pixels), len(pixels[0])
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in pixels)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def _lens(size: int, fg, bg) -> bytes:
    """A magnifying glass: ring in the upper-left, handle to the lower-right."""
    cx = cy = size * 0.42
    r_out, r_in = size * 0.30, size * 0.21
    half = size * 0.06  # handle half-thickness
    pixels = []
    for y in range(size):
        row = []
        for x in range(size):
            d = ((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2) ** 0.5
            ring = r_in <= d <= r_out
            # handle: band along the diagonal, beyond the ring, inside the canvas margin
            t = (x + 0.5 - cx + y + 0.5 - cy) / 2
            off = abs((x + 0.5 - cx) - (y + 0.5 - cy)) / 2 ** 0.5
            handle = off <= half and r_out * 0.7 <= t * 2 ** 0.5 <= size * 0.72
            row.append(fg if ring or handle else bg)
        pixels.append(row)
    return _png(pixels)


def ensure_icons() -> None:
    if not (MANIFEST_DIR / "color.png").exists():
        (MANIFEST_DIR / "color.png").write_bytes(_lens(192, WHITE, ACCENT))
    if not (MANIFEST_DIR / "outline.png").exists():
        (MANIFEST_DIR / "outline.png").write_bytes(_lens(32, WHITE, CLEAR))


# --- manifest ---------------------------------------------------------------
def render_manifest(bot_app_id: str, public_base_url: str = "") -> dict:
    if not GUID_RE.match(bot_app_id or ""):
        raise ValueError("BOT_APP_ID must be the Azure Bot's Microsoft App ID (a GUID)")
    text = (MANIFEST_DIR / "manifest.template.json").read_text(encoding="utf-8")
    manifest = json.loads(text.replace("{{BOT_APP_ID}}", bot_app_id))
    host = urlparse(public_base_url).hostname if public_base_url else None
    if host:
        manifest["validDomains"] = [host]
    return manifest


def build(bot_app_id: str, public_base_url: str = "", out: Path | None = None) -> Path:
    ensure_icons()
    manifest = render_manifest(bot_app_id, public_base_url)
    out = out or BUILD_DIR / "LegacyLens.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:  # files must sit at the zip root
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.write(MANIFEST_DIR / "color.png", "color.png")
        z.write(MANIFEST_DIR / "outline.png", "outline.png")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--bot-id", help="override BOT_APP_ID from .env")
    args = parser.parse_args()
    s = get_settings()
    try:
        out = build(args.bot_id or s.bot_app_id, s.public_base_url)
    except ValueError as e:
        print(f"FAIL  {e}")
        sys.exit(1)
    print(f"ok    wrote {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
