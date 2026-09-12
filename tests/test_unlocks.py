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

TAG_COUNTS = {"Event": 158, "Observation Insight": 13, "Starting": 1}


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
    config = UnlockConfig(route_tags={"scripted_effects:insight": "Observation Insight"})
    assert [(r.kind, r.key) for r in routes("t", index, config)] == [
        (RouteKind.TAGGED, "Observation Insight")
    ]


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
    assert config.route_tags["scripted_effects:add_observation_insight_effect"] == "Observation Insight"


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


def _tag(built, key: str) -> str | None:
    return tag_for(built[key], built.unlock_routes.get(key, ()), built.unlock_config)


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
