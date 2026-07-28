"""L013 integration smoke: SPA hosting, routes, Mock safety."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.main import FRONTEND_DIST, create_app

HAS_FRONTEND_DIST = (FRONTEND_DIST / "index.html").is_file()


@pytest.mark.skipif(not HAS_FRONTEND_DIST, reason="frontend/dist missing; run npm run build first")
def test_spa_index_and_deep_links_serve_html() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as client:
        for path in ("/", "/presets", "/setup", "/settings"):
            response = client.get(path, follow_redirects=True)
            assert response.status_code == 200
            assert "text/html" in response.headers.get("content-type", "")
            assert "ORNG LED CONTROL" in response.text
            assert "/assets/" in response.text


@pytest.mark.skipif(not HAS_FRONTEND_DIST, reason="frontend/dist missing; run npm run build first")
def test_missing_asset_is_not_spa_html() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as client:
        response = client.get("/assets/definitely-missing-bundle.js")
        assert response.status_code == 404
        body = response.text.lower()
        assert "<!doctype html>" not in body


def test_home_runtime_stays_mock_disarmed_zero() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    assert runtime.output.transport_kind.value == "mock"
    assert runtime.output.armed is False
    assert runtime.output.allow_real_network is False
    assert runtime.engine.overlays.blackout is True
    state = runtime.build_state()
    assert state.frame == [0] * DMX_UNIVERSE_SIZE
    assert state.output.network_allowed is False
    # No real UDP socket is created in Mock mode.
    assert getattr(runtime.output.transport, "socket", None) is None


def test_frontend_dist_path_resolves_under_repo() -> None:
    # Path must stay valid even with Cyrillic/spaces in the repo location.
    assert FRONTEND_DIST.name == "dist"
    assert FRONTEND_DIST.parent.name == "frontend"
    assert isinstance(FRONTEND_DIST, Path)
