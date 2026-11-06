"""Bounded tempo worker. The renderer never waits on inspect_tempo.

One job slot, one result slot. A newer window replaces an unprocessed one.
Jobs carry PCM; results are a compact snapshot plus hearing scalars, never a
sample buffer. Replacing a snapshot carries pending hearings so a drop ident
is not discarded with the old BPM reading. A dead worker is a missed analysis,
not a dark frame.
"""

from __future__ import annotations

from multiprocessing import Queue
from multiprocessing.context import SpawnContext
from queue import Empty, Full
from typing import Any
import time

HEARING_BACKLOG = 8


def analysis_worker(jobs: Queue, results: Queue, stop_token: Any) -> None:
    import numpy as np

    from orng_led.audio.auto_show import analyze_slice, window_energy
    from orng_led.audio.bpm import inspect_tempo

    while not stop_token.is_set():
        try:
            job = jobs.get(timeout=0.1)
        except Empty:
            continue
        if job is None:
            break
        (
            samples,
            rate,
            captured_at,
            sample_now,
            novel,
            novel_s,
            generation,
            enqueue_mono,
        ) = job[:8]
        window_id = job[8] if len(job) > 8 else None
        compute_start = time.monotonic()
        try:
            window = np.asarray(samples, dtype=np.float64)
            reading, reject, detail = inspect_tempo(window, int(rate))
            heard = np.asarray(novel if novel is not None else window, dtype=np.float64)
            hearing = analyze_slice(heard, int(rate))
            energy = window_energy(heard)
            peak, clipping = _peak_clip(heard)
            computed_at = time.monotonic()
            payload = (
                "ok",
                reading,
                reject,
                detail,
                captured_at,
                sample_now,
                generation,
                enqueue_mono,
                hearing,
                float(novel_s),
                float(energy),
                float(peak),
                float(clipping),
                (),
                computed_at,
                window_id,
                compute_start,
            )
        except Exception as exc:  # noqa: BLE001
            computed_at = time.monotonic()
            payload = (
                "error",
                f"{type(exc).__name__}: {exc}"[:180],
                None,
                None,
                captured_at,
                sample_now,
                generation,
                enqueue_mono,
                None,
                None,
                None,
                None,
                None,
                (),
                computed_at,
                window_id,
                compute_start,
            )
        put_latest(results, payload)


def _peak_clip(samples: object) -> tuple[float, float]:
    import numpy as np

    block = np.asarray(samples, dtype=np.float64).reshape(-1)
    if block.size == 0:
        return 0.0, 0.0
    finite = np.isfinite(block)
    if not bool(np.all(finite)):
        block = np.nan_to_num(block, nan=0.0, posinf=0.0, neginf=0.0)
    magnitude = np.abs(block)
    return float(np.max(magnitude)), float(np.mean(magnitude >= 0.98))


def _payload_extras(payload: object) -> tuple[object, ...]:
    from orng_led.audio.auto_show import SliceHearing

    if not isinstance(payload, tuple) or len(payload) < 14:
        return ()
    extra = payload[13]
    if not isinstance(extra, tuple):
        return ()
    return tuple(item for item in extra if isinstance(item, SliceHearing))


def _attack_instant(hearing: object, captured: object, novel: object) -> float | None:
    if not isinstance(captured, (int, float)):
        return None
    end = float(captured)
    window = float(novel) if isinstance(novel, (int, float)) else 0.0
    attacks = getattr(hearing, "attacks", ()) or ()
    if attacks and window > 0.0:
        last = float(attacks[-1])
        if 0.0 <= last <= window + 1e-6:
            return end - window + last
    return end


def _stamp_hearing(hearing: object, payload: object) -> object:
    from dataclasses import replace

    from orng_led.audio.auto_show import SliceHearing

    if not isinstance(hearing, SliceHearing) or not isinstance(payload, tuple):
        return hearing
    captured = payload[4] if len(payload) > 4 else None
    novel = payload[9] if len(payload) > 9 else 0.0
    generation = payload[6] if len(payload) > 6 and isinstance(payload[6], int) else None
    origin = hearing.origin_s
    if origin is None:
        origin = _attack_instant(hearing, captured, novel)
    generation = hearing.generation if hearing.generation is not None else generation
    return replace(hearing, origin_s=origin, generation=generation)


def _dropped_hearings(payload: object) -> tuple[object, ...]:
    from orng_led.audio.auto_show import SliceHearing

    pending = list(_payload_extras(payload))
    if isinstance(payload, tuple) and payload and payload[0] == "ok":
        hearing = payload[8] if len(payload) > 8 else None
        if isinstance(hearing, SliceHearing):
            pending.append(hearing)
    stamped = [_stamp_hearing(item, payload) for item in pending]
    return tuple(stamped[-HEARING_BACKLOG:])


def put_latest(queue: Queue, item: object) -> None:
    """Keep one replaceable snapshot. Carry bounded hearings from the unread one."""
    dropped = None
    try:
        queue.put_nowait(item)
        return
    except Full:
        pass
    try:
        dropped = queue.get_nowait()
    except Empty:
        pass
    kind = item[0] if isinstance(item, tuple) and item else None
    if dropped is not None and isinstance(kind, str) and kind == "ok":
        extras = (_dropped_hearings(dropped) + _payload_extras(item))[-HEARING_BACKLOG:]
        base = tuple(item[:13]) if len(item) >= 13 else item
        if len(base) < 13:
            base = base + (None,) * (13 - len(base))
        tail = tuple(item[14:]) if isinstance(item, tuple) and len(item) > 14 else ()
        item = base + (extras,) + tail
    try:
        queue.put_nowait(item)
    except Full:
        return


def start_analysis_process(ctx: SpawnContext) -> tuple[Any, Queue, Queue, Any]:
    jobs: Queue = ctx.Queue(maxsize=1)
    results: Queue = ctx.Queue(maxsize=1)
    stop = ctx.Event()
    process = ctx.Process(
        target=analysis_worker,
        args=(jobs, results, stop),
        name="orng-tempo",
        daemon=True,
    )
    process.start()
    return process, jobs, results, stop
