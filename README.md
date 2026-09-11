# Stellaris Tech Tree

Generates an interactive technology tree from a Stellaris installation and a
chosen mod load order, and publishes it as a static site.

The published build targets **Gigastructural Engineering & More**, but nothing
about Gigastructures is hard-coded: the pipeline is driven entirely by
`config/build.toml`, so pointing it at a different load order produces a
different tree.

## How it works

Three stages, strictly separated. The browser never parses Clausewitz script and
never computes geometry.

| Stage | Runs | Produces |
| --- | --- | --- |
| Extract | Python, locally | Canonical expanded technology records, localisation, icons |
| Compute | Python, locally | Graph, empire profiles, gates, layout, geometry |
| Render | TypeScript, browser | The page |

Vanilla game data cannot be redistributed, so CI cannot build the dataset. The
dataset is built locally, published as a release artifact, and deployed from
there. See `docs/` for the details.

## Getting started

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"    # Windows
python tools/vendor_sync.py                        # fetch pinned mod sources
.venv/Scripts/python -m pytest
```

Stellaris is located automatically through Steam. Override with `STELLARIS_PATH`
if it lives somewhere unusual.

## Mod sources

Mods are fetched from git and pinned to an exact commit rather than read from a
local Steam Workshop copy, which can be stale or sitting on a feature branch.
`tools/vendor_sync.py --check` reports when a pin has fallen behind its branch.

Only the directories the build reads are fetched, via a blobless sparse
checkout: Gigastructures is 1.9 GB, of which ~49 MB is relevant.

## Documentation

- `docs/KNOWN-CORPUS-DEFECTS.md` — third-party data problems the pipeline copes with

## Licence and attribution

Technology names, descriptions and icons are the property of Paradox Interactive
and the respective mod authors, and are used here to build a community reference
tool.
