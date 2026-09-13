"""Emit the dataset the browser loads.

The browser never parses Clausewitz or reads a trigger, so everything it needs
to know about the game is baked here: placement (row, column and order in a
cell), resolved names, what each empire profile sees, and an icon atlas.

Pixels are the browser's. An empire profile hides cards and the rest close up
around the gaps, so positions depend on the profile chosen; the one formula
that turns placement into pixels lives in ``client/src/geometry.ts``.

Two files, because they have different access patterns. ``dataset.json`` holds
what is needed to draw the tree and is fetched up front. ``details.json`` holds
descriptions, which are only read when someone opens a popup, and would
otherwise double the initial payload for text nobody has asked to see yet.

Node fields are abbreviated. It reads worse but the file is ~1000 records of
near-identical shape, so the key names would otherwise be a large fraction of
the bytes.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from . import gates as gates_mod
from . import profiles as profiles_mod
from . import unlocks as unlocks_mod
from .clausewitz import Block, serialize
from .graph import EdgeKind, TechGraph
from .icons import IconIndex, load_image
from .layout import Layout, Slot
from .localisation import Localisation
from .records import Extraction, TechnologyRecord
from .rows import RowAssignment

#: Icons are 52-58px in the corpus, so 56 is close to native for most and keeps
#: packing trivial: one cell size means the renderer derives every source
#: rectangle arithmetically and no per-icon coordinates need shipping.
CELL = 56
#: 2048 is the safe maximum texture size on essentially all hardware.
SHEET = 2048
PER_ROW = SHEET // CELL
PER_SHEET = PER_ROW * PER_ROW

#: Edge kinds, as indices, in the order the client expects.
EDGE_KINDS = (EdgeKind.PREREQUISITE, EdgeKind.ALTERNATIVE, EdgeKind.POTENTIAL_GATE)


@dataclass
class EmitResult:
    directory: Path
    files: dict[str, int]

    def summary(self) -> str:
        total = sum(self.files.values())
        parts = ", ".join(
            f"{name} {size / 1024:.0f} KB" for name, size in sorted(self.files.items())
        )
        return f"{total / 1024:.0f} KB total -> {parts}"


def build_atlas(
    icons: IconIndex, stems: list[str], directory: Path
) -> tuple[dict[str, int], list[str]]:
    """Pack the icons actually used into fixed-grid sheets.

    Returns a stem -> slot index map. The renderer derives the sheet and the
    source rectangle arithmetically, so no per-icon coordinates need shipping.
    """
    from PIL import Image

    unique = sorted(set(stems))
    sheets: list[Image.Image] = []
    slots: dict[str, int] = {}

    for index, stem in enumerate(unique):
        sheet_number, cell = divmod(index, PER_SHEET)
        while len(sheets) <= sheet_number:
            sheets.append(Image.new("RGBA", (SHEET, SHEET), (0, 0, 0, 0)))
        path = icons.technologies.get(stem) or icons.ascension_perks.get(stem)
        if path is None:
            continue
        image = load_image(path)
        if image.size != (CELL, CELL):
            image = image.resize((CELL, CELL), Image.LANCZOS)
        row, column = divmod(cell, PER_ROW)
        sheets[sheet_number].paste(image, (column * CELL, row * CELL))
        slots[stem] = index

    names: list[str] = []
    for number, sheet in enumerate(sheets):
        # WebP rather than PNG: these sheets are ~1000 pieces of detailed RGBA
        # art, where lossless PNG runs to several megabytes. Quality 90 is
        # visually indistinguishable at the sizes icons are drawn and cuts the
        # payload by roughly 85%.
        name = f"icons-{number}.webp"
        sheet.save(directory / name, "WEBP", quality=90, method=4)
        names.append(name)
    return slots, names


def slot_profile(record: TechnologyRecord, slot: Slot) -> frozenset[str]:
    """The empire conditions under which ``slot`` is the one on screen.

    A primary slot has none. A variant slot has its swap's trigger, broken into
    its top-level conditions -- an implicit AND -- and rendered to canonical text
    so two swaps asking the same thing compare equal.
    """
    if slot.swap is None:
        return frozenset()
    swap = record.swap_named(slot.swap)
    if swap is None or swap.trigger is None:
        return frozenset()
    return frozenset(serialize(Block(items=[item])).strip() for item in swap.trigger.items)


def wire_edges(
    graph: TechGraph, slots: list[Slot], profiles: list[frozenset[str]]
) -> list[list[int]]:
    """Dependency edges as ``[source slot, target slot, kind]``.

    Edges are between technologies but cards are slots, and nine technologies
    have more than one slot. Every slot of a target gets its edge, since every
    presentation of a technology has the same prerequisites. Which *source*
    slot it comes from is chosen by profile: the source slot visible under the
    most specific conditions the target slot's own conditions imply. A
    bio-ship empire's Improved Fighter Wing therefore draws from Basic Fighter
    Wing in society/biology, not from Carrier Operations in engineering, while
    anything with no bio-ship variant of its own draws from the primary slot.

    Without an empire profile to choose one presentation, that is the most
    that can honestly be drawn: a primary target is not told which variant of
    its prerequisite a given empire will see, so it points at the default.
    """
    by_technology: dict[str, list[int]] = defaultdict(list)
    for index, slot in enumerate(slots):
        by_technology[slot.technology].append(index)

    edges: list[list[int]] = []
    for edge in graph.edges:
        sources = by_technology.get(edge.source)
        targets = by_technology.get(edge.target)
        if not sources or not targets:
            continue
        kind = EDGE_KINDS.index(edge.kind)
        for target in targets:
            wanted = profiles[target]
            source = max(
                (s for s in sources if profiles[s] <= wanted),
                key=lambda s: (len(profiles[s]), -s),
                default=None,
            )
            if source is not None:
                edges.append([source, target, kind])
    return edges


def emit(
    extraction: Extraction,
    graph: TechGraph,
    layout: Layout,
    localisation: Localisation,
    assignment: RowAssignment | None,
    directory: Path | str,
) -> EmitResult:
    """Write the dataset, the detail payload and the icon atlas."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    crisis_rows = {c.key: c.name for c in (assignment.crises if assignment else ())}
    icons = extraction.icons
    config = extraction.unlock_config
    all_gates = gates_mod.with_inherited(graph, extraction.gates)
    levels = extraction.crisis_levels
    crisis_names = gates_mod.crisis_level_names(levels, localisation, config.names)

    # Stable node order so diffs between builds are readable. Node index is
    # position in this list, and nothing else may be used to address a node:
    # numbering by technology instead drifted by one at every variant slot and
    # drew 740 of 976 edges between the wrong cards.
    slots = sorted(layout.slots, key=lambda s: (s.row.area, s.row.category, s.column, s.cell_index))

    row_index = {row.key: row.index for row in layout.rows}
    used_icon_stems: list[str] = []
    nodes = []
    profiles: list[frozenset[str]] = []
    views = profiles_mod.compute_views(
        graph,
        slots,
        extraction.profile_definitions,
        extraction.profiles,
        unlocks_mod.routes_finder(extraction.unlock_routes, extraction.unlocks, config),
        extraction.unlocks.component_prerequisites,
    )

    def presentation(record: TechnologyRecord, swap) -> tuple[str, str]:
        """Name and icon stem of a swap."""
        name = localisation.get(swap.name) or localisation.name(record.key)
        icon = (
            icons.swap(
                record.key,
                swap.name,
                inherit_icon=swap.inherit_icon,
                declared_icon=record.declared_icon,
            )
            if icons
            else None
        )
        return name, icon.stem if icon and icon.stem else ""

    for index, slot in enumerate(slots):
        record = extraction[slot.technology]
        swap = record.swap_named(slot.swap) if slot.swap else None
        profiles.append(slot_profile(record, slot))

        # A variant slot is what its empires actually see, so it wears the
        # swap's name and art rather than the default presentation's.
        if swap is not None:
            name, stem = presentation(record, swap)
        else:
            name = localisation.name(slot.technology)
            icon = record.icon(icons) if icons else None
            stem = icon.stem if icon and icon.stem else ""
        if stem:
            used_icon_stems.append(stem)

        gates = all_gates.get(slot.technology, ())
        badge = gates_mod.strongest(gates, levels)
        flags = []
        if record.is_dangerous:
            flags.append("dangerous")
        if record.is_rare:
            flags.append("rare")
        if record.is_undrawable:
            flags.append("undrawable")
        if gates:
            flags.append("gated")
        if badge is not None and gates_mod.badge_perks(badge, levels) and badge.is_inherited:
            flags.append("perk-inherited")
        if record.start_tech:
            flags.append("start")
        if slot.spilled:
            flags.append("spilled")
        if not slot.is_primary:
            flags.append("variant")

        node = {
            "k": slot.technology,
            "n": name,
            "r": row_index[slot.row],
            "c": slot.column,
            "i": slot.cell_index,
            "t": slot.tier,
            "a": swap.area if swap and swap.area else record.area,
            "g": record.category or "",
            "ic": stem,
        }
        if swap is not None:
            node["sw"] = swap.name
        if flags:
            node["f"] = flags
        # Profiles, as bits in the order of dataset["profiles"], hex-encoded.
        if views.hidden[index]:
            node["hp"] = format(views.hidden[index], "x")
        swaps_by_name = {s.name: s for s in record.swaps}
        shown_as = []
        for swap_name, mask in views.presentations[index]:
            swap_name_text, swap_stem = presentation(record, swaps_by_name[swap_name])
            if swap_name_text == name and swap_stem == stem:
                continue
            if swap_stem:
                used_icon_stems.append(swap_stem)
            shown_as.append({"m": format(mask, "x"), "n": swap_name_text, "ic": swap_stem, "sw": swap_name})
        if shown_as:
            node["pv"] = shown_as
        tag = unlocks_mod.tag_for(
            record,
            extraction.unlock_routes.get(slot.technology, ()),
            config,
            extraction.unlocks,
            crisis_names,
        )
        if tag:
            node["tg"] = tag
        if record.cost is not None:
            node["$"] = int(record.cost)
        if record.is_repeatable:
            node["lv"] = record.levels
        nodes.append(node)

    perk_icons: dict[str, str] = {}
    if icons:
        for gates in all_gates.values():
            for gate in gates:
                for perk in gates_mod.badge_perks(gate, levels):
                    if perk in perk_icons:
                        continue
                    ref = icons.ascension_perk(perk)
                    if ref.stem and ref.is_exact:
                        perk_icons[perk] = ref.stem
                        used_icon_stems.append(ref.stem)

    slot_stems, sheets = build_atlas(icons, used_icon_stems, directory) if icons else ({}, [])
    for node in nodes:
        node["ic"] = slot_stems.get(node["ic"], -1)
        for shown in node.get("pv", ()):
            shown["ic"] = slot_stems.get(shown["ic"], -1)

    def perk_icon(perk: str) -> int:
        return slot_stems.get(perk_icons.get(perk, ""), -1)

    def badge(key: str, bit: int | None) -> tuple[int | None, bool]:
        """The card badge -- atlas slot or None, and whether inherited -- for a profile.

        One perk, from the strongest gate with perk art. Declared before
        inherited, so a card never names a gate it merely passes along when it
        has one of its own. Under a profile, a route it cannot take is no
        badge: a nomad's Tetradimensional Engineering comes through the Blokkat
        Bureau, not Gigastructural Constructs.
        """
        gates = all_gates.get(key, ())
        if bit is not None:
            ev = views.evaluators[bit]
            kept = []
            for gate in gates:
                alternatives = tuple(
                    alternative
                    for alternative in gate.alternatives
                    if not any(
                        profiles_mod.condition_truth(ev, c, levels) is profiles_mod.TV.FALSE
                        for c in alternative
                    )
                )
                if alternatives:
                    kept.append(replace(gate, alternatives=alternatives))
            gates = tuple(kept)
        strongest = gates_mod.strongest(gates, levels)
        perks = gates_mod.badge_perks(strongest, levels) if strongest is not None else ()
        if not perks:
            return None, False
        return next((s for s in map(perk_icon, perks) if s >= 0), -1), strongest.is_inherited

    for node in nodes:
        default = badge(node["k"], None)
        if default[0] is not None:
            node["pb"] = default[0]
        differs: dict[tuple[int | None, bool], int] = {}
        for bit in range(len(extraction.profiles)):
            found = badge(node["k"], bit)
            if found != default:
                differs[found] = differs.get(found, 0) | (1 << bit)
        if differs:
            node["bv"] = [
                {"m": format(mask, "x"), **({"pb": slot} if slot is not None else {}), "pi": int(inherited)}
                for (slot, inherited), mask in sorted(differs.items(), key=lambda kv: kv[1])
            ]

    edges = wire_edges(graph, slots, profiles)

    dataset = {
        "meta": {
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": [
                {"key": s.key, "name": s.name, "version": s.version}
                for s in (extraction.load_order or [])
            ],
            "counts": {
                "technologies": len(graph.records),
                "slots": len(layout.slots),
                "edges": len(edges),
                "rows": len(layout.rows),
                "columns": layout.columns,
            },
        },
        "atlas": {"sheets": sheets, "cell": CELL, "perRow": PER_ROW, "perSheet": PER_SHEET, "size": SHEET},
        "edgeKinds": [k.value for k in EDGE_KINDS],
        # Every empire an empire can be created as. A node's `hp` and a
        # presentation's `m` are masks over this list, bit 0 first.
        "profiles": [
            {
                "k": p.key,
                "l": p.label,
                "a": p.authority,
                "t": [name for name, _ in profiles_mod.TOGGLES if getattr(p, name)],
            }
            for p in extraction.profiles
        ],
        "authorities": [[key, profiles_mod.AUTHORITY_LABELS[key]] for key in profiles_mod.AUTHORITIES],
        "toggles": [list(toggle) for toggle in profiles_mod.TOGGLES],
        "rows": [
            {
                "i": row.index,
                "group": row.area,
                "key": row.category,
                "label": (
                    crisis_rows.get(row.category, row.category)
                    if row.is_crisis
                    else localisation.get(row.category) or row.category.replace("_", " ").title()
                ),
            }
            for row in layout.rows
        ],
        "bands": [{"t": b.tier, "s": b.start, "e": b.end} for b in layout.bands],
        "repeatableColumn": layout.repeatable_column,
        "columns": layout.columns,
        "nodes": nodes,
        "edges": edges,
    }

    def condition_payload(condition: gates_mod.Condition) -> dict:
        entry: dict = {
            "n": gates_mod.condition_name(condition, localisation, config.names, levels),
            "t": condition.kind,
        }
        # A crisis level wears the art of the perk that starts its path.
        if condition.kind == "perk":
            icon_slot = perk_icon(condition.key)
        elif condition.kind == "crisis" and condition.key in levels:
            icon_slot = perk_icon(levels[condition.key].perk)
        else:
            icon_slot = -1
        if icon_slot >= 0:
            entry["i"] = icon_slot
        context = config.contexts.get(condition.key) or extraction.perk_contexts.get(condition.key)
        if not context and condition.kind == "tradition":
            tree = extraction.tradition_trees.get(condition.key)
            tree_name = localisation.get(tree) if tree else None
            # "Psionic Traditions Finished" already says its tree; "Flood of
            # Supremacy" does not.
            if tree_name and tree_name.lower() not in entry["n"].lower():
                context = f"{tree_name} traditions"
        if context:
            entry["c"] = context
        impossible = views.condition_impossible(condition, levels)
        if impossible:
            entry["x"] = impossible
        return entry

    def hex_masks(payload: list[dict]) -> list[dict]:
        for gate in payload:
            for alternative in gate["a"]:
                for condition in alternative:
                    if "x" in condition:
                        condition["x"] = format(condition["x"], "x")
        return payload

    def gate_payload(key: str) -> list[dict]:
        """Gates for the detail panel, as groups of alternatives.

        ``a`` lists the alternatives; each is a list of conditions that must
        hold together. Alternatives are deduplicated by what they *read* as,
        not by key: Galactic Wonders ships as four DLC-conditional keys that
        all localise to the same words, and listing it four times would read
        as four different ways in.
        """
        payload = []
        for gate in all_gates.get(key, ()):
            alternatives: dict[tuple[str, ...], list[dict]] = {}
            for alternative in gate.alternatives:
                conditions = [condition_payload(c) for c in alternative]
                names = tuple(dict.fromkeys(c["n"] for c in conditions))
                if names in alternatives:
                    # Same words under another key. Keep whichever ships art:
                    # the DLC-conditional Galactic Wonders keys reuse the base
                    # perk's. The merged condition is impossible only where
                    # every key it stands for is.
                    for kept, fresh in zip(alternatives[names], conditions):
                        if "i" not in kept and "i" in fresh:
                            kept["i"] = fresh["i"]
                        both = kept.get("x", 0) & fresh.get("x", 0)
                        if both:
                            kept["x"] = both
                        else:
                            kept.pop("x", None)
                    continue
                alternatives[names] = list({c["n"]: c for c in conditions}.values())
            entry = {"k": gate.kind.value, "a": list(alternatives.values())}
            if gate.inherited_from:
                entry["v"] = localisation.name(gate.inherited_from)
            payload.append(entry)
        return payload

    def route_payload(key: str) -> list[dict]:
        """How an undrawable technology reaches a player, one entry per way."""
        payload: list[dict] = []
        for route in extraction.unlock_routes.get(key, ()):
            kind = route.kind
            if kind is unlocks_mod.RouteKind.PERK or kind is unlocks_mod.RouteKind.TRADITION:
                name = localisation.get(route.key) or route.key
            elif kind is unlocks_mod.RouteKind.RESEARCH:
                name = localisation.name(route.key)
            elif kind is unlocks_mod.RouteKind.CRISIS:
                name = crisis_names.get(route.key or "", unlocks_mod.TAG_CRISIS)
            elif kind is unlocks_mod.RouteKind.TAGGED:
                name = route.key
            else:
                name = ""
            entry = {"k": kind.value, "n": name}
            if entry not in payload:
                payload.append(entry)
        return payload

    def starting_conditions(record) -> list[str]:
        """Named conditions in ``starting_potential``, for "Starting technology for ..."."""
        if record.starting_potential is None:
            return []
        return [
            config.names[pair.key]
            for pair in record.starting_potential.pairs()
            if pair.key in config.names
        ]

    details = {}
    for node in nodes:
        key = node["k"]
        record = extraction[key]
        entry = {
            "d": localisation.description(key),
            "p": [list(group.options) for group in record.prerequisites],
        }
        # Each of these is empty for most technologies; omitted to keep the file small.
        gates = hex_masks(gate_payload(key))
        if gates:
            entry["ap"] = gates
        technology_routes = route_payload(key)
        if technology_routes:
            entry["u"] = technology_routes
        starting = starting_conditions(record) if record.start_tech else []
        if starting:
            entry["st"] = starting
        details[key] = entry
        # A variant slot opens under its swap's name, and its description is
        # the one its empires read.
        # A slot presented as a swap opens under the swap's name, and its
        # description is the one its empires read. A swap sharing its
        # technology's name keeps the technology's entry.
        for swap_name in [node.get("sw"), *(p["sw"] for p in node.get("pv", ()))]:
            if swap_name and swap_name != key and swap_name not in details:
                details[swap_name] = {
                    **entry,
                    "d": localisation.description(swap_name) or entry["d"],
                }

    files: dict[str, int] = {}
    for name, payload in (("dataset.json", dataset), ("details.json", details)):
        path = directory / name
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        files[name] = path.stat().st_size
    for sheet in sheets:
        files[sheet] = (directory / sheet).stat().st_size

    return EmitResult(directory=directory, files=files)
