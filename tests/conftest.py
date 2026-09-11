"""Shared fixtures.

Tests split into two kinds:

* Unit tests, which run anywhere and pin the language rules against small
  hand-written samples.
* Corpus tests, marked ``@pytest.mark.corpus``, which run against a real
  Stellaris installation and pin *measured facts about the real data*. They skip
  cleanly when no install is present.

Both kinds matter. The prior attempt at this project shipped three separate bugs
behind a green suite because every test encoded implementation behaviour rather
than a real corpus count, so the corpus tests carry absolute numbers on purpose.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.steam import Install, try_find_install  # noqa: E402

#: Workshop ids of the mods the published build cares about.
GIGAS_ID = 1121692237
ACOT_ID = 1419304439

#: Where tools/vendor_sync.py places pinned git sources.
VENDOR_ROOT = Path(__file__).resolve().parents[1] / "vendor"


@pytest.fixture(scope="session")
def install() -> Install:
    found = try_find_install()
    if found is None:
        pytest.skip("no Stellaris installation found (set STELLARIS_PATH)")
    return found


@pytest.fixture(scope="session")
def game_common(install: Install) -> Path:
    return install.game / "common"


@pytest.fixture(scope="session")
def gigas_root() -> Path:
    """Gigastructures, from the vendored git checkout.

    Deliberately *not* the Steam Workshop copy: that can be stale, and on a
    maintainer's machine it may be a working copy on a feature branch. The
    vendored tree is pinned to a commit, so a test failure means the data really
    changed rather than that someone's install drifted.

    Run ``python tools/vendor_sync.py`` to populate it.
    """
    path = VENDOR_ROOT / "gigas"
    if not (path / "common").is_dir():
        pytest.skip("vendored Gigastructures missing; run tools/vendor_sync.py")
    return path


@pytest.fixture(scope="session")
def acot_root(install: Install) -> Path:
    path = install.workshop_mod(ACOT_ID)
    if path is None:
        pytest.skip(f"Ancient Cache of Technologies ({ACOT_ID}) is not installed")
    return path
