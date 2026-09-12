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

## Running the viewer

The client only ever reads a prebuilt dataset, so build that first, then start
Vite:

```bash
.venv/Scripts/python tools/build_dataset.py --out client/public/data
npm --prefix client install
npm --prefix client run dev
```

The tree is then at <http://localhost:5173>. The dev server runs in the
foreground and dies with the terminal that started it, so if the page will not
load, check that the terminal is still alive before looking anywhere else.
`.claude/launch.json` pins port 5180 instead, for editor-driven previews.

`npm --prefix client run build` writes the deployable static site to
`client/dist`.

Controls: hover highlights a technology's ancestry and descendants, click pins
it and opens the detail panel, middle-click isolates it into a mini-tree,
<kbd>F</kbd> fits the tree, <kbd>Esc</kbd> clears. Touch equivalents: tap to
select, long-press to isolate.

## Mod sources

Mods are fetched from git and pinned to an exact commit rather than read from a
local Steam Workshop copy, which can be stale or sitting on a feature branch.
`tools/vendor_sync.py --check` reports when a pin has fallen behind its branch.

Only the directories the build reads are fetched, via a blobless sparse
checkout: Gigastructures is 1.9 GB, of which ~49 MB is relevant.

## Documentation

- `docs/KNOWN-CORPUS-DEFECTS.md` — third-party data problems the pipeline copes with
- `docs/crisis-rows.md` — why the Gigastructures crises get rows of their own

## Licence and attribution

Technology names, descriptions and icons are the property of Paradox Interactive
and the respective mod authors, and are used here to build a community reference
tool.
