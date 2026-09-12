"""Turn row/column indices into pixel positions.

The renderer reads these positions and never recomputes them. That rule exists
because the previous attempt kept a copy of the row and band formulas in the
client, the two drifted apart, and cards drew nowhere near the panels that were
supposed to contain them. One owner, one formula.

Row heights vary: a row is as tall as its busiest column needs, because most
cells hold four or five cards while a few hold dozens.

Those few are the problem. One Blokkats cell holds 37 technologies, which on its
own would set that row to 3,468 px and the whole canvas to 23,188 px tall
against 10,230 wide -- an awkward shape on any landscape screen, and mostly
empty space. So a cell taller than ``MAX_STACK`` wraps into sub-columns, and a
logical column is then as wide as the widest cell in it. Columns keep their
logical indices; only the pixel mapping changes, so the layout invariants are
untouched and a card can still only move right, never left.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .layout import CRISIS_GROUP, Layout

#: Card size. Width is set by name length -- the 95th percentile technology name
#: is about 34 characters, which fits at this width without truncation.
CARD_WIDTH = 230
CARD_HEIGHT = 78

#: Horizontal gap between columns. Generous because edge routing needs the
#: channel, and because adjacent columns are dependency steps the eye should
#: read as distinct.
COLUMN_GAP = 90

#: Vertical gap between stacked cards in one cell.
CARD_GAP = 14

#: Space above a row for its label, and below it before the next row starts.
#: The label is drawn in world units and never taller than this space, which is
#: what stops it spilling onto the cards when zoomed out; the header is sized
#: so it stays readable down to roughly a fifth of full scale.
ROW_HEADER = 64
ROW_GUTTER = 34

#: Extra separation between research areas, and before the crisis bands.
AREA_GAP = 40
CRISIS_GAP = 72

#: Left margin before column 0, and top margin above the first row.
MARGIN_X = 40
MARGIN_Y = 40

#: Cards stacked in one cell before wrapping into another sub-column. Chosen
#: against the corpus: the median cell holds 4 and the 95th percentile 14, so 8
#: leaves four fifths of cells untouched while cutting the tallest row by 78%.
MAX_STACK = 8

#: Gap between sub-columns inside one cell. Tighter than COLUMN_GAP because
#: sub-columns are the same dependency step, not consecutive ones, and should
#: read as one group.
SUBCOLUMN_GAP = 24

COLUMN_PITCH = CARD_WIDTH + COLUMN_GAP
SUBCOLUMN_PITCH = CARD_WIDTH + SUBCOLUMN_GAP
CARD_PITCH = CARD_HEIGHT + CARD_GAP


@dataclass(frozen=True)
class NodeBox:
    x: int
    y: int
    width: int = CARD_WIDTH
    height: int = CARD_HEIGHT

    @property
    def centre_x(self) -> int:
        return self.x + self.width // 2

    @property
    def centre_y(self) -> int:
        return self.y + self.height // 2


@dataclass(frozen=True)
class RowBox:
    index: int
    y: int
    height: int
    #: Tallest stack in this row, which is what set the height.
    max_stack: int


@dataclass
class Geometry:
    nodes: dict[tuple[str, int], NodeBox]
    rows: dict[int, RowBox]
    #: Left edge of each logical column, in pixels.
    columns: dict[int, int]
    #: Width of each logical column, which grows with its widest cell.
    column_widths: dict[int, int]
    width: int
    height: int

    def column_x(self, column: int) -> int:
        return self.columns.get(column, MARGIN_X)


def build(layout: Layout) -> Geometry:
    """Compute pixel boxes for every slot and row."""
    row_of = {row.key: row for row in layout.rows}

    # How many cards sit in each (row, column) cell, and so how many
    # sub-columns that cell needs.
    cell_counts: dict[tuple[int, int], int] = defaultdict(int)
    for slot in layout.slots:
        cell_counts[(row_of[slot.row].index, slot.column)] += 1

    subcolumns: dict[int, int] = defaultdict(lambda: 1)
    for (_, column), count in cell_counts.items():
        needed = max(1, -(-count // MAX_STACK))
        subcolumns[column] = max(subcolumns[column], needed)

    columns: dict[int, int] = {}
    column_widths: dict[int, int] = {}
    cursor = MARGIN_X
    for column in range(layout.columns):
        width = subcolumns[column] * SUBCOLUMN_PITCH - SUBCOLUMN_GAP
        columns[column] = cursor
        column_widths[column] = width
        cursor += width + COLUMN_GAP
    canvas_width = cursor - COLUMN_GAP + MARGIN_X

    stacks: dict[int, int] = defaultdict(int)
    for (row_index, column), count in cell_counts.items():
        stacks[row_index] = max(stacks[row_index], min(count, MAX_STACK))

    rows: dict[int, RowBox] = {}
    cursor = MARGIN_Y
    previous_group: str | None = None
    for row in layout.rows:
        group = row.area
        if previous_group is not None and group != previous_group:
            cursor += CRISIS_GAP if group == CRISIS_GROUP else AREA_GAP
        previous_group = group

        stack = max(stacks.get(row.index, 1), 1)
        height = ROW_HEADER + stack * CARD_PITCH - CARD_GAP + ROW_GUTTER
        rows[row.index] = RowBox(index=row.index, y=cursor, height=height, max_stack=stack)
        cursor += height

    nodes: dict[tuple[str, int], NodeBox] = {}
    for slot in layout.slots:
        row_index = row_of[slot.row].index
        row_box = rows[row_index]
        subcolumn, stack_index = divmod(slot.cell_index, MAX_STACK)
        nodes[(slot.technology, row_index)] = NodeBox(
            x=columns[slot.column] + subcolumn * SUBCOLUMN_PITCH,
            y=row_box.y + ROW_HEADER + stack_index * CARD_PITCH,
        )

    return Geometry(
        nodes=nodes,
        rows=rows,
        columns=columns,
        column_widths=column_widths,
        width=canvas_width,
        height=cursor + MARGIN_Y,
    )
