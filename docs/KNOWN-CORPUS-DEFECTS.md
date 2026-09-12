# Known corpus defects

Problems in third-party data (base game or mods) that the pipeline must cope
with. These are *not* bugs in this project. Each entry records the evidence so a
future maintainer does not re-investigate it.

Re-verify after every game or mod update: a defect that gets fixed upstream and
stays on this list becomes a silent workaround for nothing.

## Verified 2026-09-11 — Stellaris 4.4.6 "Pegasus", Gigastructures 3.39.4

### 1. `common/HOW_TO_MAKE_NEW_SHIPS.txt` is not script

Prose documentation sitting in `common/`. Line 53 contains
`<gfx_culture>_<ship_size_name>_entity`, which lexes as comparison operators and
cannot parse.

**Handling:** excluded by name. See `NON_SCRIPT_FILENAMES` in
`pipeline/clausewitz/roundtrip.py`.

### 2. `common/scripted_loc/scripted_loc_ruloc.txt` is missing a closing brace

111 `{` against 110 `}`. The file ends at line 322 with
`default = GALACTIC_COMMUNITY_RULOC_PREP #Fallback` and never closes the
enclosing block. The engine evidently treats EOF as closing open blocks.

**Handling:** the parser stays strict by default, because a truncated file is
normally a real problem worth halting on. `parse(..., allow_unclosed_blocks=True)`
opts into the engine's lenient behaviour. The tech-tree pipeline never reads
`scripted_loc/`, so this file is only encountered by the whole-tree sweep.

### 3. Gigastructures: two repeatables share a template `name` argument

`giga_tech_repeatable_vanilla_dyson_cap` (`giga_07_repeatables_megastructures.txt:4`)
and `giga_tech_repeatable_dyson_swarm_cap` (`:665`) both pass `name = vanilla_dyson`
to `technology/giga_mega_repeatable`. Their generated `potential` flags
(`vanilla_dyson_disabled`, `vanilla_dyson_capped_r`) and `prereqfor_desc` loc keys
therefore collide.

**Handling:** upstream bug. Surface it in the validation report rather than
silently deduplicating. Not yet implemented.

### 4. Missing technology icons (Gigastructures only)

Technology icons resolve in two ways: from an explicit `icon = <stem>` field
when the technology declares one (15 do), and otherwise by convention from the
key, `gfx/interface/icons/technologies/<key>.dds`.

The `icon` field is how several technologies share one piece of art -- for
example `tech_robot_assembly_complex` points at `tech_mega_assembly`, and
`giga_tech_arkship_neutronium_harvester` at `giga_tech_neutronium_gigaforge`.
For those technologies it is the *only* art that exists, so a resolver that
honours only the key convention reports eleven technologies as missing when in
fact three are.

**No vanilla technology lacks an icon.** Every apparent vanilla gap is covered by
an explicit `icon` field. Every declared icon in the corpus resolves to a real
file.

Three Gigastructures technologies genuinely have neither:

| Key |
| --- |
| `giga_tech_planetary_matter_dumping` |
| `giga_tech_repeatable_dyson_swarm_cap` |
| `giga_tech_repeatable_observatory_cap` |

**One swap declares its own art and ships none:**
`giga_tech_ring_world_swap_no_habitables` (`zz_giga_tech_overwrites.txt:51`) sets
`inherit_icon = no` but no such `.dds` exists. Its sibling
`..._no_habitables_bio.dds` does.

**Handling:** implemented in `pipeline/icons.py`.

- An explicit `icon` field wins over the key convention. A declared icon that
  does not exist is recorded as its own fallback reason and then falls through
  to the convention, rather than being silently ignored.
- A swap that claims its own art but ships none falls back to the parent
  technology's *resolved* icon, so a renamed variant stays recognisable and
  inherits the parent's declared icon where there is one. This applies only when
  the parent resolved exactly; if the parent is itself missing art, the swap
  reports a placeholder rather than claiming a successful inheritance.
- Anything still unresolved falls back to vanilla's own placeholder,
  `technologies/unknown.dds`, which the game registers as
  `GFX_technology_unknown` in `interface/technology_view.gfx`.
- Fallbacks are recorded and deduplicated; `IconIndex.missing_icon_keys()` is
  the worklist. A silent placeholder is indistinguishable from a sparse checkout
  that fetched nothing, so it is never applied quietly.

The set above is pinned in `tests/test_icons.py`. If a fix upstream adds art,
delete the key from both places rather than loosening the assertion.

### 5. Localisation placeholders and gaps

Found while rendering the Aeternum crisis row, where one card displayed a raw
technology key.

**`giga_tech_aeternite_weaponry` has a placeholder entry.** Its localisation
value *is* its own key:

```
 giga_tech_aeternite_weaponry:0 "giga_tech_aeternite_weaponry"
```

This defeats any check that only asks whether the key exists, which is why
`Localisation.coverage()` reports `placeholder` as a category of its own. The
renderer shows the key, which is the honest outcome, but it is an upstream bug
worth reporting to Gigastructures.

**Eight technologies have no description**, seven of them vanilla:
`giga_tech_improbable_kaiser_moon`, `tech_adaptive_bureaucracy`,
`tech_combat_computers_1`, `tech_combat_computers_3`, `tech_living_state`,
`tech_planetary_unification`, `tech_repeatable_reduced_building_cost`,
`tech_repeatable_reduced_building_time`. The detail panel shows an empty
description rather than inventing one.

## Verified 2026-09-12 — Gigastructures 3.39.4 at 4f7d3a1

### 6. Frame World defensive station technologies cannot be obtained

`tech_frameworld_defensive_station_2` through `_5` have `weight = 0`, so the
research pool never offers them, and the only effects that grant them --
`giga_frameworld_origin.303` onward in `events/giga_210_origins_frameworld.txt`,
one per starbase tier via `last_increased_tech` -- are commented out in full.
Nothing else in the load order hands them out.

**Handling:** tagged "Unobtainable" by manual override in `config/unlocks.toml`.
If the events are restored upstream, delete the four overrides; the build will
then trace them as research unlocks on its own.
