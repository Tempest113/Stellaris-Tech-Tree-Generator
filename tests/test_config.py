"""The build configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import config as build_config


def test_report_links_load_and_blanks_are_left_out(tmp_path: Path):
    path = tmp_path / "build.toml"
    path.write_text(
        '[links]\nissues = "https://github.com/owner/repo/issues"\ndiscord = ""\nother = "x"\n',
        encoding="utf-8",
    )
    assert build_config.load(path).links == {"issues": "https://github.com/owner/repo/issues"}


def test_the_shipped_config_loads():
    config = build_config.load(build_config.DEFAULT_CONFIG)
    assert set(config.links) <= set(build_config.LINK_KEYS)
    assert (config.rows, config.unlocks, config.presets) == (
        Path("config/rows.toml"),
        Path("config/unlocks.toml"),
        Path("config/presets.toml"),
    )


def test_a_build_names_its_own_config_files_and_leaves_out_the_rest(tmp_path: Path):
    rows = tmp_path / "rows.toml"
    rows.write_text("", encoding="utf-8")
    path = tmp_path / "build.toml"
    path.write_text(f'[build]\nrows = "{rows.as_posix()}"\n', encoding="utf-8")
    config = build_config.load(path)
    assert config.rows == rows
    assert config.unlocks is None and config.presets is None


def test_the_page_title_comes_from_the_site_table(tmp_path: Path):
    path = tmp_path / "build.toml"
    path.write_text('[site]\ntitle = "Some Mod Tech Tree"\n', encoding="utf-8")
    assert build_config.load(path).title == "Some Mod Tech Tree"
    path.write_text("", encoding="utf-8")
    assert build_config.load(path).title == "Stellaris Tech Tree"


def test_a_config_file_that_does_not_exist_stops_the_build(tmp_path: Path):
    path = tmp_path / "build.toml"
    path.write_text('[build]\npresets = "nowhere/presets.toml"\n', encoding="utf-8")
    with pytest.raises(build_config.ConfigError, match="does not exist"):
        build_config.load(path)
