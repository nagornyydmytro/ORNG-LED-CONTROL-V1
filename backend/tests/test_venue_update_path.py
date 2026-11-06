"""Venue update path: new candidate requires frame_cycle_alive; old previous may omit it."""

from __future__ import annotations

import json
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

from orng_led.updater.helper_protocol import HelperError, apply_staged_release, poll_health
from orng_led.updater.paths import (
    apply_request_path,
    ensure_managed_layout,
    read_pointer,
    write_pointer_atomic,
)
from orng_led.updater.state import UpdateState, load_update_state, transition


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _make_release(root: Path, version: str, build: str) -> Path:
    path = root / f"{version}-{build}"
    path.mkdir(parents=True, exist_ok=True)
    (path / "RELEASE.json").write_text(
        json.dumps(
            {
                "version": version,
                "build_id": build,
                "health_endpoint": "/api/health",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _serve(handler_cls: type[BaseHTTPRequestHandler], port: int) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), handler_cls)
    Thread(target=server.serve_forever, daemon=True).start()
    return server


def _stage_request(paths, *, version: str, build: str, staged: Path, command_id: str) -> None:
    record = load_update_state(paths)
    transition(
        record,
        UpdateState.STAGED,
        paths=paths,
        staged_version=version,
        staged_build_id=build,
        staged_path=str(staged),
        command_id=command_id,
    )
    write_pointer_atomic(
        apply_request_path(paths),
        {
            "command_id": command_id,
            "expected_version": version,
            "expected_build_id": build,
            "staged_path": str(staged),
        },
    )


@pytest.fixture()
def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ORNG_APP_ROOT", str(tmp_path / "app"))
    monkeypatch.setenv("ORNG_INSTALL_MODE", "managed")
    return ensure_managed_layout()


def test_poll_health_legacy_previous_omits_cycle_but_new_candidate_must_have_it() -> None:
    old_body = {
        "ready": True,
        "blackout": True,
        "output_armed": False,
        "version": "0.1.0",
        "build_id": "venue-old",
    }
    new_missing = {
        "ready": True,
        "blackout": True,
        "output_armed": False,
        "version": "0.2.0",
        "build_id": "candidate",
    }
    new_ok = {
        **new_missing,
        "frame_cycle_alive": True,
    }
    dead_old = {
        **old_body,
        "frame_cycle_alive": False,
    }

    server, url = _serve_bodies({"body": old_body})
    try:
        accepted = poll_health(
            url=url,
            expected_version="0.1.0",
            expected_build_id="venue-old",
            timeout_s=1.0,
            interval_s=0.05,
            require_frame_cycle=False,
        )
        assert "frame_cycle_alive" not in accepted
        with pytest.raises(HelperError, match="frame_cycle_alive=None"):
            poll_health(
                url=url,
                expected_version="0.1.0",
                expected_build_id="venue-old",
                timeout_s=0.6,
                interval_s=0.05,
                require_frame_cycle=True,
            )
    finally:
        server.shutdown()

    server, url = _serve_bodies({"body": new_missing})
    try:
        with pytest.raises(HelperError, match="frame_cycle_alive=None"):
            poll_health(
                url=url,
                expected_version="0.2.0",
                expected_build_id="candidate",
                timeout_s=0.6,
                interval_s=0.05,
                require_frame_cycle=True,
            )
    finally:
        server.shutdown()

    server, url = _serve_bodies({"body": new_ok})
    try:
        body = poll_health(
            url=url,
            expected_version="0.2.0",
            expected_build_id="candidate",
            timeout_s=1.0,
            interval_s=0.05,
            require_frame_cycle=True,
        )
        assert body["frame_cycle_alive"] is True
    finally:
        server.shutdown()

    server, url = _serve_bodies({"body": dead_old})
    try:
        with pytest.raises(HelperError, match="frame_cycle_alive=False"):
            poll_health(
                url=url,
                expected_version="0.1.0",
                expected_build_id="venue-old",
                timeout_s=0.6,
                interval_s=0.05,
                require_frame_cycle=False,
            )
    finally:
        server.shutdown()


def _serve_bodies(state: dict) -> tuple[HTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/api/health":
                self.send_response(404)
                self.end_headers()
                return
            payload = json.dumps(state["body"]).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    port = _free_port()
    return _serve(Handler, port), f"http://127.0.0.1:{port}/api/health"


def test_successful_transition_from_old_venue_health(managed) -> None:
    paths = managed
    old = _make_release(paths.releases, "0.1.0", "venue-old")
    new = _make_release(paths.releases, "0.2.0", "candidate")
    write_pointer_atomic(
        paths.current_pointer,
        {"version": "0.1.0", "build_id": "venue-old", "path": str(old)},
    )
    write_pointer_atomic(
        paths.previous_pointer,
        {"version": "0.1.0", "build_id": "venue-old", "path": str(old)},
    )
    _stage_request(paths, version="0.2.0", build="candidate", staged=new, command_id="cmd-ok")

    class Ok(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps(
                {
                    "ready": True,
                    "frame_cycle_alive": True,
                    "blackout": True,
                    "output_armed": False,
                    "version": "0.2.0",
                    "build_id": "candidate",
                }
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    port = _free_port()
    srv = _serve(Ok, port)
    try:
        out = apply_staged_release(
            paths,
            skip_process=False,
            health_url=f"http://127.0.0.1:{port}/api/health",
            health_timeout_s=3.0,
        )
    finally:
        srv.shutdown()
    assert out["status"] == "applied"
    assert read_pointer(paths.current_pointer)["build_id"] == "candidate"


def test_candidate_without_cycle_rolls_back_to_old_health_format(managed) -> None:
    paths = managed
    old = _make_release(paths.releases, "0.1.0", "venue-old")
    new = _make_release(paths.releases, "0.2.0", "bad-cycle")
    write_pointer_atomic(
        paths.current_pointer,
        {"version": "0.1.0", "build_id": "venue-old", "path": str(old)},
    )
    write_pointer_atomic(
        paths.previous_pointer,
        {"version": "0.1.0", "build_id": "venue-old", "path": str(old)},
    )
    _stage_request(paths, version="0.2.0", build="bad-cycle", staged=new, command_id="cmd-roll")

    state = {"mode": "new"}

    class Switch(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if state["mode"] == "new":
                body = {
                    "ready": True,
                    "blackout": True,
                    "output_armed": False,
                    "version": "0.2.0",
                    "build_id": "bad-cycle",
                }
            else:
                body = {
                    "ready": True,
                    "blackout": True,
                    "output_armed": False,
                    "version": "0.1.0",
                    "build_id": "venue-old",
                }
            payload = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    port = _free_port()
    srv = _serve(Switch, port)

    def after_rollback() -> None:
        state["mode"] = "old"

    try:
        out = apply_staged_release(
            paths,
            skip_process=False,
            health_url=f"http://127.0.0.1:{port}/api/health",
            health_timeout_s=2.0,
            on_after_rollback=after_rollback,
        )
    finally:
        srv.shutdown()
    assert out["status"] == "rolled_back"
    current = read_pointer(paths.current_pointer)
    assert current["version"] == "0.1.0"
    assert current["build_id"] == "venue-old"
