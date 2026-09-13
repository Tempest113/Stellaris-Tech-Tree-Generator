"""Empire profiles: what one kind of empire actually sees of the tree.

A profile fixes the handful of choices that decide which technologies exist
for an empire and what they are called: its authority, whether its founder
species is a machine, and whether it flies bio-ships, travels as nomads, is a
Wilderness, or keeps beasts. Everything else -- origins, civics, ethics,
ascension perks -- is left open.

Three-valued reading
--------------------
A trigger is read against a profile as ``TRUE``, ``FALSE`` or ``UNKNOWN``, with
Kleene's rules for ``AND``, ``OR`` and ``NOT``. Only ``FALSE`` hides anything:
a technology is gone for a profile only when its ``potential`` cannot hold for
any empire of that kind. ``UNKNOWN`` covers every choice the profile leaves
open, and so keeps the technology.

The facts a profile fixes are read at whatever level they are stated.
``is_hive_empire`` expands to ``has_authority = auth_hive_mind``, which the
profile answers directly. A few scripted triggers are answered by name instead,
because what they expand to cannot be read statically: ``country_uses_bio_ships``
is ``uses_ship_category = bio_ship`` behind ``exists = this``, and
``is_individual_machine`` looks into the founder species' scope.

Choices a profile leaves open are not simply unknown. An ascension perk, origin,
civic or tradition is *possible* for a profile or it is not, which its own
``potential`` and ``possible`` blocks say. Mechromancy's potential requires
``is_machine_empire = yes``, so for a biological empire the Vat's Mechromancy
route is ``FALSE`` and only Genetic Ascension remains.

All DLC is assumed owned: ``has_*_dlc`` is ``TRUE``.

A country flag nothing in the load order ever names, other than to test it, is
``FALSE``: nobody sets it. Gigastructures tests ``giga_one_planet_origin`` as a
hook for one-planet origin mods; without one loaded, Ring Segment's
no-habitables swap is settled by the profile alone.

Valid combinations
------------------
Not every combination of choices can be made at empire creation, and a profile
nobody can play would show a tree nobody gets. :func:`valid_profiles` keeps
only combinations the game's own definitions allow, reading the same blocks the
empire designer does: the Wilderness origin requires a hive mind, a bio-ship
set and a settled empire; a machine founder species rules out hive minds; and
every choice has to be reachable through some origin or civic that is possible
alongside the rest.
"""

from __future__ import annotations

import enum
import itertools
import re
from dataclasses import dataclass, field
from typing import Iterable

from .clausewitz import Block, Pair, Scalar
from .loadorder import LoadOrder, merge_keys, resolve_files
from .triggers import TriggerIndex

#: How deep scripted triggers and definition lookups may nest.
MAX_DEPTH = 10


class TV(enum.Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


def _truth(value: bool) -> TV:
    return TV.TRUE if value else TV.FALSE


def _not(value: TV) -> TV:
    if value is TV.TRUE:
        return TV.FALSE
    if value is TV.FALSE:
        return TV.TRUE
    return TV.UNKNOWN


def _and(values: Iterable[TV]) -> TV:
    result = TV.TRUE
    for value in values:
        if value is TV.FALSE:
            return TV.FALSE
        if value is TV.UNKNOWN:
            result = TV.UNKNOWN
    return result


def _or(values: Iterable[TV]) -> TV:
    result = TV.FALSE
    for value in values:
        if value is TV.TRUE:
            return TV.TRUE
        if value is TV.UNKNOWN:
            result = TV.UNKNOWN
    return result


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------

AUTHORITIES = ("regular", "hive", "machine")
AUTHORITY_KEYS = {"hive": "auth_hive_mind", "machine": "auth_machine_intelligence"}
#: "Regular" is the game's own word (``is_regular_empire``); a player picks
#: between individualist, hive and machine empires.
AUTHORITY_LABELS = {"regular": "Individualist", "hive": "Hive Mind", "machine": "Machine Intelligence"}

GESTALT_ETHIC = "ethic_gestalt_consciousness"
WILDERNESS_ORIGIN = "origin_wilderness"
BIO_SHIP_CATEGORY = "bio_ship"
#: Authorities no player can pick.
UNPLAYABLE_AUTHORITIES = frozenset({"auth_ancient_machine_intelligence"})

#: Toggles, in display order: (field, label).
TOGGLES = (
    ("machine_species", "Machine Species"),
    ("bio_ships", "Bio-Ships"),
    ("nomadic", "Nomadic"),
    ("wilderness", "Wilderness"),
    ("beastmasters", "Beastmasters"),
)


@dataclass(frozen=True)
class Profile:
    authority: str
    machine_species: bool = False
    bio_ships: bool = False
    nomadic: bool = False
    wilderness: bool = False
    beastmasters: bool = False

    @property
    def gestalt(self) -> bool:
        return self.authority != "regular"

    @property
    def key(self) -> str:
        on = [name.replace("_", "-") for name, _ in TOGGLES if getattr(self, name)]
        return "-".join([self.authority, *on])

    @property
    def label(self) -> str:
        on = [label for name, label in TOGGLES if getattr(self, name)]
        return ", ".join([AUTHORITY_LABELS[self.authority], *on])


# --------------------------------------------------------------------------
# Definitions a profile is read against
# --------------------------------------------------------------------------


@dataclass
class Definitions:
    triggers: TriggerIndex
    #: Civics and origins -- origins are civics with ``is_origin = yes``.
    civics: dict[str, Block] = field(default_factory=dict)
    perks: dict[str, Block] = field(default_factory=dict)
    #: Traditions by name, tradition swaps included under their own names.
    traditions: dict[str, Block] = field(default_factory=dict)
    #: Tradition -> every category holding it. A tradition is open when its own
    #: potential holds and any of its categories' does: Biogenesis ships a second
    #: Genetics tree that shares the finisher with the one it replaces.
    tradition_categories: dict[str, list[Block]] = field(default_factory=dict)
    species_classes: dict[str, Block] = field(default_factory=dict)
    #: Every playable non-gestalt authority.
    regular_authorities: frozenset[str] = frozenset()
    #: The civics ``is_beastmasters_empire`` accepts.
    beastmaster_civics: tuple[str, ...] = ()
    #: Every word the load order's script uses other than as the flag in a
    #: ``has_country_flag`` test. A flag outside it is never set. ``None`` when
    #: unknown, which leaves every flag open.
    flag_words: frozenset[str] | None = None


def load_definitions(load_order: LoadOrder, triggers: TriggerIndex) -> Definitions:
    def blocks(directory: str) -> dict[str, Block]:
        merged = merge_keys(resolve_files(load_order, directory)).blocks
        return {k: v for k, v in merged.items() if isinstance(v, Block)}

    traditions = blocks("common/traditions")
    for block in list(traditions.values()):
        for swap in block.get_all("tradition_swap"):
            name = swap.get_first("name") if isinstance(swap, Block) else None
            if isinstance(name, Scalar):
                traditions.setdefault(name.value, block)

    tradition_categories: dict[str, list[Block]] = {}
    for category in blocks("common/tradition_categories").values():
        listed = category.get_first("traditions")
        members = [s.value for s in listed.scalars()] if isinstance(listed, Block) else []
        for key in ("adoption_bonus", "finish_bonus"):
            bonus = category.get_first(key)
            if isinstance(bonus, Scalar):
                members.append(bonus.value)
        for member in members:
            tradition_categories.setdefault(member, []).append(category)
    for name, block in list(traditions.items()):
        # A swap shares its parent's categories.
        parent = next((k for k, v in traditions.items() if v is block and k in tradition_categories), None)
        if parent and name not in tradition_categories:
            tradition_categories[name] = tradition_categories[parent]

    authorities = blocks("common/governments/authorities")
    regular = frozenset(
        key
        for key in authorities
        if key not in AUTHORITY_KEYS.values() and key not in UNPLAYABLE_AUTHORITIES
    )

    beastmasters = triggers.get("is_beastmasters_empire")
    beastmaster_civics = tuple(
        pair.value.value
        for pair in _pairs_deep(beastmasters)
        if pair.key in ("has_valid_civic", "has_civic") and isinstance(pair.value, Scalar)
    ) if beastmasters is not None else ()

    return Definitions(
        triggers=triggers,
        flag_words=_flag_words(load_order),
        civics=blocks("common/governments/civics"),
        perks=blocks("common/ascension_perks"),
        traditions=traditions,
        tradition_categories=tradition_categories,
        species_classes=blocks("common/species_classes"),
        regular_authorities=regular,
        beastmaster_civics=beastmaster_civics,
    )


_FLAG_TEST = re.compile(rb"has_country_flag\s*=\s*\"?[A-Za-z0-9_.:-]+")
_WORD = re.compile(rb"[A-Za-z0-9_.:-]+")


def _flag_words(load_order: LoadOrder) -> frozenset[str]:
    """Words used anywhere in script except as a tested flag.

    Deliberately crude, so it errs towards "set": a flag named in a setter, an
    inline script parameter, a removal or a comment all count. Overridden files
    are read too, for the same reason.
    """
    words: set[bytes] = set()
    for source in load_order:
        for directory in ("common", "events"):
            root = source.root / directory
            if not root.is_dir():
                continue
            for path in root.rglob("*.txt"):
                words.update(_WORD.findall(_FLAG_TEST.sub(b" ", path.read_bytes())))
    return frozenset(w.decode("utf-8", "replace") for w in words)


def _pairs_deep(block: Block):
    for pair in block.pairs():
        if isinstance(pair.value, Block):
            yield from _pairs_deep(pair.value)
        else:
            yield pair


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------

#: Keys inside ``custom_tooltip`` that are text, not conditions.
_TOOLTIP_TEXT = {"text", "fail_text", "success_text", "title", "desc"}

#: Species scopes. Only the researching empire's own founder species is asked
#: about, which is what "Machine Species" fixes.
_SPECIES_SCOPES = {"founder_species", "owner_species"}
_MACHINE_TRAITS = {"trait_mechanical", "trait_machine_unit"}

#: Bio-ship shipsets. Treated as one choice: nothing a profile decides tells
#: the two apart.
_BIO_CULTURES = {"biogenesis_01", "biogenesis_02"}


class Evaluator:
    """Reads triggers, and the civic-style blocks of the empire designer, against one profile."""

    def __init__(self, profile: Profile, definitions: Definitions) -> None:
        self.profile = profile
        self.defs = definitions
        self._available: dict[tuple[str, str], TV] = {}
        self._pending: set[tuple[str, str]] = set()

    # -- facts -----------------------------------------------------------

    def _machine_founder(self) -> TV:
        """Whether the founder species is a machine.

        Unknown for a regular empire that did not start as one: synthetic
        ascension can still make it one.
        """
        p = self.profile
        if p.authority == "machine" or p.machine_species:
            return TV.TRUE
        if p.authority == "hive":
            return TV.FALSE
        return TV.UNKNOWN

    def _bound(self, key: str) -> TV | None:
        """Scripted triggers a profile answers by name."""
        p = self.profile
        if key == "country_uses_bio_ships":
            return _truth(p.bio_ships)
        if key == "is_beastmasters_empire":
            return _truth(p.beastmasters)
        if key == "is_wilderness_empire":
            return _truth(p.wilderness)
        if key == "is_regular_empire":
            return _truth(not p.gestalt)
        if key == "is_individual_machine":
            return TV.FALSE if p.gestalt else self._machine_founder()
        return None

    def authority(self, value: str) -> TV:
        p = self.profile
        if p.gestalt:
            return _truth(value == AUTHORITY_KEYS[p.authority])
        return TV.UNKNOWN if value in self.defs.regular_authorities else TV.FALSE

    def ethic(self, value: str) -> TV:
        if value == GESTALT_ETHIC:
            return _truth(self.profile.gestalt)
        return TV.FALSE if self.profile.gestalt else TV.UNKNOWN

    def origin(self, value: str) -> TV:
        if self.profile.wilderness:
            return _truth(value == WILDERNESS_ORIGIN)
        if value == WILDERNESS_ORIGIN:
            return TV.FALSE
        return self.available("origin", value)

    def civic(self, value: str) -> TV:
        beastmasters = self.defs.beastmaster_civics
        if value in beastmasters:
            if not self.profile.beastmasters:
                return TV.FALSE
            open_ = [c for c in beastmasters if self.available("civic", c) is not TV.FALSE]
            if value not in open_:
                return TV.FALSE
            return TV.TRUE if len(open_) == 1 else TV.UNKNOWN
        return self.available("civic", value)

    # -- triggers --------------------------------------------------------

    def trigger(self, block: Block | None, depth: int = 0) -> TV:
        """A trigger block: an implicit ``AND`` of its items."""
        if block is None:
            return TV.TRUE
        return _and(self._item(item, depth) for item in block.items)

    def _item(self, item, depth: int) -> TV:
        if isinstance(item, Block):
            return self.trigger(item, depth)
        key = getattr(item, "key", None)
        if key is None:
            return TV.UNKNOWN
        value = item.value
        lowered = key.lower()

        if isinstance(value, Block):
            if lowered in ("not", "nor"):
                return _not(_or(self._item(i, depth) for i in value.items))
            if lowered == "nand":
                return _not(self.trigger(value, depth))
            if lowered == "or":
                return _or(self._item(i, depth) for i in value.items)
            if lowered == "and":
                return self.trigger(value, depth)
            if lowered == "hidden_trigger":
                return self.trigger(value, depth)
            if lowered == "custom_tooltip":
                return _and(
                    self._item(i, depth)
                    for i in value.items
                    if getattr(i, "key", "").lower() not in _TOOLTIP_TEXT
                )
            if lowered in _SPECIES_SCOPES:
                return _and(self._species_item(i) for i in value.items)
            return TV.UNKNOWN

        if not isinstance(value, Scalar):
            return TV.UNKNOWN
        text = value.value
        yes_no = text.lower() in ("yes", "no")
        asserted = text.lower() == "yes"

        def polar(result: TV) -> TV:
            return result if asserted else _not(result)

        if lowered == "always" and yes_no:
            return _truth(asserted)
        if lowered == "is_ai" and yes_no:
            return _truth(not asserted)
        if lowered == "host_has_dlc" or (lowered.startswith("has_") and lowered.endswith("_dlc")):
            return polar(TV.TRUE) if yes_no else TV.TRUE
        if lowered == "has_country_flag" and self.defs.flag_words is not None:
            return TV.UNKNOWN if text in self.defs.flag_words else TV.FALSE
        if lowered == "is_nomadic" and yes_no:
            return polar(_truth(self.profile.nomadic))
        if lowered == "uses_ship_category":
            return _truth(self.profile.bio_ships) if text == BIO_SHIP_CATEGORY else TV.UNKNOWN
        if lowered == "has_authority":
            return self.authority(text)
        if lowered == "has_ethic":
            return self.ethic(text)
        if lowered == "has_origin":
            return self.origin(text)
        if lowered in ("has_civic", "has_valid_civic"):
            return self.civic(text)
        if lowered == "has_ascension_perk":
            return self.available("perk", text)
        if lowered in ("has_tradition", "has_active_tradition"):
            return self.available("tradition", text)

        if yes_no:
            bound = self._bound(key)
            if bound is not None:
                return polar(bound)
            body = self.defs.triggers.get(key)
            if body is not None and depth < MAX_DEPTH:
                return polar(self.trigger(body, depth + 1))
        return TV.UNKNOWN

    def _species_item(self, item) -> TV:
        key = getattr(item, "key", None)
        value = getattr(item, "value", None)
        if not isinstance(value, Scalar):
            return TV.UNKNOWN
        if key == "is_archetype" and value.value == "MACHINE":
            return self._machine_founder()
        if key == "has_trait" and value.value in _MACHINE_TRAITS:
            return self._machine_founder()
        return TV.UNKNOWN

    # -- the empire designer's blocks -------------------------------------

    def designer(self, block: Block | None, depth: int = 0) -> TV:
        """A civic, origin or species class ``potential`` / ``possible`` block.

        These are not triggers. ``authority = { value = auth_hive_mind }`` names
        what the empire has chosen, and ``AND = { limit = { ... } ... }`` applies
        its rules only where the limit holds.
        """
        if block is None:
            return TV.TRUE
        return _and(self._designer_item(item, depth) for item in block.items)

    def _designer_item(self, item, depth: int) -> TV:
        key = getattr(item, "key", None)
        value = getattr(item, "value", None)
        if key is None:
            return TV.UNKNOWN
        lowered = key.lower()
        if isinstance(value, Block):
            if lowered == "and":
                limit = value.get_first("limit")
                rest = [i for i in value.items if getattr(i, "key", None) != "limit"]
                body = _and(self._designer_item(i, depth) for i in rest)
                if isinstance(limit, Block):
                    return _or([_not(self.trigger(limit, depth)), body])
                return body
            if lowered in ("or", "nor", "not"):
                inner = _or(self._designer_item(i, depth) for i in value.items)
                return inner if lowered == "or" else _not(inner)
            test = {
                "authority": self.authority,
                "ethics": self.ethic,
                "origin": self.origin,
                "civics": self.civic,
                "graphical_culture": lambda v: (
                    _truth(self.profile.bio_ships) if v in _BIO_CULTURES else TV.UNKNOWN
                ),
                "species_archetype": lambda v: self._machine_founder() if v == "MACHINE" else TV.UNKNOWN,
            }.get(lowered)
            if test is not None:
                return self._values(value, test)
            return TV.UNKNOWN
        if lowered == "text":
            return TV.TRUE
        # Plain triggers are allowed here too: `is_nomadic = no`.
        return self._item(item, depth)

    def _values(self, block: Block, test) -> TV:
        """A ``value = x`` set inside ``authority = { ... }`` and the like."""
        results: list[TV] = []
        for item in block.items:
            key = getattr(item, "key", None)
            value = getattr(item, "value", None)
            if key is None or key == "text":
                continue
            lowered = key.lower()
            if key == "value" and isinstance(value, Scalar):
                results.append(test(value.value))
            elif isinstance(value, Block) and lowered in ("or", "nor", "not", "and"):
                inner_items = value.items
                if lowered == "and":
                    results.append(self._values(value, test))
                else:
                    any_of = _or(
                        self._values(Block(items=[i]), test)
                        for i in inner_items
                        if getattr(i, "key", None) != "text"
                    )
                    results.append(any_of if lowered == "or" else _not(any_of))
        return _and(results)

    # -- choices left open ------------------------------------------------

    def available(self, kind: str, key: str) -> TV:
        """Whether ``key`` can be chosen by this profile: ``FALSE`` or ``UNKNOWN``.

        Never ``TRUE``: a profile does not choose perks, origins, civics or
        traditions, so it cannot say an empire has one.
        """
        cache_key = (kind, key)
        if cache_key in self._available:
            return self._available[cache_key]
        if cache_key in self._pending:
            return TV.UNKNOWN
        self._pending.add(cache_key)
        try:
            result = self._availability(kind, key)
        finally:
            self._pending.discard(cache_key)
        result = TV.FALSE if result is TV.FALSE else TV.UNKNOWN
        self._available[cache_key] = result
        return result

    def _availability(self, kind: str, key: str) -> TV:
        if kind in ("civic", "origin"):
            block = self.defs.civics.get(key)
            if block is None:
                return TV.FALSE
            return _and(
                [
                    *(self.trigger(b) for b in block.get_all("playable") if isinstance(b, Block)),
                    *(self.designer(b) for b in block.get_all("potential") if isinstance(b, Block)),
                    *(self.designer(b) for b in block.get_all("possible") if isinstance(b, Block)),
                ]
            )
        if kind == "perk":
            block = self.defs.perks.get(key)
            if block is None:
                return TV.FALSE
            return _and(self.trigger(b) for b in block.get_all("potential") if isinstance(b, Block))
        if kind == "tradition":
            block = self.defs.traditions.get(key)
            if block is None:
                return TV.FALSE
            own = _and(self.trigger(b) for b in block.get_all("potential") if isinstance(b, Block))
            categories = self.defs.tradition_categories.get(key)
            if not categories:
                return own
            return _and(
                [
                    own,
                    _or(
                        _and(self.trigger(b) for b in c.get_all("potential") if isinstance(b, Block))
                        for c in categories
                    ),
                ]
            )
        if kind == "species_class":
            block = self.defs.species_classes.get(key)
            if block is None:
                return TV.FALSE
            return _and(
                [
                    *(self.trigger(b) for b in block.get_all("playable") if isinstance(b, Block)),
                    *(self.designer(b) for b in block.get_all("possible") if isinstance(b, Block)),
                ]
            )
        return TV.UNKNOWN


# --------------------------------------------------------------------------
# Which profiles exist
# --------------------------------------------------------------------------


def valid_profiles(definitions: Definitions) -> tuple[Profile, ...]:
    """Every profile an empire can actually be created as."""
    found: list[Profile] = []
    for authority in AUTHORITIES:
        for flags in itertools.product((False, True), repeat=len(TOGGLES)):
            profile = Profile(authority, **dict(zip((n for n, _ in TOGGLES), flags)))
            if achievable(profile, definitions):
                found.append(profile)
    return tuple(found)


def achievable(profile: Profile, definitions: Definitions) -> bool:
    """Whether every choice ``profile`` makes is reachable at once."""
    if profile.machine_species and profile.gestalt:
        # `is_individual_machine` is a non-gestalt empire of machines; a machine
        # intelligence is machine already, and says so another way.
        return False
    ev = Evaluator(profile, definitions)
    defs = definitions

    if profile.machine_species:
        machine_classes = [
            key
            for key, block in defs.species_classes.items()
            if block.scalar_text("archetype") == "MACHINE"
        ]
        if all(ev.available("species_class", c) is TV.FALSE for c in machine_classes):
            return False

    origins = [key for key, block in defs.civics.items() if block.scalar_text("is_origin") == "yes"]
    if profile.wilderness:
        if ev._availability("origin", WILDERNESS_ORIGIN) is TV.FALSE:
            return False
    elif all(
        ev.available("origin", o) is TV.FALSE for o in origins if o != WILDERNESS_ORIGIN
    ):
        return False

    if profile.beastmasters and all(
        ev.available("civic", c) is TV.FALSE for c in defs.beastmaster_civics
    ):
        return False
    return True


def disabled(records: dict, definitions: Definitions, profiles: tuple[Profile, ...]) -> frozenset[str]:
    """Technologies whose ``potential`` no valid profile can meet.

    Nobody can research these, so they have no card in any view. Gigastructures
    retires technologies with ``always = no`` -- Aeternite Weaponry, the Stellar
    Ring -- and disabled the Frame World origin for 4.0, taking its eight
    technologies with it. With every DLC owned, the pre-Ancient Relics
    Archaeology Lab (``has_ancrel = no``) is gone as well.
    """
    evaluators = [Evaluator(p, definitions) for p in profiles]
    return frozenset(
        key
        for key, record in records.items()
        if record.potential is not None
        and all(ev.trigger(record.potential) is TV.FALSE for ev in evaluators)
    )


# --------------------------------------------------------------------------
# What each profile sees
# --------------------------------------------------------------------------


@dataclass
class Views:
    """Per-slot visibility and presentation across every profile.

    Masks carry one bit per profile, in the order of ``profiles``.
    """

    profiles: tuple[Profile, ...]
    evaluators: tuple[Evaluator, ...]
    #: Slot index -> profiles in which the slot is not shown.
    hidden: list[int]
    #: Slot index -> ``(swap name, profiles)``: the in-place swap a slot is
    #: presented as, where the profile settles it.
    presentations: list[list[tuple[str, int]]]
    #: Technology -> profiles for which it does not exist.
    technology_hidden: dict[str, int]

    def condition_impossible(self, condition, crisis_levels) -> int:
        """Profiles for which a gate condition can never be met."""
        mask = 0
        for bit, ev in enumerate(self.evaluators):
            if condition_truth(ev, condition, crisis_levels) is TV.FALSE:
                mask |= 1 << bit
        return mask


def condition_truth(ev: Evaluator, condition, crisis_levels) -> TV:
    """Whether a gate condition can hold for the evaluator's profile."""
    kind, key = condition.kind, condition.key
    if kind == "perk":
        return ev.available("perk", key)
    if kind == "tradition":
        return ev.available("tradition", key)
    if kind == "origin":
        return ev.origin(key)
    if kind == "civic":
        return ev.civic(key)
    if kind == "crisis":
        level = crisis_levels.get(key)
        return ev.available("perk", level.perk) if level else TV.UNKNOWN
    if kind == "trigger":
        return ev._item(Pair(key, "=", Scalar("yes")), 0)
    return TV.UNKNOWN


def compute_views(
    graph,
    slots,
    definitions: Definitions,
    profiles: tuple[Profile, ...],
    routes_for=None,
    debris: frozenset[str] | set[str] = frozenset(),
) -> Views:
    """Which slots each profile shows, and under which name.

    A technology is gone for a profile when its ``potential`` is ``FALSE``, or
    when it is ordinary research and some prerequisite it cannot do without is
    gone: it would never be offered.

    A technology the research pool never offers the profile -- never offered to
    anyone, or zero-weighted by a modifier that holds for this profile -- is
    gone too when nothing a player can reach hands it out: every way in is an
    ascension perk or tradition the profile cannot take, or there is no way in
    at all. The Birch World comes only from Vast Expanses, which no nomad can
    have; the second fallen empire buildings are drawn only with Cosmogenesis,
    which no Wilderness can take. A technology a ship component needs stays,
    being learned from debris.

    Swaps are taken in order and the first whose trigger holds applies, so a
    slot shows when the swap that puts it there *can* be the first to apply. A
    slot a profile cannot settle -- Ring Segment's no-habitables swap turns on
    the origin -- stays, alongside the default.
    """
    from .layout import CRISIS_GROUP

    from .gates import _zeroing_modifiers
    from .unlocks import RouteKind

    records = graph.records
    evaluators = tuple(Evaluator(p, definitions) for p in profiles)
    routes_for = routes_for or (lambda key: ())

    def unreachable(ev: Evaluator, ways_in) -> bool:
        return all(
            r.kind in (RouteKind.PERK, RouteKind.TRADITION)
            and ev.available("perk" if r.kind is RouteKind.PERK else "tradition", r.key) is TV.FALSE
            for r in ways_in
        )

    # Per technology, per profile: does it exist, and how does each swap apply?
    exists: dict[str, int] = {}
    applies: dict[str, list[list[TV]]] = {}
    still: dict[str, list[TV]] = {}
    for key, record in records.items():
        gone = 0
        per_profile: list[list[TV]] = []
        defaults: list[TV] = []
        zeroing = _zeroing_modifiers(record.weight_modifiers)
        for bit, ev in enumerate(evaluators):
            if record.potential is not None and ev.trigger(record.potential) is TV.FALSE:
                gone |= 1 << bit
            elif record.is_undrawable:
                ways_in = routes_for(key)
                if ways_in and unreachable(ev, ways_in):
                    gone |= 1 << bit
            elif key not in debris and any(
                _and(
                    ev._item(item, 0)
                    for item in modifier.items
                    if getattr(item, "key", None) != "factor"
                )
                is TV.TRUE
                for modifier in zeroing
            ):
                if unreachable(ev, routes_for(key)):
                    gone |= 1 << bit
            remaining = TV.TRUE
            outcomes: list[TV] = []
            for swap in record.swaps:
                holds = ev.trigger(swap.trigger) if swap.trigger is not None else TV.UNKNOWN
                outcomes.append(_and([remaining, holds]))
                remaining = _and([remaining, _not(holds)])
            per_profile.append(outcomes)
            defaults.append(remaining)
        exists[key] = gone
        applies[key] = per_profile
        still[key] = defaults

    # Ordinary research behind a prerequisite that is gone is gone too.
    hidden_tech = dict(exists)
    for key in graph.topological_order():
        record = records[key]
        if record.is_undrawable:
            continue
        for group in record.prerequisites:
            options = [o for o in group.options if o in records]
            if not options:
                continue
            everywhere = ~0
            for option in options:
                everywhere &= hidden_tech[option]
            hidden_tech[key] |= everywhere

    hidden: list[int] = []
    presentations: list[list[tuple[str, int]]] = []
    for slot in slots:
        record = records[slot.technology]
        swaps = record.swaps
        relocating = {s.name for s in record.relocating_swaps}
        in_crisis = slot.row.area == CRISIS_GROUP
        mask = hidden_tech[slot.technology]
        shown_as: dict[str, int] = {}

        for bit in range(len(profiles)):
            outcomes = applies[slot.technology][bit]
            if in_crisis:
                applied = TV.TRUE
                own = list(range(len(swaps)))
            elif slot.swap is None:
                own = [
                    i
                    for i, s in enumerate(swaps)
                    if s.name not in relocating or record.swap_placement(s) == record.placement
                ]
                applied = _or([still[slot.technology][bit], *(outcomes[i] for i in own)])
            else:
                variant = record.swap_named(slot.swap)
                destination = record.swap_placement(variant)
                own = [
                    i
                    for i, s in enumerate(swaps)
                    if s.name in relocating and record.swap_placement(s) == destination
                ]
                applied = _or(outcomes[i] for i in own)
            if applied is TV.FALSE:
                mask |= 1 << bit
                continue
            if slot.swap is None:
                chosen = next((swaps[i].name for i in own if outcomes[i] is TV.TRUE), None)
                if chosen is not None:
                    shown_as[chosen] = shown_as.get(chosen, 0) | (1 << bit)

        hidden.append(mask)
        presentations.append(sorted(shown_as.items()))

    return Views(
        profiles=profiles,
        evaluators=evaluators,
        hidden=hidden,
        presentations=presentations,
        technology_hidden=hidden_tech,
    )
