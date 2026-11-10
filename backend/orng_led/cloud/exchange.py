"""One virtual day of cloud exchange, counted in billing units.

Raw WebSocket messages are Durable Object requests. The paid 20:1 ratio is
reported beside them and is not the free-plan ceiling. D1 figures follow the
schema: a primary-key lookup is one row read, and a write that changes an
indexed column also writes the index. This is not a production meta sample
and it does not open a socket to Cloudflare.

Official limits checked 2026-11-10:
https://developers.cloudflare.com/workers/platform/limits/
https://developers.cloudflare.com/durable-objects/platform/pricing/
https://developers.cloudflare.com/d1/platform/pricing/
Workers Free and Durable Objects Free reset at 00:00 UTC.
"""

from __future__ import annotations

from dataclasses import dataclass

from orng_led.cloud.budget import (
    DO_GB_PER_SECOND,
    EXECUTOR_FALLBACK_S,
    HEARTBEAT_INTERVAL_S,
    IDLE_HEARTBEAT_INTERVAL_S,
    KEEPALIVE_INTERVAL_S,
    PREVIEW_INTERVAL_S,
    PRESENCE_WRITE_INTERVAL_S,
)

DAY_MS = 86_400_000
# Handler wall time used only for the hibernating duration estimate.
HANDLER_S = 0.05
# installations.last_seen_at is indexed. The row plus that index.
PRESENCE_ROWS_READ = 1
PRESENCE_ROWS_WRITTEN = 2
# command_log row plus idx_command_log_installation. Status is not indexed.
COMMAND_INSERT_ROWS_READ = 1
COMMAND_INSERT_ROWS_WRITTEN = 2
ACK_ROWS_READ = 2
ACK_ROWS_WRITTEN = 1
# Caller, location, named executor. All primary keys.
EXECUTOR_LOOKUP_ROWS_READ = 3


@dataclass(frozen=True)
class ExchangeDay:
    """Raw Durable Object requests are do_requests. do_billing_units is the paid ratio."""

    worker_requests: int
    do_requests: int
    do_billing_units: int
    do_http: int
    do_ws_incoming: int
    do_ws_accept: int
    do_alarm: int
    do_duration_gb_s: float
    do_sqlite_rows_read: int
    do_sqlite_rows_written: int
    d1_rows_read: int
    d1_rows_written: int
    r2_reads: int
    r2_writes: int
    log_events: int
    executor_http: int
    keepalive_forwards: int
    command_log_inserts: int


def legacy_executor_http_per_day() -> int:
    """The removed Windows poll: GET /device/executor/state every 2 seconds."""
    return 86_400 // 2


def simulate_day(
    *,
    subscribers: int = 0,
    windows_watch: bool = False,
    has_token: bool = False,
    effect_change_s: float | None = None,
    outage_s: float = 0.0,
    outage_at_s: float = 3_600.0,
    hold_tabs: int = 0,
    hold_s: float = 0.0,
    presses: int = 0,
    credential_rows: int = 2,
    hibernate_between: bool = True,
) -> ExchangeDay:
    """Walk one day in 500 ms steps.

    subscribers is open browser tabs plus any Windows watch. The hall sends
    one telemetry stream for all of them. Commands and keepalive forwards are
    immediate and are not merged. Without a device token the Windows desk
    does not call Cloudflare at all.
    """
    # A Windows desk without a token never opens the executor socket.
    watching = windows_watch and has_token
    listeners = subscribers + (1 if watching else 0)
    step = 500
    preview_ms = int(PREVIEW_INTERVAL_S * 1000)
    live_beat = int(HEARTBEAT_INTERVAL_S * 1000)
    idle_beat = int(IDLE_HEARTBEAT_INTERVAL_S * 1000)
    presence_ms = int(PRESENCE_WRITE_INTERVAL_S * 1000)
    fallback_ms = int(EXECUTOR_FALLBACK_S * 1000)
    keepalive_ms = int(KEEPALIVE_INTERVAL_S * 1000)
    hold_ms = int(hold_s * 1000)
    outage_start = int(outage_at_s * 1000)
    outage_end = outage_start + int(outage_s * 1000)
    effect_ms = None if effect_change_s is None else int(effect_change_s * 1000)

    worker = 1  # the hall opens its socket once
    do_http = 0
    do_ws = 0
    do_accept = 1
    do_alarm = 0
    sqlite_read = 1  # constructor reads meta
    sqlite_write = 0
    d1_read = 0
    d1_write = 0
    executor_http = 0
    keepalives = 0
    inserts = 0
    last_preview: int | None = None
    last_presence = -presence_ms

    if subscribers:
        worker += subscribers
        do_accept += subscribers
    if watching:
        worker += 1
        do_accept += 1
    if outage_s > 0:
        worker += 1  # one link check when the socket drops
        worker += 1  # the hall opens the socket again
        do_accept += 1

    for t in range(0, DAY_MS, step):
        socket_up = outage_s <= 0 or t < outage_start or t >= outage_end
        if socket_up:
            beat = live_beat if listeners else idle_beat
            if t % beat == 0:
                do_ws += 1
                if hibernate_between:
                    sqlite_read += 1
                    sqlite_write += 1  # deleteAlarm when nothing is held
                if t - last_presence >= presence_ms:
                    d1_read += PRESENCE_ROWS_READ
                    d1_write += PRESENCE_ROWS_WRITTEN
                    sqlite_write += 1
                    last_presence = t
            if listeners and t % preview_ms == 0:
                key = 0 if effect_ms is None else t // effect_ms
                if key != last_preview:
                    do_ws += 1
                    if hibernate_between:
                        sqlite_read += 1
                        sqlite_write += 1
                    last_preview = key
            if hold_tabs and t < hold_ms:
                if t % keepalive_ms == 0:
                    do_ws += hold_tabs
                    keepalives += hold_tabs
                if t % 8_000 == 0:
                    do_alarm += 1
                    sqlite_write += 2  # persist the extended lease, arm the next one
        elif windows_watch and has_token and (t - outage_start) % fallback_ms == 0:
            executor_http += 1
            worker += 1
            do_http += 1
            d1_read += credential_rows * 2 + EXECUTOR_LOOKUP_ROWS_READ

    if presses:
        # Press, history insert, device ack. Nothing is queued behind telemetry.
        do_ws += presses * 2
        inserts += presses
        d1_read += presses * (COMMAND_INSERT_ROWS_READ + ACK_ROWS_READ)
        d1_write += presses * (COMMAND_INSERT_ROWS_WRITTEN + ACK_ROWS_WRITTEN)
        sqlite_write += presses
    if hold_s > 0 and hold_tabs:
        # One release when the lease ends. Keepalive itself wrote no history.
        inserts += 1
        do_ws += 2
        d1_read += COMMAND_INSERT_ROWS_READ + ACK_ROWS_READ
        d1_write += COMMAND_INSERT_ROWS_WRITTEN + ACK_ROWS_WRITTEN

    do_requests = do_http + do_ws + do_accept + do_alarm
    # Paid ratio only. Incoming WebSocket messages are divided. HTTP, accepts
    # and alarms stay one for one. The free cap uses do_requests.
    ws_units = (do_ws + 19) // 20
    billing_units = do_http + do_accept + do_alarm + ws_units
    active_s = do_requests * HANDLER_S
    return ExchangeDay(
        worker_requests=worker,
        do_requests=do_requests,
        do_billing_units=billing_units,
        do_http=do_http,
        do_ws_incoming=do_ws,
        do_ws_accept=do_accept,
        do_alarm=do_alarm,
        do_duration_gb_s=active_s * DO_GB_PER_SECOND,
        do_sqlite_rows_read=sqlite_read,
        do_sqlite_rows_written=sqlite_write,
        d1_rows_read=d1_read,
        d1_rows_written=d1_write,
        r2_reads=0,
        r2_writes=0,
        log_events=0,
        executor_http=executor_http,
        keepalive_forwards=keepalives,
        command_log_inserts=inserts,
    )
