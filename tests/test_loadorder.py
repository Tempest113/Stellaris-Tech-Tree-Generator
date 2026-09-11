"""Load-order resolution against the real corpus.

The counts here are measured facts about Stellaris 4.4.6 and Gigastructures
3.39.4, not fixtures. If a game or mod update moves them, these tests are
supposed to fail: that is the signal to re-verify
``docs/KNOWN-CORPUS-DEFECTS.md`` and the plan's corpus numbers, not to relax the
assertion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.clausewitz import Block
from pipeline.loadorder import (
    LoadOrder,
    base_game_source,
    merge_keys,
    mod_source,
    read_descriptor,
    resolve_files,
)
from pipeline.steam import Install

VANILLA_TECH_COUNT = 679
GIGAS_TECH_COUNT = 301
#: Gigastructures replaces exactly these two vanilla technologies, and does so
#: through stage-2 key merge (a new ``zz_`` filename), not file replacement.
GIGAS_OVERRIDES = {"tech_mega_engineering", "tech_ring_world"}


@pytest.fixture
def vanilla(install: Install) -> LoadOrder:
    return LoadOrder().add(base_game_source(install.game, install.version))


@pytest.fixture
def vanilla_plus_gigas(install: Install, gigas_root: Path) -> LoadOrder:
    return (
        LoadOrder()
        .add(base_game_source(install.game, install.version))
        .add(mod_source(gigas_root))
    )


def _techs(load_order: LoadOrder):
    return merge_keys(resolve_files(load_order, "common/technology"))


# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_descriptor_parses(gigas_root: Path):
    descriptor = read_descriptor(gigas_root / "descriptor.mod")
    assert descriptor.remote_file_id == "1121692237"
    assert "Gigastructural" in descriptor.name
    assert descriptor.version is not None


@pytest.mark.corpus
def test_vanilla_technology_count(vanilla: LoadOrder):
    assert len(_techs(vanilla).blocks) == VANILLA_TECH_COUNT


@pytest.mark.corpus
def test_technology_scan_excludes_category_and_tier_subdirectories(vanilla: LoadOrder):
    """``common/technology/{category,tier}/`` hold a different kind of entry.

    Recursing into them silently inflates the technology count by 19, which is
    exactly the sort of quiet miscount that poisons every downstream number.
    """
    files = resolve_files(vanilla, "common/technology")
    assert all("/" not in f.relative for f in files)

    recursive = resolve_files(vanilla, "common/technology", recursive=True)
    assert len(merge_keys(recursive).blocks) > VANILLA_TECH_COUNT


@pytest.mark.corpus
def test_gigas_adds_expected_technologies(vanilla_plus_gigas: LoadOrder):
    merged = _techs(vanilla_plus_gigas)
    expected = VANILLA_TECH_COUNT + GIGAS_TECH_COUNT - len(GIGAS_OVERRIDES)
    assert len(merged.blocks) == expected


@pytest.mark.corpus
def test_gigas_overrides_exactly_two_vanilla_technologies(vanilla_plus_gigas: LoadOrder):
    merged = _techs(vanilla_plus_gigas)
    assert set(merged.overridden_keys()) == GIGAS_OVERRIDES


@pytest.mark.corpus
def test_override_is_whole_block_replacement_not_a_field_merge(
    vanilla_plus_gigas: LoadOrder,
):
    """Gigas' ``tech_ring_world`` adds three technology_swap blocks.

    Vanilla's definition has none, so seeing exactly three proves the later
    block replaced the earlier one rather than merging into it.
    """
    merged = _techs(vanilla_plus_gigas)
    ring_world = merged.blocks["tech_ring_world"]
    assert isinstance(ring_world, Block)
    assert len(ring_world.get_all("technology_swap")) == 3
    assert merged.origins["tech_ring_world"].source.key == "1121692237"


@pytest.mark.corpus
def test_inline_variables_declared_mid_file_are_captured(vanilla: LoadOrder):
    """Vanilla hides three ``@var`` declarations between technology blocks.

    ``00_fallen_empire_tech.txt:1`` is at the top, but ``00_soc_tech.txt:2740``
    and ``:3140`` are not; a header-only scan misses them.
    """
    merged = _techs(vanilla)
    assert merged.inline_variables == {
        "EnigmaticEngineeringDraw": "0.025",
        "tech_gene_tailoring_POINTS": "2",
        "tech_gene_expressions_POINTS": "1",
    }


@pytest.mark.corpus
def test_variable_declarations_are_not_mistaken_for_technologies(vanilla: LoadOrder):
    merged = _techs(vanilla)
    assert not any(key.startswith("@") for key in merged.blocks)


def test_file_replacement_is_distinct_from_key_merge(tmp_path: Path):
    """A mod shipping a vanilla *filename* replaces that file wholesale.

    This is a different mechanism from a mod declaring the same *key* in a new
    file, and conflating them gets real mods wrong. ``Oops! All Engineering!``
    rewrites 663 vanilla technologies purely through file replacement, so it
    must report as file replacements with **zero** key overrides.

    Built from synthetic sources rather than a real mod so the test depends on
    nothing outside the repository.
    """
    base = tmp_path / "base" / "common" / "technology"
    base.mkdir(parents=True)
    (base / "00_tech.txt").write_text(
        """
        tech_a = { area = physics }
        tech_b = { area = physics }
        """,
        encoding="utf-8",
    )

    # Same filename -> stage-1 replacement: tech_b disappears entirely.
    replacer = tmp_path / "replacer" / "common" / "technology"
    replacer.mkdir(parents=True)
    (replacer / "00_tech.txt").write_text(
        "tech_a = { area = engineering }", encoding="utf-8"
    )

    # Different filename -> stage-2 key merge: tech_a is overridden, tech_b survives.
    merger = tmp_path / "merger" / "common" / "technology"
    merger.mkdir(parents=True)
    (merger / "zz_override.txt").write_text(
        "tech_a = { area = society }", encoding="utf-8"
    )

    def stack(*names: str) -> LoadOrder:
        order = LoadOrder().add(base_game_source(tmp_path / "base"))
        for name in names:
            order.add(mod_source(tmp_path / name, key=name))
        return order

    replaced_files = resolve_files(stack("replacer"), "common/technology")
    replaced = merge_keys(replaced_files)
    assert len([f for f in replaced_files if f.shadowed]) == 1
    assert replaced.overridden_keys() == {}
    assert set(replaced.blocks) == {"tech_a"}, "tech_b must vanish with the file"
    assert replaced.blocks["tech_a"].scalar_text("area") == "engineering"

    merged_files = resolve_files(stack("merger"), "common/technology")
    merged = merge_keys(merged_files)
    assert [f for f in merged_files if f.shadowed] == []
    assert set(merged.overridden_keys()) == {"tech_a"}
    assert set(merged.blocks) == {"tech_a", "tech_b"}, "tech_b must survive a key merge"
    assert merged.blocks["tech_a"].scalar_text("area") == "society"


@pytest.mark.corpus
def test_load_order_is_deterministic(vanilla_plus_gigas: LoadOrder):
    first = [f.relative for f in resolve_files(vanilla_plus_gigas, "common/technology")]
    second = [f.relative for f in resolve_files(vanilla_plus_gigas, "common/technology")]
    assert first == second


@pytest.mark.corpus
def test_inline_scripts_need_recursive_resolution(vanilla_plus_gigas: LoadOrder):
    """Gigas keeps weight bonuses in ``inline_scripts/technology/tech_weight_boni/``."""
    flat = resolve_files(vanilla_plus_gigas, "common/inline_scripts")
    deep = resolve_files(vanilla_plus_gigas, "common/inline_scripts", recursive=True)
    assert len(deep) > len(flat)
    assert any("tech_weight_boni/" in f.relative for f in deep)
