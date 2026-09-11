"""Language rules for the Clausewitz parser.

Each unit test below pins a hazard that is actually present in the corpus, with
a pointer to where. They are not hypothetical edge cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.clausewitz import (
    Block,
    LexError,
    ParseError,
    Pair,
    Scalar,
    check_roundtrip,
    parse,
    serialize,
)
from pipeline.clausewitz.roundtrip import PIPELINE_SUBDIRS, check_tree


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------


def test_duplicate_keys_are_all_preserved():
    """Vanilla ``tech_psi_jump_drive_1`` declares ``is_dangerous`` twice.

    A dict-backed model silently keeps one. Accumulate, never overwrite.
    """
    tree = parse("t = { is_dangerous = yes is_dangerous = yes }")
    tech = tree.get_first("t")
    assert len(tech.get_all("is_dangerous")) == 2


def test_repeated_blocks_are_all_preserved():
    """``giga_tech_repeatable_furnace_cap`` ends up with two weight_modifiers."""
    tree = parse("t = { weight_modifier = { a = 1 } weight_modifier = { b = 2 } }")
    assert len(tree.get_first("t").get_all("weight_modifier")) == 2


def test_block_keeps_source_order_of_mixed_items():
    """``prerequisites`` mixes bare literals with OR-groups; order is meaning."""
    tree = parse("prerequisites = { tech_a OR = { tech_b tech_c } tech_d }")
    items = tree.get_first("prerequisites").items
    assert [type(i).__name__ for i in items] == ["Scalar", "Pair", "Scalar"]
    assert items[0] == Scalar("tech_a")
    assert items[2] == Scalar("tech_d")


def test_quoted_and_bare_keys_are_distinguished_but_both_usable():
    """``tech_modular_engineering`` writes quoted prerequisites; others do not."""
    tree = parse('prerequisites = { "tech_starbase_3" tech_waystation_2 }')
    scalars = list(tree.get_first("prerequisites").scalars())
    assert [s.value for s in scalars] == ["tech_starbase_3", "tech_waystation_2"]
    assert scalars[0].quoted is True
    assert scalars[1].quoted is False


def test_empty_block_parses():
    """Four vanilla techs declare ``prerequisites = { }``."""
    tree = parse("prerequisites = { }")
    assert tree.get_first("prerequisites") == Block([])


def test_anonymous_nested_blocks():
    tree = parse("t = { { a = 1 } { b = 2 } }")
    assert len(list(tree.get_first("t").blocks())) == 2


# --------------------------------------------------------------------------
# Lexical hazards
# --------------------------------------------------------------------------


def test_comments_are_stripped_including_inside_blocks():
    """Gigas puts comments inside prerequisite blocks (giga_02_society.txt:137)."""
    tree = parse(
        "prerequisites = {\n"
        "    # moved terrestrial sculpting to potential so nomads ignore it\n"
        "    OR = { tech_cruisers tech_harbingers }\n"
        "}\n"
    )
    assert len(tree.get_first("prerequisites").items) == 1


def test_scripted_variable_holding_a_string():
    """``@giga_amb_flag = giga_buildcap_j`` is used as a trigger right-hand side.

    A numeric-only variable resolver breaks on this; 17 occurrences in Gigas.
    """
    tree = parse("potential = { has_global_flag = @giga_amb_flag }")
    assert tree.get_first("potential").scalar_text("has_global_flag") == "@giga_amb_flag"


def test_comparison_operators_are_preserved():
    """``check_variable`` uses ``<`` and friends, not just ``=``."""
    tree = parse("check_variable = { which = giga_x value < 5 }")
    pairs = list(tree.get_first("check_variable").pairs())
    assert [(p.key, p.op) for p in pairs] == [("which", "="), ("value", "<")]


@pytest.mark.parametrize("op", ["=", "==", "!=", "?=", "<", ">", "<=", ">="])
def test_every_operator_survives_a_roundtrip(op):
    tree = check_roundtrip(f"a {op} b")
    assert list(tree.pairs())[0].op == op


def test_bare_words_accept_paths_params_and_negative_numbers():
    tree = parse("a = gfx/interface/icons/x.dds\nb = $name$\nc = -1\nd = tech_x:0\n")
    assert tree.scalar_text("a") == "gfx/interface/icons/x.dds"
    assert tree.scalar_text("b") == "$name$"
    assert tree.scalar_text("c") == "-1"
    assert tree.scalar_text("d") == "tech_x:0"


def test_lowercase_boolean_operators_are_just_keys():
    """Gigas writes ``not = {``; vanilla writes ``NOT = {``. Both must survive.

    Case folding belongs in the trigger evaluator, not the parser.
    """
    tree = parse("a = { not = { x = 1 } }\nb = { NOT = { x = 1 } }")
    assert tree.get_first("a").has("not")
    assert tree.get_first("b").has("NOT")


def test_string_escapes_round_trip():
    tree = check_roundtrip(r'a = "say \"hi\" now"')
    assert tree.scalar_text("a") == 'say "hi" now'


def test_sloppy_whitespace_inside_value_braces():
    """ACOT writes ``category = { military_theory  }`` with a double space."""
    tree = parse("category = { military_theory  }")
    assert [s.value for s in tree.get_first("category").scalars()] == ["military_theory"]


def test_byte_order_mark_is_ignored():
    tree = parse("﻿a = 1")
    assert tree.scalar_text("a") == "1"


# --------------------------------------------------------------------------
# Failure behaviour
# --------------------------------------------------------------------------


def test_unclosed_block_fails_loudly_by_default():
    with pytest.raises(ParseError) as excinfo:
        parse("a = { b = 1", path="x.txt")
    assert "unclosed" in str(excinfo.value)
    assert "x.txt:1:" in str(excinfo.value)


def test_unclosed_block_can_be_tolerated_explicitly():
    """base game scripted_loc_ruloc.txt genuinely ships one short."""
    tree = parse("a = { b = 1", allow_unclosed_blocks=True)
    assert tree.get_first("a").scalar_text("b") == "1"


def test_stray_closing_brace_fails():
    with pytest.raises(ParseError):
        parse("a = 1 }")


def test_dangling_operator_fails():
    with pytest.raises(ParseError):
        parse("= 1")


def test_errors_carry_line_and_column():
    with pytest.raises(LexError) as excinfo:
        parse('a = "unterminated\nb = 1\n' + '"' * 0 + "\x00", path="f.txt")
    assert "f.txt:" in str(excinfo.value)


# --------------------------------------------------------------------------
# Serializer
# --------------------------------------------------------------------------


def test_serialize_then_parse_is_identity_for_nested_structures():
    source = """
    tech_x = {
        area = engineering
        prerequisites = { tech_a OR = { tech_b tech_c } }
        weight_modifier = {
            modifier = { factor = 0 NOT = { has_global_flag = x_disabled } }
        }
    }
    """
    tree = parse(source)
    assert parse(serialize(tree)) == tree


def test_quoting_is_part_of_structural_equality():
    """Otherwise the oracle would not notice a serializer that loses quotes."""
    assert parse('a = "1"') != parse("a = 1")


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


@pytest.mark.corpus
@pytest.mark.parametrize("subdir", PIPELINE_SUBDIRS)
def test_roundtrip_over_base_game(game_common: Path, subdir: str):
    directory = game_common / subdir
    if not directory.is_dir():
        pytest.skip(f"base game has no common/{subdir}")
    report = check_tree(directory)
    assert report.files_checked > 0
    assert report.ok, report.summary()


@pytest.mark.corpus
@pytest.mark.parametrize("subdir", PIPELINE_SUBDIRS)
def test_roundtrip_over_gigas(gigas_root: Path, subdir: str):
    directory = gigas_root / "common" / subdir
    if not directory.is_dir():
        pytest.skip(f"Gigas has no common/{subdir}")
    report = check_tree(directory)
    assert report.files_checked > 0
    assert report.ok, report.summary()


@pytest.mark.corpus
@pytest.mark.parametrize("subdir", PIPELINE_SUBDIRS)
def test_roundtrip_over_acot(acot_root: Path, subdir: str):
    directory = acot_root / "common" / subdir
    if not directory.is_dir():
        pytest.skip(f"ACOT has no common/{subdir}")
    report = check_tree(directory)
    assert report.files_checked > 0
    assert report.ok, report.summary()


@pytest.mark.corpus
def test_known_non_script_file_is_skipped_not_failed(game_common: Path):
    """HOW_TO_MAKE_NEW_SHIPS.txt is prose; it must be excluded, not tolerated."""
    report = check_tree(game_common, pattern="HOW_TO_MAKE_NEW_SHIPS.txt")
    assert report.files_skipped == 1
    assert report.files_checked == 0
    assert report.ok


@pytest.mark.corpus
def test_mega_engineering_prerequisite_shape(game_common: Path):
    """The canonical mixed AND/OR prerequisite block, pinned exactly."""
    from pipeline.clausewitz import parse_file

    tree = parse_file(game_common / "technology" / "00_megastructures.txt")
    prereqs = tree.get_first("tech_mega_engineering").block_at("prerequisites")

    literals = [s.value for s in prereqs.scalars()]
    or_groups = [
        [s.value for s in group.scalars()] for group in prereqs.get_all("OR")
    ]
    assert literals == ["tech_zero_point_power"]
    assert or_groups == [
        ["tech_starbase_5", "tech_arkship_tier_3"],
        ["tech_battleships", "tech_stingers"],
    ]
