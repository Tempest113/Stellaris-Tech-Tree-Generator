"""Resolve Clausewitz ``@variable`` references.

Three things about this that are easy to get wrong:

* **Variables are not always numbers.** ``@giga_amb_flag = giga_buildcap_j`` is a
  string used as a trigger right-hand side (``has_global_flag = @giga_amb_flag``,
  17 occurrences in Gigastructures). A resolver that coerces to float drops them.

* **They are not all declared in ``common/scripted_variables/``.** Vanilla
  declares three of them *inside technology files*, and two of those sit
  mid-file between technology blocks rather than in a header
  (``00_soc_tech.txt:2740`` and ``:3140``).

* **A reference can survive unresolved and that is sometimes correct.**
  Gigastructures' ACOT compatibility chain uses ``@acot_tier6cost2`` and friends,
  which only exist when ACOT is loaded. Leaving the reference intact and
  recording it beats guessing a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .clausewitz import Block, Pair, Scalar, parse_file
from .clausewitz.nodes import Item, Node
from .loadorder import LoadOrder, Source, resolve_files

#: A variable reference may itself name another variable. Nothing in the current
#: corpus does, but the cost of supporting it is one loop and the cost of not
#: supporting it is a silent wrong number.
_MAX_INDIRECTION = 8


@dataclass
class VariableTable:
    """Resolved ``@name`` values, with provenance and unresolved-reference tracking."""

    values: dict[str, str] = field(default_factory=dict)
    origins: dict[str, Source] = field(default_factory=dict)
    #: Names referenced somewhere but never declared, collected during substitution.
    missing: set[str] = field(default_factory=set)

    def declare(self, name: str, value: str, source: Source | None = None) -> None:
        self.values[name] = value
        if source is not None:
            self.origins[name] = source

    def lookup(self, name: str) -> str | None:
        """Resolve a bare variable name, following indirection."""
        seen: set[str] = set()
        current = name
        for _ in range(_MAX_INDIRECTION):
            if current in seen:
                return None  # cycle
            seen.add(current)
            value = self.values.get(current)
            if value is None:
                return None
            if value.startswith("@") and len(value) > 1:
                current = value[1:]
                continue
            return value
        return None

    def resolve_text(self, text: str) -> str:
        """Resolve ``@name`` to its value, or return the text unchanged."""
        if not text.startswith("@") or len(text) == 1:
            return text
        name = text[1:]
        resolved = self.lookup(name)
        if resolved is None:
            self.missing.add(name)
            return text
        return resolved

    def as_number(self, text: str) -> float | None:
        """Resolve and coerce to a number, or ``None`` if it is not numeric."""
        try:
            return float(self.resolve_text(text))
        except (TypeError, ValueError):
            return None

    # -- tree substitution -------------------------------------------------

    def substitute(self, node: Node) -> Node:
        """Return ``node`` with every ``@reference`` scalar resolved.

        Keys are substituted too: a handful of blocks use a variable as a key.
        The tree is rebuilt rather than mutated so the raw form stays available
        to callers that need it.
        """
        if isinstance(node, Scalar):
            resolved = self.resolve_text(node.value)
            return node if resolved == node.value else Scalar(resolved, node.quoted)

        items: list[Item] = []
        for item in node.items:
            if isinstance(item, Pair):
                items.append(
                    Pair(
                        key=self.resolve_text(item.key),
                        op=item.op,
                        value=self.substitute(item.value),
                        key_quoted=item.key_quoted,
                    )
                )
            elif isinstance(item, Scalar):
                items.append(self.substitute(item))  # type: ignore[arg-type]
            else:
                items.append(self.substitute(item))  # type: ignore[arg-type]
        return Block(items)


def collect_variables(
    load_order: LoadOrder,
    *,
    extra: dict[str, str] | None = None,
    extra_sources: LoadOrder | None = None,
) -> VariableTable:
    """Build the variable table for a load order.

    ``extra_sources`` supplies variables from sources that are read for
    reference only and are not part of the load order proper -- the published
    build reads ACOT this way, purely to resolve ``@acot_tier*cost*`` for
    Gigastructures' compatibility chain, without letting ACOT's own overrides
    apply.

    ``extra`` carries inline declarations already harvested elsewhere, such as
    the ones vanilla hides inside technology files.
    """
    table = VariableTable()

    # Reference-only sources load first so the real load order can override them.
    if extra_sources is not None:
        _absorb(table, extra_sources)
    _absorb(table, load_order)

    for name, value in (extra or {}).items():
        table.declare(name, value)

    return table


def _absorb(table: VariableTable, load_order: LoadOrder) -> None:
    for resolved in resolve_files(load_order, "common/scripted_variables", recursive=True):
        block = parse_file(resolved.path)
        for pair in block.pairs():
            if pair.key.startswith("@") and isinstance(pair.value, Scalar):
                table.declare(pair.key[1:], pair.value.value, resolved.source)
