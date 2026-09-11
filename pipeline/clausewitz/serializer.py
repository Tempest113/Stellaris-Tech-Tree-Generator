"""Render a syntax tree back to Clausewitz source.

This exists to power the roundtrip oracle, not to pretty-print mod files, so it
optimises for being obviously correct rather than for matching Paradox's house
style. Output is still readable enough to diff by eye when a test fails.
"""

from __future__ import annotations

from .nodes import Block, Item, Node, Pair, Scalar

#: A block made only of short scalars renders on one line. Purely cosmetic; it
#: keeps things like ``category = { voidcraft }`` from costing three lines.
_INLINE_SCALAR_LIMIT = 8


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _scalar_text(scalar: Scalar) -> str:
    return _quote(scalar.value) if scalar.quoted else scalar.value


def _is_inlinable(block: Block) -> bool:
    return len(block.items) <= _INLINE_SCALAR_LIMIT and all(
        isinstance(item, Scalar) for item in block.items
    )


def _render_block(block: Block, indent: int, out: list[str]) -> None:
    if not block.items:
        out.append("{ }")
        return

    if _is_inlinable(block):
        inner = " ".join(_scalar_text(item) for item in block.items)  # type: ignore[arg-type]
        out.append("{ " + inner + " }")
        return

    pad = "\t" * (indent + 1)
    out.append("{\n")
    for item in block.items:
        out.append(pad)
        _render_item(item, indent + 1, out)
        out.append("\n")
    out.append("\t" * indent + "}")


def _render_value(value: Node, indent: int, out: list[str]) -> None:
    if isinstance(value, Block):
        _render_block(value, indent, out)
    else:
        out.append(_scalar_text(value))


def _render_item(item: Item, indent: int, out: list[str]) -> None:
    if isinstance(item, Pair):
        key = _quote(item.key) if item.key_quoted else item.key
        out.append(f"{key} {item.op} ")
        _render_value(item.value, indent, out)
    elif isinstance(item, Block):
        _render_block(item, indent, out)
    else:
        out.append(_scalar_text(item))


def serialize(block: Block) -> str:
    """Render a top-level block. The outermost braces are not emitted."""
    out: list[str] = []
    for item in block.items:
        _render_item(item, 0, out)
        out.append("\n")
    return "".join(out)
