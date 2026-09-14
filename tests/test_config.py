"""The build configuration."""

from __future__ import annotations

from pathlib import Path

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
