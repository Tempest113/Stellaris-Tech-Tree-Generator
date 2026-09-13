"""The technology dependency graph.

Edges run **from the technology depended upon to the technology that declares
the dependency**, so a topological order is also a research order and the
layout's left-to-right axis falls out of it directly.

Three kinds of edge, because they mean different things and should not be drawn
or reasoned about identically:

``prerequisite``
    A hard requirement. The technology cannot be researched without it.

``alternative``
    One option within an ``OR`` group. Any single member satisfies the group, so
    treating these as hard requirements over-constrains both layout and
    reachability.

``potential-gate``
    A ``has_technology`` reference inside a ``potential`` block. Not a
    prerequisite in the game's sense -- it gates whether the technology exists
    for that empire at all -- but it is a real dependency the reader needs to
    see.

Cycles are a hard failure. Clausewitz permits them and a mod can introduce one
by accident; silently tolerating it would produce a layout that is quietly
wrong rather than obviously broken.
"""

from __future__ import annotations

import enum
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from .clausewitz import Block, Scalar
from .clausewitz.nodes import Node
from .records import Extraction, TechnologyRecord
from .triggers import NEGATING, changes_scope

class GraphError(RuntimeError):
    """The dependency graph could not be built."""


class GraphCycleError(GraphError):
    """A prerequisite cycle exists. Always a data bug."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__("prerequisite cycle: " + " -> ".join([*cycle, cycle[0]]))


class EdgeKind(enum.Enum):
    PREREQUISITE = "prerequisite"
    ALTERNATIVE = "alternative"
    POTENTIAL_GATE = "potential-gate"


@dataclass(frozen=True)
class Edge:
    """A dependency, pointing from the thing needed to the thing needing it."""

    #: The technology depended upon.
    source: str
    #: The technology that declares the dependency.
    target: str
    kind: EdgeKind
    #: Index of the OR group within the target's prerequisite list, for
    #: ``alternative`` edges. Members of one group share a group index.
    group: int | None = None

    @property
    def is_hard(self) -> bool:
        return self.kind is EdgeKind.PREREQUISITE


def find_technology_references(node: Node, *, negated: bool = False) -> list[tuple[str, bool]]:
    """Collect ``has_technology`` references, tracking negation.

    A reference inside ``NOT``/``NOR``/``NAND`` means the technology must *not*
    be held, which is an exclusion rather than a dependency. Reporting both the
    same way would draw arrows that claim the opposite of what the data says.

    References under a scope change are not collected at all: they describe
    another country's technologies, not a dependency of this one. See
    :data:`SCOPE_PREFIXES`.
    """
    found: list[tuple[str, bool]] = []
    if isinstance(node, Scalar):
        return found

    for item in node.items:
        if isinstance(item, Block):
            found.extend(find_technology_references(item, negated=negated))
            continue
        key = getattr(item, "key", None)
        if key is None:
            continue
        if key == "has_technology" and isinstance(item.value, Scalar):
            found.append((item.value.value, negated))
        elif isinstance(item.value, Block):
            if changes_scope(key):
                continue
            inner_negated = negated ^ (key.lower() in NEGATING)
            found.extend(find_technology_references(item.value, negated=inner_negated))
    return found


@dataclass
class TechGraph:
    """Nodes plus typed edges, with adjacency indexes for traversal."""

    records: dict[str, TechnologyRecord] = field(default_factory=dict)
    edges: tuple[Edge, ...] = ()
    #: Prerequisite references naming technologies outside this load order.
    dangling: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: ``has_technology`` references inside ``potential`` that are negated, so
    #: they exclude rather than require. Kept for the detail popup.
    exclusions: dict[str, tuple[str, ...]] = field(default_factory=dict)

    _out: dict[str, list[Edge]] = field(default_factory=lambda: defaultdict(list))
    _in: dict[str, list[Edge]] = field(default_factory=lambda: defaultdict(list))

    def __post_init__(self) -> None:
        for edge in self.edges:
            self._out[edge.source].append(edge)
            self._in[edge.target].append(edge)

    def __len__(self) -> int:
        return len(self.records)

    def __contains__(self, key: str) -> bool:
        return key in self.records

    def __iter__(self) -> Iterator[TechnologyRecord]:
        return iter(self.records.values())

    # -- adjacency ---------------------------------------------------------

    def incoming(self, key: str) -> list[Edge]:
        """Edges from the technologies this one depends on."""
        return list(self._in.get(key, ()))

    def outgoing(self, key: str) -> list[Edge]:
        """Edges to the technologies that depend on this one."""
        return list(self._out.get(key, ()))

    def prerequisites_of(self, key: str) -> list[str]:
        return [e.source for e in self.incoming(key)]

    def dependents_of(self, key: str) -> list[str]:
        return [e.target for e in self.outgoing(key)]

    def roots(self) -> list[str]:
        """Technologies with no dependency of any kind.

        A third of the corpus: many are start technologies, many more are
        granted by event or special project rather than researched into.
        """
        return [k for k in self.records if not self._in.get(k)]

    def leaves(self) -> list[str]:
        return [k for k in self.records if not self._out.get(k)]

    # -- traversal ---------------------------------------------------------

    def ancestors(self, key: str, *, kinds: Iterable[EdgeKind] | None = None) -> set[str]:
        """Everything ``key`` transitively depends on."""
        return self._walk(key, self._in, lambda e: e.source, kinds)

    def descendants(self, key: str, *, kinds: Iterable[EdgeKind] | None = None) -> set[str]:
        """Everything that transitively depends on ``key``."""
        return self._walk(key, self._out, lambda e: e.target, kinds)

    def isolate(self, key: str, *, kinds: Iterable[EdgeKind] | None = None) -> set[str]:
        """``key`` plus its full ancestry and descendants.

        This is the middle-click / long-press mini-tree.
        """
        return {key} | self.ancestors(key, kinds=kinds) | self.descendants(key, kinds=kinds)

    def _walk(self, key, index, pick, kinds) -> set[str]:
        wanted = set(kinds) if kinds is not None else None
        seen: set[str] = set()
        queue = deque([key])
        while queue:
            current = queue.popleft()
            for edge in index.get(current, ()):
                if wanted is not None and edge.kind not in wanted:
                    continue
                nxt = pick(edge)
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        seen.discard(key)
        return seen

    # -- ordering ----------------------------------------------------------

    def topological_order(self, *, kinds: Iterable[EdgeKind] | None = None) -> list[str]:
        """Kahn's algorithm. Raises :class:`GraphCycleError` on a cycle.

        Ordering is deterministic: ties break on the technology key, so a build
        produces identical geometry every time.
        """
        wanted = set(kinds) if kinds is not None else None
        edges = [e for e in self.edges if wanted is None or e.kind in wanted]

        indegree = {key: 0 for key in self.records}
        successors: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            if edge.source in indegree and edge.target in indegree:
                successors[edge.source].append(edge.target)
                indegree[edge.target] += 1

        ready = sorted(k for k, d in indegree.items() if d == 0)
        order: list[str] = []
        while ready:
            current = ready.pop(0)
            order.append(current)
            newly_ready = []
            for nxt in successors.get(current, ()):
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    newly_ready.append(nxt)
            if newly_ready:
                ready = sorted(ready + newly_ready)

        if len(order) != len(indegree):
            raise GraphCycleError(_find_cycle(indegree, successors))
        return order

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for edge in self.edges:
            counts[edge.kind.value] = counts.get(edge.kind.value, 0) + 1
        detail = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        return (
            f"{len(self.records)} nodes, {len(self.edges)} edges ({detail}); "
            f"{len(self.roots())} roots, "
            f"{sum(len(v) for v in self.dangling.values())} dangling references"
        )


def _find_cycle(indegree: dict[str, int], successors: dict[str, list[str]]) -> list[str]:
    """Recover one concrete cycle so the error can name it."""
    remaining = {k for k, d in indegree.items() if d > 0}
    for start in sorted(remaining):
        path: list[str] = []
        seen: set[str] = set()
        current = start
        while current not in seen:
            seen.add(current)
            path.append(current)
            nxt = next((s for s in successors.get(current, ()) if s in remaining), None)
            if nxt is None:
                break
            current = nxt
        else:
            return path[path.index(current):]
    return sorted(remaining)[:1]


def build(extraction: Extraction, *, include_potential_gates: bool = True) -> TechGraph:
    """Build the dependency graph from canonical records.

    Disabled technologies -- a potential no player can meet -- are not nodes.
    """
    records = {
        key: record
        for key, record in extraction.technologies.items()
        if key not in extraction.disabled
    }
    edges: list[Edge] = []
    dangling: dict[str, list[str]] = defaultdict(list)
    exclusions: dict[str, list[str]] = defaultdict(list)

    for record in records.values():
        for index, group in enumerate(record.prerequisites):
            kind = EdgeKind.ALTERNATIVE if group.is_choice else EdgeKind.PREREQUISITE
            for option in group.options:
                if option not in records:
                    dangling[record.key].append(option)
                    continue
                edges.append(
                    Edge(
                        source=option,
                        target=record.key,
                        kind=kind,
                        group=index if group.is_choice else None,
                    )
                )

        if not include_potential_gates or record.potential is None:
            continue

        declared = {k for group in record.prerequisites for k in group.options}
        for referenced, negated in find_technology_references(record.potential):
            if negated:
                exclusions[record.key].append(referenced)
                continue
            if referenced in declared or referenced == record.key:
                continue
            if referenced not in records:
                dangling[record.key].append(referenced)
                continue
            edges.append(Edge(referenced, record.key, EdgeKind.POTENTIAL_GATE))

    return TechGraph(
        records=records,
        edges=tuple(edges),
        dangling={k: tuple(v) for k, v in dangling.items()},
        exclusions={k: tuple(v) for k, v in exclusions.items()},
    )
