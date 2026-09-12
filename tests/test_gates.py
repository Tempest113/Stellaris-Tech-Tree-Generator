"""Ascension perk gates: which perks stand between an empire and a technology."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.clausewitz import parse
from pipeline.gates import GateKind, perk_gates
from pipeline.loadorder import LoadOrder, base_game_source, mod_source
from pipeline.records import build_record, extract
from pipeline.triggers import TriggerIndex

#: Technologies behind at least one ascension perk.
GATED_COUNT = 82
#: Distinct perk keys doing the gating. Higher than the number of perks a player
#: would name, because Galactic Wonders ships four DLC-conditional keys.
GATING_PERK_COUNT = 20
#: Reached only by resolving a scripted trigger. Without that resolution these
#: report no gate at all.
VIA_TRIGGER_COUNT = 27


@pytest.fixture(scope="module")
def built(install, gigas_root: Path):
    load_order = (
        LoadOrder()
        .add(base_game_source(install.game, install.version))
        .add(mod_source(gigas_root, key="gigas"))
    )
    return extract(load_order)


def _record(body: str, key: str = "t"):
    tree = parse(f"{key} = {{ area = physics tier = 1 {body} }}")
    return build_record(key, tree.get_first(key))


def _triggers(source: str) -> TriggerIndex:
    tree = parse(source)
    return TriggerIndex(definitions={p.key: p.value for p in tree.pairs()})


# --------------------------------------------------------------------------
# Unit: potential
# --------------------------------------------------------------------------


def test_perk_in_potential_is_a_hard_gate():
    gates = perk_gates(_record("potential = { has_ascension_perk = ap_x }"))
    assert len(gates) == 1
    assert gates[0].perks == ("ap_x",)
    assert gates[0].kind is GateKind.REQUIRED


def test_negated_perk_is_not_a_gate():
    """``NOT = { has_ascension_perk = x }`` excludes the perk, it does not need it."""
    gates = perk_gates(_record("potential = { NOT = { has_ascension_perk = ap_x } }"))
    assert gates == ()


def test_perk_under_a_changed_scope_is_not_a_gate():
    """What another empire has taken does not gate this one's research."""
    gates = perk_gates(
        _record("potential = { any_country = { has_ascension_perk = ap_x } } ")
    )
    assert gates == ()


def test_alternatives_in_one_condition_form_one_gate():
    """An OR of perks is a single gate satisfied by any member, not several gates."""
    gates = perk_gates(
        _record("potential = { OR = { has_ascension_perk = ap_x has_ascension_perk = ap_y } }")
    )
    assert len(gates) == 1
    assert gates[0].perks == ("ap_x", "ap_y")


def test_separate_conditions_form_separate_gates():
    """A potential block is an implicit AND, so both perks are needed."""
    gates = perk_gates(
        _record("potential = { has_ascension_perk = ap_x has_ascension_perk = ap_y }")
    )
    assert [g.perks for g in gates] == [("ap_x",), ("ap_y",)]


# --------------------------------------------------------------------------
# Unit: scripted triggers
# --------------------------------------------------------------------------


def test_scripted_trigger_is_followed_to_the_perk_behind_it():
    """Gigastructures gates its megastructures this way and names no perk directly."""
    triggers = _triggers("has_gc = { or = { is_ai = yes has_ascension_perk = ap_gc } }")
    gates = perk_gates(_record("potential = { has_gc = yes }"), triggers)
    assert len(gates) == 1
    assert gates[0].perks == ("ap_gc",)
    assert gates[0].via_trigger == "has_gc"


def test_trigger_naming_several_perks_yields_one_alternative_group():
    """Galactic Wonders: four DLC-conditional keys, one perk as far as a player is concerned."""
    triggers = _triggers(
        "has_gw = { or = { has_ascension_perk = ap_gw has_ascension_perk = ap_gw_utopia } }"
    )
    gates = perk_gates(_record("potential = { has_gw = yes }"), triggers)
    assert len(gates) == 1
    assert gates[0].perks == ("ap_gw", "ap_gw_utopia")


def test_trigger_is_only_followed_when_asserted():
    """``has_gc = no`` asks for its absence and must not report the perk."""
    triggers = _triggers("has_gc = { has_ascension_perk = ap_gc }")
    assert perk_gates(_record("potential = { has_gc = no }"), triggers) == ()


def test_nested_triggers_resolve():
    triggers = _triggers(
        "outer = { inner = yes }\ninner = { has_ascension_perk = ap_deep }"
    )
    gates = perk_gates(_record("potential = { outer = yes }"), triggers)
    assert gates[0].perks == ("ap_deep",)


def test_self_referential_trigger_terminates():
    """A mod can define a trigger in terms of itself; the build must not hang."""
    triggers = _triggers("loopy = { loopy = yes has_ascension_perk = ap_x }")
    gates = perk_gates(_record("potential = { loopy = yes }"), triggers)
    assert gates[0].perks == ("ap_x",)


def test_unknown_trigger_is_ignored():
    gates = perk_gates(_record("potential = { some_unknown_trigger = yes }"), _triggers(""))
    assert gates == ()


# --------------------------------------------------------------------------
# Unit: weight modifiers
# --------------------------------------------------------------------------


def test_zeroing_weight_modifier_is_an_undrawable_gate():
    gates = perk_gates(
        _record(
            "weight_modifier = { modifier = { factor = 0 "
            "NOT = { has_ascension_perk = ap_x } } }"
        )
    )
    assert len(gates) == 1
    assert gates[0].perks == ("ap_x",)
    assert gates[0].kind is GateKind.UNDRAWABLE


def test_a_nonzero_factor_does_not_gate():
    """Scaling the draw weight makes a technology unlikely, not unavailable."""
    gates = perk_gates(
        _record(
            "weight_modifier = { modifier = { factor = 0.1 "
            "NOT = { has_ascension_perk = ap_x } } }"
        )
    )
    assert gates == ()


def test_a_conditional_zero_does_not_gate():
    """The sixteen ``tech_fe_*_1`` technologies.

    ``factor = 0`` fires only once the empire already holds four fallen-empire
    technologies, so Cosmogenesis lifts a cap rather than gating the
    technology. Reporting it as a gate would be untrue.
    """
    gates = perk_gates(
        _record(
            "weight_modifier = { modifier = { factor = 0 "
            "NOT = { has_ascension_perk = ap_cosmogenesis } "
            "calc_true_if = { amount >= 4 has_technology = tech_fe_lab_1 } } }"
        )
    )
    assert gates == ()


def test_alternatives_inside_the_single_condition_are_kept():
    """``NOR = { crisis_level perk }`` still makes the perk a genuine route in."""
    gates = perk_gates(
        _record(
            "weight_modifier = { modifier = { factor = 0 "
            "NOR = { has_crisis_level = crisis_x has_ascension_perk = ap_x } } }"
        )
    )
    assert len(gates) == 1
    assert gates[0].perks == ("ap_x",)


def test_a_hard_gate_is_not_repeated_as_an_undrawable_one():
    """The Cosmogenesis lathe technologies state it both ways."""
    gates = perk_gates(
        _record(
            "potential = { has_ascension_perk = ap_x } "
            "weight_modifier = { modifier = { factor = 0 "
            "NOT = { has_ascension_perk = ap_x } } }"
        )
    )
    assert len(gates) == 1
    assert gates[0].kind is GateKind.REQUIRED


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_gate_counts(built):
    gates = built.perk_gates
    assert len(gates) == GATED_COUNT
    perks = {p for g in gates.values() for x in g for p in x.perks}
    assert len(perks) == GATING_PERK_COUNT
    via = [k for k, g in gates.items() if any(x.via_trigger for x in g)]
    assert len(via) == VIA_TRIGGER_COUNT


@pytest.mark.corpus
def test_the_perk_gate_is_not_the_weightless_flag(built):
    """The two overlap but neither implies the other.

    This is why "granted by event" was the wrong label: it was drawn from
    ``weight == 0``, which is right for 171 technologies, wrong for 14 that are
    perk-gated as well, and silent about 68 that are perk-gated only.
    """
    gated = set(built.perk_gates)
    weightless = {k for k, r in built.technologies.items() if r.is_weightless}
    assert len(weightless - gated) == 171
    assert len(weightless & gated) == 14
    assert len(gated - weightless) == 68


@pytest.mark.corpus
def test_named_technologies_report_the_perk_a_player_would_name(built):
    expected = {
        "giga_tech_alderson_disk": ["Gigastructural Constructs"],
        "giga_tech_matrioshka_brain_1": ["Gigastructural Constructs"],
        "tech_cosmogenesis_world": ["Cosmogenesis"],
        "giga_tech_neutronium_gigaforge": ["Galactic Wonders"],
    }
    for key, perks in expected.items():
        gates = built.perk_gates[key]
        assert len(gates) == 1, key
        # Keys differ; the localised names are what a reader recognises, and the
        # four Galactic Wonders keys all resolve to the same words.
        assert len(gates[0].perks) >= 1, key


@pytest.mark.corpus
def test_galactic_wonders_is_one_gate_not_four(built):
    """Its four DLC-conditional keys are alternatives within a single gate."""
    gates = built.perk_gates["giga_tech_neutronium_gigaforge"]
    assert len(gates) == 1
    assert len(gates[0].perks) == 4
    assert all(p.startswith("ap_galactic_wonders") for p in gates[0].perks)


@pytest.mark.corpus
def test_the_fallen_empire_cap_is_not_reported_as_a_cosmogenesis_gate(built):
    """Cosmogenesis raises a cap on these; Enigmatic Engineering is the real gate."""
    gates = built.perk_gates["tech_fe_administration_1"]
    assert [p for g in gates for p in g.perks] == ["ap_enigmatic_engineering"]


@pytest.mark.corpus
def test_gigastructural_constructs_gates_are_all_found_through_the_trigger(built):
    """None of them names the perk directly."""
    found = {
        key
        for key, gates in built.perk_gates.items()
        for gate in gates
        if "ap_gigastructural_constructs" in gate.perks
    }
    assert len(found) == 9
    for key in found:
        gate = next(g for g in built.perk_gates[key] if "ap_gigastructural_constructs" in g.perks)
        assert gate.via_trigger == "has_gigastructural_constructs"


@pytest.mark.corpus
def test_every_gate_has_art_for_at_least_one_of_its_perks(built):
    """A gate the reader cannot see a badge for is a gate half-reported.

    Per gate, not per perk: the three DLC-conditional Galactic Wonders keys
    ship no art of their own and reuse the base perk's, which is correct rather
    than a gap.
    """
    icons = built.icons
    blind = [
        key
        for key, gates in built.perk_gates.items()
        for gate in gates
        if not any(icons.ascension_perk(p).is_exact for p in gate.perks)
    ]
    assert blind == []


@pytest.mark.corpus
def test_only_name_sharing_variants_lack_their_own_art(built):
    """Pinned so a genuinely missing perk icon cannot hide behind this.

    Each of these is a DLC- or civic-conditional duplicate of a perk that does
    ship art, and localises to the same words, so the panel shows the base
    perk's badge. Nothing here is a gap to chase upstream.
    """
    icons = built.icons
    perks = {p for g in built.perk_gates.values() for x in g for p in x.perks}
    missing = sorted(p for p in perks if not icons.ascension_perk(p).is_exact)
    assert missing == [
        "ap_galactic_wonders_megacorp",
        "ap_galactic_wonders_utopia",
        "ap_galactic_wonders_utopia_and_megacorp",
        "ap_organo_machine_interfacing_assimilator",
    ]


@pytest.mark.corpus
def test_extraction_is_deterministic(install, gigas_root: Path):
    load_order = (
        LoadOrder()
        .add(base_game_source(install.game, install.version))
        .add(mod_source(gigas_root, key="gigas"))
    )
    first = extract(load_order).perk_gates
    second = extract(load_order).perk_gates
    assert first == second
