"""Asset availability across the sources a build reads.

Technology icons are resolved by convention rather than declaration -- a
technology never carries an ``icon`` field, so the renderer looks for
``technologies/<key>.dds``. That makes icon coverage a data question worth
asserting rather than assuming.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.paths import ICON_SUBDIRS, SCRIPT_SUBDIRS, SOURCE_SUBDIRS, icon_directories


def test_icon_directories_are_part_of_the_vendored_set():
    """A mod whose icons are not fetched looks identical to a mod with none."""
    for subdir in ICON_SUBDIRS:
        assert subdir in SOURCE_SUBDIRS


def test_script_and_icon_lists_do_not_overlap():
    assert not set(SCRIPT_SUBDIRS) & set(ICON_SUBDIRS)


@pytest.mark.corpus
@pytest.mark.parametrize("subdir", ICON_SUBDIRS)
def test_base_game_ships_expected_icon_directories(install, subdir: str):
    directory = install.game / subdir
    assert directory.is_dir(), f"base game has no {subdir}"
    assert list(directory.glob("*.dds")), f"{subdir} is empty"


@pytest.mark.corpus
@pytest.mark.parametrize("subdir", ICON_SUBDIRS)
def test_vendored_gigas_ships_expected_icon_directories(gigas_root: Path, subdir: str):
    """Guards the sparse checkout: a missing path here means files were never fetched."""
    directory = gigas_root / subdir
    assert directory.is_dir(), f"vendored Gigas has no {subdir}; re-run tools/vendor_sync.py"
    assert list(directory.glob("*.dds")), f"{subdir} is empty"


@pytest.mark.corpus
def test_icon_counts_are_plausible(install, gigas_root: Path):
    """Rough floors, not exact counts, so a content update does not fail the build."""
    counts = {
        ("vanilla", sub): len(list((install.game / sub).glob("*.dds"))) for sub in ICON_SUBDIRS
    }
    counts.update(
        {("gigas", sub): len(list((gigas_root / sub).glob("*.dds"))) for sub in ICON_SUBDIRS}
    )
    assert counts[("vanilla", "gfx/interface/icons/technologies")] > 700
    assert counts[("vanilla", "gfx/interface/icons/ascension_perks")] > 40
    assert counts[("gigas", "gfx/interface/icons/technologies")] > 250
    assert counts[("gigas", "gfx/interface/icons/ascension_perks")] > 0


@pytest.mark.corpus
def test_icon_directories_helper_finds_both_sources(install, gigas_root: Path):
    assert len(icon_directories(install.game)) == len(ICON_SUBDIRS)
    assert len(icon_directories(gigas_root)) == len(ICON_SUBDIRS)


@pytest.mark.corpus
def test_dds_icons_are_readable_by_pillow(install, gigas_root: Path):
    """The icons are uncompressed BGRA8, which Pillow reads directly.

    Worth pinning: if a future game update switches them to a compressed
    format, the atlas builder needs to know before it silently produces
    garbage.
    """
    from PIL import Image

    for root in (install.game, gigas_root):
        for subdir in ICON_SUBDIRS:
            sample = sorted((root / subdir).glob("*.dds"))[:3]
            assert sample, f"no icons to sample in {root / subdir}"
            for path in sample:
                with Image.open(path) as image:
                    assert image.size[0] > 0 and image.size[1] > 0
                    assert image.mode in ("RGBA", "RGB"), f"{path.name}: mode {image.mode}"
