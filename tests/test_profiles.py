"""Empire profiles: what one kind of empire sees of the tree."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import graph as graph_mod
from pipeline import layout as layout_mod
from pipeline import rows as rows_mod
from pipeline.clausewitz import parse
from pipeline.gates import Condition
from pipeline.loadorder import LoadOrder, base_game_source, mod_source
from pipeline.profiles import (
    TV,
    Definitions,
    Evaluator,
    Profile,
    compute_views,
)
from pipeline.records import extract
from pipeline.triggers import TriggerIndex

#: Every combination an empire can be created as, all DLC owned. 16 regular (the
#: four toggles other than Wilderness, free), 10 hive minds (Wilderness forces
#: bio-ships and rules out Nomadic), 8 machine intelligences (no machine
#: species toggle: they are machines already).
PROFILE_COUNT = 34


def _definitions(**blocks: str) -> Definitions:
    """Definitions from inline Clausewitz, one directory per keyword."""
    triggers = TriggerIndex(
        definitions={p.key: p.value for p in parse(blocks.pop("triggers", "")).pairs()}
    )
    parsed = {name: {p.key: p.value for p in parse(text).pairs()} for name, text in blocks.items()}
    return Definitions(
        triggers=triggers,
        civics=parsed.get("civics", {}),
        perks=parsed.get("perks", {}),
        species_classes=parsed.get("species_classes", {}),
        regular_authorities=frozenset({"auth_democratic", "auth_corporate"}),
    )


def _trigger(source: str, profile: Profile, definitions: Definitions | None = None) -> TV:
    return Evaluator(profile, definitions or _definitions()).trigger(parse(source))


# --------------------------------------------------------------------------
# Unit: reading triggers against a profile
# --------------------------------------------------------------------------


def test_the_profile_answers_what_it_fixes():
    hive = Profile("hive", bio_ships=True)
    assert _trigger("has_authority = auth_hive_mind", hive) is TV.TRUE
    assert _trigger("has_ethic = ethic_gestalt_consciousness", hive) is TV.TRUE
    assert _trigger("country_uses_bio_ships = yes", hive) is TV.TRUE
    assert _trigger("is_nomadic = yes", hive) is TV.FALSE


def test_what_a_profile_leaves_open_is_unknown_and_hides_nothing():
    regular = Profile("regular")
    assert _trigger("has_authority = auth_democratic", regular) is TV.UNKNOWN
    assert _trigger("has_country_flag = anything", regular) is TV.UNKNOWN
    assert _trigger("NOT = { has_country_flag = anything }", regular) is TV.UNKNOWN


def test_all_dlc_is_owned():
    assert _trigger("has_biogenesis_dlc = yes", Profile("regular")) is TV.TRUE
    assert _trigger("has_ancrel = no", Profile("regular"), _definitions(triggers="has_ancrel = { host_has_dlc = x }")) is TV.FALSE


def test_kleene_logic():
    regular = Profile("regular")
    assert _trigger("OR = { has_country_flag = x is_nomadic = no }", regular) is TV.TRUE
    assert _trigger("has_country_flag = x is_nomadic = yes", regular) is TV.FALSE
    assert _trigger("NOR = { has_country_flag = x is_gestalt = yes }", regular) is TV.UNKNOWN


def test_a_machine_founder_is_unknown_for_a_biological_empire():
    """Synthetic ascension can still make one."""
    source = "founder_species = { is_archetype = MACHINE }"
    assert _trigger(source, Profile("regular")) is TV.UNKNOWN
    assert _trigger(source, Profile("regular", machine_species=True)) is TV.TRUE
    assert _trigger(source, Profile("hive")) is TV.FALSE
    assert _trigger("is_individual_machine = yes", Profile("machine")) is TV.FALSE


def test_a_perk_is_impossible_when_its_own_potential_is():
    """Mechromancy is for machine intelligences; the Vat's route through it is not a biological empire's."""
    defs = _definitions(
        triggers="is_machine_empire = { has_authority = auth_machine_intelligence }",
        perks="ap_mechromancy = { potential = { is_machine_empire = yes } }",
    )
    assert _trigger("has_ascension_perk = ap_mechromancy", Profile("regular"), defs) is TV.FALSE
    assert _trigger("has_ascension_perk = ap_mechromancy", Profile("machine"), defs) is TV.UNKNOWN


def test_designer_blocks_read_value_sets_and_limits():
    defs = _definitions(
        civics="""
            civic_hive_only = { potential = { authority = { value = auth_hive_mind } } }
            civic_not_corporate = { potential = { authority = { NOT = { value = auth_corporate } } } }
        """,
        species_classes="""
            MACHINE = {
                archetype = MACHINE
                possible = { AND = { limit = { has_machine_age_dlc = yes } authority = { NOT = { value = auth_hive_mind } } } }
            }
        """,
    )
    regular = Evaluator(Profile("regular"), defs)
    assert regular.civic("civic_hive_only") is TV.FALSE
    assert regular.civic("civic_not_corporate") is TV.UNKNOWN
    assert Evaluator(Profile("hive"), defs).available("species_class", "MACHINE") is TV.FALSE


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built(install, gigas_root: Path):
    load_order = (
        LoadOrder()
        .add(base_game_source(install.game, install.version))
        .add(mod_source(gigas_root, key="gigas"))
    )
    extraction = extract(load_order)
    graph = graph_mod.build(extraction)
    assignment = rows_mod.assign(extraction, graph, rows_mod.load_config(rows_mod.DEFAULT_ROWS_CONFIG))
    layout = layout_mod.build(graph, assignment)
    slots = sorted(layout.slots, key=lambda s: (s.row.area, s.row.category, s.column, s.cell_index))
    views = compute_views(graph, slots, extraction.profile_definitions, extraction.profiles)
    return extraction, slots, views


def _bit(extraction, key: str) -> int:
    return 1 << [p.key for p in extraction.profiles].index(key)


@pytest.mark.corpus
def test_only_creatable_empires_are_profiles(built):
    extraction, _, _ = built
    profiles = extraction.profiles
    assert len(profiles) == PROFILE_COUNT
    assert all(p.authority == "hive" and p.bio_ships and not p.nomadic for p in profiles if p.wilderness)
    assert all(p.authority == "regular" for p in profiles if p.machine_species)
    assert any(p.authority == "machine" and p.bio_ships for p in profiles)
    assert any(p.authority == "hive" and p.nomadic for p in profiles)


@pytest.mark.corpus
def test_technologies_no_empire_can_research_are_disabled(built):
    extraction, _, _ = built
    assert extraction.disabled == {
        "giga_tech_aeternite_weaponry",
        "giga_tech_amb_supertensiles_acot_alpha",
        "giga_tech_amb_supertensiles_acot_delta",
        "giga_tech_amb_supertensiles_acot_phanon",
        "giga_tech_amb_supertensiles_acot_sigma",
        "giga_tech_interstellar_ringworld",
        "giga_tech_orbital_elysium",
        "giga_tech_stellar_ring_habitat",
        "tech_archeology_lab",
        "tech_frameworld_defensive_station_2",
        "tech_frameworld_defensive_station_3",
        "tech_frameworld_defensive_station_4",
        "tech_frameworld_defensive_station_5",
        "tech_frameworld_harvest_1",
        "tech_frameworld_harvest_2",
        "tech_frameworld_harvest_3",
        "tech_frameworld_repeatable_outpost_output",
    }


@pytest.mark.corpus
def test_the_vat_routes_by_empire(built):
    """Galactic Wonders and Genetic Ascension for a biological empire, Mechromancy for a machine one."""
    extraction, _, views = built
    mechromancy = views.condition_impossible(Condition("perk", "ap_mechromancy"), extraction.crisis_levels)
    genetics = views.condition_impossible(
        Condition("trigger", "has_genetically_ascended"), extraction.crisis_levels
    )
    assert mechromancy & _bit(extraction, "regular")
    assert not mechromancy & _bit(extraction, "machine")
    assert genetics & _bit(extraction, "machine")
    assert not genetics & _bit(extraction, "regular")


@pytest.mark.corpus
def test_a_relocated_technology_shows_in_one_row_per_profile(built):
    extraction, slots, views = built
    titans = {s.swap: i for i, s in enumerate(slots) if s.technology == "tech_titans"}
    regular, bio = _bit(extraction, "regular"), _bit(extraction, "regular-bio-ships")
    assert not views.hidden[titans[None]] & regular
    assert views.hidden[titans["tech_biogenesis_titans"]] & regular
    assert views.hidden[titans[None]] & bio
    assert not views.hidden[titans["tech_biogenesis_titans"]] & bio


@pytest.mark.corpus
def test_the_first_swap_that_holds_applies(built):
    """A nomadic bio-ship empire's Titans are the nomads', listed first."""
    extraction, slots, views = built
    both = _bit(extraction, "regular-bio-ships-nomadic")
    titans = {s.swap: i for i, s in enumerate(slots) if s.technology == "tech_titans"}
    assert views.hidden[titans["tech_biogenesis_titans"]] & both
    assert ("tech_nomads_titans", both) in [(n, m & both) for n, m in views.presentations[titans[None]]]


@pytest.mark.corpus
def test_an_in_place_swap_renames_a_card(built):
    extraction, slots, views = built
    lasers = next(i for i, s in enumerate(slots) if s.technology == "tech_lasers_1")
    shown = dict(views.presentations[lasers])
    assert shown["tech_bio_lasers_1"] & _bit(extraction, "regular-bio-ships")
    assert not shown["tech_bio_lasers_1"] & _bit(extraction, "regular")


@pytest.mark.corpus
def test_nomad_only_technologies_are_hidden_from_settled_empires(built):
    extraction, _, views = built
    arkship = views.technology_hidden["tech_arkship_construction"]
    assert arkship & _bit(extraction, "regular")
    assert not arkship & _bit(extraction, "regular-nomadic")
