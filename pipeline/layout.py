"""Assign every technology a row and a column.

The pipeline owns all geometry. The renderer consumes emitted positions and
never recomputes them from a parallel formula -- when the previous attempt let
the client re-derive row and band positions, the two drifted and cards drew
nowhere near their panels.

Horizontal axis
---------------
Two rules are in tension, and the corpus proves they cannot both hold:

1. tier 0 sits furthest left, increasing tier moves right;
2. a technology never sits left of, or vertically in line with, a prerequisite.

Twenty-four technologies have a prerequisite in a *higher* tier -- for instance
``tech_reactor_boosters_3`` is tier 1 and needs tier-3 ``tech_antimatter_power``.
Rule 2 wins here, so those technologies spill past their tier band and carry
their true tier on the card instead:

    column(t) = max(band_start[tier(t)], 1 + max(column(p) for p in prereqs(t)))

Band starts come from tier-internal chain depth only, computed before any
column is assigned, which makes the whole thing a single deterministic pass over
a topological order rather than a fixpoint.

Repeatables are lifted out into a terminal band on the far right regardless of
declared tier. Verified safe: no edge of any kind leaves a repeatable, so they
are true sinks and moving them cannot place anything left of its prerequisite.

Vertical axis
-------------
One row per ``(area, category)`` pair, grouped physics, society, engineering.
Keying on category alone would be wrong: ``blokkats`` holds physics, society and
engineering technologies at once, so a single row could not carry an honest area
tint. Every other category is single-area, so in practice this is "one row per
category" with ``blokkats`` split three ways.

A technology that a ``technology_swap`` relocates gets one slot per distinct
placement, all sharing a column since a swap can change area and category but
never tier. Exactly one slot is visible for any given empire.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterator

from .graph import EdgeKind, TechGraph
from .records import AREAS, TechnologyRecord
from .rows import RowAssignment

#: Pseudo-area marking a crisis row. Sorts after the three real areas, so
#: crisis bands sit below the main tree, and tells the renderer to tint the row
#: by crisis rather than by research area -- a crisis chain spans all three.
CRISIS_GROUP = "crisis"

#: Row groups, in reading order. Crisis rows come last.
AREA_ORDER = {area: index for index, area in enumerate(AREAS)}
AREA_ORDER[CRISIS_GROUP] = len(AREAS)

#: Edge kinds that constrain horizontal position.
#:
#: Deliberately excludes ``potential-gate``. Such an edge gates whether a
#: technology exists for an empire at all, not the order it is researched in,
#: and 7 of the 27 in the corpus run from a higher tier to a lower one. Letting
#: them push columns produces nonsense: ``tech_missiles_1`` is tier 0, and its
#: only incoming edge is a potential-gate from tier-5 ``tech_cosmogenesis_escort``
#: (bio-ship empires can field missiles only via Cosmogenesis). Constraining on
#: it drags the entire missile and torpedo chain to column 20 and beyond.
#:
#: Those edges are still drawn, in their own style, and are simply allowed to
#: run backward.
COLUMN_CONSTRAINT_KINDS = (EdgeKind.PREREQUISITE, EdgeKind.ALTERNATIVE)


class LayoutError(RuntimeError):
    """A technology could not be placed."""


@dataclass(frozen=True)
class RowKey:
    area: str
    category: str

    def __str__(self) -> str:
        return f"{self.area}/{self.category}"


@dataclass(frozen=True)
class Row:
    key: RowKey
    index: int
    #: How many slots sit in this row.
    population: int

    @property
    def area(self) -> str:
        return self.key.area

    @property
    def category(self) -> str:
        return self.key.category

    @property
    def is_crisis(self) -> bool:
        return self.key.area == CRISIS_GROUP


@dataclass(frozen=True)
class Band:
    """The column range belonging to one tier."""

    tier: int
    start: int
    #: Last column the tier's own chain structure reserves. Spilled technologies
    #: may sit beyond it.
    end: int

    @property
    def width(self) -> int:
        return self.end - self.start + 1

    def contains(self, column: int) -> bool:
        return self.start <= column <= self.end


@dataclass(frozen=True)
class Slot:
    """One placed node.

    A technology has one slot per distinct placement it can occupy; exactly one
    is visible for a given empire.
    """

    technology: str
    row: RowKey
    column: int
    #: Vertical position within the ``(row, column)`` cell.
    cell_index: int
    tier: int
    #: True for the technology's default placement.
    is_primary: bool
    is_repeatable: bool
    #: True when the column lies outside the technology's own tier band, because
    #: a prerequisite in a higher tier pushed it right.
    spilled: bool


@dataclass
class Layout:
    slots: tuple[Slot, ...] = ()
    rows: tuple[Row, ...] = ()
    bands: tuple[Band, ...] = ()
    #: Column holding the terminal repeatable band.
    repeatable_column: int = 0
    columns: int = 0

    def __iter__(self) -> Iterator[Slot]:
        return iter(self.slots)

    def __len__(self) -> int:
        return len(self.slots)

    def primary(self) -> dict[str, Slot]:
        return {s.technology: s for s in self.slots if s.is_primary}

    def slots_for(self, technology: str) -> list[Slot]:
        return [s for s in self.slots if s.technology == technology]

    @property
    def spilled(self) -> list[Slot]:
        return [s for s in self.slots if s.spilled and s.is_primary]

    def summary(self) -> str:
        alternates = len(self.slots) - len(self.primary())
        return (
            f"{len(self.slots)} slots ({len(self.primary())} technologies "
            f"+ {alternates} alternates), {len(self.rows)} rows, "
            f"{self.columns} columns, {len(self.spilled)} spilled"
        )


def _placement_rows(record: TechnologyRecord, rows: RowAssignment | None) -> list[RowKey]:
    """Rows a technology occupies.

    A technology in a crisis occupies exactly that one row. Pulling it into a
    crisis band and *also* leaving it in its category row would double-count it
    and make both bands lie about their contents.
    """
    crisis = rows.crisis_of(record.key) if rows else None
    if crisis:
        return [RowKey(CRISIS_GROUP, crisis)]
    return [
        RowKey(area, category)
        for area, categories in record.placements
        for category in (categories or ("uncategorised",))
    ]


def _row_keys(graph: TechGraph, rows: RowAssignment | None) -> list[RowKey]:
    """Every row any technology can occupy."""
    population: dict[RowKey, int] = defaultdict(int)
    for record in graph:
        for key in _placement_rows(record, rows):
            population[key] += 1
    return sorted(
        population,
        key=lambda key: (AREA_ORDER.get(key.area, len(AREAS)), key.category),
    ), population


def _tier_band_starts(graph: TechGraph, records: dict[str, TechnologyRecord]) -> dict[int, Band]:
    """Reserve a column range per tier from its internal chain depth.

    Computed before any column is assigned, using only edges whose endpoints
    share a tier. That keeps band boundaries independent of the spill behaviour
    they are about to permit, which is what makes column assignment a single
    pass rather than a fixpoint.
    """
    constrained = set(COLUMN_CONSTRAINT_KINDS)
    by_tier: dict[int, list[str]] = defaultdict(list)
    for key, record in records.items():
        if not record.is_repeatable:
            by_tier[record.tier].append(key)

    bands: dict[int, Band] = {}
    cursor = 0
    for tier in sorted(by_tier):
        members = set(by_tier[tier])
        depth = {key: 0 for key in members}
        # Longest path within the tier. The subgraph is small and acyclic, so a
        # relaxation over a topological order of the whole graph suffices.
        for key in graph.topological_order():
            if key not in members:
                continue
            for edge in graph.incoming(key):
                if edge.kind in constrained and edge.source in members:
                    depth[key] = max(depth[key], depth[edge.source] + 1)
        width = max(depth.values(), default=0) + 1
        bands[tier] = Band(tier=tier, start=cursor, end=cursor + width - 1)
        cursor += width
    return bands


def build(graph: TechGraph, rows: RowAssignment | None = None) -> Layout:
    """Place every technology. Deterministic: identical input, identical output.

    ``rows`` optionally lifts crisis technologies out of their category rows and
    into their own bands.
    """
    records = graph.records
    if not records:
        return Layout()

    row_keys, population = _row_keys(graph, rows)
    bands = _tier_band_starts(graph, records)

    order = graph.topological_order()
    column: dict[str, int] = {}

    for key in order:
        record = records[key]
        if record.is_repeatable:
            continue
        band = bands.get(record.tier)
        floor = band.start if band else 0
        for edge in graph.incoming(key):
            if edge.kind in COLUMN_CONSTRAINT_KINDS and edge.source in column:
                floor = max(floor, column[edge.source] + 1)
        column[key] = floor

    # Repeatables form a terminal band beyond everything else. Safe because no
    # edge leaves a repeatable, so this cannot place anything left of a
    # prerequisite.
    repeatable_column = max(column.values(), default=-1) + 1
    for key, record in records.items():
        if record.is_repeatable:
            column[key] = repeatable_column

    missing = [k for k in records if k not in column]
    if missing:
        raise LayoutError(f"{len(missing)} technologies were never assigned a column")

    # One slot per placement, all sharing the technology's column.
    cells: dict[tuple[RowKey, int], list[tuple[str, bool]]] = defaultdict(list)
    for key in order:
        record = records[key]
        placement_rows = _placement_rows(record, rows)
        for index, row_key in enumerate(placement_rows):
            cells[(row_key, column[key])].append((key, index == 0))

    slots: list[Slot] = []
    for (row_key, col), members in cells.items():
        for cell_index, (key, is_primary) in enumerate(members):
            record = records[key]
            band = bands.get(record.tier)
            slots.append(
                Slot(
                    technology=key,
                    row=row_key,
                    column=col,
                    cell_index=cell_index,
                    tier=record.tier,
                    is_primary=is_primary,
                    is_repeatable=record.is_repeatable,
                    spilled=bool(band and not record.is_repeatable and not band.contains(col)),
                )
            )

    row_order = {key: index for index, key in enumerate(row_keys)}
    slots.sort(key=lambda s: (row_order[s.row], s.column, s.cell_index))

    return Layout(
        slots=tuple(slots),
        rows=tuple(
            Row(key=key, index=index, population=population[key])
            for index, key in enumerate(row_keys)
        ),
        bands=tuple(bands[t] for t in sorted(bands)),
        repeatable_column=repeatable_column,
        columns=repeatable_column + 1,
    )


def check_invariants(layout: Layout, graph: TechGraph) -> list[str]:
    """Assert the properties the layout exists to guarantee.

    Returned as a list rather than raised so a build report can show every
    violation at once instead of only the first.
    """
    problems: list[str] = []
    columns = {s.technology: s.column for s in layout.slots}

    for edge in graph.edges:
        if edge.kind not in COLUMN_CONSTRAINT_KINDS:
            continue  # potential-gate edges are permitted to run backward
        if edge.source not in columns or edge.target not in columns:
            continue
        if columns[edge.source] >= columns[edge.target]:
            problems.append(
                f"{edge.target} (column {columns[edge.target]}) is not right of its "
                f"{edge.kind.value} {edge.source} (column {columns[edge.source]})"
            )

    if any(s.column < 0 for s in layout.slots):
        problems.append("negative column index")
    if any(s.cell_index < 0 for s in layout.slots):
        problems.append("negative cell index")

    seen: set[tuple[RowKey, int, int]] = set()
    for slot in layout.slots:
        cell = (slot.row, slot.column, slot.cell_index)
        if cell in seen:
            problems.append(f"two slots occupy {cell}")
        seen.add(cell)

    for slot in layout.slots:
        if slot.is_repeatable and slot.column != layout.repeatable_column:
            problems.append(f"{slot.technology} is repeatable but outside the terminal band")

    return problems
