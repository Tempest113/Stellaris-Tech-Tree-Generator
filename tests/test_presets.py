"""Gigastructures settings presets: the global flags each leaves, and what they hide."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import graph as graph_mod
from pipeline import layout as layout_mod
from pipeline import rows as rows_mod
from pipeline.clausewitz import parse
from pipeline.loadorder import LoadOrder, base_game_source, mod_source
from pipeline.presets import flags_after, flags_named, load_config
from pipeline.profiles import TV, Definitions, compute_views
from pipeline.records import extract
from pipeline.triggers import TriggerIndex
from pipeline.unlocks import routes_finder


def _effects(source: str) -> dict:
    return {p.key: p.value for p in parse(source).pairs()}


def test_a_preset_clears_then_sets_and_follows_its_conditions():
    effects = _effects(
        """
        reset = { remove_global_flag = warplanet_disabled remove_global_flag = old_mode }
        preset = {
            reset = yes
            set_global_flag = warplanet_disabled
            if = { limit = { NOT = { has_global_flag = giga_game_started } } set_global_flag = at_start }
            if = { limit = { has_global_flag = giga_game_started } set_global_flag = mid_game }
            else = { set_global_flag = otherwise }
            if = { limit = { is_multiplayer = yes } set_global_flag = multiplayer }
            random_list = { 1 = { set_global_flag = rolled } }
        }
        """
    )
    flags = flags_after("preset", effects, Definitions(triggers=TriggerIndex(definitions={})))
    assert flags["warplanet_disabled"] is TV.TRUE
    assert flags["old_mode"] is TV.FALSE
    assert flags["at_start"] is TV.TRUE
    assert "mid_game" not in flags or flags["mid_game"] is TV.FALSE
    assert flags["otherwise"] is TV.TRUE
    assert flags["multiplayer"] is TV.UNKNOWN
    assert flags["rolled"] is TV.UNKNOWN


def test_the_flags_an_effect_names_are_found_through_what_it_calls():
    effects = _effects(
        """
        reset_kaiser = { remove_global_flag = kaiser_off inner = yes }
        inner = { if = { limit = { always = yes } set_global_flag = kaiser_hard } }
        """
    )
    assert flags_named(["reset_kaiser"], effects) == {"kaiser_off", "kaiser_hard"}


def test_the_shipped_config_defaults_to_arcade():
    config = load_config()
    assert config.default == "arcade"
    assert [p.key for p in config.presets] == ["arcade", "giga-experience", "vanilla-plus", "non-default"]


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
    return extraction, views


def _gone_for_everyone(extraction, views, key: str, preset: str) -> bool:
    bits = [bit for bit, p in enumerate(extraction.profiles) if p.preset == preset]
    return all(views.technology_hidden[key] >> bit & 1 for bit in bits)


@pytest.mark.corpus
def test_each_preset_hides_what_its_flags_switch_off(built):
    extraction, views = built
    flags = extraction.profile_definitions.presets
    assert flags["vanilla-plus"]["warplanet_disabled"] is TV.TRUE
    assert flags["arcade"]["warplanet_disabled"] is TV.FALSE
    # Planetcraft is off in Vanilla Plus, and what needs it goes with it.
    assert _gone_for_everyone(extraction, views, "giga_tech_war_planet", "vanilla-plus")
    assert _gone_for_everyone(extraction, views, "giga_tech_maginot_planetcraft_upgrade", "vanilla-plus")
    assert not _gone_for_everyone(extraction, views, "giga_tech_war_planet", "arcade")
    # Megastructure materials are Giga-Experience's build caps only.
    assert _gone_for_everyone(extraction, views, "giga_tech_amb_supertensiles", "arcade")
    assert not _gone_for_everyone(extraction, views, "giga_tech_amb_supertensiles", "giga-experience")


@pytest.mark.corpus
def test_a_preset_settles_no_crisis_setting(built):
    """Crises are left to the player: the Kaiser's mode is open under every preset."""
    extraction, views = built
    for preset in ("arcade", "giga-experience", "vanilla-plus"):
        flags = extraction.profile_definitions.presets[preset]
        assert "katzenartig_disabled" not in flags
        assert "giga_blokkats_disabled" not in flags
        assert not _gone_for_everyone(extraction, views, "giga_tech_stellarite_kaiser_moon", preset)


@pytest.mark.corpus
def test_a_technology_only_custom_settings_enable_stays_in_the_tree(built):
    """No preset caps megastructures by research, but the Management Protocols exist for anyone who does."""
    extraction, views = built
    assert "giga_tech_repeatable_dyson_swarm_cap" not in extraction.disabled
    for preset in ("arcade", "giga-experience", "vanilla-plus"):
        assert _gone_for_everyone(extraction, views, "giga_tech_repeatable_dyson_swarm_cap", preset)
    # Settings changed by hand leave the flag open.
    assert not _gone_for_everyone(extraction, views, "giga_tech_repeatable_dyson_swarm_cap", "non-default")
    assert not _gone_for_everyone(extraction, views, "giga_tech_stellarite_kaiser_moon", "non-default")
