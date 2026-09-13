"""Emission: what the browser receives has to say what the graph says."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import emit as emit_mod
from pipeline import graph as graph_mod
from pipeline import layout as layout_mod
from pipeline import localisation as loc_mod
from pipeline import rows as rows_mod
from pipeline.graph import Edge, EdgeKind, TechGraph
from pipeline.layout import RowKey, Slot
from pipeline.loadorder import LoadOrder, base_game_source, mod_source
from pipeline.records import extract


def _slot(technology: str, row: str, swap: str | None = None) -> Slot:
    return Slot(
        technology=technology,
        row=RowKey("engineering", row),
        column=0,
        cell_index=0,
        tier=1,
        is_primary=swap is None,
        is_repeatable=False,
        spilled=False,
        swap=swap,
    )


# --------------------------------------------------------------------------
# Unit: edge wiring
# --------------------------------------------------------------------------


def test_edges_address_slots_not_technologies():
    """The defect this file exists for.

    With a variant slot sorted between two technologies, numbering by
    technology shifted every later index by one and drew the edge between the
    wrong cards.
    """
    slots = [
        _slot("a", "voidcraft"),
        _slot("a", "biology", swap="a_bio"),
        _slot("b", "voidcraft"),
        _slot("c", "voidcraft"),
    ]
    graph = TechGraph(edges=(Edge("b", "c", EdgeKind.PREREQUISITE),))
    edges = emit_mod.wire_edges(graph, slots, [frozenset()] * 4)
    assert edges == [[2, 3, 0]]


def test_every_slot_of_a_target_gets_its_edge():
    slots = [_slot("a", "x"), _slot("b", "x"), _slot("b", "y", swap="b_bio")]
    graph = TechGraph(edges=(Edge("a", "b", EdgeKind.PREREQUISITE),))
    profiles = [frozenset(), frozenset(), frozenset({"bio = yes"})]
    assert emit_mod.wire_edges(graph, slots, profiles) == [[0, 1, 0], [0, 2, 0]]


def test_a_variant_target_draws_from_the_matching_variant_source():
    """Improved Fighter Wing (bio) comes from Basic Fighter Wing (bio)."""
    bio = frozenset({"country_uses_bio_ships = yes"})
    slots = [
        _slot("a", "voidcraft"),
        _slot("a", "biology", swap="a_bio"),
        _slot("b", "voidcraft"),
        _slot("b", "biology", swap="b_bio"),
    ]
    graph = TechGraph(edges=(Edge("a", "b", EdgeKind.PREREQUISITE),))
    edges = emit_mod.wire_edges(graph, slots, [frozenset(), bio, frozenset(), bio])
    assert edges == [[0, 2, 0], [1, 3, 0]]


def test_a_primary_target_draws_from_the_primary_source():
    bio = frozenset({"country_uses_bio_ships = yes"})
    slots = [_slot("a", "voidcraft"), _slot("a", "biology", swap="a_bio"), _slot("b", "x")]
    graph = TechGraph(edges=(Edge("a", "b", EdgeKind.PREREQUISITE),))
    assert emit_mod.wire_edges(graph, slots, [frozenset(), bio, frozenset()]) == [[0, 2, 0]]


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def emitted(install, gigas_root: Path, tmp_path_factory):
    load_order = (
        LoadOrder()
        .add(base_game_source(install.game, install.version))
        .add(mod_source(gigas_root, key="gigas"))
    )
    extraction = extract(load_order)
    graph = graph_mod.build(extraction)
    assignment = rows_mod.assign(extraction, graph, rows_mod.load_config(rows_mod.DEFAULT_ROWS_CONFIG))
    layout = layout_mod.build(graph, assignment)
    localisation = loc_mod.load(load_order)
    out = tmp_path_factory.mktemp("data")
    emit_mod.emit(extraction, graph, layout, localisation, assignment, out)
    dataset = json.loads((out / "dataset.json").read_text(encoding="utf-8"))
    details = json.loads((out / "details.json").read_text(encoding="utf-8"))
    return graph, dataset, details


def _pairs(dataset) -> set[tuple[str, str, str]]:
    nodes, kinds = dataset["nodes"], dataset["edgeKinds"]
    return {(nodes[s]["k"], nodes[t]["k"], kinds[k]) for s, t, k in dataset["edges"]}


@pytest.mark.corpus
def test_every_drawn_edge_is_a_real_dependency(emitted):
    graph, dataset, _ = emitted
    real = {(e.source, e.target, e.kind.value) for e in graph.edges}
    assert _pairs(dataset) - real == set()


@pytest.mark.corpus
def test_every_real_dependency_is_drawn(emitted):
    graph, dataset, _ = emitted
    real = {(e.source, e.target, e.kind.value) for e in graph.edges}
    assert real - _pairs(dataset) == set()


@pytest.mark.corpus
def test_the_reported_phantom_edges_are_gone(emitted):
    _, dataset, _ = emitted
    pairs = {(a, b) for a, b, _ in _pairs(dataset)}
    assert ("tech_giga_planetary_shield_generator_2", "giga_tech_amb_supertensiles_acot_delta") not in pairs
    assert ("tech_scourge_deployment", "tech_doctrine_navy_size_1") not in pairs


@pytest.mark.corpus
def test_a_variant_slot_wears_its_swap(emitted):
    """Ring Segment relocated to society/biology is a different technology to read."""
    _, dataset, details = emitted
    ring = {n.get("sw"): n for n in dataset["nodes"] if n["k"] == "tech_ring_world"}
    assert ring[None]["n"] == "Ring Segment"
    bio = ring["giga_tech_ring_world_swap_no_habitables_bio"]
    assert bio["n"] == "Artificial Deconstructor Ecologies"
    assert bio["ic"] != ring[None]["ic"]
    assert "giga_tech_ring_world_swap_no_habitables_bio" in details


@pytest.mark.corpus
def test_bio_fighter_wings_chain_through_their_own_row(emitted):
    _, dataset, _ = emitted
    nodes = dataset["nodes"]
    index = {(n["k"], n.get("sw")): i for i, n in enumerate(nodes)}
    target = index[("tech_strike_craft_2", "tech_bio_strike_craft_2")]
    sources = {(nodes[s]["k"], nodes[s].get("sw")) for s, t, _ in dataset["edges"] if t == target}
    assert ("tech_strike_craft_1", "tech_bio_strike_craft_1") in sources
    assert ("tech_strike_craft_1", None) not in sources


@pytest.mark.corpus
def test_perk_badges_cover_inherited_gates(emitted):
    _, dataset, details = emitted
    by_key = {n["k"]: n for n in dataset["nodes"] if "sw" not in n}
    qso = by_key["giga_tech_quasi_stellar_4"]
    assert qso.get("pb", -1) >= 0
    assert "perk-inherited" in qso["f"]
    assert details["giga_tech_quasi_stellar_4"]["ap"][0]["v"] == "Hyperdimensional Shielding"
    assert by_key["tech_dyson_sphere"].get("pb", -1) >= 0


@pytest.mark.corpus
def test_profiles_travel_with_the_dataset(emitted):
    """Masks, renames and their descriptions: all the browser needs to show one empire's tree."""
    _, dataset, details = emitted
    profiles = [p["k"] for p in dataset["profiles"]]
    bio = 1 << profiles.index("regular-bio-ships")
    lasers = next(n for n in dataset["nodes"] if n["k"] == "tech_lasers_1")
    renamed = next(p for p in lasers["pv"] if p["sw"] == "tech_bio_lasers_1")
    assert int(renamed["m"], 16) & bio
    assert renamed["n"] != lasers["n"]
    assert "tech_bio_lasers_1" in details
    arkship = next(n for n in dataset["nodes"] if n["k"] == "tech_arkship_construction")
    assert int(arkship["hp"], 16) & (1 << profiles.index("regular"))
    vat = details["giga_tech_the_vat"]["ap"]
    mechromancy = next(c for gate in vat for alt in gate["a"] for c in alt if c["n"] == "Mechromancy")
    assert int(mechromancy["x"], 16) & (1 << profiles.index("regular"))


@pytest.mark.corpus
def test_a_badge_follows_the_routes_a_profile_can_take(emitted):
    """A nomad reaches Tetradimensional Engineering through the Blokkat Bureau, not Constructs."""
    _, dataset, _ = emitted
    profiles = [p["k"] for p in dataset["profiles"]]
    nomad = 1 << profiles.index("regular-nomadic")
    tetra = next(n for n in dataset["nodes"] if n["k"] == "giga_tech_tetradimensional_engineering")
    assert tetra.get("pb", -1) >= 0
    under_nomads = next(b for b in tetra["bv"] if int(b["m"], 16) & nomad)
    assert "pb" not in under_nomads


@pytest.mark.corpus
def test_unlock_routes_name_what_they_run_through(emitted):
    """Voidworm Immunity's special project is Voidworm Research; a hive mind is not offered it by first contact."""
    _, dataset, details = emitted
    routes = {r["n"] or r["k"]: r for r in details["tech_voidworm_immunity"]["u"]}
    assert {"n": "Voidworm Research", "c": "Special Project"} in routes["First Contact"]["s"]
    xeno = {r["n"]: r for r in details["tech_xeno_linguistics"]["u"] if r["k"] == "tagged"}
    names = {s["n"] for s in xeno["First Contact"]["s"]}
    assert "First Alien Encounter" in names
    hive = 1 << [p["k"] for p in dataset["profiles"]].index("hive")
    assert int(xeno["First Contact"]["x"], 16) & hive


@pytest.mark.corpus
def test_no_route_name_has_lost_words_to_runtime_state(emitted):
    """"Materiality Engine on [planet.GetName]" must not show as "Materiality Engine on"."""
    _, _, details = emitted
    names = {s["n"] for entry in details.values() for r in entry.get("u", ()) for s in r.get("s", ())}
    assert "Materiality Engine on" not in names
    assert all(name and not name.endswith((" on", " of", " the", ":")) for name in names)
