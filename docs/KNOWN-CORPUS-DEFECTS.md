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

### 4. Gigastructures: a declared swap icon does not exist

`giga_tech_ring_world_swap_no_habitables` (`zz_giga_tech_overwrites.txt:51`)
declares `inherit_icon = no` but ships no
`giga_tech_ring_world_swap_no_habitables.dds` in either base or Gigas. Its
sibling `..._no_habitables_bio.dds` does exist.

**Handling:** icon resolution must fall back to the parent tech's icon and warn.
Not yet implemented.
