"""Emit the dataset the browser loads.

The browser never parses Clausewitz and never computes geometry, so everything
it needs is baked here: positions, row and band structure, resolved names, and
an icon atlas.

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import geometry as geom
from .graph import EdgeKind, TechGraph
from .icons import IconIndex, load_image
from .layout import CRISIS_GROUP, Layout
from .localisation import Localisation
from .records import Extraction
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

    crisis_names = {c.key: c.name for c in (assignment.crises if assignment else ())}
    icons = extraction.icons
    perk_gates = extraction.perk_gates

    # Stable node order so diffs between builds are readable.
    slots = sorted(layout.slots, key=lambda s: (s.row.area, s.row.category, s.column, s.cell_index))
    index_of: dict[str, int] = {}
    for slot in slots:
        index_of.setdefault(slot.technology, len(index_of))

    row_index = {row.key: row.index for row in layout.rows}
    boxes = geom.build(layout)
    used_icon_stems: list[str] = []
    nodes = []

    for slot in slots:
        record = extraction[slot.technology]
        icon = record.icon(icons) if icons else None
        stem = icon.stem if icon and icon.stem else ""
        if stem:
            used_icon_stems.append(stem)

        flags = []
        if record.is_dangerous:
            flags.append("dangerous")
        if record.is_rare:
            flags.append("rare")
        if record.is_weightless:
            flags.append("weightless")
        if perk_gates.get(slot.technology):
            # Two separate facts. A technology can be perk-gated and still
            # event-granted, and the panel says which applies.
            flags.append("perk-gated")
        if record.start_tech:
            flags.append("start")
        if slot.spilled:
            flags.append("spilled")
        if not slot.is_primary:
            flags.append("variant")

        box = boxes.nodes[(slot.technology, row_index[slot.row])]
        node = {
            "k": slot.technology,
            "n": localisation.name(slot.technology),
            "r": row_index[slot.row],
            "c": slot.column,
            "i": slot.cell_index,
            "x": box.x,
            "y": box.y,
            "t": slot.tier,
            "a": record.area,
            "g": record.category or "",
            "ic": stem,
        }
        if flags:
            node["f"] = flags
        if record.cost is not None:
            node["$"] = int(record.cost)
        if record.is_repeatable:
            node["lv"] = record.levels
        nodes.append(node)

    perk_icons: dict[str, str] = {}
    if icons:
        for gates in perk_gates.values():
            for gate in gates:
                for perk in gate.perks:
                    if perk in perk_icons:
                        continue
                    ref = icons.ascension_perk(perk)
                    if ref.stem:
                        perk_icons[perk] = ref.stem
                        used_icon_stems.append(ref.stem)

    slot_stems, sheets = build_atlas(icons, used_icon_stems, directory) if icons else ({}, [])
    for node in nodes:
        node["ic"] = slot_stems.get(node["ic"], -1)

    edges = [
        [index_of[e.source], index_of[e.target], EDGE_KINDS.index(e.kind)]
        for e in graph.edges
        if e.source in index_of and e.target in index_of
    ]

    dataset = {
        "meta": {
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": [
                {"key": s.key, "name": s.name, "version": s.version}
                for s in (extraction.load_order or [])
            ],
            "counts": {
                "technologies": len(extraction),
                "slots": len(layout.slots),
                "edges": len(edges),
                "rows": len(layout.rows),
                "columns": layout.columns,
            },
        },
        "atlas": {"sheets": sheets, "cell": CELL, "perRow": PER_ROW, "perSheet": PER_SHEET, "size": SHEET},
        "canvas": {
            "width": boxes.width,
            "height": boxes.height,
            "card": {"w": geom.CARD_WIDTH, "h": geom.CARD_HEIGHT},
            "columnPitch": geom.COLUMN_PITCH,
        },
        "edgeKinds": [k.value for k in EDGE_KINDS],
        "rows": [
            {
                "i": row.index,
                "group": row.area,
                "key": row.category,
                "label": (
                    crisis_names.get(row.category, row.category)
                    if row.is_crisis
                    else localisation.get(row.category) or row.category.replace("_", " ").title()
                ),
                "n": row.population,
                "y": boxes.rows[row.index].y,
                "h": boxes.rows[row.index].height,
                "header": geom.ROW_HEADER,
            }
            for row in layout.rows
        ],
        "bands": [
            {
                "t": b.tier,
                "s": b.start,
                "e": b.end,
                "x": boxes.column_x(b.start) - geom.COLUMN_GAP // 2,
                "w": (
                    boxes.column_x(b.end)
                    + boxes.column_widths.get(b.end, geom.CARD_WIDTH)
                    - boxes.column_x(b.start)
                    + geom.COLUMN_GAP
                ),
            }
            for b in layout.bands
        ],
        "repeatableBand": {
            "x": boxes.column_x(layout.repeatable_column) - geom.COLUMN_GAP // 2,
            "w": boxes.column_widths.get(layout.repeatable_column, geom.CARD_WIDTH) + geom.COLUMN_GAP,
        },
        "repeatableColumn": layout.repeatable_column,
        "columns": layout.columns,
        "nodes": nodes,
        "edges": edges,
    }

    def gate_payload(key: str) -> list[dict]:
        """Perk gates for the detail panel.

        Perk *names* are deduplicated, not perk keys: Galactic Wonders ships as
        four DLC-conditional keys that all localise to the same words, and
        listing it four times would read as four requirements.
        """
        payload = []
        for gate in perk_gates.get(key, ()):
            names: list[str] = []
            slots: list[int] = []
            for perk in gate.perks:
                name = localisation.name(perk)
                slot = slot_stems.get(perk_icons.get(perk, ""), -1)
                if name in names:
                    # Same perk under another name-sharing key. Keep whichever
                    # one ships art: the three DLC-conditional Galactic Wonders
                    # keys have none of their own and reuse the base perk's.
                    at = names.index(name)
                    if slots[at] < 0:
                        slots[at] = slot
                    continue
                names.append(name)
                slots.append(slot)
            payload.append({"k": gate.kind.value, "n": names, "i": slots})
        return payload

    details = {}
    for node in nodes:
        key = node["k"]
        entry = {
            "d": localisation.description(key),
            "p": [list(group.options) for group in extraction[key].prerequisites],
        }
        gates = gate_payload(key)
        if gates:  # 82 of 978 technologies; omitted elsewhere to keep this small
            entry["ap"] = gates
        details[key] = entry

    files: dict[str, int] = {}
    for name, payload in (("dataset.json", dataset), ("details.json", details)):
        path = directory / name
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        files[name] = path.stat().st_size
    for sheet in sheets:
        files[sheet] = (directory / sheet).stat().st_size

    return EmitResult(directory=directory, files=files)
