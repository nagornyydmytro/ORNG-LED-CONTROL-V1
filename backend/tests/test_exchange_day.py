"""One virtual day of the cloud exchange. Nothing here contacts Cloudflare."""

from orng_led.cloud.budget import (
    DO_GB_PER_SECOND,
    FREE_D1_ROWS_READ_PER_DAY,
    FREE_D1_ROW_WRITES_PER_DAY,
    FREE_DO_DURATION_GB_S_PER_DAY,
    FREE_DO_REQUESTS_PER_DAY,
    FREE_DO_SQLITE_ROWS_READ_PER_DAY,
    FREE_DO_SQLITE_ROWS_WRITTEN_PER_DAY,
    FREE_WORKER_REQUESTS_PER_DAY,
)
from orng_led.cloud.exchange import legacy_executor_http_per_day, simulate_day

RESIDENT_DAY_GB_S = 86_400 * DO_GB_PER_SECOND


def _under_free(day) -> None:
    assert day.do_requests > 0
    assert day.do_requests < FREE_DO_REQUESTS_PER_DAY
    assert day.worker_requests < FREE_WORKER_REQUESTS_PER_DAY
    assert day.d1_rows_read < FREE_D1_ROWS_READ_PER_DAY
    assert day.d1_rows_written < FREE_D1_ROW_WRITES_PER_DAY
    assert day.do_sqlite_rows_read < FREE_DO_SQLITE_ROWS_READ_PER_DAY
    assert day.do_sqlite_rows_written < FREE_DO_SQLITE_ROWS_WRITTEN_PER_DAY
    if day.do_duration_gb_s is None:
        assert day.duration_basis == "unmeasured"
    else:
        assert day.duration_basis == "resident_day"
        assert day.do_duration_gb_s == RESIDENT_DAY_GB_S
        assert day.do_duration_gb_s < FREE_DO_DURATION_GB_S_PER_DAY
    assert day.r2_reads == 0
    assert day.r2_writes == 0
    assert day.log_events == 0
    # The paid 20:1 figure is not the number that closes the free plan.
    assert day.do_billing_units < day.do_requests


def test_no_desk_sends_presence_only() -> None:
    day = simulate_day()
    assert day.executor_http == 0
    assert day.keepalive_forwards == 0
    assert day.command_log_inserts == 0
    # 120s presence, hibernating between beats. No preview.
    assert day.do_ws_incoming == 720
    assert day.d1_rows_written == 720 * 2
    _under_free(day)


def test_one_desk_publishes_every_frame_and_a_stopped_renderer_does_not() -> None:
    running = simulate_day(subscribers=1, windows_watch=True, has_token=True)
    stopped = simulate_day(
        subscribers=1,
        windows_watch=True,
        has_token=True,
        frames_advance=False,
    )
    assert running.executor_http == 0
    assert stopped.executor_http == 0
    assert running.executor_http < legacy_executor_http_per_day()
    # 5s sequence samples, not one preview for a stable effect name.
    assert running.do_ws_incoming - stopped.do_ws_incoming == (86_400 // 5) - 1
    _under_free(running)
    _under_free(stopped)


def test_resident_day_is_a_ceiling_and_hibernating_duration_is_not_invented() -> None:
    asleep = simulate_day(subscribers=1)
    awake = simulate_day(subscribers=1, hibernate_between=False)
    assert asleep.duration_basis == "unmeasured"
    assert asleep.do_duration_gb_s is None
    assert awake.duration_basis == "resident_day"
    assert awake.do_duration_gb_s == RESIDENT_DAY_GB_S
    assert awake.do_requests == asleep.do_requests
    _under_free(awake)


def test_a_hold_counts_alarm_requests_and_set_alarm_writes() -> None:
    stored = simulate_day(subscribers=1, hold_tabs=1, hold_s=60)
    resident = simulate_day(subscribers=1, hold_tabs=1, hold_s=60, hibernate_between=False)
    assert stored.do_alarm == 1
    assert stored.set_alarm_writes == 1
    assert stored.keepalive_forwards > stored.set_alarm_writes
    assert resident.do_alarm == 60_000 // 8_000
    assert resident.set_alarm_writes == 1 + resident.do_alarm
    _under_free(stored)
    _under_free(resident)


def test_several_desks_share_one_telemetry_stream() -> None:
    one = simulate_day(subscribers=1)
    three = simulate_day(subscribers=3, hold_tabs=2, hold_s=3_600)
    assert three.do_ws_accept == one.do_ws_accept + 2
    # Two holding tabs add their own refreshes. They do not each open a preview.
    assert three.keepalive_forwards > 0
    assert three.command_log_inserts == 1
    assert three.do_ws_incoming - three.keepalive_forwards < one.do_ws_incoming + 50
    _under_free(three)


def test_outage_fallback_is_bounded_and_a_missing_token_sends_nothing() -> None:
    paired = simulate_day(
        windows_watch=True,
        has_token=True,
        outage_s=600,
        outage_at_s=1_000,
    )
    laptop = simulate_day(
        windows_watch=True,
        has_token=False,
        outage_s=600,
        outage_at_s=1_000,
    )
    assert paired.executor_http == 20
    assert laptop.executor_http == 0
    assert laptop.worker_requests < paired.worker_requests
    assert paired.executor_http < legacy_executor_http_per_day()
    _under_free(paired)
    _under_free(laptop)


def test_presses_are_counted_apart_from_telemetry() -> None:
    idle = simulate_day(subscribers=1)
    played = simulate_day(subscribers=1, presses=40)
    assert played.command_log_inserts == idle.command_log_inserts + 40
    assert played.do_ws_incoming == idle.do_ws_incoming + 80
    _under_free(played)
