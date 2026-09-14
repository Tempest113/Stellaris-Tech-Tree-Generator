# Stellaris Tech Tree

Generates an interactive technology tree from a Stellaris installation and a
chosen mod load order, and publishes it as a static site.

The published build targets **Gigastructural Engineering & More**, but nothing
about Gigastructures is hard-coded: the pipeline is driven entirely by
`config/build.toml` and the crisis row, unlock and settings preset files it
names, so pointing it at a different load order produces a different tree.
Cloning the repository and building as-is gives the Gigastructures tree.

## How it works

Three stages, strictly separated. The browser never parses Clausewitz script or
reads a trigger: everything it knows about the game, including what each empire
profile sees, is decided in Python. It only turns placement into pixels, because
a profile hides cards and the rest close up around the gaps.

| Stage | Runs | Produces |
| --- | --- | --- |
| Extract | Python, locally | Canonical expanded technology records, localisation, icons |
| Compute | Python, locally | Graph, ascension perk gates, empire profiles under each Gigastructures settings preset, layout |
| Render | TypeScript, browser | Geometry for the chosen profile, and the page |

Vanilla game data cannot be put in a public build machine, so CI cannot build
the dataset. It is built locally, and the site is published from there. See
`docs/usage-guide.md` for updating and publishing the page.

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

To try the tree on a phone, run `npm --prefix client run dev:phone` and open
the Network address it prints, on the same Wi-Fi. It serves over HTTPS with a
self-signed certificate the phone asks you to accept once: phone browsers that
force HTTPS refuse the plain dev server, and Copy Link needs a secure page.

`npm --prefix client run build` writes the deployable static site to
`client/dist`.

`npm --prefix client test` runs the client tests: layout and per-empire views
against small fixtures, links, and, once the dataset is built, checks that every
empire's view and every isolated lineage draws no trace to a hidden card, no
overlapping cards and no prerequisite to the right of what needs it.

Controls: the dock at the top left holds search, the guide, the empire picker
and the settings preset; on a phone the empire and preset fold behind an Empire
button. Hover highlights a technology's ancestry and
descendants, click pins it and opens the detail panel, middle-click (or Isolate
in the panel) isolates it into a mini-tree, <kbd>/</kbd> searches, <kbd>F</kbd>
fits the tree, <kbd>Esc</kbd> clears, <kbd>?</kbd> opens the guide. Touch
equivalents: tap to select, long-press to isolate, pinch to zoom. The address
bar carries the empire, preset, pinned technology and isolated lineage, so a
copied address reopens the same view.

Settings presets are configured in `config/presets.toml`, and each build writes
`build/presets.md`, listing what each preset hides and why. `[links]` in
`config/build.toml` sets where players report mistakes (GitHub issues, the
Gigastructures Discord); the panel's Report a Problem button appears once either
is set.

## Mod sources

Mods are fetched from git and pinned to an exact commit rather than read from a
local Steam Workshop copy, which can be stale or sitting on a feature branch.
`tools/vendor_sync.py --check` reports when a pin has fallen behind its branch.

Only the directories the build reads are fetched, via a blobless sparse
checkout: Gigastructures is 1.9 GB, of which ~49 MB is relevant.

## Documentation

- `docs/usage-guide.md` — updating the page for a new game or mod version, and publishing it
- `docs/KNOWN-CORPUS-DEFECTS.md` — third-party data problems the pipeline copes with
- `docs/crisis-rows.md` — why the Gigastructures crises get rows of their own
- `config/unlocks.toml` — names for gate conditions, tags for unlock routes such as
  Observation Insights, and manual per-technology tag overrides

## Licence and attribution

Technology names, descriptions and icons are the property of Paradox Interactive
and the respective mod authors, and are used here to build a community reference
tool.
