"""Localisation parsing, reference resolution and markup handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import localisation as loc
from pipeline.localisation import Localisation, LocEntry, clean, coverage
from pipeline.loadorder import LoadOrder, base_game_source, mod_source
from pipeline.records import extract


def _table(**entries: str) -> Localisation:
    table = Localisation()
    for key, value in entries.items():
        table.entries[key] = LocEntry(raw=value)
    return table


# --------------------------------------------------------------------------
# Unit
# --------------------------------------------------------------------------


def test_a_name_with_runtime_parts_is_not_fixed():
    """"Materiality Engine on [planet.GetName]" would read "Materiality Engine on"."""
    table = _table(
        engine="Materiality Engine",
        project="$engine$ on §H[planet.GetName]§!",
        nested="$project$",
        valued="Gain $VALUE|0$",
        plain="$engine$ Readied",
    )
    assert table.fixed("plain") == "Materiality Engine Readied"
    assert table.fixed("project") is None
    assert table.fixed("nested") is None
    assert table.fixed("valued") is None
    assert table.fixed("missing") is None


def test_every_token_on_a_line_resolves_not_just_the_first():
    """The exact bug that left 16.4% of names broken in the previous attempt."""
    table = _table(
        parent="$a$ and $b$",
        a="Alpha",
        b="Beta",
    )
    assert table.get("parent") == "Alpha and Beta"


def test_references_resolve_through_several_hops():
    table = _table(
        top="$mid$",
        mid="$leaf$: value",
        leaf="Corvette Hull Points",
    )
    assert table.get("top") == "Corvette Hull Points: value"


def test_colour_markup_is_stripped():
    table = _table(x="$m$: \u00a7G+10%\u00a7!", m="Hull Points")
    assert table.get("x") == "Hull Points: +10%"


@pytest.mark.parametrize("code", ["Y", "H", "G", "R", "L", "S", "E", "B", "M", "P", "!"])
def test_every_colour_code_in_the_corpus_is_stripped(code: str):
    assert clean(f"\u00a7{code}text\u00a7!") == "text"


def test_icon_references_are_removed():
    assert clean("cost \u00a3unity\u00a3 200") == "cost 200"


def test_runtime_commands_are_removed():
    assert clean("the [Root.GetName] system") == "the system"


def test_escaped_newlines_become_real_ones():
    assert clean("a\nb") == "a\nb"


def test_unknown_reference_is_dropped_and_recorded():
    """$VALUE|0$ is a runtime formatting token with no key behind it."""
    table = _table(x="gain $VALUE|0$ energy")
    assert table.get("x") == "gain energy"
    assert "VALUE" in table.unresolved


def test_reference_cycle_does_not_hang():
    table = _table(a="$b$", b="$a$")
    assert table.get("a") == ""


def test_missing_key_falls_back_to_the_key_itself():
    assert _table().name("tech_absent") == "tech_absent"


def test_coverage_separates_missing_empty_and_placeholder():
    table = _table(
        good="Real Name",
        placeholder="placeholder",
        empty="$nothing_defined$",
    )
    report = coverage(table, ["good", "placeholder", "empty", "absent"])
    assert report["missing"] == ["absent"]
    assert report["empty"] == ["empty"]
    assert report["placeholder"] == ["placeholder"]


def test_entry_values_may_contain_hashes_and_colons(tmp_path: Path):
    path = tmp_path / "x_l_english.yml"
    path.write_text(
        'l_english:\n key_a:0 "C# is a language: really"\n # a comment\n',
        encoding="utf-8",
    )
    assert loc.parse_file(path) == {"key_a": "C# is a language: really"}


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus(install, gigas_root: Path):
    load_order = (
        LoadOrder()
        .add(base_game_source(install.game, install.version))
        .add(mod_source(gigas_root, key="gigas"))
    )
    return extract(load_order), loc.load(load_order)


@pytest.mark.corpus
def test_no_technology_name_or_description_keeps_raw_markup(corpus):
    """0% here, against 16.4% of names and 22.8% of descriptions previously."""
    extraction, table = corpus
    for key in extraction.technologies:
        name = table.name(key)
        description = table.description(key)
        assert "$" not in name and "\u00a7" not in name, key
        assert "$" not in description and "\u00a7" not in description, key


@pytest.mark.corpus
def test_every_technology_has_a_name(corpus):
    extraction, table = corpus
    report = coverage(table, sorted(extraction.technologies))
    assert report["missing"] == []
    assert report["empty"] == []


@pytest.mark.corpus
def test_the_one_known_placeholder(corpus):
    """Upstream Gigastructures bug; see docs/KNOWN-CORPUS-DEFECTS.md."""
    extraction, table = corpus
    report = coverage(table, sorted(extraction.technologies))
    assert report["placeholder"] == ["giga_tech_aeternite_weaponry"]


@pytest.mark.corpus
def test_multi_token_vanilla_entry_resolves_fully(corpus):
    _, table = corpus
    resolved = table.get("tech_corvette_hull_effect")
    assert resolved == "Corvette Hull Points: +10%\nFrigate Hull Points: +10%"


@pytest.mark.corpus
def test_localisation_replace_directory_overrides(corpus):
    """Gigastructures rewrites vanilla strings through localisation/replace/."""
    _, table = corpus
    replaced = [k for k, e in table.entries.items() if e.is_replacement]
    assert replaced, "expected Gigastructures' replace/ directory to contribute"
