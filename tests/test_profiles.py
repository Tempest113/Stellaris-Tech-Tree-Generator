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
    route_truth,
)
from pipeline.records import extract
from pipeline.triggers import TriggerIndex
from pipeline.unlocks import Definition, RouteKind, UnlockConfig, UnlockIndex, routes, routes_finder

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


def test_a_flag_nothing_names_is_never_set():
    defs = _definitions()
    defs.flag_words = frozenset({"set_somewhere"})
    assert _trigger("has_country_flag = set_somewhere", Profile("regular"), defs) is TV.UNKNOWN
    assert _trigger("has_country_flag = never_named", Profile("regular"), defs) is TV.FALSE


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


def test_a_scoped_flag_is_the_flag():
    """``fotd_hunter@capital_scope.observation_outpost_owner`` is ``fotd_hunter``, set per scope."""
    defs = _definitions()
    defs.flag_words = frozenset({"fotd_hunter"})
    assert _trigger("has_country_flag = fotd_hunter@root", Profile("regular"), defs) is TV.UNKNOWN


def test_a_route_is_closed_where_a_condition_on_it_cannot_hold():
    """An event only gestalt empires see, reached through an option only militarists can pick."""
    index = UnlockIndex(
        definitions={
            ("event", "e.1"): Definition(grants=["t"], trigger=parse("is_gestalt = yes")),
            ("event", "e.2"): Definition(
                calls=[("event", "e.1")],
                call_guards={("event", "e.1"): [(parse("is_militarist = yes"),)]},
            ),
        }
    )
    defs = _definitions(
        triggers="""
            is_gestalt = { has_ethic = ethic_gestalt_consciousness }
            is_militarist = { has_ethic = ethic_militarist }
        """
    )
    [route] = routes("t", index, UnlockConfig())
    assert route_truth(Evaluator(Profile("regular"), defs), route, "t", index) is TV.FALSE
    assert route_truth(Evaluator(Profile("hive"), defs), route, "t", index) is TV.FALSE

    # A second way to the event, with no option in the way, opens it to hive minds.
    index.definitions[("event", "e.3")] = Definition(calls=[("event", "e.1")])
    index._callers = None
    [route] = routes("t", index, UnlockConfig())
    assert len(route.chains) == 2
    assert route_truth(Evaluator(Profile("hive"), defs), route, "t", index) is not TV.FALSE
    assert route_truth(Evaluator(Profile("regular"), defs), route, "t", index) is TV.FALSE


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
    views = compute_views(
        graph,
        slots,
        extraction.profile_definitions,
        extraction.profiles,
        routes_finder(extraction.unlock_routes, extraction.unlocks, extraction.unlock_config),
        extraction.unlocks.component_prerequisites,
        extraction.unlocks,
        extraction.crisis_levels,
    )
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


@pytest.mark.corpus
def test_a_flag_nothing_sets_settles_ring_segment(built):
    """``giga_one_planet_origin`` is a hook for other mods; in this load order nobody sets it."""
    extraction, slots, views = built
    ring = {s.swap: i for i, s in enumerate(slots) if s.technology == "tech_ring_world"}
    settled = _bit(extraction, "regular")
    assert not views.hidden[ring[None]] & settled
    assert views.hidden[ring["giga_tech_ring_world_swap_no_habitables"]] & settled



@pytest.mark.corpus
def test_an_ai_only_grant_is_no_way_in(built):
    """Nomads cannot take Galactic Wonders, and Ring Segment's only other grant is for AI empires."""
    extraction, slots, views = built
    nomad = _bit(extraction, "regular-nomadic")
    assert all(views.hidden[i] & nomad for i, s in enumerate(slots) if s.technology == "tech_ring_world")
    kinds = {r.kind.value for r in extraction.unlock_routes["tech_ring_world"]}
    assert kinds == {"perk"}


@pytest.mark.corpus
def test_a_technology_never_drawn_for_a_profile_and_never_granted_is_gone(built):
    """The second fallen empire buildings are drawn only with Cosmogenesis, which no Wilderness can take."""
    extraction, _, views = built
    lab = views.technology_hidden["tech_fe_lab_2"]
    assert lab & _bit(extraction, "hive-bio-ships-wilderness")
    assert not lab & _bit(extraction, "hive-bio-ships")
    # `factor = 0 is_gestalt = yes`, and nothing hands it out.
    assert views.technology_hidden["tech_fe_market_1"] & _bit(extraction, "machine")


@pytest.mark.corpus
def test_a_technology_only_a_perk_hands_out_is_gone_without_the_perk(built):
    """The Birch World comes only from Vast Expanses, which no nomad can take."""
    extraction, _, views = built
    birch = views.technology_hidden["giga_tech_birch_world_1"]
    assert birch & _bit(extraction, "regular-nomadic")
    assert not birch & _bit(extraction, "regular")


@pytest.mark.corpus
def test_an_event_only_one_origin_sees_is_no_way_in_for_the_rest(built):
    """Materiality Engine comes from the Shroud-Forged situation, an origin for machine intelligences."""
    extraction, _, views = built
    engine = views.technology_hidden["tech_materiality_engine"]
    assert not engine & _bit(extraction, "machine")
    assert engine & _bit(extraction, "regular-machine-species")
    assert engine & _bit(extraction, "hive")


@pytest.mark.corpus
def test_an_event_that_excludes_machine_intelligences_hides_its_grant(built):
    """Enhanced Cryosleep Sedatives: ``OR = { is_machine_empire = no is_individual_machine = yes }``."""
    extraction, _, views = built
    sedatives = views.technology_hidden["tech_enhanced_cryosleep_sedatives"]
    assert sedatives & _bit(extraction, "machine")
    assert not sedatives & _bit(extraction, "regular-machine-species")


@pytest.mark.corpus
def test_a_route_no_empire_can_take_is_dropped(built):
    """Psionic Shields' tradition is in the pre-Shroud Psionics tree, which owning the DLC replaces."""
    extraction, _, _ = built
    kinds = {r.kind for r in extraction.unlock_routes["tech_psionic_shield"]}
    assert RouteKind.TRADITION not in kinds


def test_a_chosen_origin_and_civics_settle_those_choices():
    """The ordinary empire of :mod:`pipeline.starting`: an origin nothing names, and no civics."""
    defs = _definitions(
        civics="""
            origin_payback = { is_origin = yes }
            civic_eager_explorers = { }
            civic_hive_only = { potential = { authority = { value = auth_hive_mind } } }
        """,
        triggers="is_eager_explorer_empire = { has_civic = civic_eager_explorers }",
    )
    ordinary = Evaluator(Profile("regular"), defs, origin="", civics=frozenset())
    assert ordinary.trigger(parse("has_origin = origin_payback")) is TV.FALSE
    assert ordinary.trigger(parse("is_eager_explorer_empire = no")) is TV.TRUE
    chosen = Evaluator(Profile("regular"), defs, origin="origin_payback", civics=frozenset({"civic_eager_explorers"}))
    assert chosen.trigger(parse("has_origin = origin_payback is_eager_explorer_empire = yes")) is TV.TRUE
    # A civic the empire could never take stays impossible when chosen.
    assert Evaluator(Profile("regular"), defs, civics=frozenset({"civic_hive_only"})).civic("civic_hive_only") is TV.FALSE
    assert _trigger("is_primitive = no", Profile("regular")) is TV.TRUE


@pytest.mark.corpus
def test_what_an_ordinary_empire_starts_with_and_what_changes_it(built):
    from pipeline.starting import starts

    extraction, _, _ = built
    finder = routes_finder(extraction.unlock_routes, extraction.unlocks, extraction.unlock_config)
    profiles = {p.key: p for p in extraction.profiles}

    def start(key: str, profile: str):
        return starts(extraction[key], finder(key), profiles[profile], extraction.profile_definitions, extraction.unlocks)

    lab = start("tech_basic_science_lab_1", "regular")
    assert lab.ordinary
    assert {("origin", "origin_broken_shackles"), ("origin", "origin_payback"), ("civic", "civic_eager_explorers")} <= set(lab.exceptions)
    # Eager Explorers is not open to a bio-ship empire.
    assert ("civic", "civic_eager_explorers") not in start("tech_basic_science_lab_1", "regular-bio-ships").exceptions
    fruit = start("tech_critter_feeder", "regular")
    assert not fruit.ordinary and fruit.exceptions == [("origin", "origin_fruitful")]
    # Handed out at the start by an event only beastmasters see.
    assert start("tech_thrusters_bio_integration", "regular-beastmasters").ordinary
    assert start("tech_thrusters_bio_integration", "regular") is None
