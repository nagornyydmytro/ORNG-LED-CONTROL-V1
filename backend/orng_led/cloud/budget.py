"""How often the hall may speak to Cloudflare on the free plan.

Three daily ceilings reset at 00:00 UTC and stop the product when crossed:
100_000 Durable Object requests (each incoming WebSocket message is one),
100_000 Worker requests (an open socket is one; each ordinary POST is one),
5_000_000 D1 row reads and 100_000 D1 row writes. Outgoing socket messages
and protocol pings are free. The paid 20:1 ratio does not raise these caps.

Automatic traffic for a hall that stays up all day, plus one open desk,
stays under a third of every ceiling when the preview is one message per
slot. Presses, holds and reconnects use the rest. Sequence advances with
each published frame, so a stable effect name is still a new preview.
The hall does not send a fixture picture, and it does not re-read the
command history on a timer.
"""

from __future__ import annotations

import math

# One message names the effect that is playing. It is not a fixture picture.
PREVIEW_INTERVAL_S = 5.0
PREVIEW_SEND_TIMEOUT_S = 1.0
HEARTBEAT_INTERVAL_S = 45.0
# Not the live reserve. A refused socket posts on LINK_INTERVAL_S with
# wait_ms 0. These three numbers are the rejected held-request experiment.
LINK_WAIT_S = 2.0
LINK_POLL_STEP_S = 0.25
LINK_GAP_S = 0.05
LINK_INTERVAL_S = 20.0
LINK_SOCKET_RETRY_S = 90.0
# No open desk: the hall stays quiet. A presence beat is enough.
IDLE_HEARTBEAT_INTERVAL_S = 120.0
# Same snapshot must not rewrite D1 or object storage on every beat.
PRESENCE_WRITE_INTERVAL_S = 120.0
# One Windows desk, and only while its executor socket is down.
EXECUTOR_FALLBACK_S = 30.0
# Desk refresh. The object extends the lease in memory and forwards it.
KEEPALIVE_INTERVAL_S = 3.5
FREE_DO_REQUESTS_PER_DAY = 100_000
FREE_WORKER_REQUESTS_PER_DAY = 100_000
FREE_D1_ROWS_READ_PER_DAY = 5_000_000
FREE_D1_ROW_WRITES_PER_DAY = 100_000
FREE_DO_SQLITE_ROWS_READ_PER_DAY = 5_000_000
FREE_DO_SQLITE_ROWS_WRITTEN_PER_DAY = 100_000
# 128 MB / 1 GB, the ratio used in the Durable Objects pricing examples.
FREE_DO_DURATION_GB_S_PER_DAY = 13_000
DO_GB_PER_SECOND = 0.128
# Two thirds of each ceiling stay free for presses, a second desk and retries.
AUTOMATIC_SHARE = 0.3


def messages_per_day(interval_s: float) -> int:
    if interval_s <= 0:
        raise ValueError("interval must be positive")
    return math.ceil(86_400 / interval_s)


def device_socket_messages_per_day() -> int:
    """A running renderer publishes every preview slot while a desk is open."""
    return messages_per_day(PREVIEW_INTERVAL_S) + messages_per_day(HEARTBEAT_INTERVAL_S)


def heartbeat_interval_s(subscribers: int) -> float:
    """Open desks get the live beat. An empty desk does not."""
    if subscribers > 0:
        return HEARTBEAT_INTERVAL_S
    return IDLE_HEARTBEAT_INTERVAL_S


def preview_key(packet: dict[str, object]) -> tuple[object, ...]:
    return (
        packet.get("session"),
        packet.get("sequence"),
        packet.get("look"),
        packet.get("program"),
        packet.get("preset_id"),
    )


def should_publish_preview(
    subscribers: int,
    previous: tuple[object, ...] | None,
    packet: dict[str, object],
) -> bool:
    """A listening desk gets a frame when session, sequence or the effect changed."""
    if subscribers <= 0:
        return False
    return preview_key(packet) != previous
