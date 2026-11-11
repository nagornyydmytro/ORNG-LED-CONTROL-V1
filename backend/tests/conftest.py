"""Keep pytest off the working ORNG BAR tree.

Reads still see a copy of ``config/``. Writes stay in this temporary root.
"""

from __future__ import annotations

import gc
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture(scope="session", autouse=True)
def isolated_repo_config(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("orng-config-root")
    shutil.copytree(_REPO_CONFIG, root, dirs_exist_ok=True)
    previous = os.environ.get("ORNG_CONFIG_ROOT")
    os.environ["ORNG_CONFIG_ROOT"] = str(root)
    yield root
    if previous is None:
        os.environ.pop("ORNG_CONFIG_ROOT", None)
    else:
        os.environ["ORNG_CONFIG_ROOT"] = previous


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Drop analysis queues before the interpreter joins their feeder threads.

    A full run leaves at least one ``multiprocessing.Queue`` whose feeder is
    blocked in ``send_bytes``. The queue finalizer joins that thread, so the
    process prints the summary and then never returns. Cancelling the join
    lets the daemon feeder die with the interpreter. This does not hide a
    failed test: it runs after the summary.
    """
    del session, exitstatus
    from multiprocessing import queues
    from multiprocessing.process import active_children

    for child in active_children():
        child.terminate()
    for child in active_children():
        child.join(timeout=1.0)
    for obj in gc.get_objects():
        if not isinstance(obj, queues.Queue):
            continue
        try:
            obj.close()
        except (OSError, ValueError):
            pass
        try:
            obj.cancel_join_thread()
        except (OSError, ValueError):
            pass
