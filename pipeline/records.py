"""The canonical technology record.

**Everything downstream reads technologies through this module and nothing
else.** That is the whole point of it.

Several technology fields exist only *after* `inline_script` expansion and
`@variable` resolution. Fifty Gigastructures technologies have no ``area``,
``tier``, ``levels``, ``prerequisites`` or ``potential`` at all until their
template is expanded. A component that reaches for a raw parsed block instead of
a record here does not fail -- it quietly receives a body with almost nothing in
it and draws a confident wrong conclusion. That exact mistake produced three
independent silent bugs in the previous attempt at this project, each found by
accident rather than by a test.

So: build records once, through :func:`extract`, and pass them around. The raw
tree stays reachable through :attr:`TechnologyRecord.raw` for the few places
that genuinely need it (trigger evaluation wants the real syntax tree), but it
is deliberately awkward to get at and always the *expanded* form.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from .clausewitz import Block, Scalar
from .clausewitz.nodes import Node
from .gates import PerkGate
from .gates import build as build_perk_gates
from .gates import build_grants as build_perk_grants
from .icons import IconIndex, IconRef
from .icons import build_index as build_icon_index
from .inline_scripts import Expander, ExpansionStats
from .inline_scripts import build_index as build_script_index
from .triggers import TriggerIndex
from .triggers import build_index as build_trigger_index
from .loadorder import KeyOrigin, LoadOrder, Source, merge_keys, resolve_files
from .variables import VariableTable, collect_variables

#: The only three research areas Stellaris has ever had.
AREAS = ("physics", "society", "engineering")

#: Boolean-ish field values. Stellaris accepts yes/no.
_TRUE = {"yes", "true"}
_FALSE = {"no", "false"}


class RecordError(RuntimeError):
    """A technology could not be turned into a usable record."""


def _flag(text: str | None) -> bool | None:
    """Parse a tri-state yes/no field. ``None`` means the field was absent.

    ``is_rare`` genuinely uses all three states: 228 vanilla technologies say
    yes, 9 say no, the rest say nothing.
    """
    if text is None:
        return None
    lowered = text.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    return None


def _number(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class PrerequisiteGroup:
    """One requirement: a single technology, or a choice between several.

    The prerequisite grammar is exactly *AND of (literal | OR of literals)* --
    no negation, no nesting deeper than one level, no variables. Every group in
    a technology's list must be satisfied; a group with more than one option is
    satisfied by any of them.
    """

    options: tuple[str, ...]

    @property
    def is_choice(self) -> bool:
        return len(self.options) > 1

    @property
    def only(self) -> str:
        if len(self.options) != 1:
            raise ValueError(f"group has {len(self.options)} options, not 1")
        return self.options[0]

    def __iter__(self) -> Iterator[str]:
        return iter(self.options)


@dataclass(frozen=True)
class TechnologySwap:
    """An alternative presentation of a technology for some empires.

    A swap can change the displayed name, the icon, the effects, and -- this is
    the part that matters for layout -- the ``area`` and ``category``, moving
    the technology into a different research tab entirely.
    """

    name: str
    trigger: Block | None
    inherit_icon: bool
    inherit_effects: bool
    area: str | None
    categories: tuple[str, ...]

    @property
    def declares_placement(self) -> bool:
        """True when the swap states an ``area`` or ``category`` of its own.

        Deliberately *not* the same question as "does this move the
        technology". Four swaps declare a placement identical to their parent's
        -- the bio psionic weapons restate ``society/psionics``, which is where
        they already are. Treating declaration as relocation would create union
        layout slots for technologies that never move.

        Use :meth:`TechnologyRecord.relocating_swaps` for the real question; it
        can compare against the parent, which a swap alone cannot.
        """
        return self.area is not None or bool(self.categories)


@dataclass(frozen=True)
class TechnologyRecord:
    """A fully expanded, variable-resolved technology."""

    key: str
    area: str
    categories: tuple[str, ...]
    tier: int

    prerequisites: tuple[PrerequisiteGroup, ...] = ()
    swaps: tuple[TechnologySwap, ...] = ()

    cost: float | None = None
    weight: float | None = None
    #: Present iff the technology is repeatable. ``-1`` means unlimited.
    levels: int | None = None
    cost_per_level: float | None = None

    is_rare: bool | None = None
    is_dangerous: bool = False
    is_reverse_engineerable: bool | None = None
    start_tech: bool = False
    is_insight: bool = False
    gateway: str | None = None
    declared_icon: str | None = None
    feature_flags: tuple[str, ...] = ()
    weight_groups: tuple[str, ...] = ()

    #: Trigger deciding whether this technology exists for an empire at all.
    potential: Block | None = None
    #: May repeat: a template-emitted block plus one written beside the call.
    weight_modifiers: tuple[Block, ...] = ()
    modifiers: tuple[Block, ...] = ()
    ai_weight: Block | None = None
    starting_potential: Block | None = None
    prereqfor_desc: tuple[Block, ...] = ()

    #: Which source won this key, and which lost.
    origin: KeyOrigin | None = None
    #: The expanded syntax tree. Needed by trigger evaluation; avoid elsewhere.
    raw: Block | None = None

    # -- derived -----------------------------------------------------------

    @property
    def is_repeatable(self) -> bool:
        """Membership rule: the technology declares ``levels`` at all.

        Not ``levels < 0``. That sign test finds only the unlimited ones and
        misses every capped repeatable, which is how the previous attempt
        counted 76 instead of 88.
        """
        return self.levels is not None

    @property
    def is_unlimited(self) -> bool:
        return self.levels is not None and self.levels < 0

    @property
    def level_cap(self) -> int | None:
        """Maximum level for a capped repeatable; ``None`` if unlimited or N/A."""
        if self.levels is None or self.levels < 0:
            return None
        return self.levels

    @property
    def is_weightless(self) -> bool:
        """Never drawn from the research pool; granted by event or ascension.

        99 vanilla and 86 Gigastructures technologies work this way. In a
        Gigastructures build this is a far more informative signal than
        ``is_rare``, which 74% of its technologies set.
        """
        return self.weight == 0

    @property
    def is_undrawable(self) -> bool:
        """Never offered for research on its own merits, whatever the empire.

        Wider than :attr:`is_weightless`. Vanilla writes the same intent a
        second way, a declared weight with an unconditional ``factor = 0`` at
        the top of ``weight_modifier``: ``tech_dyson_sphere``,
        ``tech_ring_world`` and ``tech_matter_decompressor`` all do, and each
        reaches an empire only because something -- here the Galactic Wonders
        perk -- adds it as a research option.

        A conditional zero inside a ``modifier`` block does not count; that
        restricts the draw for some empires, which is what
        :mod:`pipeline.gates` reads.
        """
        if self.weight == 0:
            return True
        for group in self.weight_modifiers:
            for pair in group.pairs():
                if pair.key == "factor" and isinstance(pair.value, Scalar):
                    if _number(pair.value.value) == 0:
                        return True
        return False

    @property
    def category(self) -> str | None:
        """The single category. Cardinality is 1 everywhere in the corpus."""
        return self.categories[0] if self.categories else None

    @property
    def prerequisite_keys(self) -> tuple[str, ...]:
        """Every technology named as a prerequisite, choices flattened.

        Use for reachability. For layout and for drawing edges, iterate
        :attr:`prerequisites` instead so alternatives stay distinguishable --
        flattening them into the required list is what silently corrupted
        layout, faction derivation and rendering scope in the previous attempt.
        """
        return tuple(option for group in self.prerequisites for option in group.options)

    @property
    def placement(self) -> tuple[str, tuple[str, ...]]:
        """Where this technology sits by default: ``(area, categories)``."""
        return self.area, self.categories

    def swap_placement(self, swap: TechnologySwap) -> tuple[str, tuple[str, ...]]:
        """Where ``swap`` puts the technology. Unstated fields inherit."""
        return swap.area or self.area, swap.categories or self.categories

    @property
    def relocating_swaps(self) -> tuple[TechnologySwap, ...]:
        """Swaps that genuinely move the technology to a different row.

        Nine such swaps exist across eight technologies, and they are the whole
        reason layout needs union slots. Four further swaps declare a placement
        identical to their parent's and must not be counted here.
        """
        return tuple(
            swap
            for swap in self.swaps
            if swap.declares_placement and self.swap_placement(swap) != self.placement
        )

    @property
    def placement_variants(
        self,
    ) -> tuple[tuple[tuple[str, tuple[str, ...]], TechnologySwap | None], ...]:
        """Every distinct row this technology can occupy, with the swap that puts it there.

        The default placement comes first, paired with ``None``. A slot needs
        to know its swap because the swap is what the empire in that row
        actually sees: ``tech_ring_world`` relocated to society/biology is
        "Artificial Deconstructor Ecologies" with art of its own, not "Ring
        Segment". Where two swaps share a destination the first one wins.
        """
        seen: list[tuple[str, tuple[str, ...]]] = [self.placement]
        variants: list[tuple[tuple[str, tuple[str, ...]], TechnologySwap | None]] = [
            (self.placement, None)
        ]
        for swap in self.relocating_swaps:
            destination = self.swap_placement(swap)
            if destination not in seen:
                seen.append(destination)
                variants.append((destination, swap))
        return tuple(variants)

    @property
    def placements(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Every distinct row this technology can occupy, default first.

        One layout slot is precomputed per entry, and exactly one is visible for
        any given empire.
        """
        return tuple(destination for destination, _ in self.placement_variants)

    def swap_named(self, name: str) -> TechnologySwap | None:
        """The relocating swap called ``name``, if there is one."""
        return next((s for s in self.relocating_swaps if s.name == name), None)

    def icon(self, icons: IconIndex) -> IconRef:
        return icons.technology(self.key, self.declared_icon)


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------


def _scalars(block: Block | None) -> tuple[str, ...]:
    return tuple(s.value for s in block.scalars()) if block else ()


def _parse_prerequisites(block: Block | None) -> tuple[PrerequisiteGroup, ...]:
    """Read a prerequisites block into required groups.

    Bare entries are single-option groups; ``OR = { ... }`` becomes a
    multi-option one. Order is preserved because it is the only thing
    distinguishing otherwise identical groups.
    """
    if block is None:
        return ()

    groups: list[PrerequisiteGroup] = []
    for item in block.items:
        if isinstance(item, Scalar):
            groups.append(PrerequisiteGroup((item.value,)))
            continue
        key = getattr(item, "key", None)
        if key and key.upper() == "OR":
            options = _scalars(item.value if isinstance(item.value, Block) else None)
            if options:
                groups.append(PrerequisiteGroup(options))
    return tuple(groups)


def _parse_swaps(block: Block) -> tuple[TechnologySwap, ...]:
    swaps: list[TechnologySwap] = []
    for raw in block.get_all("technology_swap"):
        if not isinstance(raw, Block):
            continue
        name = raw.scalar_text("name")
        if not name:
            continue
        swaps.append(
            TechnologySwap(
                name=name,
                trigger=raw.block_at("trigger"),
                # Both default to inheriting when the field is absent.
                inherit_icon=_flag(raw.scalar_text("inherit_icon")) is not False,
                inherit_effects=_flag(raw.scalar_text("inherit_effects")) is not False,
                area=raw.scalar_text("area"),
                categories=_scalars(raw.block_at("category")),
            )
        )
    return tuple(swaps)


def _parse_cost(block: Block) -> float | None:
    """Read ``cost``, which has two shapes and is sometimes absent entirely.

    Ten vanilla technologies write it as a block whose ``factor`` carries the
    number; five omit it altogether.
    """
    value = block.get_first("cost")
    if value is None:
        return None
    if isinstance(value, Scalar):
        return _number(value.value)
    return _number(value.scalar_text("factor"))


def _blocks(block: Block, key: str) -> tuple[Block, ...]:
    return tuple(v for v in block.get_all(key) if isinstance(v, Block))


def build_record(key: str, block: Block, *, origin: KeyOrigin | None = None) -> TechnologyRecord:
    """Turn one expanded, variable-resolved block into a record.

    Fails loudly on a missing ``area`` or ``tier`` rather than defaulting. A
    technology without them is almost always a sign that expansion did not run,
    and a silent default would bury that.
    """
    area = block.scalar_text("area")
    if area is None:
        raise RecordError(
            f"{key}: no 'area'. If this is a template-driven technology, "
            "inline_script expansion has not run."
        )
    if area not in AREAS:
        raise RecordError(f"{key}: unknown area {area!r}; expected one of {AREAS}")

    tier_text = block.scalar_text("tier")
    tier = _number(tier_text)
    if tier is None:
        raise RecordError(
            f"{key}: no usable 'tier' (found {tier_text!r}). If this is a "
            "template-driven technology, inline_script expansion has not run; "
            "if it is an unresolved @variable, the variable table is incomplete."
        )

    levels = _number(block.scalar_text("levels"))

    return TechnologyRecord(
        key=key,
        area=area,
        categories=_scalars(block.block_at("category")),
        tier=int(tier),
        prerequisites=_parse_prerequisites(block.block_at("prerequisites")),
        swaps=_parse_swaps(block),
        cost=_parse_cost(block),
        weight=_number(block.scalar_text("weight")),
        levels=int(levels) if levels is not None else None,
        cost_per_level=_number(block.scalar_text("cost_per_level")),
        is_rare=_flag(block.scalar_text("is_rare")),
        is_dangerous=_flag(block.scalar_text("is_dangerous")) is True,
        is_reverse_engineerable=_flag(block.scalar_text("is_reverse_engineerable")),
        start_tech=_flag(block.scalar_text("start_tech")) is True,
        is_insight=_flag(block.scalar_text("is_insight")) is True,
        gateway=block.scalar_text("gateway"),
        declared_icon=block.scalar_text("icon"),
        feature_flags=_scalars(block.block_at("feature_flags")),
        weight_groups=_scalars(block.block_at("weight_groups")),
        potential=block.block_at("potential"),
        weight_modifiers=_blocks(block, "weight_modifier"),
        modifiers=_blocks(block, "modifier"),
        ai_weight=block.block_at("ai_weight"),
        starting_potential=block.block_at("starting_potential"),
        prereqfor_desc=_blocks(block, "prereqfor_desc"),
        origin=origin,
        raw=block,
    )


@dataclass
class Extraction:
    """Everything stage 1 produces, with the provenance to explain it."""

    technologies: dict[str, TechnologyRecord] = field(default_factory=dict)
    variables: VariableTable | None = None
    icons: IconIndex | None = None
    expansion: ExpansionStats | None = None
    load_order: LoadOrder | None = None
    triggers: TriggerIndex | None = None
    #: Technology -> the ascension perks whose effects add it as a research
    #: option. Includes perks that merely offer a drawable technology early;
    #: :mod:`pipeline.gates` decides which grants are gates.
    perk_grants: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Ascension perks gating a technology, for the technologies that declare
    #: any. Inherited gates need the graph and are derived later, by
    #: :func:`pipeline.gates.with_inherited`.
    perk_gates: dict[str, tuple[PerkGate, ...]] = field(default_factory=dict)
    #: Non-fatal problems worth surfacing in the build report.
    problems: list[str] = field(default_factory=list)

    def __iter__(self) -> Iterator[TechnologyRecord]:
        return iter(self.technologies.values())

    def __len__(self) -> int:
        return len(self.technologies)

    def __getitem__(self, key: str) -> TechnologyRecord:
        return self.technologies[key]

    def get(self, key: str) -> TechnologyRecord | None:
        return self.technologies.get(key)

    @property
    def repeatables(self) -> list[TechnologyRecord]:
        return [r for r in self if r.is_repeatable]

    def dangling_prerequisites(self) -> dict[str, list[str]]:
        """Prerequisites naming technologies absent from this load order.

        Expected, not exceptional: Gigastructures names four ACOT technologies
        that only exist when ACOT is loaded.
        """
        missing: dict[str, list[str]] = {}
        for record in self:
            absent = [k for k in record.prerequisite_keys if k not in self.technologies]
            if absent:
                missing[record.key] = absent
        return missing

    def by_area(self) -> dict[str, list[TechnologyRecord]]:
        grouped: dict[str, list[TechnologyRecord]] = {area: [] for area in AREAS}
        for record in self:
            grouped[record.area].append(record)
        return grouped

    def summary(self) -> str:
        dangling = self.dangling_prerequisites()
        return (
            f"{len(self.technologies)} technologies "
            f"({len(self.repeatables)} repeatable), "
            f"{sum(len(v) for v in dangling.values())} dangling prerequisites "
            f"across {len(dangling)} technologies"
        )


def extract(
    load_order: LoadOrder,
    *,
    reference_load_order: LoadOrder | None = None,
) -> Extraction:
    """Run the whole extraction and return canonical records.

    This is the only supported way to obtain technologies. It resolves the load
    order, expands inline scripts, resolves variables and indexes icons in the
    one order that produces correct results.

    ``reference_load_order`` supplies variables from sources that are read for
    reference only and whose own overrides must not apply -- how the published
    build borrows ``@acot_tier*cost*`` without letting ACOT's 761 technologies
    and 4 vanilla overrides into the tree.
    """
    raw = merge_keys(resolve_files(load_order, "common/technology"))
    variables = collect_variables(
        load_order, extra=raw.inline_variables, extra_sources=reference_load_order
    )
    expander = Expander(build_script_index(load_order))
    icons = build_icon_index(load_order)
    triggers = build_trigger_index(load_order)

    extraction = Extraction(
        variables=variables,
        icons=icons,
        expansion=expander.stats,
        load_order=load_order,
        triggers=triggers,
    )

    for key, block in raw.blocks.items():
        if not isinstance(block, Block):
            extraction.problems.append(f"{key}: not a block; skipped")
            continue
        expanded = variables.substitute(expander.expand(block))
        extraction.technologies[key] = build_record(
            key, expanded, origin=raw.origins.get(key)
        )

    # After the loop: a gate is read off the expanded record, and inline script
    # expansion is what puts `potential` and `weight_modifier` there at all.
    extraction.perk_grants = build_perk_grants(load_order)
    extraction.perk_gates = build_perk_gates(
        extraction.technologies, triggers, extraction.perk_grants
    )

    if expander.stats.missing_scripts:
        extraction.problems.append(
            "inline scripts referenced but not found: "
            + ", ".join(sorted(expander.stats.missing_scripts))
        )
    if expander.stats.unsupplied_parameters:
        extraction.problems.append(
            "inline script parameters never supplied: "
            + ", ".join(sorted(expander.stats.unsupplied_parameters))
        )
    if variables.missing:
        extraction.problems.append(
            "unresolved @variables: " + ", ".join(sorted(variables.missing))
        )

    return extraction
