"""Unlock routes: how a technology the research pool never offers reaches a player."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.clausewitz import parse
from pipeline.loadorder import LoadOrder, base_game_source, mod_source
from pipeline.records import build_record, extract
from pipeline.unlocks import (
    TAG_EVENT,
    TAG_START,
    Definition,
    Route,
    RouteKind,
    UnlockConfig,
    UnlockIndex,
    _scan,
    load_config,
    routes,
    tag_for,
)

#: Crisis levels count under their generic tag here: naming them needs
#: localisation, which these tests do not load.
TAG_COUNTS = {
    "Blokkat Bureau": 36,
    "Crisis Level": 14,
    "Observation Insight": 13,
    "Special Project": 13,
    "Debris": 13,
    "Covenant": 9,
    "Reality Code": 9,
    "Anomaly": 7,
    "Archaeology": 7,
    "Minor Artifact": 7,
    "Mutation Project": 7,
    "Situation": 7,
    "First Contact": 4,
    "Unobtainable": 4,
    "Astral Rift": 3,
    "Event": 3,
    "Enclave": 2,
    "Aeternum Bureau": 2,
    "Caravaneers": 2,
    "Combat": 1,
    "Flusion Operation": 1,
    "Megastructure": 1,
    "Paragon": 1,
    "Shroud": 1,
    "Starting": 1,
}


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


def _definition(source: str, effects: set[str] = frozenset()) -> Definition:
    definition = Definition()
    _scan(parse(source), definition, set(effects))
    return definition


def _index(**definitions: Definition) -> UnlockIndex:
    return UnlockIndex(
        definitions={tuple(name.split("__", 1)): d for name, d in definitions.items()}
    )


# --------------------------------------------------------------------------
# Unit: scanning
# --------------------------------------------------------------------------


def test_every_grant_effect_shape_is_read():
    definition = _definition(
        "add_research_option = a research_technology = b give_technology = { tech = c message = no }"
    )
    assert definition.grants == ["a", "b", "c"]


def test_a_grant_to_a_spawned_country_is_not_ours():
    """Fallen empire fragments are equipped inside ``create_country``."""
    definition = _definition("create_country = { effect = { give_technology = { tech = a } } }")
    assert definition.grants == []


def test_a_grant_to_every_other_country_is_not_ours():
    definition = _definition("every_playable_country = { add_research_option = a }")
    assert definition.grants == []


def test_random_list_is_a_dice_roll_not_a_scope():
    """Observation insights are handed out from inside a ``random_list``."""
    definition = _definition("random_list = { 1 = { add_research_option = a } }")
    assert definition.grants == ["a"]


def test_calls_are_recorded():
    definition = _definition(
        "country_event = { id = x.1 } enable_special_project = { name = PROJ } my_effect = yes",
        effects={"my_effect"},
    )
    assert definition.calls == [("event", "x.1"), ("special_project", "PROJ"), ("scripted_effects", "my_effect")]


# --------------------------------------------------------------------------
# Unit: classification
# --------------------------------------------------------------------------


def test_a_chain_rooted_in_a_perk_is_a_perk_route():
    """The Colossus Project, four hops from the grant."""
    index = _index(
        ascension_perks__ap_colossus=Definition(calls=[("event", "apoc.100")]),
        event__apoc_100=Definition(),
        **{"event__apoc.100": Definition(calls=[("special_project", "COLOSSUS")])},
        special_project__COLOSSUS=Definition(calls=[("event", "apoc.120")]),
        **{"event__apoc.120": Definition(grants=["tech_colossus"])},
    )
    found = routes("tech_colossus", index, UnlockConfig())
    assert [(r.kind, r.key) for r in found] == [(RouteKind.PERK, "ap_colossus")]


def test_a_chain_rooted_in_a_crisis_level_is_tagged_with_the_level():
    """The Cosmogenesis technologies, handed out as each level is reached."""
    index = _index(
        crisis_levels__crisis_cosmo_level_2=Definition(calls=[("scripted_effects", "perks_2")]),
        crisis_levels__crisis_cosmo_level_3=Definition(calls=[("scripted_effects", "perks_3")]),
        scripted_effects__perks_2=Definition(grants=["t"]),
        scripted_effects__perks_3=Definition(grants=["t"]),
    )
    found = routes("t", index, UnlockConfig())
    assert {(r.kind, r.key) for r in found} == {
        (RouteKind.CRISIS, "crisis_cosmo_level_2"),
        (RouteKind.CRISIS, "crisis_cosmo_level_3"),
    }
    names = {"crisis_cosmo_level_2": "Cosmogenesis Level 2", "crisis_cosmo_level_3": "Cosmogenesis Level 3"}
    record = _record("weight = 0", key="t")
    assert tag_for(record, found, UnlockConfig(), crisis_names=names) == "Cosmogenesis Level 2"
    assert tag_for(record, found, UnlockConfig()) == "Crisis Level"


def test_an_event_fired_on_research_is_a_research_route():
    index = _index(
        on_actions__on_tech_increased=Definition(calls=[("event", "ga.7400")]),
        **{"event__ga.7400": Definition(grants=["t"], trigger_technologies=("tech_mutations",))},
    )
    found = routes("t", index, UnlockConfig())
    assert [(r.kind, r.key) for r in found] == [(RouteKind.RESEARCH, "tech_mutations")]


def test_a_configured_route_takes_its_tag():
    index = _index(
        scripted_effects__insight=Definition(grants=["t"]),
        **{"event__obs.1": Definition(calls=[("scripted_effects", "insight")])},
    )
    config = UnlockConfig(route_tags=[("scripted_effects:insight", "Observation Insight")])
    assert [(r.kind, r.key) for r in routes("t", index, config)] == [
        (RouteKind.TAGGED, "Observation Insight")
    ]


def test_route_patterns_are_wildcards_and_the_earliest_rule_wins():
    index = _index(
        anomalies__DISTAR_CAT=Definition(calls=[("event", "distar.50")]),
        **{"event__distar.50": Definition(grants=["t"])},
    )
    config = UnlockConfig(route_tags=[("anomalies:*", "Anomaly"), ("event:distar.*", "Event Chain")])
    assert tag_for(_record("weight = 0", key="t"), routes("t", index, config), config) == "Anomaly"


def test_a_research_chain_is_a_research_route_whatever_it_passes_through():
    """Psionic Aura Intensification: offered on research, via a Shroud event."""
    index = _index(
        on_actions__on_tech_increased=Definition(calls=[("event", "shroud.1560")]),
        **{"event__shroud.1560": Definition(grants=["t"], trigger_technologies=("tech_aura",))},
    )
    config = UnlockConfig(route_tags=[("event:shroud.*", "Shroud")])
    assert [(r.kind, r.key) for r in routes("t", index, config)] == [(RouteKind.RESEARCH, "tech_aura")]


def test_a_value_naming_an_event_fires_it():
    """An anomaly's ``on_success = distar.50`` is a call like any other."""
    definition = Definition()
    _scan(parse("on_success = distar.50 stage = { event = distar.51 }"), definition, set(), {"distar.50", "distar.51"})
    assert definition.calls == [("event", "distar.50"), ("event", "distar.51")]


def test_add_tech_progress_is_a_grant():
    assert _definition("add_tech_progress = { tech = tech_secrets_baol progress = 0.1 }").grants == [
        "tech_secrets_baol"
    ]


def test_a_parameterised_effect_grants_at_the_call_site():
    """``add_tech_option_or_research_effect = { TECH = tech_neuroregeneration }``."""
    from pipeline.unlocks import _resolve_parameterised

    index = _index(
        scripted_effects__give=Definition(grants=["$TECH$"]),
        **{"event__bio.280": _definition("give = { TECH = tech_x }", effects={"give"})},
    )
    _resolve_parameterised(index)
    assert index.granted_by["tech_x"] == [("event", "bio.280")]
    assert "$TECH$" not in index.granted_by


def test_a_declared_parameter_grant_hands_out_the_parameter():
    """Astral rift rewards: the script only sets a flag, a later event grants."""
    from pipeline.unlocks import _resolve_parameterised

    caller = _definition('inline_script = { script = "rift/tech_option" TECH = tech_y }')
    index = _index(inline_scripts__rift=Definition(), **{"event__rift.1": caller})
    config = UnlockConfig(parameter_grants=[("inline_scripts:rift/tech_option", "TECH")])
    _resolve_parameterised(index, config)
    assert index.granted_by["tech_y"] == [("event", "rift.1")]


def test_no_route_and_a_component_prerequisite_is_debris():
    index = UnlockIndex(component_prerequisites={"t"})
    assert tag_for(_record("weight = 0", key="t"), (), UnlockConfig(), index) == "Debris"


def test_no_route_and_no_component_is_unknown_not_a_guess():
    from pipeline.unlocks import TAG_UNKNOWN

    assert tag_for(_record("weight = 0", key="t"), (), UnlockConfig(), UnlockIndex()) == TAG_UNKNOWN


def test_fallen_empire_roots_are_not_player_routes():
    index = _index(fallen_empires__fe=Definition(grants=["t"]))
    assert routes("t", index, UnlockConfig()) == ()


def test_on_actions_from_every_file_are_merged(tmp_path: Path):
    """On actions are additive; the last file must not replace the others."""
    from pipeline.loadorder import Source
    from pipeline.unlocks import build_index

    root = tmp_path / "game"
    (root / "common" / "on_actions").mkdir(parents=True)
    (root / "events").mkdir()
    (root / "common" / "on_actions" / "a.txt").write_text("on_tech_increased = { events = { ev.1 } }")
    (root / "common" / "on_actions" / "b.txt").write_text("on_tech_increased = { events = { ev.2 } }")
    (root / "events" / "e.txt").write_text(
        "country_event = { id = ev.1 trigger = { last_increased_tech = x } "
        "immediate = { add_research_option = t } }"
    )
    index = build_index(LoadOrder().add(Source(key="game", name="game", root=root)))
    assert [(r.kind, r.key) for r in routes("t", index, UnlockConfig())] == [(RouteKind.RESEARCH, "x")]


# --------------------------------------------------------------------------
# Unit: tags
# --------------------------------------------------------------------------


def test_a_drawable_technology_gets_no_tag():
    assert tag_for(_record("weight = 5"), (Route(RouteKind.EVENT, None, ()),), UnlockConfig()) is None


def test_a_starting_technology_is_tagged_starting():
    """Subspace Drive: never drawn, given at the start to Eager Explorers."""
    record = _record("weight = 0 start_tech = yes starting_potential = { is_eager_explorer_empire = yes }")
    assert tag_for(record, (), UnlockConfig()) == TAG_START


def test_a_research_unlock_takes_no_event_tag():
    """The Grand Archive bio-integration technologies."""
    found = (Route(RouteKind.RESEARCH, "x", ()), Route(RouteKind.START, None, ()))
    assert tag_for(_record("weight = 0"), found, UnlockConfig()) is None


def test_a_perk_route_takes_no_event_tag():
    found = (Route(RouteKind.PERK, "ap_colossus", ()), Route(RouteKind.EVENT, None, ()))
    assert tag_for(_record("weight = 0"), found, UnlockConfig()) is None


def test_an_event_only_technology_is_tagged_event():
    assert tag_for(_record("weight = 0"), (Route(RouteKind.EVENT, None, ()),), UnlockConfig()) == TAG_EVENT


def test_a_manual_override_wins_and_can_clear():
    record = _record("weight = 0", key="t")
    assert tag_for(record, (), UnlockConfig(technology_tags={"t": "Custom"})) == "Custom"
    assert tag_for(record, (), UnlockConfig(technology_tags={"t": ""})) is None


def test_the_shipped_config_loads():
    config = load_config()
    assert config.names["has_genetically_ascended"] == "Genetic Ascension"
    assert ("scripted_effects:add_observation_insight_effect", "Observation Insight") in config.route_tags
    assert config.debris_tag == "Debris"
    assert config.parameter_grants


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


def _tag(built, key: str) -> str | None:
    return tag_for(built[key], built.unlock_routes.get(key, ()), built.unlock_config, built.unlocks)


@pytest.mark.corpus
def test_tag_counts(built):
    from collections import Counter

    counts = Counter(_tag(built, key) for key in built.technologies)
    del counts[None]
    assert dict(counts) == TAG_COUNTS


@pytest.mark.corpus
def test_subspace_drive_is_a_starting_technology(built):
    assert _tag(built, "tech_subspace_drive") == TAG_START


@pytest.mark.corpus
def test_grand_archive_bio_integration_is_unlocked_by_research(built):
    for key in (
        "tech_thrusters_bio_integration",
        "tech_hyper_drive_bio_integration",
        "tech_sensors_bio_integration",
        "tech_combat_computers_bio_integration",
    ):
        found = built.unlock_routes[key]
        assert (RouteKind.RESEARCH, "tech_controlled_mutations") in {(r.kind, r.key) for r in found}, key
        assert _tag(built, key) is None, key


@pytest.mark.corpus
def test_observation_insights_are_tagged(built):
    """The thirteen from the wiki's Pre-FTL situations page."""
    insights = {
        "tech_alien_topography", "tech_atmospheric_orbital_mechanics", "tech_compact_living",
        "tech_lost_building_methods", "tech_new_numbers", "tech_ordered_retreat",
        "tech_predatory_tactics", "tech_satisfying_insults", "tech_supreme_alloy",
        "tech_temple_of_transportation", "tech_trinary_computing", "tech_unusual_senses",
        "tech_xeno_aesthetics",
    }
    tagged = {key for key in built.technologies if _tag(built, key) == "Observation Insight"}
    assert tagged == insights


@pytest.mark.corpus
def test_colossi_are_not_tagged_event(built):
    assert _tag(built, "tech_colossus") is None
    assert (RouteKind.PERK, "ap_colossus") in {(r.kind, r.key) for r in built.unlock_routes["tech_colossus"]}


@pytest.mark.corpus
def test_inferred_tags_for_representative_routes(built):
    """One technology per inference, checked against how the game hands it out."""
    expected = {
        "giga_tech_blokkat_laser": "Blokkat Bureau",
        "tech_cosmogenesis_crisis_3": "Crisis Level",
        "tech_voidworm_immunity": "Special Project",
        "tech_extradimensional_weapon_1": "Debris",
        "tech_secrets_baol": "Minor Artifact",
        "tech_unique_mutation_tiyanki": "Mutation Project",
        "tech_covenant_cradle": "Covenant",
        "tech_alloy_fossilization": "Anomaly",
        "giga_tech_blokkat_history": "Archaeology",
        "tech_psionic_barrier": "Astral Rift",
        "tech_xeno_linguistics": "First Contact",
        "tech_strike_craft_skrand": "Paragon",
        "tech_prescient_data_modeling": "Caravaneers",
        "null_void_beam": "Special Project",
        "tech_space_cloud_weapon_1": "Debris",
        "tech_frameworld_defensive_station_2": "Unobtainable",
    }
    assert {key: _tag(built, key) for key in expected} == expected


@pytest.mark.corpus
def test_null_void_beam_is_found_through_its_carrier_event(built):
    """A ``carrier_event``; missing that event kind hid over a thousand vanilla events."""
    assert ("event", "colony.3008") in built.unlocks.granted_by["null_void_beam"]


@pytest.mark.corpus
def test_research_unlocks_keep_no_tag_whatever_else_matches(built):
    assert _tag(built, "tech_aura_intensification") is None
    assert _tag(built, "tech_lgate_activation") is None
