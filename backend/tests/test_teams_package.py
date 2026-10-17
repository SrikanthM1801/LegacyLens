"""Teams app package builder: manifest filled from the bot id, icons sized as Teams requires."""

import json
import struct
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import build_teams_package as pkg  # noqa: E402

BOT_ID = "11111111-2222-3333-4444-555555555555"


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_package_contains_manifest_and_icons(tmp_path):
    out = pkg.build(BOT_ID, "https://abc123-8000.inc1.devtunnels.ms", out=tmp_path / "app.zip")
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["color.png", "manifest.json", "outline.png"]
        manifest = json.loads(z.read("manifest.json"))
        assert _png_size(z.read("color.png")) == (192, 192)
        assert _png_size(z.read("outline.png")) == (32, 32)

    assert manifest["name"]["short"] == "LegacyLens"
    assert manifest["bots"][0]["botId"] == BOT_ID
    assert manifest["webApplicationInfo"]["id"] == BOT_ID
    assert manifest["supportsChannelFeatures"] == "tier1"  # required for 'team' scope on manifest >= 1.25
    assert manifest["validDomains"] == ["abc123-8000.inc1.devtunnels.ms"]
    rsc = {p["name"] for p in manifest["authorization"]["permissions"]["resourceSpecific"]}
    assert rsc == {"ChannelMessage.Read.Group", "ChatMessage.Read.Chat"}
    assert "{{" not in json.dumps(manifest)


def test_rejects_missing_bot_id():
    with pytest.raises(ValueError):
        pkg.render_manifest("")
