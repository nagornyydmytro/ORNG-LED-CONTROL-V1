"""Keep pytest off the working ORNG BAR tree.

Reads still see a copy of ``config/``. Writes stay in this temporary root.
"""

from __future__ import annotations

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
