"""Read-only venue observation. Does not open audio or change output."""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request

BASE = "http://127.0.0.1:8000"


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def journal() -> None:
    proc = subprocess.run(
        [
            "journalctl",
            "-u",
            "orng-led-control",
            "--since",
            "45 min ago",
            "-o",
            "cat",
            "--no-pager",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    lines = (proc.stdout or "").splitlines()
    keys = (
        "audio block failed",
        "audio capture",
        "Traceback",
        "Error",
        "RuntimeError",
        "Exception",
    )
    hits = [line for line in lines if any(key in line for key in keys)]
    print("JOURNAL_LINES", len(lines))
    print("JOURNAL_HITS", len(hits))
    for line in hits[-80:]:
        print("J", line[:400])


def diag_samples() -> None:
    print("DIAG_BEGIN")
    for _ in range(6):
        started = time.monotonic()
        body = get("/api/audio/diagnostics")
        elapsed = time.monotonic() - started
        keep = {
            key: body.get(key)
            for key in (
                "capture",
                "sample_rate",
                "channels",
                "rms",
                "peak",
                "clipping",
                "callback_age_s",
                "analysis_age_s",
                "raw_bpm",
                "accepted_bpm",
                "confidence",
                "lock_reason",
                "reconnects",
                "overflows",
            )
        }
        print("DIAG", round(elapsed, 3), json.dumps(keep, ensure_ascii=False))
        time.sleep(0.4)


def frame_gaps() -> None:
    print("FRAMES_BEGIN")
    marks: list[tuple[float, int, int, float]] = []
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        started = time.monotonic()
        state = get("/api/state")
        elapsed = time.monotonic() - started
        output = state.get("output") or {}
        engine = state.get("engine") or {}
        bpm = state.get("bpm") or {}
        marks.append(
            (
                time.monotonic(),
                int(output.get("frames_sent") or 0),
                int(state.get("sequence") or 0),
                elapsed,
            )
        )
        if len(marks) == 1:
            print(
                "STATE",
                json.dumps(
                    {
                        "build": state.get("build_id"),
                        "preset": engine.get("preset_id"),
                        "blackout": engine.get("blackout"),
                        "armed": output.get("armed"),
                        "preview_speed": state.get("preview_speed"),
                        "bpm_enabled": bpm.get("enabled"),
                        "bpm_scope": bpm.get("scope"),
                        "bpm_capture": bpm.get("capture"),
                        "bpm_signal": bpm.get("signal"),
                        "bpm": bpm.get("bpm"),
                        "locked": bpm.get("locked"),
                    },
                    ensure_ascii=False,
                ),
            )
        time.sleep(0.03)
    deltas = []
    stalls = []
    for previous, current in zip(marks, marks[1:]):
        sent = current[1] - previous[1]
        wall = current[0] - previous[0]
        if sent > 0:
            deltas.append(wall / sent)
        if wall > 0.1:
            stalls.append((round(wall, 3), sent, round(current[3], 3)))
    deltas.sort()

    def pct(q: float) -> float | None:
        if not deltas:
            return None
        index = min(len(deltas) - 1, int(q * (len(deltas) - 1)))
        return round(deltas[index] * 1000, 1)

    print(
        "FRAME_MS",
        json.dumps(
            {
                "samples": len(deltas),
                "p50": pct(0.50),
                "p95": pct(0.95),
                "p99": pct(0.99),
                "max": None if not deltas else round(deltas[-1] * 1000, 1),
                "stalls_over_100ms": stalls[:12],
                "api_p95_ms": round(
                    sorted(item[3] for item in marks)[int(0.95 * (len(marks) - 1))] * 1000,
                    1,
                ),
            }
        ),
    )


def host() -> None:
    print("HOST_BEGIN")
    commands = [
        ["cat", "/sys/class/thermal/thermal_zone0/temp"],
        ["cat", "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"],
        ["vcgencmd", "get_throttled"],
        ["vcgencmd", "measure_temp"],
        ["nproc"],
    ]
    for command in commands:
        proc = subprocess.run(command, check=False, capture_output=True, text=True, timeout=5)
        print("HOST", command[0], (proc.stdout or proc.stderr or "").strip()[:200])
    proc = subprocess.run(
        ["ps", "-eo", "pid,pcpu,pmem,comm", "--sort=-pcpu"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    lines = [line for line in (proc.stdout or "").splitlines() if "python" in line or "orng" in line or line.startswith(" PID") or line.startswith("PID")]
    print("PROCS")
    for line in lines[:12]:
        print(line)


if __name__ == "__main__":
    journal()
    diag_samples()
    frame_gaps()
    host()
