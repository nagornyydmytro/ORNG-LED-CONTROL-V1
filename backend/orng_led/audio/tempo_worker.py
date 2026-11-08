"""Bounded tempo worker. The renderer never waits on inspect_tempo.

Hearing is published before the tempo search, so a short attack can be drawn
while the long estimate is still running. One job slot, one result slot. A
newer window replaces an unprocessed one. Jobs carry PCM; results are a
compact snapshot plus hearing scalars, never a sample buffer. Replacing a
snapshot carries pending hearings so a drop ident is not discarded with the
old BPM reading. A dead worker is a missed analysis, not a dark frame.
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
            heard = np.asarray(novel if novel is not None else window, dtype=np.float64)
            hearing = analyze_slice(heard, int(rate))
            energy = window_energy(heard)
            peak, clipping = _peak_clip(heard)
            heard_at = time.monotonic()
            early = (
                "hearing",
                None,
                "",
                {},
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
                heard_at,
                window_id,
                compute_start,
            )
            _stamp_hearing(hearing, early)
            put_latest(results, early)
        except Exception as exc:  # noqa: BLE001
            computed_at = time.monotonic()
            put_latest(
                results,
                _error_payload(
                    exc,
                    captured_at,
                    sample_now,
                    generation,
                    enqueue_mono,
                    computed_at,
                    window_id,
                    compute_start,
                ),
            )
            continue
        try:
            reading, reject, detail = inspect_tempo(window, int(rate))
        except Exception as exc:  # noqa: BLE001
            computed_at = time.monotonic()
            put_latest(
                results,
                _error_payload(
                    exc,
                    captured_at,
                    sample_now,
                    generation,
                    enqueue_mono,
                    computed_at,
                    window_id,
                    compute_start,
                ),
            )
            continue
        computed_at = time.monotonic()
        put_latest(
            results,
            (
                "ok",
                reading,
                reject,
                detail,
                captured_at,
                sample_now,
                generation,
                enqueue_mono,
                None,
                float(novel_s),
                float(energy),
                float(peak),
                float(clipping),
                (),
                computed_at,
                window_id,
                compute_start,
            ),
        )


def _error_payload(
    exc: BaseException,
    captured_at: object,
    sample_now: object,
    generation: object,
    enqueue_mono: object,
    computed_at: float,
    window_id: object,
    compute_start: object,
) -> tuple[object, ...]:
    return (
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
    from orng_led.audio.auto_show import SliceHearing

    if not isinstance(hearing, SliceHearing) or not isinstance(payload, tuple):
        return hearing
    captured = payload[4] if len(payload) > 4 else None
    novel = payload[9] if len(payload) > 9 else 0.0
    generation = payload[6] if len(payload) > 6 and isinstance(payload[6], int) else None
    if hearing.origin_s is None:
        origin = _attack_instant(hearing, captured, novel)
        if origin is not None:
            object.__setattr__(hearing, "origin_s", origin)
            object.__setattr__(hearing, "origin_clock", "monotonic")
    if hearing.generation is None and generation is not None:
        object.__setattr__(hearing, "generation", generation)
    return hearing


def _dropped_hearings(payload: object) -> tuple[object, ...]:
    from orng_led.audio.auto_show import SliceHearing

    pending = list(_payload_extras(payload))
    if isinstance(payload, tuple) and payload and payload[0] in {"ok", "hearing"}:
        hearing = payload[8] if len(payload) > 8 else None
        if isinstance(hearing, SliceHearing):
            pending.append(hearing)
    stamped = [_stamp_hearing(item, payload) for item in pending]
    return tuple(stamped[-HEARING_BACKLOG:])


def _with_extras(payload: tuple[object, ...], extras: tuple[object, ...]) -> tuple[object, ...]:
    base = payload[:13]
    if len(base) < 13:
        base = base + (None,) * (13 - len(base))
    tail = payload[14:] if len(payload) > 14 else ()
    return base + (extras[-HEARING_BACKLOG:],) + tail


def _merge_latest(dropped: object, item: object) -> object:
    """A newer attack updates hearings. It does not replace an unread tempo."""
    if not isinstance(dropped, tuple) or not isinstance(item, tuple) or not item:
        return item
    new_kind = item[0]
    if not isinstance(new_kind, str):
        return item
    old_kind = dropped[0] if dropped else None
    if new_kind == "hearing" and old_kind in {"ok", "error"}:
        hearing = item[8] if len(item) > 8 else None
        stamped = _stamp_hearing(hearing, item)
        pending = list(_dropped_hearings(dropped))
        pending.extend(_payload_extras(item))
        from orng_led.audio.auto_show import SliceHearing

        if isinstance(stamped, SliceHearing):
            pending.append(stamped)
        return _with_extras(dropped, tuple(pending))
    if new_kind in {"ok", "hearing", "error"}:
        extras = (_dropped_hearings(dropped) + _payload_extras(item))[-HEARING_BACKLOG:]
        return _with_extras(item, extras)
    return item


def _take_occupant(queue: Queue) -> object | None:
    """Read the one slot. ``get_nowait`` can miss a ``put_nowait`` still in the feeder."""
    try:
        return queue.get(timeout=0.05)
    except Empty:
        return None


def put_latest(queue: Queue, item: object) -> None:
    """Keep one replaceable snapshot. Carry bounded hearings from the unread one.

    An early hearing for the next window must not evict a tempo reading that
    the renderer has not drained yet. The unread BPM stays; the attack is
    appended to the bounded hearing list with its own capture time.

    ``multiprocessing.Queue`` can answer Full and then Empty: the occupant is
    not readable yet. A short timed read waits for that delivery. This runs on
    the capture and worker processes, not inside the frame tick.
    """
    try:
        queue.put_nowait(item)
        return
    except Full:
        pass
    dropped = _take_occupant(queue)
    if dropped is not None:
        item = _merge_latest(dropped, item)
    try:
        queue.put_nowait(item)
        return
    except Full:
        pass
    dropped = _take_occupant(queue)
    if dropped is not None:
        item = _merge_latest(dropped, item)
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
