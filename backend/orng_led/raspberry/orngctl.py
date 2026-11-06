"""Local orngctl CLI — no tokens, no arbitrary shell, no auto-arm."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from orng_led.raspberry.diagnostics import build_venue_diagnostics, redact_mapping
from orng_led.raspberry.network import NetworkPlanError, build_artnet_ethernet_plan


def _http_json(method: str, path: str, body: dict | None = None) -> dict[str, Any]:
    import os
    import urllib.error
    import urllib.request

    base = os.environ.get("ORNG_LOCAL_API", "http://127.0.0.1:8000").rstrip("/")
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={"content-type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        return {"ok": False, "http_status": exc.code, "error": detail}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def cmd_status(as_json: bool) -> int:
    health = _http_json("GET", "/api/health")
    upd = _http_json("GET", "/api/app/update/status")
    cloud = _http_json("GET", "/api/cloud/status")
    out = redact_mapping(
        {
            "health": health,
            "update": {
                k: upd.get(k)
                for k in (
                    "install_mode",
                    "app_version",
                    "build_id",
                    "update_state",
                    "current_release",
                    "last_error",
                )
                if isinstance(upd, dict)
            },
            "cloud": {
                k: cloud.get(k)
                for k in ("configured", "connected", "has_token", "installation_id")
                if isinstance(cloud, dict)
            },
        }
    )
    # Never include token fields even if API leaked them.
    for block in out.values():
        if isinstance(block, dict):
            for bad in list(block):
                if "token" in bad.lower() or "bearer" in bad.lower():
                    block[bad] = "[redacted]"
    if as_json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        h = out.get("health") or {}
        print(
            f"ready={h.get('ready')} blackout={h.get('blackout')} "
            f"armed={h.get('output_armed')} version={h.get('version')} "
            f"build={h.get('build_id')} mode={h.get('install_mode')}"
        )
    return 0


def cmd_doctor(as_json: bool) -> int:
    diag = build_venue_diagnostics(None)
    health = _http_json("GET", "/api/health")
    diag["local_health"] = health if isinstance(health, dict) else {"error": health}
    # Doctor must never arm.
    diag["actions"] = {"armed": False, "note": "doctor never arms"}
    if as_json:
        print(json.dumps(diag, indent=2, sort_keys=True, default=str))
    else:
        print(f"hardware_acceptance={diag.get('hardware_acceptance')}")
        print(f"platform={diag.get('platform')} arch={diag.get('arch')}")
        print(f"blackout={diag.get('blackout')} armed={diag.get('armed')}")
        print(f"keyboard_connected={diag.get('keyboard_connected')}")
        print(f"cloud_connected={diag.get('cloud_connected')}")
    return 0


def cmd_network_check() -> int:
    plan = build_artnet_ethernet_plan()
    print(json.dumps({"ok": True, "plan": plan.to_dict()}, indent=2, sort_keys=True))
    return 0


def cmd_artnet_check() -> int:
    # Diagnostic only — no arm, no real UDP from orngctl on developer hosts unless asked.
    plan = build_artnet_ethernet_plan()
    print(
        json.dumps(
            {
                "ok": True,
                "target": f"{plan.target}:{plan.port}",
                "source": plan.cidr,
                "checks": plan.checks,
                "note": "commands not executed by orngctl on this host",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def cmd_keyboard_list() -> int:
    st = _http_json("GET", "/api/input/status")
    print(json.dumps(redact_mapping(st if isinstance(st, dict) else {"raw": st}), indent=2))
    return 0


def cmd_keyboard_test() -> int:
    print(
        json.dumps(
            {
                "ok": True,
                "note": "Press keys on the device USB keyboard; watch /api/input/status. "
                "This command does not drive Art-Net or arm output.",
            },
            indent=2,
        )
    )
    return 0


def cmd_pair_start() -> int:
    body = _http_json("POST", "/api/cloud/pair/start", {})
    # Strip anything sensitive.
    safe = {
        k: body.get(k)
        for k in ("pairing_id", "code", "expires_at", "ok", "error", "http_status")
        if isinstance(body, dict) and k in body
    }
    print(json.dumps(safe, indent=2, sort_keys=True))
    return 0 if body.get("code") or body.get("pairing_id") else 1


def cmd_pair_complete() -> int:
    body = _http_json("POST", "/api/cloud/pair/complete", {})
    safe = {
        k: body.get(k)
        for k in ("ok", "installation_id", "error", "http_status", "paired")
        if isinstance(body, dict) and k in body
    }
    if isinstance(body, dict) and body.get("token"):
        safe["token"] = "[redacted]"
    print(json.dumps(safe, indent=2, sort_keys=True))
    return 0 if body.get("ok") or body.get("paired") or body.get("installation_id") else 1


def cmd_scenes() -> int:
    print(json.dumps(_http_json("GET", "/api/scenes"), indent=2, sort_keys=True))
    return 0


def cmd_scene_current() -> int:
    print(json.dumps(_http_json("GET", "/api/scenes/active"), indent=2, sort_keys=True))
    return 0


def cmd_update_status() -> int:
    st = _http_json("GET", "/api/app/update/status")
    if isinstance(st, dict):
        st.pop("pending_result", None)
    print(json.dumps(st, indent=2, sort_keys=True))
    return 0


def cmd_logs() -> int:
    print(
        json.dumps(
            {
                "ok": True,
                "hint": "journalctl -u orng-led-control -n 200 --no-pager",
                "note": "orngctl does not dump journals or secrets",
            },
            indent=2,
        )
    )
    return 0


def cmd_blackout() -> int:
    print(json.dumps(_http_json("POST", "/api/commands/blackout", {"enabled": True}), indent=2))
    return 0


def cmd_disarm() -> int:
    print(json.dumps(_http_json("POST", "/api/output/disarm", {}), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orngctl")
    sub = parser.add_subparsers(dest="cmd", required=True)

    st = sub.add_parser("status")
    st.add_argument("--json", action="store_true")
    doc = sub.add_parser("doctor")
    doc.add_argument("--json", action="store_true")

    net = sub.add_parser("network")
    net_sub = net.add_subparsers(dest="net_cmd", required=True)
    net_sub.add_parser("check")

    art = sub.add_parser("artnet")
    art_sub = art.add_subparsers(dest="art_cmd", required=True)
    art_sub.add_parser("check")

    kb = sub.add_parser("keyboard")
    kb_sub = kb.add_subparsers(dest="kb_cmd", required=True)
    kb_sub.add_parser("list")
    kb_sub.add_parser("test")

    pair = sub.add_parser("pair")
    pair_sub = pair.add_subparsers(dest="pair_cmd", required=True)
    pair_sub.add_parser("start")
    pair_sub.add_parser("complete")

    sub.add_parser("scenes")
    sc = sub.add_parser("scene")
    sc_sub = sc.add_subparsers(dest="scene_cmd", required=True)
    sc_sub.add_parser("current")

    upd = sub.add_parser("update")
    upd_sub = upd.add_subparsers(dest="upd_cmd", required=True)
    upd_sub.add_parser("status")

    sub.add_parser("logs")
    sub.add_parser("blackout")
    sub.add_parser("disarm")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "status":
            return cmd_status(args.json)
        if args.cmd == "doctor":
            return cmd_doctor(args.json)
        if args.cmd == "network" and args.net_cmd == "check":
            return cmd_network_check()
        if args.cmd == "artnet" and args.art_cmd == "check":
            return cmd_artnet_check()
        if args.cmd == "keyboard" and args.kb_cmd == "list":
            return cmd_keyboard_list()
        if args.cmd == "keyboard" and args.kb_cmd == "test":
            return cmd_keyboard_test()
        if args.cmd == "pair" and args.pair_cmd == "start":
            return cmd_pair_start()
        if args.cmd == "pair" and args.pair_cmd == "complete":
            return cmd_pair_complete()
        if args.cmd == "scenes":
            return cmd_scenes()
        if args.cmd == "scene" and args.scene_cmd == "current":
            return cmd_scene_current()
        if args.cmd == "update" and args.upd_cmd == "status":
            return cmd_update_status()
        if args.cmd == "logs":
            return cmd_logs()
        if args.cmd == "blackout":
            return cmd_blackout()
        if args.cmd == "disarm":
            return cmd_disarm()
    except NetworkPlanError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "message": exc.message}))
        return 1
    print("unknown command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
