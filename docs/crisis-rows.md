# Crisis rows

Gigastructures' crises each have a self-contained research chain, so they get
their own row rather than being scattered across the three research areas.
Membership is declared in `config/rows.toml`; `build/crisis-rows.md` is
regenerated on every build and lists what landed where and why.

## Where the names come from

Gigastructures names its own crises in the `giga_category_*` localisation keys
it uses for situation-log headings:

| Key | Name |
| --- | --- |
| `giga_category_katzens` | Katzenartig Imperium |
| `giga_category_sirens` | Sirenalia |
| `giga_category_aeternum` | Aeternum |
| `giga_category_blokkats` | The Blokkats |
| `giga_category_compound` | The Compound |
| `giga_category_ehof` | E.H.O.F. |

## Current assignment

| Row | Technologies | Confidence |
| --- | --- | --- |
| Katzenartig Imperium | 6 | High |
| Sirenalia | 14 | High |
| Aeternum | 3 | High |
| The Blokkats | 42 | High |
| The Compound | 15 | Reviewed, kept as is |

The four high-confidence rows come straight from source files: Gigastructures
keeps each chain in its own file (`giga_10_katzen.txt`, `giga_18_eawaf.txt` —
EAWAF is its internal name for Sirenalia — `giga_11_aeternum.txt`,
`giga_13_blokkat.txt`). `tech_katzen_atomic_munitions` is the one exception,
living in the shared special-project file, so it is listed explicitly.

Blokkats is the one group that already spanned all three research areas, so a
single crisis row is strictly more honest than the three category rows it
replaces.

## Decided: The Compound and E.H.O.F.

**Reviewed 2026-09-13 and left as is (option 1 below).** Nothing in the
technology data settles it, so this records the call and the reasoning.

The Compound is currently defined as everything reachable from
`tech_qnm_utilities`, which is weight 0 and therefore event-granted. That gives
15 technologies: the root plus the negative mass weapon set and eight
`tech_sm_*` Sentient Metal weapons.

The problem is that Gigastructures treats **The Compound and E.H.O.F. as two
separate event chains** — `compound_crisis_chain`, `compound_solution_chain` and
`ehof_system_list` all appear in `giga_event_chains.txt` with different
`situation_log_category` values — yet their technologies share two files,
`giga_08_ehof_components.txt` and `giga_09_ehof_other.txt`.

The result is a split through what looks like one family:

- Eight `tech_sm_*` weapons hang off `tech_qnm_utilities` and land in The Compound.
- Five more `tech_sm_*` and five `tech_qnm_*` component technologies gate on
  `tech_ehof_sentient_tier_N` instead, and stay in ordinary category rows.

So `tech_sm_mass_drivers` sits in a crisis row while `tech_sm_armor` does not,
which is hard to defend from a player's point of view.

**Three options:**

1. **Leave as is.** The Compound row means "things gated behind the Compound
   event", which is defensible and matches how the row was described.
2. **Add an E.H.O.F. row** for the other 36, using Gigastructures' own
   `giga_category_ehof` name. The two families stay separate but both become
   readable. E.H.O.F. is a player project rather than a threat, so it does not
   meet the "what do I need to not die" purpose the crisis rows exist for — but
   it is a 50-technology self-contained chain either way.
3. **Merge them** into one Compound/E.H.O.F. row of ~51 technologies.

The 36 technologies left in category rows are listed under "Needs review" in
`build/crisis-rows.md`.

## Adding or correcting a row

Edit `config/rows.toml`. Rules are tried strongest first — explicit `keys`, then
`source_files`, then `categories`, then `key_prefixes`, then `reachable_from` —
and a technology joins the first crisis that matches, so ordering resolves
overlaps.

Prefer `keys` and `source_files`. `reachable_from` follows real graph edges and
can wander somewhere unintended, which is exactly what happened with The
Compound.

The build fails if a rule names a technology or file that does not exist, so a
stale rule left behind by a mod update stops the build rather than silently
placing nothing.
