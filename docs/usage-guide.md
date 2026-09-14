# Usage guide

How to update the published tree when Stellaris or Gigastructures changes, from
fetching the new mod version to putting the new page online.

The page never updates on its own. Nothing is built in CI, because the base
game's data cannot be put in a public build machine. Pushing code to GitHub does
not change the page either: it changes when you rebuild the dataset on your own
machine and publish the result. That includes the "Built from Stellaris … and
Gigastructural Engineering & More …, on …" line in the guide, which is read at
build time from your Steam install's game version, the pinned Gigastructures
commit's `descriptor.mod`, and the date of the build.

Commands below run from the repository root, in Git Bash. In PowerShell, write
`.venv\Scripts\python` for `.venv/Scripts/python`.

## One-time setup

You need Python 3.10 or newer, Node.js 20 or newer, Git, and Stellaris
installed through Steam.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
npm --prefix client install
```

Subscribe to [Ancient Cache of Technologies](https://steamcommunity.com/sharedfiles/filedetails/?id=1419304439)
on the Steam Workshop. It is not loaded into the tree, but four Gigastructures
technologies name ACOT technologies and costs, and without it those stay
unresolved (the build says so and carries on).

Stellaris is found through Steam. If it lives somewhere Steam does not know
about, set `STELLARIS_PATH` to the game directory.

## Updating the page

### 1. Move to the new Gigastructures version

Gigastructures is read from its git repository at an exact commit, pinned in
`config/build.toml`, never from your Workshop folder.

```bash
.venv/Scripts/python tools/vendor_sync.py --update
```

This prints the current commit of `Live-Branch`, the branch that matches the
Workshop release. Paste it over `commit = "…"` under `[[sources]]` in
`config/build.toml`, then fetch it:

```bash
.venv/Scripts/python tools/vendor_sync.py
```

`vendor_sync.py --check` only reports whether the pin is behind, and fetches
nothing.

A Stellaris update needs no step here: Steam updates the game, and the next
build reads it.

### 2. Build the dataset

```bash
.venv/Scripts/python tools/build_dataset.py --out client/public/data
```

About two minutes. It prints a summary of each stage, and stops with an error when
a configuration file names something the new version no longer has (see
[When the build stops](#when-the-build-stops)).

### 3. Review what changed

Each build writes three reports to `build/`. They are there to be read, and
comparing them with the previous build's is the fastest way to spot a mistake.
The build overwrites them, so copy the old ones somewhere before step 2.

| Report | Lists | Fix mistakes in |
| --- | --- | --- |
| `build/crisis-rows.md` | Every technology in a crisis row, the rule that put it there, and technologies that look like they belong to a crisis but are not in one | `config/rows.toml` |
| `build/unlock-tags.md` | Every technology that is never offered as research, with the tag on its card and the event chain behind it | `config/unlocks.toml` |
| `build/presets.md` | What each settings preset hides, and the flags that decide it | `config/presets.toml` |

Also read the build's own output for `warning:` lines, and for technologies
listed as using the placeholder icon or missing a name.

### 4. Look at it

```bash
npm --prefix client run dev
```

Open <http://localhost:5173>. Check the new technologies under a few empires and
each preset. To try it on a phone on the same Wi-Fi, run
`npm --prefix client run dev:phone` instead and open the Network address it
prints.

### 5. Run the tests

```bash
.venv/Scripts/python -m pytest
npm --prefix client test
```

Many Python tests pin exact counts from the real data on purpose (how many
technologies there are, which are hidden for whom), so a mod update is expected
to fail some of them. Each failure says what changed. If the change is real,
update the number in the test. If it is not, the pipeline has misread
something. The client tests check every empire's view of the dataset you just
built for overlapping cards and lines to hidden cards.

### 6. Publish

```bash
npm --prefix client run build
```

This writes the whole site, dataset included, to `client/dist`. It is static
files only, so any static host will serve it.

The published Gigastructures tree lives in a repository of its own, holding
nothing but the built site:
[Tempest113/Gigas-Tech-Tree](https://github.com/Tempest113/Gigas-Tech-Tree),
served by GitHub Pages from its `main` branch at
<https://tempest113.github.io/Gigas-Tech-Tree/>. The generator's repository keeps
its name because it can build other trees; the page's address says what it
shows.

`client/dist` is a clone of that site repository. Rebuilding keeps the clone,
because the build clears everything in `client/dist` except `.git`, so
publishing is committing whatever the build left there.

On a new machine, clone it once, before building (the folder must not exist
yet):

```bash
git clone https://github.com/Tempest113/Gigas-Tech-Tree.git client/dist
```

Every time:

```bash
git -C client/dist add -A
git -C client/dist commit -m "Stellaris v4.4.6, Gigastructures 3.39.4"
git -C client/dist push origin main
```

In GitHub Desktop, `client/dist` can be added as a repository of its own (File →
Add local repository) and pushed from there instead. GitHub takes a minute or
two to update the page after a push.

Commit the configuration changes (the new pin, any fixes to `config/`) to the
generator's repository as well, so the next build starts from them.

To publish a tree somewhere else, make `client/dist` a clone of that repository
instead, and in its **Settings → Pages** choose **Deploy from a branch**, the
branch you push, `/ (root)`.

## When Gigastructures adds technologies

Most of a new technology needs nothing from you. Its place in the tree, its
prerequisites, cost, icon, name, description, which empires can research it,
and which events hand it out are all read from the mod's own files. What the
build cannot know is written in `config/`, and that is where to look when
something new is wrong.

**New Aeternite (or other crisis) technologies.** A crisis row takes every
technology declared in its crisis's files, listed as `source_files` in
`config/rows.toml`. New Aeternum technologies in `giga_11_aeternum.txt` join the
Aeternum row by themselves. A crisis technology declared in some other file
does not: add its key to that crisis's `keys`. Anything that looks crisis-related
but is not in a row is listed under "Needs review" in `build/crisis-rows.md`.

**A new crisis.** Add a `[[crisis]]` to `config/rows.toml` with a `key`, a `name`
(Gigastructures' own name for it, from its `giga_category_*` localisation), a
`colour`, and rules saying which technologies belong. The comments at the top of
the file explain each rule.

**A technology that comes from an event or special project.** Its card is
tagged "Event" unless a rule in `config/unlocks.toml` names the menu or chain a
player works through. `build/unlock-tags.md` lists every tag with the chain
behind it. Add a `[[route]]` there for a better name.

**A requirement shown under the wrong name, or expanded into its parts.** Name
the scripted trigger or flag under `[conditions]` in `config/unlocks.toml`.

**A new settings preset, or a renamed one.** Presets are listed in
`config/presets.toml` by the scripted effect Gigastructures runs from its menu,
in `common/scripted_effects/giga_menu_effects.txt`. A renamed effect stops the
build. A new crisis setting belongs under `[unsettled]`, so presets never hide
crisis technologies.

**A problem in the mod's own files** (a typo in a key, a technology that can
never be researched): record it in `docs/KNOWN-CORPUS-DEFECTS.md` with the
evidence. Re-check that file after each update and remove anything fixed
upstream.

## When the build stops

Stale configuration stops the build instead of quietly producing a wrong tree.

- **`crisis 'x' names technology 'y', which does not exist`**: the mod renamed or
  removed a technology `config/rows.toml` names. Find its new key, or remove it.
- **`settings presets name scripted effects this load order does not define`**:
  a preset or crisis-setting effect in `config/presets.toml` was renamed. Find
  the new name in `giga_menu_effects.txt`.
- **`nothing vendored at vendor/gigas`**: run `tools/vendor_sync.py`.
- **`could not find Stellaris`**: set `STELLARIS_PATH`.
- **`LAYOUT INVARIANTS VIOLATED`**: the layout put a card after something that
  needs it, or two cards in one place. This is a bug in the pipeline, not in
  the configuration.

## Report links

`[links]` in `config/build.toml` sets where the page sends players who find a
mistake: `issues` is the GitHub repository's issues page
(`https://github.com/<owner>/<repo>/issues`), `discord` an invite. Report a
Problem in the details panel, and the guide's "Found a mistake?" line, appear
only for the links that are set. Rebuild the dataset after changing them.

## Building a tree for another load order

Nothing about Gigastructures is written into the pipeline or the page. What
makes this repository's tree the Gigastructures one is `config/build.toml`,
which lists Gigastructures as the load order and names the three files that
describe it: `config/rows.toml`, `config/unlocks.toml` and
`config/presets.toml`.

To build another tree, write another build config and pass it with `--config`:

```bash
.venv/Scripts/python tools/build_dataset.py --config config/my-build.toml --out client/public/data
```

A config with no `[[sources]]` builds the base game alone. Add mods as
`[[sources]]` (`kind = "git"` pinned to a commit, `"workshop"` by Workshop id,
or `"local"` for a folder). Under `[build]`, name that load order's own `rows`,
`unlocks` and `presets` files, or leave them out to have no crisis rows, no
names for unlock conditions, and no preset picker. The Gigastructures rows and
presets files would stop the build for any other load order, since they name
its technologies and effects.
