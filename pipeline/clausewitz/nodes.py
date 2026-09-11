"""The Clausewitz syntax tree.

The single most important property of this model is that a block is an **ordered
list**, not a mapping. Clausewitz permits the same key to appear more than once
inside one block and the corpus genuinely does it:

* ``tech_psi_jump_drive_1`` declares ``is_dangerous = yes`` twice (hand-written
  vanilla, not a mod artefact).
* Gigastructures' ``inline_script`` templates emit a ``weight_modifier`` block
  that merges with a sibling ``weight_modifier`` written at the call site, so
  ``giga_tech_repeatable_furnace_cap`` ends up with two of them.

Collapsing a block into a dict silently discards half of those. Accessors here
are therefore plural by default; the singular ones are explicit about which
occurrence they take.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Union

Node = Union["Scalar", "Block"]
Item = Union["Pair", "Scalar", "Block"]


@dataclass(frozen=True)
class Scalar:
    """A leaf value: a bare word, a number, or a quoted string.

    ``quoted`` is carried so the serializer can reproduce the original form.
    It is part of structural equality, which makes the roundtrip oracle strict
    about quoting rather than merely about text.
    """

    value: str
    quoted: bool = False

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Pair:
    """A ``key <op> value`` entry inside a block."""

    key: str
    op: str
    value: Node
    key_quoted: bool = False


@dataclass
class Block:
    """An ordered, heterogeneous sequence of entries.

    A block may be a list (``{ a b c }``), a mapping (``{ a = 1 }``), a mix of
    both (``prerequisites = { tech_a OR = { ... } }``), or a sequence of
    anonymous sub-blocks. All four shapes occur in the corpus.
    """

    items: list[Item] = field(default_factory=list)

    # -- iteration ---------------------------------------------------------

    def pairs(self) -> Iterator[Pair]:
        """Every ``key = value`` entry, in source order."""
        for item in self.items:
            if isinstance(item, Pair):
                yield item

    def scalars(self) -> Iterator[Scalar]:
        """Every bare list element, in source order."""
        for item in self.items:
            if isinstance(item, Scalar):
                yield item

    def blocks(self) -> Iterator["Block"]:
        """Every anonymous sub-block, in source order."""
        for item in self.items:
            if isinstance(item, Block):
                yield item

    # -- lookup ------------------------------------------------------------

    def get_all(self, key: str) -> list[Node]:
        """Every value declared under ``key``. The default accessor."""
        return [p.value for p in self.pairs() if p.key == key]

    def get_first(self, key: str) -> Node | None:
        """The first value declared under ``key``, or ``None``.

        Use when a repeat would be a data error worth ignoring. When the engine
        would take the *last* declaration instead, use :meth:`get_last`.
        """
        for pair in self.pairs():
            if pair.key == key:
                return pair.value
        return None

    def get_last(self, key: str) -> Node | None:
        """The last value declared under ``key``, or ``None``."""
        found: Node | None = None
        for pair in self.pairs():
            if pair.key == key:
                found = pair.value
        return found

    def has(self, key: str) -> bool:
        return any(p.key == key for p in self.pairs())

    def scalar_text(self, key: str) -> str | None:
        """Text of the first scalar value under ``key``, if it is a scalar."""
        value = self.get_first(key)
        return value.value if isinstance(value, Scalar) else None

    def block_at(self, key: str) -> "Block | None":
        """The first block value under ``key``, if it is a block."""
        value = self.get_first(key)
        return value if isinstance(value, Block) else None

    def keys(self) -> list[str]:
        """All keys in source order, including repeats."""
        return [p.key for p in self.pairs()]

    def __len__(self) -> int:
        return len(self.items)
