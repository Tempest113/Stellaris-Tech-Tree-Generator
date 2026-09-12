"""The canonical technology record.

Corpus counts here are measured facts about Stellaris 4.4.6 plus
Gigastructures at the pinned commit. They are absolute on purpose: a test that
merely asserts internal consistency would have passed for every one of the
silent bugs this module exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.clausewitz import parse
from pipeline.loadorder import LoadOrder, base_game_source, mod_source
from pipeline.records import (
    AREAS,
    PrerequisiteGroup,
    RecordError,
    build_record,
    extract,
)

TECHNOLOGY_COUNT = 978
REPEATABLE_COUNT = 88
#: Of the 88 repeatables, 76 are unlimited. Worth stating explicitly: 76 is the
#: number the previous attempt reported as its *total* repeatable count, because
#: it tested ``levels < 0`` instead of "declares levels".
UNLIMITED_REPEATABLE_COUNT = 76

#: Swaps that state an area/category of their own...
SWAPS_DECLARING_PLACEMENT = 13
#: ...of which only these actually move the technology somewhere new.
RELOCATING_SWAPS = 9
RELOCATING_TECHNOLOGIES = 8

#: Gigastructures' ACOT compatibility chain names four technologies that exist
#: only when ACOT is loaded. Expected, not a defect.
DANGLING_PREREQUISITES = {
    "giga_tech_amb_supertensiles_acot_alpha": ["tech_dark_matter_power_core_ae"],
    "giga_tech_amb_supertensiles_acot_delta": ["tech_dark_matter_power_core_dm"],
    "giga_tech_amb_supertensiles_acot_phanon": ["tech_civil_phanon_application"],
    "giga_tech_amb_supertensiles_acot_sigma": ["tech_dark_matter_power_core_se"],
}


@pytest.fixture(scope="module")
def records(install, gigas_root: Path):
    load_order = (
        LoadOrder()
        .add(base_game_source(install.game, install.version))
        .add(mod_source(gigas_root, key="gigas"))
    )
    return extract(load_order)


def _record(source: str, key: str = "t"):
    return build_record(key, parse(source).get_first(key))


# --------------------------------------------------------------------------
# Unit: field parsing
# --------------------------------------------------------------------------


def test_missing_area_fails_loudly():
    """Almost always means expansion did not run. A default would bury that."""
    with pytest.raises(RecordError, match="area"):
        _record("t = { tier = 1 }")


def test_missing_tier_fails_loudly():
    with pytest.raises(RecordError, match="tier"):
        _record("t = { area = physics }")


def test_unresolved_tier_variable_fails_rather_than_defaulting():
    with pytest.raises(RecordError, match="tier"):
        _record("t = { area = physics tier = @never_declared }")


def test_unknown_area_fails():
    with pytest.raises(RecordError, match="unknown area"):
        _record("t = { area = diplomacy tier = 1 }")


def test_is_rare_is_tri_state():
    """228 vanilla technologies say yes, 9 say no, the rest say nothing."""
    assert _record("t = { area = physics tier = 1 is_rare = yes }").is_rare is True
    assert _record("t = { area = physics tier = 1 is_rare = no }").is_rare is False
    assert _record("t = { area = physics tier = 1 }").is_rare is None


def test_repeatable_membership_is_declaring_levels():
    capped = _record("t = { area = physics tier = 5 levels = 5 }")
    unlimited = _record("t = { area = physics tier = 5 levels = -1 }")
    plain = _record("t = { area = physics tier = 5 }")

    assert capped.is_repeatable and not capped.is_unlimited and capped.level_cap == 5
    assert unlimited.is_repeatable and unlimited.is_unlimited
    assert unlimited.level_cap is None
    assert not plain.is_repeatable


def test_cost_accepts_both_shapes_and_absence():
    """Ten vanilla technologies write cost as a block; five omit it entirely."""
    assert _record("t = { area = physics tier = 1 cost = 100 }").cost == 100
    assert _record("t = { area = physics tier = 1 cost = { factor = 250 } }").cost == 250
    assert _record("t = { area = physics tier = 1 }").cost is None


def test_repeated_blocks_are_all_kept():
    """A template-emitted block plus one written beside the call."""
    record = _record(
        "t = { area = physics tier = 1"
        "      weight_modifier = { factor = 0 }"
        "      weight_modifier = { factor = 2 }"
        "      modifier = { a = 1 } modifier = { b = 2 } }"
    )
    assert len(record.weight_modifiers) == 2
    assert len(record.modifiers) == 2


def test_weightless_is_distinct_from_absent_weight():
    """weight = 0 means 'never drawn', which is not the same as unstated."""
    assert _record("t = { area = physics tier = 1 weight = 0 }").is_weightless
    assert not _record("t = { area = physics tier = 1 weight = 10 }").is_weightless
    assert not _record("t = { area = physics tier = 1 }").is_weightless


# --------------------------------------------------------------------------
# Unit: prerequisites
# --------------------------------------------------------------------------


def test_prerequisites_preserve_and_or_structure():
    record = _record(
        "t = { area = physics tier = 1 prerequisites = {"
        "  OR = { tech_a tech_b } tech_c OR = { tech_d tech_e } } }"
    )
    assert record.prerequisites == (
        PrerequisiteGroup(("tech_a", "tech_b")),
        PrerequisiteGroup(("tech_c",)),
        PrerequisiteGroup(("tech_d", "tech_e")),
    )
    assert [g.is_choice for g in record.prerequisites] == [True, False, True]


def test_prerequisite_keys_flattens_but_groups_stay_available():
    """Flattening for reachability is fine; flattening for layout is not.

    Collapsing alternatives into the required list is what silently corrupted
    layout, faction derivation and rendering scope in the previous attempt,
    because all three shared one helper.
    """
    record = _record(
        "t = { area = physics tier = 1 prerequisites = { tech_a OR = { tech_b tech_c } } }"
    )
    assert record.prerequisite_keys == ("tech_a", "tech_b", "tech_c")
    assert len(record.prerequisites) == 2


def test_quoted_prerequisites_are_unquoted():
    record = _record(
        'tech_modular_engineering = { area = engineering tier = 2 '
        'prerequisites = { OR = { "tech_starbase_3" "tech_waystation_2" } } }',
        key="tech_modular_engineering",
    )
    assert record.prerequisites[0].options == ("tech_starbase_3", "tech_waystation_2")


def test_empty_prerequisites_block_yields_no_groups():
    assert _record("t = { area = physics tier = 1 prerequisites = { } }").prerequisites == ()


# --------------------------------------------------------------------------
# Unit: swaps and placement
# --------------------------------------------------------------------------


def test_swap_inherit_flags_default_to_inheriting():
    record = _record(
        "t = { area = physics tier = 1 technology_swap = { name = t_alt trigger = { x = yes } } }"
    )
    swap = record.swaps[0]
    assert swap.inherit_icon is True and swap.inherit_effects is True


def test_swap_declaring_the_parents_own_placement_does_not_relocate():
    """Four bio psionic swaps restate society/psionics, where they already are.

    Counting these as relocations would create union layout slots for
    technologies that never move.
    """
    record = _record(
        "t = { area = society tier = 3 category = { psionics }"
        "      technology_swap = { name = t_bio area = society category = { psionics } } }"
    )
    assert record.swaps[0].declares_placement is True
    assert record.relocating_swaps == ()
    assert record.placements == (("society", ("psionics",)),)


def test_swap_changing_area_relocates():
    record = _record(
        "t = { area = engineering tier = 4 category = { voidcraft }"
        "      technology_swap = { name = t_bio area = society category = { biology } } }"
    )
    assert len(record.relocating_swaps) == 1
    assert record.placements == (
        ("engineering", ("voidcraft",)),
        ("society", ("biology",)),
    )


def test_swap_omitting_a_field_inherits_it():
    record = _record(
        "t = { area = engineering tier = 4 category = { voidcraft }"
        "      technology_swap = { name = t_alt category = { industry } } }"
    )
    assert record.swap_placement(record.swaps[0]) == ("engineering", ("industry",))


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_extraction_is_clean(records):
    assert records.problems == []


@pytest.mark.corpus
def test_technology_count(records):
    assert len(records) == TECHNOLOGY_COUNT


@pytest.mark.corpus
def test_every_technology_has_a_resolved_tier_and_known_area(records):
    assert all(isinstance(r.tier, int) for r in records)
    assert {r.area for r in records} <= set(AREAS)


@pytest.mark.corpus
def test_tier_range_reaches_nine(records):
    """Vanilla stops at 5; Gigastructures' ACOT chain runs 6 through 9."""
    tiers = {r.tier for r in records}
    assert min(tiers) == 0
    assert max(tiers) == 9
    for tier in (6, 7, 8, 9):
        assert sum(1 for r in records if r.tier == tier) == 1


@pytest.mark.corpus
def test_repeatable_counts(records):
    assert len(records.repeatables) == REPEATABLE_COUNT
    unlimited = [r for r in records.repeatables if r.is_unlimited]
    assert len(unlimited) == UNLIMITED_REPEATABLE_COUNT
    assert len(unlimited) != REPEATABLE_COUNT, "the sign test must stay visibly wrong"


@pytest.mark.corpus
def test_finite_repeatable_caps(records):
    assert {r.level_cap for r in records.repeatables if r.level_cap} == {5, 20, 40}


@pytest.mark.corpus
def test_categories_are_always_single(records):
    """The schema allows a list, but nothing in the corpus uses more than one."""
    assert all(len(r.categories) <= 1 for r in records)


@pytest.mark.corpus
def test_dangling_prerequisites_are_exactly_the_acot_chain(records):
    assert records.dangling_prerequisites() == DANGLING_PREREQUISITES


@pytest.mark.corpus
def test_relocating_swaps_are_distinguished_from_declared_placement(records):
    declared = sum(1 for r in records for s in r.swaps if s.declares_placement)
    moving = sum(len(r.relocating_swaps) for r in records)
    assert declared == SWAPS_DECLARING_PLACEMENT
    assert moving == RELOCATING_SWAPS
    assert sum(1 for r in records if r.relocating_swaps) == RELOCATING_TECHNOLOGIES


@pytest.mark.corpus
def test_layout_slot_count(records):
    """One slot per placement. Exactly one is visible for any given empire."""
    slots = sum(len(r.placements) for r in records)
    assert slots == TECHNOLOGY_COUNT + RELOCATING_SWAPS


@pytest.mark.corpus
def test_bio_shipset_moves_hulls_into_society_biology(records):
    """The relocation story is coherent: bio-ship empires research hulls as biology."""
    titans = records["tech_titans"]
    assert titans.placement == ("engineering", ("voidcraft",))
    assert ("society", ("biology",)) in titans.placements


@pytest.mark.corpus
def test_ring_world_has_two_alternate_placements(records):
    assert len(records["tech_ring_world"].placements) == 3


@pytest.mark.corpus
def test_gigas_override_won_and_is_whole_block(records):
    mega = records["tech_mega_engineering"]
    assert mega.origin.source.key == "gigas"
    assert mega.origin.overridden, "vanilla's definition should be recorded as overridden"
    assert len(mega.prerequisites) == 3
    assert [g.is_choice for g in mega.prerequisites] == [True, False, True]


@pytest.mark.corpus
def test_canary_record_is_fully_materialised(records):
    """Entirely template-emitted; nothing here exists before expansion."""
    canary = records["giga_tech_repeatable_vanilla_dyson_cap"]
    assert canary.area == "physics"
    assert canary.tier == 5
    assert canary.is_repeatable and canary.is_unlimited
    assert canary.cost == 96000
    assert canary.cost_per_level == 288000
    assert canary.potential is not None
    assert [g.only for g in canary.prerequisites] == ["tech_dyson_sphere"]


@pytest.mark.corpus
def test_signal_counts_for_rendering(records):
    """is_rare is near-useless in a Gigas build; is_dangerous and weight=0 are not."""
    rare = sum(1 for r in records if r.is_rare)
    assert sum(1 for r in records if r.is_dangerous) == 62
    assert sum(1 for r in records if r.is_weightless) == 185
    assert rare > len(records) * 0.4


@pytest.mark.corpus
def test_every_record_resolves_an_icon(records):
    assert all(not r.icon(records.icons).is_missing for r in records)
