"""The backend serves the built dashboard at /app so one public URL (the dev tunnel) carries the API and the UI."""

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def make_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<!doctype html><title>LegacyLens Transparency</title><script src="./assets/app.js"></script>')
    (dist / "assets" / "app.js").write_text("console.log('dashboard')")
    return dist


def test_serves_the_built_dashboard_next_to_the_api(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "ui_dist_dir", make_dist(tmp_path))
    c = TestClient(create_app())
    page = c.get("/app/")
    assert page.status_code == 200 and "LegacyLens Transparency" in page.text and page.headers["content-type"].startswith("text/html")
    assert c.get("/app/assets/app.js").text == "console.log('dashboard')"
    assert c.get("/app/assets/missing.js").status_code == 404
    assert c.get("/health").status_code == 200  # the API is untouched, on the same origin
    assert c.get("/dashboard/hierarchy").status_code != 404


def test_no_build_means_no_ui_route_and_the_api_is_unaffected(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "ui_dist_dir", tmp_path / "does-not-exist")
    c = TestClient(create_app())
    assert c.get("/app/").status_code == 404
    assert c.get("/health").status_code == 200


def test_the_ui_does_not_expose_data_without_the_admin_key(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "ui_dist_dir", make_dist(tmp_path))
    monkeypatch.setattr(s, "admin_api_key", "k3y")
    c = TestClient(create_app())
    assert c.get("/app/").status_code == 200                       # the page itself holds no data
    assert c.get("/dashboard/summary").status_code == 401          # the numbers do, and they need the key
