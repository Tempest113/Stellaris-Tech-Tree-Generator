"""Which empires begin the game with a technology already researched.

Two things hand out technologies at the start. A technology with
``start_tech = yes`` is researched for every empire whose
``starting_potential`` holds. And an ``on_game_start`` event can grant one:
beastmaster empires get the Grand Archive bio-integration technologies this
way, and the Broken Shackles and Payback origins get Xeno-Linguistics.

A profile fixes too little to answer outright. Scientific Method is researched
at the start unless ``is_low_tech_start``, which holds for two origins and four
exploration civics, none of which a profile decides. So each profile is read
as an *ordinary* empire of its kind -- an origin and civics that no condition
names -- and then each origin and civic the conditions do name is tried in turn
to find the exceptions: "researched at the start, unless the empire has the
Broken Shackles or Payback origin, or the Eager Explorers civic".

As everywhere else, only ``FALSE`` rules anything out. An ordinary empire starts
with a technology unless a condition cannot hold for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .clausewitz import Block, Scalar
from .profiles import TV, Evaluator, Profile, _chain_truth
from .unlocks import RouteKind

#: An origin no condition names, for the ordinary empire.
ORDINARY_ORIGIN = ""
#: How deep scripted triggers are followed when collecting what they name.
MAX_DEPTH = 10


@dataclass
class Start:
    """How one profile begins with one technology."""

    #: An ordinary empire of the profile starts with it.
    ordinary: bool
    #: ``(kind, key)`` of origins and civics that change that: ones that take it
    #: away when ``ordinary``, ones that grant it when not.
    exceptions: list[tuple[str, str]] = field(default_factory=list)


def named_choices(blocks, triggers) -> tuple[list[str], list[str]]:
    """Origins and civics the blocks test for, through scripted triggers too."""
    origins: list[str] = []
    civics: list[str] = []
    seen: set[str] = set()

    def walk(block: Block, depth: int) -> None:
        for item in block.items:
            if isinstance(item, Block):
                walk(item, depth)
                continue
            key = getattr(item, "key", None)
            value = getattr(item, "value", None)
            if key is None:
                continue
            if isinstance(value, Block):
                walk(value, depth)
                continue
            if not isinstance(value, Scalar):
                continue
            lowered = key.lower()
            if lowered == "has_origin" and value.value not in origins:
                origins.append(value.value)
            elif lowered in ("has_civic", "has_valid_civic") and value.value not in civics:
                civics.append(value.value)
            elif value.value.lower() in ("yes", "no") and key not in seen and depth < MAX_DEPTH:
                body = triggers.get(key)
                if body is not None:
                    seen.add(key)
                    walk(body, depth + 1)

    for block in blocks:
        if block is not None:
            walk(block, 0)
    return origins, civics


def _start_truth(ev: Evaluator, record, routes, index) -> TV:
    """Whether an empire read by ``ev`` begins with the technology."""
    ways: list[TV] = []
    if record.start_tech:
        ways.append(ev.trigger(record.starting_potential))
    for route in routes:
        if route.kind is not RouteKind.START:
            continue
        for chain in route.chains or (route.chain,):
            ways.append(_chain_truth(ev, chain, record.key, index))
    if not ways:
        return TV.FALSE
    result = TV.FALSE
    for way in ways:
        if way is TV.TRUE:
            return TV.TRUE
        if way is TV.UNKNOWN:
            result = TV.UNKNOWN
    return result


def _blocks(record, routes, index) -> list[Block]:
    """Every condition that decides whether the technology is had at the start."""
    found: list[Block] = []
    if record.start_tech and record.starting_potential is not None:
        found.append(record.starting_potential)
    for route in routes:
        if route.kind is not RouteKind.START:
            continue
        for chain in route.chains or (route.chain,):
            for position, container in enumerate(chain):
                definition = index.definitions.get(container)
                if definition is None:
                    continue
                if definition.trigger is not None:
                    found.append(definition.trigger)
                guards = (
                    definition.grant_guards.get(record.key)
                    if position == 0
                    else definition.call_guards.get(chain[position - 1])
                )
                for occurrence in guards or ():
                    found.extend(occurrence)
    return found


def starts(
    record, routes, profile: Profile, definitions, index, impossible: frozenset[str] = frozenset()
) -> Start | None:
    """How ``profile`` begins with the technology, or ``None`` when no empire of it can.

    ``impossible`` are technologies the profile can never have.
    """
    if not record.start_tech and not any(r.kind is RouteKind.START for r in routes):
        return None

    def reader(**choices) -> Evaluator:
        return Evaluator(profile, definitions, impossible_technologies=impossible, **choices)

    ordinary = reader(origin=ORDINARY_ORIGIN, civics=frozenset())
    base = _start_truth(ordinary, record, routes, index) is not TV.FALSE

    # Only an origin or civic an empire of this kind can actually take is an exception.
    open_ = reader()
    origins, civics = named_choices(_blocks(record, routes, index), definitions.triggers)
    exceptions: list[tuple[str, str]] = []
    for origin in origins:
        if open_.origin(origin) is TV.FALSE:
            continue
        chosen = reader(origin=origin, civics=frozenset())
        if (_start_truth(chosen, record, routes, index) is not TV.FALSE) != base:
            exceptions.append(("origin", origin))
    for civic in civics:
        if open_.civic(civic) is TV.FALSE:
            continue
        chosen = reader(origin=ORDINARY_ORIGIN, civics=frozenset({civic}))
        if (_start_truth(chosen, record, routes, index) is not TV.FALSE) != base:
            exceptions.append(("civic", civic))
    if not base and not exceptions:
        return None
    return Start(ordinary=base, exceptions=exceptions)
