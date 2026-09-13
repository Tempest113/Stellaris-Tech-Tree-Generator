"""Gates: what an empire has to have chosen before it can research a technology."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import graph as graph_mod
from pipeline.clausewitz import parse
from pipeline.gates import (
    Condition,
    CrisisLevel,
    Gate,
    GateKind,
    badge_perks,
    condition_name,
    gates_for,
    strongest,
    with_inherited,
)
from pipeline.loadorder import LoadOrder, base_game_source, mod_source
from pipeline.records import Extraction, build_record, extract
from pipeline.triggers import TriggerIndex
from pipeline.unlocks import Route, RouteKind

#: Technologies declaring at least one gate.
GATED_COUNT = 114
#: Of those, gated by at least one ascension perk.
PERK_GATED_COUNT = 91
#: Technologies with no gate of their own that inherit one through what they need.
INHERITED_ONLY_COUNT = 51


def perk(key: str) -> Condition:
    return Condition("perk", key)


def one(*conditions: Condition) -> tuple[tuple[Condition, ...], ...]:
    """A gate with one alternative per condition."""
    return tuple((c,) for c in conditions)


@pytest.fixture(scope="module")
def built(install, gigas_root: Path):
    load_order = (
        LoadOrder()
        .add(base_game_source(install.game, install.version))
        .add(mod_source(gigas_root, key="gigas"))
    )
    return extract(load_order)


@pytest.fixture(scope="module")
def effective(built):
    return with_inherited(graph_mod.build(built), built.gates)


def _record(body: str, key: str = "t"):
    tree = parse(f"{key} = {{ area = physics tier = 1 {body} }}")
    return build_record(key, tree.get_first(key))


def _triggers(source: str) -> TriggerIndex:
    tree = parse(source)
    return TriggerIndex(definitions={p.key: p.value for p in tree.pairs()})


def _gates(body: str, **kwargs):
    return gates_for(_record(body), **kwargs)


# --------------------------------------------------------------------------
# Unit: potential
# --------------------------------------------------------------------------


def test_perk_in_potential_is_a_hard_gate():
    gates = _gates("potential = { has_ascension_perk = ap_x }")
    assert [(g.alternatives, g.kind) for g in gates] == [(one(perk("ap_x")), GateKind.REQUIRED)]


def test_negated_perk_is_not_a_gate():
    """``NOT = { has_ascension_perk = x }`` excludes the perk, it does not need it."""
    assert _gates("potential = { NOT = { has_ascension_perk = ap_x } }") == ()


def test_perk_under_a_changed_scope_is_not_a_gate():
    """What another empire has taken does not gate this one's research."""
    assert _gates("potential = { any_country = { has_ascension_perk = ap_x } }") == ()


def test_alternatives_in_one_condition_form_one_gate():
    gates = _gates("potential = { OR = { has_ascension_perk = ap_x has_ascension_perk = ap_y } }")
    assert [g.alternatives for g in gates] == [one(perk("ap_x"), perk("ap_y"))]


def test_separate_conditions_form_separate_gates():
    """A potential block is an implicit AND, so both perks are needed."""
    gates = _gates("potential = { has_ascension_perk = ap_x has_ascension_perk = ap_y }")
    assert [g.alternatives for g in gates] == [one(perk("ap_x")), one(perk("ap_y"))]


def test_an_unconstrained_alternative_removes_the_gate():
    """The old bug: an OR with any other route in does not require the perk."""
    gates = _gates("potential = { OR = { has_country_flag = x has_ascension_perk = ap_y } }")
    assert gates == ()


def test_non_perk_choices_are_alternatives_too():
    """The Vat: a tradition route and a perk route are both ways in."""
    gates = _gates(
        "potential = { has_ascension_perk = ap_wonders "
        "OR = { has_active_tradition = tr_genetics has_ascension_perk = ap_mechromancy } }"
    )
    assert [g.alternatives for g in gates] == [
        one(perk("ap_wonders")),
        one(Condition("tradition", "tr_genetics"), perk("ap_mechromancy")),
    ]


def test_ai_only_alternatives_are_dropped():
    """``is_ai = yes`` can never hold for a player."""
    gates = _gates(
        "potential = { OR = { AND = { is_ai = yes has_country_flag = x } has_ascension_perk = ap_y } }"
    )
    assert [g.alternatives for g in gates] == [one(perk("ap_y"))]


def test_a_condition_the_load_order_never_defines_is_not_a_route():
    """Compatibility checks for absent mods can never be met."""
    defined = {"tradition": frozenset({"tr_real"}), "perk": frozenset({"ap_y"})}
    gates = _gates(
        "potential = { OR = { has_active_tradition = tr_other_mod has_ascension_perk = ap_y } }",
        defined=defined,
    )
    assert [g.alternatives for g in gates] == [one(perk("ap_y"))]


def test_nested_and_inside_or_becomes_a_combined_alternative():
    gates = _gates(
        "potential = { OR = { AND = { has_ascension_perk = ap_a has_origin = origin_b } "
        "has_ascension_perk = ap_c } }"
    )
    assert [g.alternatives for g in gates] == [
        ((perk("ap_a"), Condition("origin", "origin_b")), (perk("ap_c"),))
    ]


def test_multi_child_not_is_a_nor():
    """``NOT = { a b }`` in Stellaris holds when neither holds; neither is required."""
    assert _gates("potential = { NOT = { has_ascension_perk = ap_a has_ascension_perk = ap_b } }") == ()


# --------------------------------------------------------------------------
# Unit: scripted triggers and flags
# --------------------------------------------------------------------------


def test_scripted_trigger_is_followed_to_the_perk_behind_it():
    triggers = _triggers("has_gc = { or = { is_ai = yes has_ascension_perk = ap_gc } }")
    gates = _gates("potential = { has_gc = yes }", triggers=triggers)
    assert [(g.alternatives, g.via_trigger) for g in gates] == [(one(perk("ap_gc")), "has_gc")]


def test_a_named_trigger_is_kept_whole():
    """``has_genetically_ascended`` reads as one choice, not four tradition finishers."""
    triggers = _triggers("has_ga = { OR = { has_tradition = tr_a has_tradition = tr_b } }")
    gates = _gates("potential = { has_ga = yes }", triggers=triggers, named=frozenset({"has_ga"}))
    assert [g.alternatives for g in gates] == [one(Condition("trigger", "has_ga"))]


def test_trigger_equals_no_negates_its_body():
    triggers = _triggers("has_gc = { has_ascension_perk = ap_gc }")
    assert _gates("potential = { has_gc = no }", triggers=triggers) == ()


def test_self_referential_trigger_terminates():
    triggers = _triggers("loopy = { loopy = yes has_ascension_perk = ap_x }")
    gates = _gates("potential = { loopy = yes }", triggers=triggers)
    assert gates[0].perks == ("ap_x",)


def test_a_flag_is_gated_by_whatever_sets_it():
    """The planet killers test ``colossus_project``, set only by the Colossus Project."""
    setters = {"colossus_project": (perk("ap_colossus"),)}
    gates = _gates("potential = { has_country_flag = colossus_project }", flags=setters.get)
    assert [g.alternatives for g in gates] == [one(perk("ap_colossus"))]


def test_always_no_disables_a_technology():
    from pipeline.gates import is_disabled

    assert is_disabled(_record("potential = { always = no has_ascension_perk = ap_x }"))
    assert is_disabled(_record("potential = { is_ai = yes }"))
    assert not is_disabled(_record("potential = { OR = { always = no has_ascension_perk = ap_x } }"))
    assert not is_disabled(_record("potential = { always = yes }"))


def test_a_named_flag_is_kept_as_a_condition():
    """Tetradimensional Engineering: Gigastructural Constructs, or the Blokkat Bureau."""
    gates = _gates(
        "potential = { OR = { has_ascension_perk = ap_gc has_country_flag = bureau } }",
        flags=lambda flag: None,
        named=frozenset({"bureau"}),
    )
    assert [g.alternatives for g in gates] == [one(perk("ap_gc"), Condition("flag", "bureau"))]


def test_a_flag_anyone_can_come_by_is_unconstrained():
    gates = _gates("potential = { has_country_flag = anything }", flags=lambda flag: None)
    assert gates == ()


# --------------------------------------------------------------------------
# Unit: weight modifiers and unlock routes
# --------------------------------------------------------------------------


def test_zeroing_weight_modifier_is_an_undrawable_gate():
    gates = _gates("weight_modifier = { modifier = { factor = 0 NOT = { has_ascension_perk = ap_x } } }")
    assert [(g.alternatives, g.kind) for g in gates] == [(one(perk("ap_x")), GateKind.UNDRAWABLE)]


def test_a_nonzero_factor_does_not_gate():
    gates = _gates("weight_modifier = { modifier = { factor = 0.1 NOT = { has_ascension_perk = ap_x } } }")
    assert gates == ()


def test_a_conditional_zero_does_not_gate():
    """The sixteen ``tech_fe_*_1`` technologies: the zero bites only past a cap."""
    gates = _gates(
        "weight_modifier = { modifier = { factor = 0 "
        "NOT = { has_ascension_perk = ap_cosmogenesis } "
        "calc_true_if = { amount >= 4 has_technology = tech_fe_lab_1 } } }"
    )
    assert gates == ()


def test_alternatives_inside_a_zeroing_nor_are_kept():
    gates = _gates(
        "weight_modifier = { modifier = { factor = 0 "
        "NOR = { has_crisis_level = crisis_x_level_5 has_ascension_perk = ap_x } } }"
    )
    assert [g.alternatives for g in gates] == [one(Condition("crisis", "crisis_x_level_5"), perk("ap_x"))]


def test_a_perk_route_gates_an_undrawable_technology():
    routes = (Route(RouteKind.PERK, "ap_wonders", ()),)
    gates = gates_for(_record("weight = 0"), unlock_routes=routes)
    assert [(g.alternatives, g.kind) for g in gates] == [(one(perk("ap_wonders")), GateKind.GRANTED)]


def test_a_perk_route_does_not_gate_a_drawable_technology():
    """The perk is a shortcut there, not the only way in."""
    routes = (Route(RouteKind.PERK, "ap_wonders", ()),)
    assert gates_for(_record("weight = 10"), unlock_routes=routes) == ()


def test_a_hard_gate_is_not_repeated_as_an_undrawable_one():
    gates = _gates(
        "potential = { has_ascension_perk = ap_x } "
        "weight_modifier = { modifier = { factor = 0 NOT = { has_ascension_perk = ap_x } } }"
    )
    assert [g.kind for g in gates] == [GateKind.REQUIRED]


# --------------------------------------------------------------------------
# Unit: inheritance and presentation
# --------------------------------------------------------------------------


def _graph(source: str):
    extraction = Extraction()
    for pair in parse(source).pairs():
        extraction.technologies[pair.key] = build_record(pair.key, pair.value)
    return graph_mod.build(extraction)


def test_a_gate_is_inherited_through_a_hard_prerequisite():
    graph = _graph(
        "a = { area = physics tier = 1 }\n"
        "b = { area = physics tier = 2 prerequisites = { a } }\n"
        "c = { area = physics tier = 3 prerequisites = { b } }\n"
    )
    own = {"a": (Gate(one(perk("ap_x")), GateKind.REQUIRED),)}
    assert with_inherited(graph, own)["c"] == (
        Gate(one(perk("ap_x")), GateKind.REQUIRED, inherited_from="a"),
    )


def test_an_or_group_passes_on_only_what_every_option_carries():
    graph = _graph(
        "a = { area = physics tier = 1 }\n"
        "b = { area = physics tier = 1 }\n"
        "c = { area = physics tier = 2 prerequisites = { OR = { a b } } }\n"
    )
    own = {"a": (Gate(one(perk("ap_x")), GateKind.REQUIRED),)}
    assert "c" not in with_inherited(graph, own)
    both = {**own, "b": (Gate(one(perk("ap_x")), GateKind.REQUIRED),)}
    assert with_inherited(graph, both)["c"][0].perks == ("ap_x",)


def test_a_declared_gate_outranks_the_same_gate_inherited():
    graph = _graph(
        "a = { area = physics tier = 1 }\n"
        "b = { area = physics tier = 2 prerequisites = { a } }\n"
    )
    own = {
        "a": (Gate(one(perk("ap_x")), GateKind.REQUIRED),),
        "b": (Gate(one(perk("ap_x")), GateKind.UNDRAWABLE),),
    }
    gates = with_inherited(graph, own)["b"]
    assert len(gates) == 1 and not gates[0].is_inherited
    assert strongest(gates) is gates[0]


def test_the_badge_prefers_a_gate_that_names_a_perk():
    tradition = Gate(one(Condition("tradition", "tr_x")), GateKind.REQUIRED)
    perk_gate = Gate(one(perk("ap_x")), GateKind.UNDRAWABLE)
    assert strongest((tradition, perk_gate)) is perk_gate


class _Loc:
    def __init__(self, entries):
        self.entries = entries

    def get(self, key, default=None):
        return self.entries.get(key, default)


def test_a_crisis_level_is_named_after_its_path():
    """Localised alone, a crisis level is just "Danger"."""
    loc = _Loc({"ap_become_the_crisis": "Galactic Nemesis", "crisis_level_3": "Danger"})
    levels = {"crisis_level_3": CrisisLevel(perk="ap_become_the_crisis", level=3)}
    name = condition_name(Condition("crisis", "crisis_level_3"), loc, {}, levels)
    assert name == "Galactic Nemesis Level 3"


def test_an_unindexed_crisis_level_is_named_from_its_key():
    loc = _Loc({"ap_cosmogenesis": "Cosmogenesis"})
    name = condition_name(Condition("crisis", "crisis_cosmogenesis_level_5"), loc, {})
    assert name == "Cosmogenesis Level 5"


def test_a_crisis_level_badges_the_perk_that_starts_its_path():
    gate = Gate(alternatives=one(Condition("crisis", "c5")), kind=GateKind.UNDRAWABLE)
    levels = {"c5": CrisisLevel(perk="ap_cosmogenesis", level=5)}
    assert badge_perks(gate, levels) == ("ap_cosmogenesis",)
    assert strongest((gate,), levels) is gate


def test_a_configured_name_wins():
    loc = _Loc({"tr_x": "Something Else"})
    assert condition_name(Condition("tradition", "tr_x"), loc, {"tr_x": "Genetic Ascension"}) == "Genetic Ascension"


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_gate_counts(built, effective):
    assert len(built.gates) == GATED_COUNT
    assert sum(1 for gates in built.gates.values() if any(g.perks for g in gates)) == PERK_GATED_COUNT
    assert len(effective) - len(built.gates) == INHERITED_ONLY_COUNT


@pytest.mark.corpus
def test_the_vat_needs_wonders_and_one_ascension_route(built):
    """The reported case: genetic ascension or Mechromancy, alongside Galactic Wonders."""
    gates = built.gates["giga_tech_the_vat"]
    assert len(gates) == 2
    wonders, route = gates
    assert all(c.key.startswith("ap_galactic_wonders") for c in wonders.conditions)
    assert set(route.conditions) == {
        Condition("trigger", "has_genetically_ascended"),
        Condition("tradition", "tr_genetics_finish_extra_traits"),
        perk("ap_mechromancy"),
    }


@pytest.mark.corpus
def test_colossi_are_gated_by_the_colossus_project(built):
    """Four hops: perk -> event -> special project -> event -> give_technology."""
    gates = built.gates["tech_colossus"]
    assert [(g.alternatives, g.kind) for g in gates] == [(one(perk("ap_colossus")), GateKind.GRANTED)]


@pytest.mark.corpus
def test_planet_killers_are_gated_by_the_colossus_project_flag(built):
    for key in ("tech_pk_cracker", "tech_pk_neutron", "tech_pk_shielder", "tech_pk_godray"):
        conditions = {c for g in built.gates[key] for c in g.conditions}
        assert perk("ap_colossus") in conditions, key


@pytest.mark.corpus
def test_galactic_wonders_megastructures_are_handed_out_by_the_perk(built):
    for key in ("tech_dyson_sphere", "tech_ring_world", "tech_matter_decompressor"):
        gates = built.gates[key]
        assert [g.kind for g in gates] == [GateKind.GRANTED], key
        assert all(c.key.startswith("ap_galactic_wonders") for c in gates[0].conditions), key


@pytest.mark.corpus
def test_a_perk_offering_a_drawable_technology_early_is_not_a_gate(built):
    """Galactic Wonders offers Mega-Engineering; any empire can draw it anyway."""
    for key in ("tech_mega_engineering", "tech_habitat_2"):
        assert key in built.unlocks.granted_by, key
        assert key not in built.gates, key


@pytest.mark.corpus
def test_the_fallen_empire_cap_is_not_reported_as_a_cosmogenesis_gate(built):
    """Cosmogenesis raises a cap on these; the real gate is Enigmatic Engineering or a late crisis."""
    conditions = {c for g in built.gates["tech_fe_administration_1"] for c in g.conditions}
    assert perk("ap_cosmogenesis") not in conditions
    assert perk("ap_enigmatic_engineering") in conditions


@pytest.mark.corpus
def test_interstellar_habitat_accepts_the_void_dweller_origins(built):
    """``is_void_dweller_empire`` ORs Voidborne with two origins; Voidborne alone was wrong."""
    conditions = {c for g in built.gates["giga_tech_interstellar_habitat"] for c in g.conditions}
    assert perk("ap_voidborn") in conditions
    assert Condition("origin", "origin_void_dwellers") in conditions


@pytest.mark.corpus
def test_compatibility_checks_for_absent_mods_are_dropped(built):
    conditions = {c for g in built.gates["giga_tech_maginot_world"] for c in g.conditions}
    assert Condition("tradition", "frr_supremacy_deterrence_c") not in conditions


@pytest.mark.corpus
def test_the_qso_chain_inherits_its_gate_from_its_first_technology(effective):
    for n in range(2, 7):
        gates = effective[f"giga_tech_quasi_stellar_{n}"]
        assert [(g.perks, g.inherited_from) for g in gates] == [
            (("ap_qso",), "giga_tech_quasi_stellar_1")
        ]


@pytest.mark.corpus
def test_gigastructural_constructs_gates_are_found_through_the_trigger(built):
    via = {
        key
        for key, gates in built.gates.items()
        for gate in gates
        if "ap_gigastructural_constructs" in gate.perks and gate.via_trigger == "has_gigastructural_constructs"
    }
    assert {"giga_tech_alderson_disk", "giga_tech_matrioshka_brain_1"} <= via


@pytest.mark.corpus
def test_every_badged_perk_has_art(built, effective):
    """A gate the reader cannot see a badge for is a gate half-reported."""
    icons = built.icons
    levels = built.crisis_levels
    blind = [
        key
        for key, gates in effective.items()
        if (badge := strongest(gates, levels)) is not None
        and (perks := badge_perks(badge, levels))
        and not any(icons.ascension_perk(p).is_exact for p in perks)
    ]
    assert blind == []


@pytest.mark.corpus
def test_retired_and_absent_mod_technologies_are_disabled(built):
    """``has_acot`` is Gigastructures' ``always = no`` stub until ACOT overrides it."""
    assert built.disabled == {
        "giga_tech_aeternite_weaponry",
        "giga_tech_amb_supertensiles_acot_alpha",
        "giga_tech_amb_supertensiles_acot_delta",
        "giga_tech_amb_supertensiles_acot_phanon",
        "giga_tech_amb_supertensiles_acot_sigma",
        "giga_tech_interstellar_ringworld",
        "giga_tech_orbital_elysium",
        "giga_tech_stellar_ring_habitat",
    }


@pytest.mark.corpus
def test_crisis_levels_know_their_perk_and_place(built):
    """Path and perk names do not line up, so neither is guessed from the other."""
    levels = built.crisis_levels
    assert levels["crisis_cosmogenesis_level_5"] == CrisisLevel("ap_cosmogenesis", 5)
    assert levels["crisis_level_3"] == CrisisLevel("ap_become_the_crisis", 3)
    assert levels["crisis_behemoth_level_1"] == CrisisLevel("ap_behemoths", 1)
    assert levels["crisis_hyperthermia_level_2"] == CrisisLevel("ap_galactic_hyperthermia", 2)


@pytest.mark.corpus
def test_the_fallen_empire_megaworkshops_carry_the_cosmogenesis_badge(built, effective):
    """Drawable only at Cosmogenesis Level 5; the second inherits it from the first."""
    levels = built.crisis_levels
    for key in ("giga_tech_fe_megaworkshop_1", "giga_tech_fe_megaworkshop_2"):
        badge = strongest(effective[key], levels)
        assert badge_perks(badge, levels) == ("ap_cosmogenesis",), key
    assert not strongest(effective["giga_tech_fe_megaworkshop_1"], levels).is_inherited


@pytest.mark.corpus
def test_tetradimensional_engineering_needs_constructs_or_the_blokkat_bureau(built):
    gates = built.gates["giga_tech_tetradimensional_engineering"]
    assert [set(g.conditions) for g in gates] == [
        {perk("ap_gigastructural_constructs"), Condition("flag", "blokkat_bureau_unlocked")}
    ]


@pytest.mark.corpus
def test_extraction_is_deterministic(install, gigas_root: Path):
    load_order = (
        LoadOrder()
        .add(base_game_source(install.game, install.version))
        .add(mod_source(gigas_root, key="gigas"))
    )
    assert extract(load_order).gates == extract(load_order).gates
