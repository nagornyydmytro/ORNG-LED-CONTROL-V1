"""The live preview keeps one picture. It does not queue stale frames."""

from orng_led.cloud.preview import PreviewOutbox


def test_a_slow_sender_keeps_only_the_newest_picture() -> None:
    box = PreviewOutbox()
    for index in range(30):
        box.offer({"n": index, "stage": {"pars": [{"id": "par_1", "intensity": index / 30}]}})
    opened = box.start()
    assert opened is not None
    first, epoch = opened
    assert first["n"] == 29
    assert box.replaced == 29
    newer = {"n": 40, "stage": {"pars": []}}
    box.offer(newer)
    assert box.start() is None
    nxt = box.finish(120, epoch)
    assert nxt is not None
    packet, epoch = nxt
    assert packet is newer
    assert box.finish(80, epoch) is None
    assert box.sent == 2
    assert box.bytes_sent == 200


def test_an_old_attempt_cannot_free_the_slot_a_new_attempt_owns() -> None:
    box = PreviewOutbox()
    box.offer({"n": 1})
    _packet, old = box.start()
    assert old is not None
    box.release(old)
    box.offer({"n": 2})
    opened = box.start()
    assert opened is not None
    _fresh, new = opened
    box.release(old)
    box.drop(old)
    assert box._busy is True
    finished = box.finish(12, new)
    assert box.sent == 1
    assert finished is None


def test_two_desks_read_one_packet() -> None:
    box = PreviewOutbox()
    box.offer({"n": 1, "bytes": 40})
    opened = box.start()
    assert opened is not None
    packet, epoch = opened
    desks = [dict(packet), dict(packet)]
    assert desks[0] == desks[1]
    assert box.sent == 0
    box.finish(40, epoch)
    assert box.sent == 1
    assert box.bytes_sent == 40
