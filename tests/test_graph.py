"""The dependency graph."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import graph as graph_mod
from pipeline.clausewitz import parse
from pipeline.graph import Edge, EdgeKind, GraphCycleError, TechGraph, find_technology_references
from pipeline.loadorder import LoadOrder, base_game_source, mod_source
from pipeline.records import extract

#: 978 technologies, less seventeen whose potential no empire can meet: four
#: Gigastructures retired with `always = no`, four ACOT compatibility
#: technologies behind `has_acot` (`always = no` without ACOT), eight behind the
#: Frame World origin Gigastructures disabled for 4.0, and the Archaeology Lab
#: that only exists without Ancient Relics.
NODE_COUNT = 961
PREREQUISITE_EDGES = 862
ALTERNATIVE_EDGES = 76
#: Gates surviving the scope filter. The corpus holds 27 raw ``has_technology``
#: references inside ``potential``; 6 of them sit under ``any_country`` or
#: ``count_country`` in the E.H.O.F. sentient metal chain and describe the state
#: of the galaxy rather than a dependency, and a 7th is a self-reference. Four
#: more belong to the disabled Frame World technologies.
POTENTIAL_GATE_EDGES = 17

@pytest.fixture(scope="module")
def built(install, gigas_root: Path):
    load_order = (
        LoadOrder()
        .add(base_game_source(install.game, install.version))
        .add(mod_source(gigas_root, key="gigas"))
    )
    return graph_mod.build(extract(load_order))


def _chain(*keys: str) -> TechGraph:
    return TechGraph(
        records={k: None for k in keys},
        edges=tuple(
            Edge(a, b, EdgeKind.PREREQUISITE) for a, b in zip(keys, keys[1:])
        ),
    )


# --------------------------------------------------------------------------
# Unit
# --------------------------------------------------------------------------


def test_edges_point_from_the_thing_needed_to_the_thing_needing_it():
    """So a topological order is also a research order."""
    graph = _chain("a", "b")
    assert graph.prerequisites_of("b") == ["a"]
    assert graph.dependents_of("a") == ["b"]


def test_or_group_members_are_alternatives_not_hard_requirements():
    """Treating either branch as required would over-constrain the layout."""
    source = (
        "t = { area = physics tier = 2 prerequisites = { a OR = { b c } } }\n"
        "a = { area = physics tier = 1 }\n"
        "b = { area = physics tier = 1 }\n"
        "c = { area = physics tier = 1 }\n"
    )
    graph = _graph_from(source)
    kinds = {e.source: e.kind for e in graph.incoming("t")}
    assert kinds["a"] is EdgeKind.PREREQUISITE
    assert kinds["b"] is EdgeKind.ALTERNATIVE
    assert kinds["c"] is EdgeKind.ALTERNATIVE


def test_alternatives_in_one_group_share_a_group_index():
    source = (
        "t = { area = physics tier = 2 prerequisites = { OR = { a b } OR = { c d } } }\n"
        "a = { area = physics tier = 1 }\nb = { area = physics tier = 1 }\n"
        "c = { area = physics tier = 1 }\nd = { area = physics tier = 1 }\n"
    )
    graph = _graph_from(source)
    groups = {e.source: e.group for e in graph.incoming("t")}
    assert groups["a"] == groups["b"]
    assert groups["c"] == groups["d"]
    assert groups["a"] != groups["c"]


def test_potential_gate_is_an_edge_but_not_a_prerequisite():
    source = (
        "t = { area = physics tier = 2 potential = { has_technology = a } }\n"
        "a = { area = physics tier = 1 }\n"
    )
    graph = _graph_from(source)
    edge = graph.incoming("t")[0]
    assert edge.kind is EdgeKind.POTENTIAL_GATE
    assert not edge.is_hard


def test_potential_gate_duplicating_a_prerequisite_is_not_drawn_twice():
    source = (
        "t = { area = physics tier = 2 prerequisites = { a } potential = { has_technology = a } }\n"
        "a = { area = physics tier = 1 }\n"
    )
    assert len(_graph_from(source).incoming("t")) == 1


def test_negated_technology_reference_is_an_exclusion_not_a_dependency():
    """``NOT = { has_technology = x }`` means the opposite of a requirement.

    Drawing it as an edge would point an arrow that claims the reverse of what
    the data says.
    """
    source = (
        "t = { area = physics tier = 2 potential = { NOT = { has_technology = a } } }\n"
        "a = { area = physics tier = 1 }\n"
    )
    graph = _graph_from(source)
    assert graph.incoming("t") == []
    assert graph.exclusions["t"] == ("a",)


def test_negation_detection_is_case_insensitive():
    """Vanilla writes NOT; Gigastructures writes not."""
    for wrapper in ("NOT", "not", "NOR", "nor"):
        block = parse(f"potential = {{ {wrapper} = {{ has_technology = a }} }}")
        found = find_technology_references(block.get_first("potential"))
        assert found == [("a", True)], wrapper


def test_double_negation_is_a_requirement_again():
    block = parse("p = { NOT = { NOR = { has_technology = a } } }")
    assert find_technology_references(block.get_first("p")) == [("a", False)]


def test_reference_under_a_changed_scope_is_not_a_dependency():
    """``any_country = { has_technology = x }`` is a fact about the galaxy.

    ``tech_ehof_sentient_tier_1`` unlocks once *anyone* reaches tier 4. Reading
    that as a dependency inverts the chain and lays tier 1 out to the right of
    tier 4.
    """
    source = (
        "t = { area = physics tier = 2 potential = { "
        "any_country = { has_technology = a } } }\n"
        "a = { area = physics tier = 1 }\n"
    )
    graph = _graph_from(source)
    assert graph.incoming("t") == []
    assert graph.exclusions.get("t", ()) == ()


def test_scope_change_is_detected_across_its_families():
    for wrapper in ("any_country", "every_owned_planet", "random_system",
                    "count_country", "owner", "FROM"):
        block = parse(f"p = {{ {wrapper} = {{ has_technology = a }} }}")
        assert find_technology_references(block.get_first("p")) == [], wrapper


def test_self_scope_keywords_are_not_a_scope_change():
    """Inside a technology's potential these still mean the researching country."""
    for wrapper in ("this", "root", "prev"):
        block = parse(f"p = {{ {wrapper} = {{ has_technology = a }} }}")
        assert find_technology_references(block.get_first("p")) == [("a", False)], wrapper


def test_scope_change_still_blocks_a_reference_it_negates():
    """A scope change outranks negation: the reference is not ours either way."""
    block = parse("p = { any_country = { NOT = { has_technology = a } } }")
    assert find_technology_references(block.get_first("p")) == []


def test_cycle_raises_and_names_the_cycle():
    graph = TechGraph(
        records={k: None for k in "abc"},
        edges=(
            Edge("a", "b", EdgeKind.PREREQUISITE),
            Edge("b", "c", EdgeKind.PREREQUISITE),
            Edge("c", "a", EdgeKind.PREREQUISITE),
        ),
    )
    with pytest.raises(GraphCycleError) as excinfo:
        graph.topological_order()
    assert set(excinfo.value.cycle) == {"a", "b", "c"}


def test_topological_order_is_deterministic():
    graph = _chain("a", "b", "c")
    assert graph.topological_order() == graph.topological_order()
    assert graph.topological_order() == ["a", "b", "c"]


def test_isolation_collects_ancestry_and_descendants():
    graph = _chain("a", "b", "c", "d")
    assert graph.ancestors("c") == {"a", "b"}
    assert graph.descendants("b") == {"c", "d"}
    assert graph.isolate("b") == {"a", "b", "c", "d"}


def test_traversal_can_be_restricted_to_hard_prerequisites():
    graph = TechGraph(
        records={k: None for k in "abc"},
        edges=(
            Edge("a", "b", EdgeKind.PREREQUISITE),
            Edge("b", "c", EdgeKind.POTENTIAL_GATE),
        ),
    )
    assert graph.ancestors("c") == {"a", "b"}
    assert graph.ancestors("c", kinds=[EdgeKind.PREREQUISITE]) == set()


def _graph_from(source: str):
    from pipeline.records import Extraction, build_record

    tree = parse(source)
    extraction = Extraction()
    for pair in tree.pairs():
        extraction.technologies[pair.key] = build_record(pair.key, pair.value)
    return graph_mod.build(extraction)


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_graph_shape(built):
    counts = {kind: 0 for kind in EdgeKind}
    for edge in built.edges:
        counts[edge.kind] += 1
    assert len(built) == NODE_COUNT
    assert counts[EdgeKind.PREREQUISITE] == PREREQUISITE_EDGES
    assert counts[EdgeKind.ALTERNATIVE] == ALTERNATIVE_EDGES
    assert counts[EdgeKind.POTENTIAL_GATE] == POTENTIAL_GATE_EDGES


@pytest.mark.corpus
def test_corpus_is_acyclic(built):
    assert len(built.topological_order()) == NODE_COUNT


@pytest.mark.corpus
def test_topological_order_respects_every_hard_edge(built):
    position = {key: i for i, key in enumerate(built.topological_order())}
    for edge in built.edges:
        assert position[edge.source] < position[edge.target], edge


@pytest.mark.corpus
def test_build_is_deterministic(built):
    assert built.topological_order() == built.topological_order()


@pytest.mark.corpus
def test_the_acot_chain_is_disabled_rather_than_dangling(built):
    """Its four technologies need ACOT's, and ``has_acot`` is false without ACOT.

    They are disabled, so nothing left in the graph names a missing technology.
    """
    referenced = {target for targets in built.dangling.values() for target in targets}
    assert referenced == set()
    assert "giga_tech_amb_supertensiles_acot_delta" not in built.records


@pytest.mark.corpus
def test_a_quarter_of_the_corpus_has_no_dependencies(built):
    """Many technologies are granted by event or special project, not researched.

    Layout has to place these sensibly rather than assuming every node hangs off
    something.
    """
    roots = built.roots()
    assert 200 < len(roots) < 320
    assert "giga_tech_blokkat_armor" in roots


@pytest.mark.corpus
def test_isolation_produces_a_useful_mini_tree(built):
    """The middle-click feature, on the most connected technology in the mod."""
    isolated = built.isolate("tech_mega_engineering")
    assert len(built.ancestors("tech_mega_engineering")) > 15
    assert len(built.descendants("tech_mega_engineering")) > 50
    assert "tech_mega_engineering" in isolated


@pytest.mark.corpus
def test_alternative_edges_come_in_groups_of_at_least_two(built):
    groups: dict[tuple[str, int], int] = {}
    for edge in built.edges:
        if edge.kind is EdgeKind.ALTERNATIVE:
            groups[(edge.target, edge.group)] = groups.get((edge.target, edge.group), 0) + 1
    assert groups
    assert all(count >= 2 for count in groups.values())
