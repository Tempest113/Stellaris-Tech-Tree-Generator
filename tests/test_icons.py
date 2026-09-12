"""Icon resolution and its fallback chain."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import icons as icons_mod
from pipeline.icons import Fallback, IconIndex, PLACEHOLDER_STEM

#: Technologies with neither a ``<key>.dds`` nor a usable declared ``icon``.
#: All three are Gigastructures; no vanilla technology lacks an icon.
MISSING_ICON_KEYS = {
    "giga_tech_planetary_matter_dumping",
    "giga_tech_repeatable_dyson_swarm_cap",
    "giga_tech_repeatable_observatory_cap",
}

#: Technologies that point at another technology's art via ``icon =``. Sampled,
#: not exhaustive; 15 technologies use the field.
DECLARED_ICONS = {
    "giga_tech_arkship_neutronium_harvester": "giga_tech_neutronium_gigaforge",
    "tech_robot_assembly_complex": "tech_mega_assembly",
    "tech_mine_exotic_gases": "tech_exotic_gases",
    "tech_archeology_lab_ancrel": "tech_archeology_lab",
}

#: The one swap that declares its own art and ships none.
SWAP_WITHOUT_ICON = ("tech_ring_world", "giga_tech_ring_world_swap_no_habitables")


def _index(**technologies: str) -> IconIndex:
    index = IconIndex()
    index.technologies.update({k: Path(f"{k}.dds") for k in technologies})
    return index


# --------------------------------------------------------------------------
# Unit
# --------------------------------------------------------------------------


def test_technology_icon_is_found_by_key_convention():
    index = _index(tech_lasers_1="")
    ref = index.technology("tech_lasers_1")
    assert ref.is_exact and ref.stem == "tech_lasers_1"
    assert index.fallbacks == []


def test_declared_icon_wins_over_the_key_convention():
    """For these technologies the declared icon is the only art that exists."""
    index = _index(tech_mega_assembly="", unknown="")
    ref = index.technology("tech_robot_assembly_complex", "tech_mega_assembly")
    assert ref.is_exact
    assert ref.stem == "tech_mega_assembly"
    assert index.fallbacks == []


def test_declared_icon_is_preferred_even_when_a_key_named_file_exists():
    index = _index(tech_a="", tech_b="")
    assert index.technology("tech_a", "tech_b").stem == "tech_b"


def test_declared_icon_that_does_not_exist_is_reported_then_falls_through():
    """A broken icon reference is a data error, but the convention may still work."""
    index = _index(tech_a="", unknown="")
    ref = index.technology("tech_a", "tech_nonexistent")
    assert ref.stem == "tech_a"
    assert [r.fallback for r in index.fallbacks] == [Fallback.DECLARED_ICON_MISSING]


def test_missing_icon_falls_back_to_the_vanilla_placeholder():
    index = _index(unknown="")
    ref = index.technology("tech_absent")
    assert ref.fallback is Fallback.PLACEHOLDER
    assert ref.stem == PLACEHOLDER_STEM
    assert not ref.is_missing


def test_missing_icon_with_no_placeholder_is_reported_not_invented():
    index = _index()
    ref = index.technology("tech_absent")
    assert ref.fallback is Fallback.NONE_AVAILABLE
    assert ref.is_missing


def test_swap_with_inherit_icon_uses_the_parent_and_is_not_a_fallback():
    """The common case: ~93 bio-shipset swaps inherit rather than ship art."""
    index = _index(tech_titans="")
    ref = index.swap("tech_titans", "tech_biogenesis_titans", inherit_icon=True)
    assert ref.stem == "tech_titans"
    assert ref.is_exact
    assert index.fallbacks == []


def test_inheriting_swap_picks_up_the_parents_declared_icon():
    """Inheritance must follow the parent's resolution, not just its key."""
    index = _index(tech_mega_assembly="", unknown="")
    ref = index.swap(
        "tech_robot_assembly_complex",
        "swap_name",
        inherit_icon=True,
        declared_icon="tech_mega_assembly",
    )
    assert ref.is_exact and ref.stem == "tech_mega_assembly"


def test_swap_with_own_icon_uses_it():
    index = _index(tech_lasers_1="", tech_bio_lasers_1="")
    ref = index.swap("tech_lasers_1", "tech_bio_lasers_1", inherit_icon=False)
    assert ref.is_exact and ref.stem == "tech_bio_lasers_1"


def test_swap_claiming_own_icon_without_shipping_it_falls_back_to_parent():
    """Preferred over the placeholder: a renamed variant should stay recognisable."""
    index = _index(tech_ring_world="", unknown="")
    ref = index.swap(*SWAP_WITHOUT_ICON, inherit_icon=False)
    assert ref.fallback is Fallback.SWAP_TO_PARENT
    assert ref.stem == "tech_ring_world"


def test_swap_fallback_to_parent_honours_the_parents_declared_icon():
    index = _index(tech_mega_assembly="", unknown="")
    ref = index.swap(
        "tech_robot_assembly_complex",
        "swap_without_art",
        inherit_icon=False,
        declared_icon="tech_mega_assembly",
    )
    assert ref.fallback is Fallback.SWAP_TO_PARENT
    assert ref.stem == "tech_mega_assembly"


def test_swap_falls_through_to_placeholder_when_parent_is_also_missing():
    index = _index(unknown="")
    ref = index.swap("tech_absent", "swap_absent", inherit_icon=False)
    assert ref.fallback is Fallback.PLACEHOLDER


def test_fallbacks_are_deduplicated():
    """This list is an upstream-bug worklist, not a call log.

    A technology whose swap inherits its icon resolves the parent twice, and
    must still be reported once.
    """
    index = _index(unknown="")
    index.technology("tech_absent")
    index.swap("tech_absent", "swap_of_absent", inherit_icon=True)
    index.technology("tech_absent")
    assert len(index.fallbacks) == 1
    assert index.missing_icon_keys() == ["tech_absent"]


def test_distinct_fallback_reasons_are_reported_separately():
    index = _index(tech_parent="", unknown="")
    index.technology("tech_absent")
    index.swap("tech_parent", "swap_absent", inherit_icon=False)
    assert {ref.fallback for ref in index.fallbacks} == {
        Fallback.PLACEHOLDER,
        Fallback.SWAP_TO_PARENT,
    }


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def resolved(install, gigas_root: Path):
    """Every technology and swap in the published load order, icon-resolved."""
    from pipeline.inline_scripts import Expander
    from pipeline.inline_scripts import build_index as script_index
    from pipeline.loadorder import (
        LoadOrder,
        base_game_source,
        merge_keys,
        mod_source,
        resolve_files,
    )
    from pipeline.variables import collect_variables

    load_order = (
        LoadOrder()
        .add(base_game_source(install.game, install.version))
        .add(mod_source(gigas_root, key="gigas"))
    )
    raw = merge_keys(resolve_files(load_order, "common/technology"))
    variables = collect_variables(load_order, extra=raw.inline_variables)
    expander = Expander(script_index(load_order))
    technologies = {
        k: variables.substitute(expander.expand(b)) for k, b in raw.blocks.items()
    }

    index = icons_mod.build_index(load_order)
    for key, block in sorted(technologies.items()):
        declared = block.scalar_text("icon")
        index.technology(key, declared)
        for swap in block.get_all("technology_swap"):
            name = swap.scalar_text("name")
            if name:
                inherit = (swap.scalar_text("inherit_icon") or "yes").lower() != "no"
                index.swap(key, name, inherit_icon=inherit, declared_icon=declared)
    return index, technologies


@pytest.mark.corpus
def test_placeholder_exists_in_the_base_game(resolved):
    index, _ = resolved
    assert index.placeholder is not None
    assert index.placeholder.name == "unknown.dds"


@pytest.mark.corpus
def test_ascension_perk_icons_come_from_both_sources(resolved):
    index, _ = resolved
    assert len(index.ascension_perks) > 55, "expected vanilla's 53 plus Gigas' additions"


@pytest.mark.corpus
def test_missing_icons_match_the_recorded_set(resolved):
    """Pinned so an upstream fix, or a new gap, shows up as a test failure.

    If this fails because a mod added art, delete the key here and from
    docs/KNOWN-CORPUS-DEFECTS.md rather than loosening the assertion.
    """
    index, _ = resolved
    assert set(index.missing_icon_keys()) == MISSING_ICON_KEYS


@pytest.mark.corpus
def test_only_one_swap_claims_art_it_does_not_ship(resolved):
    index, _ = resolved
    swap_fallbacks = [r for r in index.fallbacks if r.fallback is Fallback.SWAP_TO_PARENT]
    assert [r.requested for r in swap_fallbacks] == [SWAP_WITHOUT_ICON[1]]


@pytest.mark.corpus
def test_every_technology_resolves_to_a_real_file(resolved):
    """The point of the fallback chain: no technology renders a hole."""
    index, technologies = resolved
    unresolved = [
        key
        for key, block in technologies.items()
        if index.technology(key, block.scalar_text("icon")).is_missing
    ]
    assert unresolved == []


@pytest.mark.corpus
def test_no_vanilla_technology_lacks_an_icon(resolved):
    """Every vanilla gap is covered by an explicit ``icon`` field."""
    index, _ = resolved
    assert [k for k in index.missing_icon_keys() if not k.startswith("giga_")] == []


@pytest.mark.corpus
def test_declared_icons_all_resolve_to_real_files(resolved):
    """No technology points at art that does not exist."""
    index, _ = resolved
    broken = [
        ref.requested
        for ref in index.fallbacks
        if ref.fallback is Fallback.DECLARED_ICON_MISSING
    ]
    assert broken == []


@pytest.mark.corpus
@pytest.mark.parametrize("tech_key,expected", sorted(DECLARED_ICONS.items()))
def test_known_declared_icons_are_honoured(resolved, tech_key: str, expected: str):
    index, technologies = resolved
    ref = index.technology(tech_key, technologies[tech_key].scalar_text("icon"))
    assert ref.stem == expected
    assert ref.is_exact


@pytest.mark.corpus
def test_resolved_icons_load_as_images(resolved):
    index, technologies = resolved
    sample = sorted(technologies)[:: max(1, len(technologies) // 25)]
    for key in sample:
        image = icons_mod.load_image(index.technology(key).path)
        assert image.mode == "RGBA"
        assert image.size[0] > 0
