"""What an empire has to have chosen before it can research a technology.

Ascension perks are the strongest such gate -- a perk is one of a handful of
permanent choices across a campaign -- but they rarely stand alone.
``giga_tech_the_vat`` needs Galactic Wonders *and* one of genetic ascension, the
Genetics finisher, or Mechromancy. A reader that collects perks and ignores the
rest reports "Galactic Wonders + Mechromancy", which is simply false: a
biological empire never takes Mechromancy and still gets the Vat.

So a gate is read as a real boolean expression and reduced to what a player can
act on.

Conditions
----------
The conditions kept are the choices a player makes and can read about:
ascension perks, traditions, origins, civics and crisis levels. A scripted
trigger named in ``config/unlocks.toml`` -- ``has_genetically_ascended``, say --
is kept whole under its configured name instead of being expanded into the four
tradition finishers it stands for; any other scripted trigger is expanded.

Everything else a trigger can test -- DLC, game rules, flags, council traits --
is *unconstrained*: not a gate a card should name. The consequence that matters
is in an ``OR``. One unconstrained alternative is a way round every other one,
so the whole ``OR`` stops being a gate. ``OR = { has_country_flag = x
has_ascension_perk = y }`` does not require the perk, and saying it did was the
old bug.

Some things are known to be *false* for a player. ``always = no`` is one --
Gigastructures disables obsolete technologies with it. ``is_ai = yes`` is another, which
is what lets ``has_gigastructural_constructs`` -- "an AI with an override flag,
or the perk" -- still reduce to the perk. The other is a condition naming a perk,
tradition, origin, civic or crisis level the load order never defines; see
:data:`DEFINITION_DIRS`. A technology no empire can ever meet the potential of
is not in the tree at all: see :func:`pipeline.profiles.disabled`.

A country flag is resolved through whatever sets it. The planet-killer
technologies test ``has_country_flag = colossus_project``, and the only thing
that sets that flag is the event at the end of the Colossus Project, so they are
gated by the Colossus Project perk. A flag set anywhere a player can reach
without a perk or tradition is unconstrained like any other test -- unless
``config/unlocks.toml`` names it. ``giga_tech_tetradimensional_engineering``
needs Gigastructural Constructs *or* ``blokkat_bureau_unlocked``, which the
Blokkat questline sets; unnamed, that flag would be a way round the perk and the
whole gate would vanish, so it is kept as a condition called "Blokkat Bureau".

Where gates are read
--------------------
``potential``
    The technology does not exist for the empire unless this holds.

``weight_modifier``
    A ``modifier`` with ``factor = 0`` zeroes the draw when its conditions hold,
    so the technology is drawable only when they do not. Reading that negation
    through the same evaluator is what correctly ignores the sixteen
    ``tech_fe_*_1`` modifiers: they zero the weight only once an empire already
    holds four fallen-empire technologies, so the perk beside that condition
    lifts a cap rather than gating anything.

unlock routes
    A technology the research pool never offers, and that an ascension perk or
    tradition hands out, is gated by that perk or tradition. See
    :mod:`pipeline.unlocks`: Dyson Sphere comes from Galactic Wonders, Colossi
    from the Colossus Project four hops away.

On top of a technology's own gates sit those it inherits through what it
cannot do without. See :func:`with_inherited`.

Negation and scope
------------------
Tracked for the same reasons :func:`~pipeline.graph.find_technology_references`
tracks them. ``NOT = { has_ascension_perk = x }`` excludes a perk rather than
requiring it, and a condition under ``any_country`` asks about somebody else.
Neither gates this technology. In Stellaris a multi-child ``NOT`` is a ``NOR``.
"""

from __future__ import annotations

import enum
import itertools
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Union

from .clausewitz import Block, Scalar
from .loadorder import LoadOrder, merge_keys, resolve_files
from .triggers import TriggerIndex, changes_scope
from .unlocks import Route, RouteKind

if TYPE_CHECKING:
    from .graph import TechGraph

#: How deep scripted triggers may nest before we stop following them. The corpus
#: never exceeds 2; the bound exists so a mod that defines a trigger in terms of
#: itself cannot hang the build.
MAX_TRIGGER_DEPTH = 8

#: Cap on alternatives produced when an ``OR`` holds nested ``AND``s of ``OR``s.
MAX_ALTERNATIVES = 16

#: Trigger key -> condition kind, for the conditions a player chooses.
CONDITION_KEYS = {
    "has_ascension_perk": "perk",
    "has_tradition": "tradition",
    "has_active_tradition": "tradition",
    "has_origin": "origin",
    "has_valid_civic": "civic",
    "has_civic": "civic",
    "has_crisis_level": "crisis",
}

#: Where each condition kind is defined, so a condition naming nothing real can
#: be recognised as impossible. Origins are civics with ``is_origin = yes``.
#: A condition naming something the load order never defines is *false*:
#: Gigastructures carries compatibility checks for other mods --
#: ``has_active_tradition = frr_supremacy_deterrence_c`` -- that no player of this
#: load order can ever meet, and offering one as a route in would be a lie.
DEFINITION_DIRS = {
    "perk": ("common/ascension_perks",),
    "tradition": ("common/traditions",),
    "origin": ("common/governments/civics",),
    "civic": ("common/governments/civics",),
    "crisis": ("common/crisis_levels",),
}

#: Blocks whose contents are evaluated as if written in place.
_TRANSPARENT = {"custom_tooltip", "hidden_trigger"}

#: Empire kinds an ascension perk's own ``potential`` can restrict it to, and
#: how a reader names them.
_EMPIRE_CONTEXTS = {
    ("is_machine_empire", "yes"): "machine empires",
    ("is_hive_empire", "yes"): "hive minds",
    ("is_gestalt", "yes"): "gestalt empires",
    ("country_uses_bio_ships", "yes"): "bio-ship empires",
    ("is_lithoid_empire", "yes"): "lithoid empires",
}


class GateKind(enum.Enum):
    """How firmly a gate holds a technology."""

    #: ``potential`` -- the technology does not exist without it.
    REQUIRED = "required"
    #: Never drawable on its own, and this is what hands it out.
    GRANTED = "granted"
    #: A ``factor = 0`` weight modifier -- it exists but is never offered without it.
    UNDRAWABLE = "undrawable"


#: Strongest first. Decides which gate a card has room to show.
KIND_ORDER = (GateKind.REQUIRED, GateKind.GRANTED, GateKind.UNDRAWABLE)


@dataclass(frozen=True, order=True)
class Condition:
    #: ``"perk"``, ``"tradition"``, ``"origin"``, ``"civic"``, ``"crisis"``,
    #: ``"trigger"`` (a named scripted trigger) or ``"flag"`` (a named country flag).
    kind: str
    key: str


#: Conditions that must all hold together.
Alternative = tuple[Condition, ...]


@dataclass(frozen=True)
class Gate:
    """One requirement standing between an empire and a technology.

    ``alternatives`` are ways of meeting it: satisfy every condition in any one
    of them. A technology's gates are a conjunction of these.

    Galactic Wonders is why a single perk can still be several alternatives. It
    ships as four keys -- base, Utopia, MegaCorp and both -- that are one perk in
    different DLC clothing, and ``has_galactic_wonders`` ORs all four.
    """

    alternatives: tuple[Alternative, ...]
    kind: GateKind
    #: Set when the gate was reached through a scripted trigger rather than
    #: written out, so a build report can show the indirection.
    via_trigger: str | None = None
    #: For an inherited gate: the technology that declares it.
    inherited_from: str | None = None

    @property
    def conditions(self) -> tuple[Condition, ...]:
        seen: list[Condition] = []
        for alternative in self.alternatives:
            for condition in alternative:
                if condition not in seen:
                    seen.append(condition)
        return tuple(seen)

    @property
    def perks(self) -> tuple[str, ...]:
        return tuple(c.key for c in self.conditions if c.kind == "perk")

    @property
    def is_required(self) -> bool:
        return self.kind is GateKind.REQUIRED

    @property
    def is_inherited(self) -> bool:
        return self.inherited_from is not None


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


class Truth(enum.Enum):
    #: Unconstrained: nothing here a player has to choose.
    TRUE = "true"
    #: Impossible for a player, such as ``is_ai = yes``.
    FALSE = "false"


Expr = Union[Truth, Condition, tuple]  # tuple: ("and" | "or", (Expr, ...))


@dataclass(frozen=True)
class _Context:
    triggers: TriggerIndex | None
    named: frozenset[str]
    #: Flag -> the conditions that set it, or ``None`` when a player can come
    #: by it without any.
    flags: Callable[[str], tuple[Condition, ...] | None] | None = None
    #: Condition kind -> every key the load order defines for it.
    defined: dict[str, frozenset[str]] | None = None


def _combine(op: str, children: list) -> Expr:
    flat: list = []
    for child in children:
        if op == "and":
            if child is Truth.FALSE:
                return Truth.FALSE
            if child is Truth.TRUE:
                continue
        else:
            if child is Truth.TRUE:
                return Truth.TRUE
            if child is Truth.FALSE:
                continue
        if isinstance(child, tuple) and child[0] == op:
            flat.extend(child[1])
        elif child not in flat:
            flat.append(child)
    if not flat:
        return Truth.TRUE if op == "and" else Truth.FALSE
    if len(flat) == 1:
        return flat[0]
    return (op, tuple(flat))


def _evaluate_block(
    block: Block, ctx: _Context, *, negated: bool, op: str, depth: int, seen: frozenset[str]
) -> Expr:
    # De Morgan: under negation an AND of negated children is an OR, and back.
    effective = op if not negated else ("or" if op == "and" else "and")
    children = [
        _evaluate_item(item, ctx, negated=negated, depth=depth, seen=seen) for item in block.items
    ]
    return _combine(effective, children)


def _evaluate_item(item, ctx: _Context, *, negated: bool, depth: int, seen: frozenset[str]) -> Expr:
    if isinstance(item, Block):
        return _evaluate_block(item, ctx, negated=negated, op="and", depth=depth, seen=seen)
    key = getattr(item, "key", None)
    if key is None:
        return Truth.TRUE
    value = item.value
    lowered = key.lower()

    if isinstance(value, Block):
        if lowered in ("not", "nor"):
            return _evaluate_block(value, ctx, negated=not negated, op="or", depth=depth, seen=seen)
        if lowered == "nand":
            return _evaluate_block(value, ctx, negated=not negated, op="and", depth=depth, seen=seen)
        if lowered in ("or", "and"):
            return _evaluate_block(value, ctx, negated=negated, op=lowered, depth=depth, seen=seen)
        if lowered in _TRANSPARENT and not changes_scope(key):
            return _evaluate_block(value, ctx, negated=negated, op="and", depth=depth, seen=seen)
        return Truth.TRUE

    if not isinstance(value, Scalar):
        return Truth.TRUE
    text = value.value.lower()

    if key in CONDITION_KEYS:
        kind = CONDITION_KEYS[key]
        if ctx.defined and kind in ctx.defined and value.value not in ctx.defined[kind]:
            return Truth.TRUE if negated else Truth.FALSE
        return Truth.TRUE if negated else Condition(kind, value.value)
    if key == "has_country_flag" and not negated and value.value in ctx.named:
        return Condition("flag", value.value)
    if key == "has_country_flag" and not negated and ctx.flags is not None:
        setters = ctx.flags(value.value)
        return _combine("or", list(setters)) if setters else Truth.TRUE
    if lowered == "always" and text in ("yes", "no"):
        return Truth.TRUE if (text == "yes") != negated else Truth.FALSE
    if lowered == "is_ai" and text in ("yes", "no"):
        holds_for_player = (text == "no") != negated
        return Truth.TRUE if holds_for_player else Truth.FALSE

    if text in ("yes", "no"):
        asserted = (text == "yes") != negated
        if key in ctx.named:
            return Condition("trigger", key) if asserted else Truth.TRUE
        triggers = ctx.triggers
        if triggers is not None and key in triggers and key not in seen and depth < MAX_TRIGGER_DEPTH:
            body = triggers.get(key)
            return _evaluate_block(
                body, ctx, negated=not asserted, op="and", depth=depth + 1, seen=seen | {key}
            )
    return Truth.TRUE


def _alternatives(expr: Expr) -> list[Alternative]:
    if isinstance(expr, Condition):
        return [(expr,)]
    if isinstance(expr, tuple):
        op, children = expr
        if op == "or":
            return [alt for child in children for alt in _alternatives(child)]
        combos = itertools.islice(
            itertools.product(*(_alternatives(child) for child in children)), MAX_ALTERNATIVES
        )
        return [tuple(dict.fromkeys(c for part in combo for c in part)) for combo in combos]
    return []


def _groups(expr: Expr) -> list[tuple[Alternative, ...]]:
    """Reduce an expression to a conjunction of alternative-groups."""
    if isinstance(expr, Condition):
        return [((expr,),)]
    if isinstance(expr, tuple):
        op, children = expr
        if op == "and":
            return [group for child in children for group in _groups(child)]
        return [tuple(dict.fromkeys(_alternatives(expr)))]
    return []


def _trigger_name(item) -> str | None:
    key = getattr(item, "key", None)
    value = getattr(item, "value", None)
    if key and isinstance(value, Scalar) and value.value.lower() in ("yes", "no"):
        return key
    return None


def _zeroing_modifiers(weight_modifiers) -> list[Block]:
    """``modifier`` blocks that set ``factor = 0``."""
    found: list[Block] = []
    for group in weight_modifiers:
        for pair in group.pairs():
            if pair.key != "modifier" or not isinstance(pair.value, Block):
                continue
            factor = pair.value.get_first("factor")
            if isinstance(factor, Scalar):
                try:
                    if float(factor.value) == 0:
                        found.append(pair.value)
                except ValueError:
                    pass
    return found


def gates_for(
    record,
    *,
    triggers: TriggerIndex | None = None,
    named: frozenset[str] = frozenset(),
    unlock_routes: tuple[Route, ...] = (),
    flags: Callable[[str], tuple[Condition, ...] | None] | None = None,
    defined: dict[str, frozenset[str]] | None = None,
) -> tuple[Gate, ...]:
    """Every gate ``record`` declares, strongest kind first."""
    ctx = _Context(triggers=triggers, named=frozenset(named), flags=flags, defined=defined)
    gates: list[Gate] = []
    seen: set[tuple[Alternative, ...]] = set()

    def add(alternatives: tuple[Alternative, ...], kind: GateKind, via: str | None) -> None:
        if alternatives and alternatives not in seen:
            seen.add(alternatives)
            gates.append(Gate(alternatives=alternatives, kind=kind, via_trigger=via))

    if record.potential is not None:
        for item in record.potential.items:
            expr = _evaluate_item(item, ctx, negated=False, depth=0, seen=frozenset())
            via = _trigger_name(item) if not isinstance(expr, Truth) else None
            if via is not None and via not in (triggers or ()) and via not in named:
                via = None
            for group in _groups(expr):
                add(group, GateKind.REQUIRED, via)

    if record.is_undrawable:
        handed_out = tuple((c,) for c in route_conditions(unlock_routes) or ())
        add(handed_out, GateKind.GRANTED, None)

    for modifier in _zeroing_modifiers(record.weight_modifiers):
        # Drawable only when the modifier's conditions do NOT all hold.
        conditions = [item for item in modifier.items if getattr(item, "key", None) != "factor"]
        expr = _combine(
            "or",
            [_evaluate_item(i, ctx, negated=True, depth=0, seen=frozenset()) for i in conditions],
        )
        via = next((n for n in map(_trigger_name, conditions) if n in named), None)
        for group in _groups(expr):
            add(group, GateKind.UNDRAWABLE, via)

    return tuple(gates)


def route_conditions(
    routes: tuple[Route, ...], *, strict: bool = False
) -> tuple[Condition, ...] | None:
    """The perks and traditions behind a set of routes.

    ``strict`` returns ``None`` when any route reaches a player some other way
    -- an event, a research unlock -- because then no perk or tradition is
    required. Flags are read strictly. A technology's own handed-out gate is
    not: Colossi are also given to spawned fallen empires and the nanite
    weapons to anyone who salvages nanite ships, but the perk or tradition is
    still the route a player plans around.
    """
    if strict and any(r.kind not in (RouteKind.PERK, RouteKind.TRADITION) for r in routes):
        return None
    chosen = tuple(
        dict.fromkeys(
            Condition("perk" if r.kind is RouteKind.PERK else "tradition", r.key)
            for r in routes
            if r.kind in (RouteKind.PERK, RouteKind.TRADITION) and r.key
        )
    )
    return chosen or None


def build(
    records: dict,
    *,
    triggers: TriggerIndex | None = None,
    named: frozenset[str] = frozenset(),
    routes: dict[str, tuple[Route, ...]] | None = None,
    flags: Callable[[str], tuple[Condition, ...] | None] | None = None,
    defined: dict[str, frozenset[str]] | None = None,
) -> dict[str, tuple[Gate, ...]]:
    """Gates for every technology that declares any."""
    found: dict[str, tuple[Gate, ...]] = {}
    for key, record in records.items():
        gates = gates_for(
            record,
            triggers=triggers,
            named=named,
            unlock_routes=(routes or {}).get(key, ()),
            flags=flags,
            defined=defined,
        )
        if gates:
            found[key] = gates
    return found


def with_inherited(
    graph: TechGraph, own: dict[str, tuple[Gate, ...]]
) -> dict[str, tuple[Gate, ...]]:
    """Every technology's own gates plus those it inherits through what it needs.

    A gate passes forward along anything a technology cannot do without: a hard
    prerequisite, or a ``potential`` requirement on another technology. An OR
    group passes on only what *every* option carries, because a single ungated
    option is a way round. Inherited gates keep the kind they were declared
    with and name the technology that declares them, so the panel can say where
    the gate actually sits.

    A technology's own gate with the same alternatives outranks an inherited one.
    """
    # Imported here, not at module level: graph imports records, which imports
    # this module to derive gates during extraction.
    from .graph import EdgeKind

    effective: dict[str, tuple[Gate, ...]] = {}
    for key in graph.topological_order():
        gates = list(own.get(key, ()))
        held = {g.alternatives for g in gates}

        groups: dict[tuple, list[str]] = {}
        for edge in graph.incoming(key):
            if edge.kind is EdgeKind.ALTERNATIVE:
                groups.setdefault(("or", edge.group), []).append(edge.source)
            else:
                groups[("one", edge.source)] = [edge.source]

        for options in groups.values():
            common = {g.alternatives for g in effective.get(options[0], ())}
            for other in options[1:]:
                common &= {g.alternatives for g in effective.get(other, ())}
            for gate in effective.get(options[0], ()):
                if gate.alternatives not in common or gate.alternatives in held:
                    continue
                held.add(gate.alternatives)
                gates.append(
                    Gate(
                        alternatives=gate.alternatives,
                        kind=gate.kind,
                        via_trigger=gate.via_trigger,
                        inherited_from=gate.inherited_from or options[0],
                    )
                )

        if gates:
            effective[key] = tuple(gates)
    return effective


def badge_perks(gate: Gate, crisis_levels: dict[str, CrisisLevel] | None = None) -> tuple[str, ...]:
    """Perks whose art can stand for ``gate`` on a card.

    Its own perks, then the perk behind each crisis level it names:
    ``giga_tech_fe_megaworkshop_1`` is gated by Cosmogenesis Level 5, which
    only an empire that took Cosmogenesis ever reaches.
    """
    found = list(gate.perks)
    for condition in gate.conditions:
        if condition.kind == "crisis" and crisis_levels and condition.key in crisis_levels:
            found.append(crisis_levels[condition.key].perk)
    return tuple(dict.fromkeys(found))


def strongest(
    gates: tuple[Gate, ...], crisis_levels: dict[str, CrisisLevel] | None = None
) -> Gate | None:
    """The one gate a card has room to badge.

    Declared before inherited, then one with perk art -- a perk, or a crisis
    level on a perk's path -- then by kind.
    """
    if not gates:
        return None
    return min(
        gates,
        key=lambda g: (
            g.is_inherited,
            not badge_perks(g, crisis_levels),
            KIND_ORDER.index(g.kind),
        ),
    )


def defined_conditions(load_order: LoadOrder) -> dict[str, frozenset[str]]:
    """Every key the load order defines, per condition kind."""
    defined: dict[str, frozenset[str]] = {}
    for kind, directories in DEFINITION_DIRS.items():
        keys: set[str] = set()
        for directory in directories:
            blocks = merge_keys(resolve_files(load_order, directory)).blocks
            keys.update(blocks)
            # A tradition swap is a tradition in its own right as far as
            # `has_active_tradition` is concerned: The Vat accepts
            # `tr_genetics_finish_extra_traits`, which exists only as a swap of
            # `tr_genetics_finish`. Vanilla has 194 such swaps.
            for block in blocks.values():
                if not isinstance(block, Block):
                    continue
                for swap in block.get_all("tradition_swap"):
                    name = swap.get_first("name") if isinstance(swap, Block) else None
                    if isinstance(name, Scalar):
                        keys.add(name.value)
        defined[kind] = frozenset(keys)
    return defined


def condition_name(
    condition: Condition,
    localisation,
    names: dict[str, str],
    crisis_levels: dict[str, CrisisLevel] | None = None,
) -> str:
    """What a reader calls ``condition``.

    A configured name wins. Crisis levels are localised as a bare stage name --
    "Danger", "Calamity" -- that says nothing out of context, so they are named
    after the ascension perk that starts their path instead: "Cosmogenesis
    Level 5", "Galactic Nemesis Level 3".
    """
    if condition.key in names:
        return names[condition.key]
    if condition.kind == "crisis":
        found = (crisis_levels or {}).get(condition.key)
        if found is not None:
            label = localisation.get(found.perk) or found.perk.replace("_", " ").title()
            return f"{label} Level {found.level}"
        match = re.fullmatch(r"crisis_(?:(\w+)_)?level_(\d+)", condition.key)
        if match:
            path, level = match.groups()
            perk = f"ap_{path}" if path else "ap_become_the_crisis"
            label = localisation.get(perk) or (path or "crisis").replace("_", " ").title()
            return f"{label} Level {level}"
    return localisation.get(condition.key) or condition.key.replace("_", " ").title()


@dataclass(frozen=True)
class CrisisLevel:
    #: The ascension perk that sets an empire on this level's path.
    perk: str
    #: Position on that path, from 1.
    level: int


def crisis_levels(load_order: LoadOrder) -> dict[str, CrisisLevel]:
    """Crisis level -> the perk whose path it is on, and how far along.

    Neither half is written beside the level. A path in ``common/crisis_paths``
    lists its levels in order, and the perk that starts it says
    ``activate_crisis_progression = <path>``. Path and perk names do not line
    up -- ``behemoth_path`` belongs to ``ap_behemoths``, ``nemesis_path`` to
    ``ap_become_the_crisis`` -- so guessing one from the other is wrong.
    """
    perks = merge_keys(resolve_files(load_order, "common/ascension_perks")).blocks
    path_perks: dict[str, str] = {}
    for perk, block in perks.items():
        if isinstance(block, Block):
            for path in _scalar_values(block, "activate_crisis_progression"):
                path_perks.setdefault(path, perk)

    levels: dict[str, CrisisLevel] = {}
    paths = merge_keys(resolve_files(load_order, "common/crisis_paths")).blocks
    for path, block in paths.items():
        perk = path_perks.get(path)
        listed = block.get_first("levels") if isinstance(block, Block) else None
        if perk is None or not isinstance(listed, Block):
            continue
        for position, level in enumerate(listed.scalars(), start=1):
            levels.setdefault(level.value, CrisisLevel(perk=perk, level=position))
    return levels


def crisis_level_names(
    levels: dict[str, CrisisLevel], localisation, names: dict[str, str]
) -> dict[str, str]:
    """Crisis level -> what a card calls it."""
    return {
        key: condition_name(Condition("crisis", key), localisation, names, levels)
        for key in levels
    }


def _scalar_values(block: Block, key: str) -> list[str]:
    """Every scalar under ``key`` anywhere inside ``block``."""
    found: list[str] = []
    for pair in block.pairs():
        if isinstance(pair.value, Block):
            found.extend(_scalar_values(pair.value, key))
        elif pair.key == key and isinstance(pair.value, Scalar):
            found.append(pair.value.value)
    return found


def tradition_trees(load_order: LoadOrder) -> dict[str, str]:
    """Tradition -> the tradition category it belongs to.

    A tradition's own name rarely says which tree it is from -- the Nanotech
    tradition that hands out the nanite weapons is called "Flood of
    Supremacy" -- so the panel names the tree beside it.
    """
    merged = merge_keys(resolve_files(load_order, "common/tradition_categories"))
    trees: dict[str, str] = {}
    for category, block in merged.blocks.items():
        if not isinstance(block, Block):
            continue
        listed = block.get_first("traditions")
        members = [s.value for s in listed.scalars()] if isinstance(listed, Block) else []
        for key in ("adoption_bonus", "finish_bonus"):
            bonus = block.get_first(key)
            if isinstance(bonus, Scalar):
                members.append(bonus.value)
        for tradition in members:
            trees.setdefault(tradition, category)
    return trees


def perk_contexts(load_order: LoadOrder) -> dict[str, str]:
    """The empires each ascension perk is restricted to, where its ``potential`` says.

    Mechromancy's potential requires ``is_machine_empire = yes``, which is how a
    gate reading "Genetic Ascension or Mechromancy" can say which empires take
    which route without anyone writing it down.
    """
    merged = merge_keys(resolve_files(load_order, "common/ascension_perks"))
    contexts: dict[str, str] = {}
    for perk, block in merged.blocks.items():
        if not isinstance(block, Block):
            continue
        potential = block.get_first("potential")
        if not isinstance(potential, Block):
            continue
        for pair in potential.pairs():
            if isinstance(pair.value, Scalar):
                label = _EMPIRE_CONTEXTS.get((pair.key, pair.value.value.lower()))
                if label:
                    contexts[perk] = label
                    break
    return contexts


def summary(
    gates: dict[str, tuple[Gate, ...]],
    effective: dict[str, tuple[Gate, ...]] | None = None,
) -> str:
    def count(test) -> int:
        return sum(1 for g in gates.values() if any(test(x) for x in g))

    perks = {p for g in gates.values() for x in g for p in x.perks}
    text = (
        f"{len(gates)} technologies gated, {count(lambda x: x.perks)} by ascension perks "
        f"across {len(perks)} perk keys ({count(lambda x: x.is_required)} hard, "
        f"{count(lambda x: x.kind is GateKind.GRANTED)} handed out, "
        f"{count(lambda x: x.via_trigger)} via scripted triggers)"
    )
    if effective is not None:
        text += f"; {len(effective) - len(gates)} more inherit a gate"
    return text
