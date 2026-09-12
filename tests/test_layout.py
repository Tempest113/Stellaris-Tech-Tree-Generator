"""Layout: rows, tier bands, columns and the spill rule."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import graph as graph_mod
from pipeline import layout as layout_mod
from pipeline.clausewitz import parse
from pipeline.graph import EdgeKind
from pipeline.layout import COLUMN_CONSTRAINT_KINDS, RowKey, check_invariants
from pipeline.loadorder import LoadOrder, base_game_source, mod_source
from pipeline.records import Extraction, build_record, extract

SLOT_COUNT = 987
ROW_COUNT = 16
#: Technologies pushed past their own tier band by a higher-tier prerequisite.
SPILLED_COUNT = 25
#: Potential-gate edges that end up running right-to-left. Permitted: they gate
#: existence, not research order.
BACKWARD_GATE_EDGES = 7


@pytest.fixture(scope="module")
def built(install, gigas_root: Path):
    load_order = (
        LoadOrder()
        .add(base_game_source(install.game, install.version))
        .add(mod_source(gigas_root, key="gigas"))
    )
    graph = graph_mod.build(extract(load_order))
    return graph, layout_mod.build(graph)


def _layout(source: str):
    tree = parse(source)
    extraction = Extraction()
    for pair in tree.pairs():
        extraction.technologies[pair.key] = build_record(pair.key, pair.value)
    graph = graph_mod.build(extraction)
    return graph, layout_mod.build(graph)


def _columns(layout) -> dict[str, int]:
    return {s.technology: s.column for s in layout.slots}


# --------------------------------------------------------------------------
# Unit: columns
# --------------------------------------------------------------------------


def test_tier_zero_sits_left_and_tiers_increase_rightwards():
    _, layout = _layout(
        "a = { area = physics tier = 0 }\n"
        "b = { area = physics tier = 1 }\n"
        "c = { area = physics tier = 2 }\n"
    )
    columns = _columns(layout)
    assert columns["a"] < columns["b"] < columns["c"]


def test_a_technology_is_never_level_with_its_prerequisite():
    """Same tier still means a column to the right."""
    _, layout = _layout(
        "a = { area = physics tier = 2 }\n"
        "b = { area = physics tier = 2 prerequisites = { a } }\n"
    )
    columns = _columns(layout)
    assert columns["b"] == columns["a"] + 1


def test_higher_tier_prerequisite_makes_the_dependent_spill():
    """The locked resolution: the dependency rule wins, the tier badge carries the truth."""
    graph, layout = _layout(
        "low = { area = physics tier = 1 prerequisites = { high } }\n"
        "high = { area = physics tier = 3 }\n"
        "filler = { area = physics tier = 2 }\n"
    )
    columns = _columns(layout)
    assert columns["low"] > columns["high"]
    spilled = {s.technology for s in layout.spilled}
    assert spilled == {"low"}
    assert check_invariants(layout, graph) == []


def test_spilled_slot_keeps_its_true_tier():
    _, layout = _layout(
        "low = { area = physics tier = 1 prerequisites = { high } }\n"
        "high = { area = physics tier = 3 }\n"
    )
    slot = next(s for s in layout.slots if s.technology == "low")
    assert slot.tier == 1 and slot.spilled


def test_potential_gate_does_not_constrain_columns():
    """``tech_missiles_1`` is tier 0 and gated by tier-5 Cosmogenesis escorts.

    Constraining on that edge drags the whole missile chain to the far right,
    which is why gates are drawn but never push a column.
    """
    graph, layout = _layout(
        "early = { area = engineering tier = 0 potential = { has_technology = late } }\n"
        "late = { area = engineering tier = 5 }\n"
    )
    columns = _columns(layout)
    assert columns["early"] < columns["late"]
    assert EdgeKind.POTENTIAL_GATE not in COLUMN_CONSTRAINT_KINDS
    assert check_invariants(layout, graph) == []


def test_alternatives_do_constrain_columns():
    """Either branch of an OR still has to be researched first."""
    graph, layout = _layout(
        "a = { area = physics tier = 1 }\n"
        "b = { area = physics tier = 1 }\n"
        "t = { area = physics tier = 1 prerequisites = { OR = { a b } } }\n"
    )
    columns = _columns(layout)
    assert columns["t"] > max(columns["a"], columns["b"])


def test_repeatables_are_lifted_to_a_terminal_band():
    _, layout = _layout(
        "early = { area = physics tier = 0 }\n"
        "rep = { area = physics tier = 1 levels = -1 prerequisites = { early } }\n"
    )
    columns = _columns(layout)
    assert columns["rep"] == layout.repeatable_column
    assert layout.repeatable_column > columns["early"]


# --------------------------------------------------------------------------
# Unit: rows
# --------------------------------------------------------------------------


def test_rows_are_keyed_by_area_and_category():
    """``blokkats`` spans all three areas; a category-only row could not be tinted honestly."""
    _, layout = _layout(
        "a = { area = physics tier = 1 category = { blokkats } }\n"
        "b = { area = society tier = 1 category = { blokkats } }\n"
    )
    assert {str(r.key) for r in layout.rows} == {"physics/blokkats", "society/blokkats"}


def test_rows_are_grouped_by_area_in_reading_order():
    _, layout = _layout(
        "a = { area = engineering tier = 1 category = { industry } }\n"
        "b = { area = physics tier = 1 category = { computing } }\n"
        "c = { area = society tier = 1 category = { biology } }\n"
    )
    assert [r.area for r in layout.rows] == ["physics", "society", "engineering"]


def test_relocating_swap_produces_a_second_slot_in_the_same_column():
    graph, layout = _layout(
        "t = { area = engineering tier = 4 category = { voidcraft }"
        "      technology_swap = { name = t_bio area = society category = { biology } } }\n"
    )
    slots = layout.slots_for("t")
    assert len(slots) == 2
    assert {s.column for s in slots} == {slots[0].column}, "a swap never changes tier"
    assert {str(s.row) for s in slots} == {"engineering/voidcraft", "society/biology"}
    assert sum(1 for s in slots if s.is_primary) == 1


def test_non_relocating_swap_produces_no_extra_slot():
    _, layout = _layout(
        "t = { area = society tier = 3 category = { psionics }"
        "      technology_swap = { name = t_bio area = society category = { psionics } } }\n"
    )
    assert len(layout.slots_for("t")) == 1


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_layout_invariants_hold(built):
    graph, layout = built
    assert check_invariants(layout, graph) == []


@pytest.mark.corpus
def test_slot_and_row_counts(built):
    _, layout = built
    assert len(layout.slots) == SLOT_COUNT
    assert len(layout.rows) == ROW_COUNT


@pytest.mark.corpus
def test_blokkats_is_the_only_category_split_across_areas(built):
    _, layout = built
    by_category: dict[str, set[str]] = {}
    for row in layout.rows:
        by_category.setdefault(row.category, set()).add(row.area)
    split = {c for c, areas in by_category.items() if len(areas) > 1}
    assert split == {"blokkats"}


@pytest.mark.corpus
def test_tier_bands_are_contiguous_and_ordered(built):
    _, layout = built
    assert [b.tier for b in layout.bands] == list(range(10))
    for previous, band in zip(layout.bands, layout.bands[1:]):
        assert band.start == previous.end + 1


@pytest.mark.corpus
def test_repeatable_band_is_last(built):
    _, layout = built
    assert layout.repeatable_column > layout.bands[-1].end
    repeatables = [s for s in layout.slots if s.is_repeatable]
    assert repeatables
    assert all(s.column == layout.repeatable_column for s in repeatables)


@pytest.mark.corpus
def test_spilled_count(built):
    _, layout = built
    assert len(layout.spilled) == SPILLED_COUNT
    assert all(s.tier is not None for s in layout.spilled)


@pytest.mark.corpus
def test_missile_chain_is_not_dragged_right_by_its_cosmogenesis_gate(built):
    """The concrete case that made potential-gate a non-constraining edge."""
    _, layout = built
    columns = _columns(layout)
    assert columns["tech_missiles_1"] < 5
    assert columns["tech_missiles_1"] < columns["tech_missiles_5"]


@pytest.mark.corpus
def test_backward_edges_are_only_potential_gates(built):
    graph, layout = built
    columns = _columns(layout)
    backward = [
        e
        for e in graph.edges
        if e.source in columns
        and e.target in columns
        and columns[e.source] > columns[e.target]
    ]
    assert all(e.kind is EdgeKind.POTENTIAL_GATE for e in backward)
    assert len(backward) == BACKWARD_GATE_EDGES


@pytest.mark.corpus
def test_layout_is_deterministic(built):
    graph, layout = built
    again = layout_mod.build(graph)
    assert [(s.technology, s.row, s.column, s.cell_index) for s in layout.slots] == [
        (s.technology, s.row, s.column, s.cell_index) for s in again.slots
    ]


@pytest.mark.corpus
def test_every_technology_has_at_least_one_slot(built):
    graph, layout = built
    placed = {s.technology for s in layout.slots}
    assert placed == set(graph.records)
