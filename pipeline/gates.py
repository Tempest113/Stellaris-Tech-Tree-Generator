"""Which ascension perks gate a technology, and how strongly.

An ascension perk is the strongest gate in the game: a perk is one of a handful
of permanent choices an empire makes across a whole campaign, so a technology
behind one is unreachable for most empires rather than merely unlikely. That
makes it the single most useful thing to say about a technology the reader
cannot otherwise explain the absence of.

The corpus expresses the same idea three ways, and only reading all three gives
an honest answer:

``potential``
    ``potential = { has_ascension_perk = ap_cosmogenesis }``. A hard gate: the
    technology does not exist for the empire at all. 22 technologies.

``weight_modifier``
    ``modifier = { factor = 0  NOT = { has_ascension_perk = ap_x } }``. The
    technology exists but can never be drawn from the research pool without the
    perk, which for a reader is the same thing. 40 technologies. Kept as its own
    kind because the distinction is real -- such a technology can still be
    granted by an event -- and because a factor of 0 is the only weight
    modifier that gates rather than merely nudges.

scripted triggers
    ``potential = { has_gigastructural_constructs = yes }``, where that name is
    defined in ``common/scripted_triggers`` as
    ``or = { and = { is_ai = yes has_country_flag = ... } has_ascension_perk =
    ap_gigastructural_constructs }``. Gigastructures gates most of its
    headline megastructures this way, so a resolver that only reads
    ``has_ascension_perk`` literally misses them entirely.

Why this matters
----------------
Before this existed the renderer had one flag, ``weightless``, drawn from
``weight == 0`` and labelled "granted by event". 185 technologies set it, but
only 174 are really event grants; the other 11 are perk-gated, and a further 45
perk-gated technologies carry no weight-based signal at all and so said nothing.
The label was wrong for 11 technologies and missing for 45.

Negation and scope
------------------
Both are tracked, for the same reasons :func:`~pipeline.graph.find_technology_references`
tracks them. ``NOT = { has_ascension_perk = x }`` inside ``potential`` excludes
the perk rather than requiring it, and a reference under ``any_country`` asks
what somebody else has taken. Neither is a gate on this technology.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Iterable, Iterator

from .clausewitz import Block, Scalar
from .clausewitz.nodes import Node
from .triggers import NEGATING, TriggerIndex, changes_scope

#: How deep scripted triggers may nest before we stop following them. The corpus
#: never exceeds 2; the bound exists so a mod that defines a trigger in terms of
#: itself cannot hang the build.
MAX_TRIGGER_DEPTH = 8


class GateKind(enum.Enum):
    """How firmly a perk gates a technology."""

    #: ``potential`` -- the technology does not exist without the perk.
    REQUIRED = "required"
    #: ``weight_modifier`` with ``factor = 0`` -- it exists but is never drawn.
    UNDRAWABLE = "undrawable"


@dataclass(frozen=True)
class PerkGate:
    """One condition standing between an empire and a technology.

    ``perks`` are alternatives: holding any single one satisfies the gate. A
    technology's gates are a conjunction of these, mirroring the script, where
    a ``potential`` block is an implicit AND of its top-level conditions and
    each condition may be an OR inside.

    Galactic Wonders is why this cannot be a flat list of perks. It ships as
    four keys -- base, Utopia, MegaCorp and both -- which are one perk wearing
    different DLC hats, and ``has_galactic_wonders`` ORs all four. Flattening
    them would claim a technology needs four perks at once.
    """

    perks: tuple[str, ...]
    kind: GateKind
    #: Set when the perks were reached through a scripted trigger rather than
    #: named directly, so a build report can show the indirection.
    via_trigger: str | None = None

    @property
    def is_required(self) -> bool:
        return self.kind is GateKind.REQUIRED


def find_ascension_perks(
    node: Node | None,
    triggers: TriggerIndex | None = None,
    *,
    negated: bool = False,
    _depth: int = 0,
    _seen: frozenset[str] = frozenset(),
) -> list[tuple[str, str | None]]:
    """Perks positively required somewhere under ``node``.

    Returns ``(perk, via_trigger)`` pairs. Negated references and references
    under a changed scope are skipped: neither is a requirement on the empire
    researching this technology.
    """
    found: list[tuple[str, str | None]] = []
    if node is None or isinstance(node, Scalar):
        return found

    for item in node.items:
        if isinstance(item, Block):
            found.extend(
                find_ascension_perks(
                    item, triggers, negated=negated, _depth=_depth, _seen=_seen
                )
            )
            continue
        key = getattr(item, "key", None)
        if key is None:
            continue

        if key == "has_ascension_perk" and isinstance(item.value, Scalar):
            if not negated:
                found.append((item.value.value, None))
            continue

        # `some_trigger = yes` pulls in that trigger's whole body.
        if (
            triggers is not None
            and isinstance(item.value, Scalar)
            and item.value.value.lower() == "yes"
            and key in triggers
            and key not in _seen
            and _depth < MAX_TRIGGER_DEPTH
        ):
            body = triggers.get(key)
            for perk, inner in find_ascension_perks(
                body,
                triggers,
                negated=negated,
                _depth=_depth + 1,
                _seen=_seen | {key},
            ):
                found.append((perk, inner or key))
            continue

        if isinstance(item.value, Block):
            if changes_scope(key):
                continue
            found.extend(
                find_ascension_perks(
                    item.value,
                    triggers,
                    negated=negated ^ (key.lower() in NEGATING),
                    _depth=_depth,
                    _seen=_seen,
                )
            )
    return found


def _gating_modifiers(weight_modifiers: Iterable[Block]) -> Iterator[Block]:
    """``modifier`` blocks whose only effect is to zero the weight for lack of a perk.

    Two conditions, both necessary.

    *The factor must be exactly 0.* Anything else scales the draw weight, and a
    technology merely made unlikely is not a technology made unavailable.

    *The zero must be unconditional apart from the perk test.* A modifier body
    is an implicit AND, so a sibling condition means the zero only bites in
    narrower circumstances and the perk is not what gates the technology. The
    sixteen ``tech_fe_*_1`` technologies are exactly this trap::

        modifier = {
            factor = 0
            NOT = { has_ascension_perk = ap_cosmogenesis }
            calc_true_if = { amount >= 4  has_technology = tech_fe_affluence_1 ... }
        }

    That zeroes the weight only once the empire already holds four fallen-empire
    technologies: Cosmogenesis lifts a cap, it does not gate the technology, and
    reporting it as a gate would be simply untrue. Sixteen modifiers in the
    corpus are conditional like this and are skipped; forty are not.

    Alternatives *inside* the single condition are fine and are kept, because
    they widen rather than narrow the unlock. ``NOR = { has_crisis_level = x
    has_ascension_perk = y }`` says the weight is zero unless the empire has one
    or the other, so the perk is a genuine route in.
    """
    for group in weight_modifiers:
        for pair in group.pairs():
            if pair.key != "modifier" or not isinstance(pair.value, Block):
                continue
            factor: float | None = None
            conditions = 0
            for item in pair.value.items:
                key = getattr(item, "key", None)
                if key == "factor":
                    if isinstance(item.value, Scalar):
                        try:
                            factor = float(item.value.value)
                        except ValueError:
                            factor = None
                    continue
                conditions += 1
            if factor == 0 and conditions == 1:
                yield pair.value


def _condition_groups(
    block: Block | None, triggers: TriggerIndex | None, *, negated: bool
) -> Iterator[tuple[tuple[str, ...], str | None]]:
    """One ``(perks, via_trigger)`` group per top-level condition bearing perks.

    The block is an implicit AND, so each of its top-level conditions is a
    separate gate; the perks found beneath one condition are alternatives to
    each other.
    """
    if block is None:
        return
    for item in block.items:
        found = find_ascension_perks(Block(items=[item]), triggers, negated=negated)
        if not found:
            continue
        perks: list[str] = []
        for perk, _ in found:
            if perk not in perks:
                perks.append(perk)
        via = next((v for _, v in found if v), None)
        yield tuple(perks), via


def perk_gates(record, triggers: TriggerIndex | None = None) -> tuple[PerkGate, ...]:
    """Every ascension-perk gate on ``record``, hard ones first.

    A perk group already required outright is not repeated as an undrawable
    one. The six Cosmogenesis lathe technologies state it both ways; the weaker
    statement adds nothing once the hard one holds.
    """
    gates: list[PerkGate] = []
    seen: set[tuple[str, ...]] = set()

    for perks, via in _condition_groups(record.potential, triggers, negated=False):
        if perks in seen:
            continue
        seen.add(perks)
        gates.append(PerkGate(perks=perks, kind=GateKind.REQUIRED, via_trigger=via))

    for modifier in _gating_modifiers(record.weight_modifiers):
        # `factor = 0` when the empire does NOT hold the perk is what makes the
        # perk a gate, so here it is the negated reference that matters.
        for perks, via in _condition_groups(modifier, triggers, negated=True):
            if perks in seen:
                continue
            seen.add(perks)
            gates.append(
                PerkGate(perks=perks, kind=GateKind.UNDRAWABLE, via_trigger=via)
            )

    return tuple(gates)


def build(records: dict, triggers: TriggerIndex | None) -> dict[str, tuple[PerkGate, ...]]:
    """Perk gates for every technology that has any."""
    found: dict[str, tuple[PerkGate, ...]] = {}
    for key, record in records.items():
        gates = perk_gates(record, triggers)
        if gates:
            found[key] = gates
    return found


def summary(gates: dict[str, tuple[PerkGate, ...]]) -> str:
    required = sum(1 for g in gates.values() if any(x.is_required for x in g))
    indirect = sum(1 for g in gates.values() if any(x.via_trigger for x in g))
    perks = {p for g in gates.values() for x in g for p in x.perks}
    return (
        f"{len(gates)} technologies behind {len(perks)} ascension perks "
        f"({required} hard-gated, {indirect} via scripted triggers)"
    )
