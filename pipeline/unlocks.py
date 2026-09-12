"""How a technology that is never drawn reaches a player.

About one in five technologies is never offered by the research pool. Each one
reaches an empire some other way, and the old answer -- "granted by event" for
all of them -- was wrong often enough to mislead:

* ``tech_subspace_drive`` is a starting technology for Eager Explorers;
* the Grand Archive bio-integration technologies appear as research options the
  moment Controlled Mutations is researched, through an ``on_tech_increased``
  event that a player never sees as an event;
* Colossi arrive through the Colossus Project perk, four hops away: the perk
  fires an event, the event enables a special project, and the project's
  completion event gives the technology;
* the thirteen observation insights come from watching a pre-FTL civilisation.

So this module indexes every effect in the load order that grants a technology,
records what contains it, and records what fires each container, then walks
from the grant back to whatever a player actually meets.

The index reads ``events/`` and all of ``common/``. Grants in foreign scopes are
skipped: ``create_country = { effect = { give_technology = ... } }`` equips a
freshly spawned fallen empire, not the player, and an effect under
``every_country`` hands the technology to everybody else.
"""

from __future__ import annotations

import enum
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from .clausewitz import Block, Scalar, parse_file
from .clausewitz.roundtrip import NON_SCRIPT_FILENAMES
from .loadorder import LoadOrder, resolve_files

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

DEFAULT_UNLOCKS_CONFIG = Path("config/unlocks.toml")

#: Effects that hand a technology to the country running them.
GRANT_EFFECTS = frozenset({"give_technology", "add_research_option", "research_technology"})

#: Effect keys that fire an event by id.
EVENT_KINDS = frozenset(
    {
        "event", "country_event", "planet_event", "fleet_event", "ship_event", "pop_event",
        "observer_event", "situation_event", "species_event", "first_contact_event",
        "leader_event", "archaeology_event", "system_event", "pop_faction_event",
        "espionage_operation_event", "astral_rift_event", "megastructure_event",
        "starbase_event", "army_event", "federation_event", "estate_event",
        "agreement_event", "deposit_event",
    }
)

#: Effect scopes that act on some other country. Deliberately narrower than the
#: trigger rule in :mod:`pipeline.triggers`: in an effect, ``owner`` and
#: ``from`` usually *are* the player, and ``random_list`` is a dice roll, not a
#: scope.
_FOREIGN_SCOPE = re.compile(
    r"^(every|random|any)_\w*(country|countries|empire|member|ally|allies|neighbor|relation|subject|overlord)"
)
_FOREIGN_KEYS = frozenset({"create_country"})

#: Root containers that only ever run for AI-controlled or spawned countries.
NON_PLAYER_ROOTS = frozenset({"fallen_empires"})

#: How far back from a grant to follow callers, and how many routes to keep per
#: grant. The corpus needs four hops at most; the bounds stop a dense web of
#: mutually firing events from exploding.
MAX_DEPTH = 12
MAX_ROUTES = 24

Container = tuple[str, str]


class RouteKind(enum.Enum):
    """What a player meets on the way to a technology."""

    #: Taking an ascension perk.
    PERK = "perk"
    #: Adopting a tradition.
    TRADITION = "tradition"
    #: A route matched by a rule in ``config/unlocks.toml``.
    TAGGED = "tagged"
    #: Researching another technology; the event behind it is invisible.
    RESEARCH = "research"
    #: The start of the game, as part of an origin or empire setup.
    START = "start"
    #: An event, special project, situation or anything else a player plays through.
    EVENT = "event"


@dataclass(frozen=True)
class Route:
    kind: RouteKind
    #: The perk, tradition or technology involved, or the configured tag.
    key: str | None
    #: The grant's container first, its root last.
    chain: tuple[Container, ...]


@dataclass
class Definition:
    """What one container grants and fires."""

    grants: list[str] = field(default_factory=list)
    calls: list[Container] = field(default_factory=list)
    #: Country flags this container sets. A technology whose potential tests a
    #: flag is gated by whatever sets it: the planet killers check
    #: ``colossus_project``, which only the Colossus Project's event sets.
    flags: list[str] = field(default_factory=list)
    #: For an event: technologies named in its trigger, ``last_increased_tech``
    #: first, so a research-driven event can say which research drives it.
    trigger_technologies: tuple[str, ...] = ()


@dataclass
class UnlockIndex:
    definitions: dict[Container, Definition] = field(default_factory=dict)
    unparsed: list[str] = field(default_factory=list)
    _callers: dict[Container, set[Container]] | None = None
    _granted_by: dict[str, list[Container]] | None = None
    _flag_setters: dict[str, list[Container]] | None = None

    @property
    def callers(self) -> dict[Container, set[Container]]:
        if self._callers is None:
            callers: dict[Container, set[Container]] = defaultdict(set)
            for container, definition in self.definitions.items():
                for callee in definition.calls:
                    if callee != container:
                        callers[callee].add(container)
            self._callers = dict(callers)
        return self._callers

    @property
    def granted_by(self) -> dict[str, list[Container]]:
        if self._granted_by is None:
            granted: dict[str, list[Container]] = defaultdict(list)
            for container, definition in self.definitions.items():
                for technology in definition.grants:
                    if container not in granted[technology]:
                        granted[technology].append(container)
            self._granted_by = dict(granted)
        return self._granted_by

    @property
    def flag_setters(self) -> dict[str, list[Container]]:
        if self._flag_setters is None:
            setters: dict[str, list[Container]] = defaultdict(list)
            for container, definition in self.definitions.items():
                for flag in definition.flags:
                    if container not in setters[flag]:
                        setters[flag].append(container)
            self._flag_setters = dict(setters)
        return self._flag_setters

    def chains(self, technology: str) -> list[tuple[Container, ...]]:
        """Every caller chain from a grant of ``technology`` back to a root."""
        return self.chains_from(self.granted_by.get(technology, ()))

    def chains_from(self, starts) -> list[tuple[Container, ...]]:
        """Every caller chain from each of ``starts`` back to a root."""
        found: list[tuple[Container, ...]] = []
        for start in starts:
            queue = deque([(start,)])
            kept = 0
            while queue and kept < MAX_ROUTES:
                chain = queue.popleft()
                callers = self.callers.get(chain[-1], ())
                fresh = [c for c in sorted(callers) if c not in chain]
                if not fresh or len(chain) >= MAX_DEPTH:
                    found.append(chain)
                    kept += 1
                    continue
                for caller in fresh:
                    queue.append(chain + (caller,))
        return found

    def summary(self) -> str:
        grants = sum(len(d.grants) for d in self.definitions.values())
        broken = f", {len(self.unparsed)} unparsed" if self.unparsed else ""
        return (
            f"{len(self.definitions)} effect containers, {grants} technology grants "
            f"across {len(self.granted_by)} technologies{broken}"
        )


@dataclass
class UnlockConfig:
    """The human-maintained half of unlock handling. See ``config/unlocks.toml``."""

    #: Condition key -> display name, for conditions the game names badly or not at all.
    names: dict[str, str] = field(default_factory=dict)
    #: Condition key -> the empires it applies to, e.g. "machine empires".
    contexts: dict[str, str] = field(default_factory=dict)
    #: ``"kind:key"`` container -> tag, for routes recognised by what they pass through.
    route_tags: dict[str, str] = field(default_factory=dict)
    #: Technology -> tag, applied over whatever the build derived. ``""`` clears it.
    technology_tags: dict[str, str] = field(default_factory=dict)


def load_config(path: Path | str = DEFAULT_UNLOCKS_CONFIG) -> UnlockConfig:
    path = Path(path)
    if not path.is_file():
        return UnlockConfig()
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    names: dict[str, str] = {}
    contexts: dict[str, str] = {}
    for key, value in data.get("conditions", {}).items():
        if isinstance(value, str):
            names[key] = value
        else:
            if "name" in value:
                names[key] = value["name"]
            if "context" in value:
                contexts[key] = value["context"]
    return UnlockConfig(
        names=names,
        contexts=contexts,
        route_tags={r["via"]: r["tag"] for r in data.get("route", [])},
        technology_tags=dict(data.get("technology", {})),
    )


# --------------------------------------------------------------------------
# Indexing
# --------------------------------------------------------------------------

_PARSED: dict[tuple[str, float], Block | None] = {}

#: An identifier used as a key: ``name =``.
_KEY_TOKEN = re.compile(rb"([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _parse(path: Path) -> Block | None:
    """Parse once per file version. The corpus tests build the index repeatedly."""
    key = (str(path), path.stat().st_mtime)
    if key not in _PARSED:
        try:
            _PARSED[key] = parse_file(path, allow_unclosed_blocks=True)
        except Exception:  # noqa: BLE001 - reported through UnlockIndex.unparsed
            _PARSED[key] = None
    return _PARSED[key]


def _is_foreign(key: str) -> bool:
    return key in _FOREIGN_KEYS or bool(_FOREIGN_SCOPE.match(key.lower()))


def _scan(node: Block, definition: Definition, effect_names: set[str]) -> None:
    for item in node.items:
        if isinstance(item, Block):
            _scan(item, definition, effect_names)
            continue
        key = getattr(item, "key", None)
        if key is None:
            continue
        value = item.value

        if key == "set_country_flag" and isinstance(value, Scalar):
            definition.flags.append(value.value)
            continue
        if key in GRANT_EFFECTS:
            technology = value.value if isinstance(value, Scalar) else None
            if isinstance(value, Block):
                tech = value.get_first("tech")
                technology = tech.value if isinstance(tech, Scalar) else None
            if technology:
                definition.grants.append(technology)
            continue
        if key in EVENT_KINDS and isinstance(value, Block):
            event_id = value.get_first("id")
            if isinstance(event_id, Scalar):
                definition.calls.append(("event", event_id.value))
            continue
        if key == "enable_special_project" and isinstance(value, Block):
            name = value.get_first("name")
            if isinstance(name, Scalar):
                definition.calls.append(("special_project", name.value))
            continue
        if key in effect_names:
            definition.calls.append(("scripted_effects", key))
        if isinstance(value, Block) and not _is_foreign(key):
            _scan(value, definition, effect_names)


def _trigger_technologies(event: Block) -> tuple[str, ...]:
    trigger = event.get_first("trigger")
    if not isinstance(trigger, Block):
        return ()
    found: list[str] = []
    for key in ("last_increased_tech", "has_technology"):
        for pair in trigger.pairs():
            if pair.key == key and isinstance(pair.value, Scalar) and pair.value.value not in found:
                found.append(pair.value.value)
    return tuple(found)


def build_index(load_order: LoadOrder) -> UnlockIndex:
    """Index every technology grant, and every call, in the load order.

    A later definition of the same container replaces an earlier one, as the
    engine does, so a mod overriding an event or a perk replaces its grants.
    """
    index = UnlockIndex()
    common = resolve_files(load_order, "common", recursive=True)
    events = resolve_files(load_order, "events")

    effect_names: set[str] = set()
    for resolved in common:
        if resolved.relative.split("/", 1)[0] != "scripted_effects":
            continue
        block = _parse(resolved.path)
        if block is not None:
            effect_names.update(pair.key for pair in block.pairs())
    # Most of common/ -- name lists, planet classes, graphical cultures -- can
    # neither grant a technology nor fire anything that does. Parsing it all
    # costs most of the build, so a file is only parsed when its text mentions
    # something that could matter. The effect-name test is a set intersection
    # over the file's keys; an alternation of a thousand names is slower than
    # just parsing.
    effect_tokens = {name.encode() for name in effect_names}

    def relevant(data: bytes) -> bool:
        if any(word in data for word in (b"technology", b"research_option", b"_event", b"special_project")):
            return True
        return not effect_tokens.isdisjoint(_KEY_TOKEN.findall(data))

    for resolved in events:
        if resolved.path.name in NON_SCRIPT_FILENAMES:
            continue
        block = _parse(resolved.path)
        if block is None:
            index.unparsed.append(f"events/{resolved.relative}")
            continue
        for pair in block.pairs():
            if pair.key not in EVENT_KINDS or not isinstance(pair.value, Block):
                continue
            event_id = pair.value.get_first("id")
            if not isinstance(event_id, Scalar):
                continue
            definition = Definition(trigger_technologies=_trigger_technologies(pair.value))
            _scan(pair.value, definition, effect_names)
            index.definitions[("event", event_id.value)] = definition

    for resolved in common:
        if resolved.path.name in NON_SCRIPT_FILENAMES:
            continue
        kind = resolved.relative.split("/", 1)[0]
        if kind == "technology":
            continue
        if kind not in ("scripted_effects", "on_actions") and not relevant(
            resolved.path.read_bytes()
        ):
            continue
        block = _parse(resolved.path)
        if block is None:
            index.unparsed.append(f"common/{resolved.relative}")
            continue
        for pair in block.pairs():
            if not isinstance(pair.value, Block):
                continue
            key = pair.key
            if kind == "special_projects":
                named = pair.value.get_first("key")
                if not isinstance(named, Scalar):
                    continue
                key = named.value
                kind_key = "special_project"
            else:
                kind_key = kind
            definition = Definition()
            _scan(pair.value, definition, effect_names)
            if kind == "on_actions":
                for list_key in ("events", "random_events"):
                    listed = pair.value.get_first(list_key)
                    if isinstance(listed, Block):
                        for scalar in listed.scalars():
                            if not re.fullmatch(r"-?\d+(\.\d+)?", scalar.value):
                                definition.calls.append(("event", scalar.value))
            if not (definition.grants or definition.calls):
                continue
            if kind == "on_actions" and (kind_key, key) in index.definitions:
                # On actions are additive: every file that names one adds its
                # events to the same hook. Replacing would sever every route
                # through all but the last file -- which is how the Grand
                # Archive's research unlocks first read as plain events.
                merged = index.definitions[(kind_key, key)]
                merged.grants.extend(definition.grants)
                merged.calls.extend(c for c in definition.calls if c not in merged.calls)
                continue
            index.definitions[(kind_key, key)] = definition
    return index


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def routes(technology: str, index: UnlockIndex, config: UnlockConfig) -> tuple[Route, ...]:
    """The distinct ways a player can come by ``technology``, strongest kind first."""
    return _routes(index.chains(technology), index, config)


def flag_routes(flag: str, index: UnlockIndex, config: UnlockConfig) -> tuple[Route, ...]:
    """The distinct ways a player's country comes to carry ``flag``."""
    return _routes(index.chains_from(index.flag_setters.get(flag, ())), index, config)


def _routes(chains, index: UnlockIndex, config: UnlockConfig) -> tuple[Route, ...]:
    found: dict[tuple[RouteKind, str | None], Route] = {}
    for chain in chains:
        route = _classify(chain, index, config)
        if route is not None:
            found.setdefault((route.kind, route.key), route)
    order = list(RouteKind)
    return tuple(sorted(found.values(), key=lambda r: (order.index(r.kind), r.key or "")))


def _classify(
    chain: tuple[Container, ...], index: UnlockIndex, config: UnlockConfig
) -> Route | None:
    root_kind, root_key = chain[-1]
    if root_kind in NON_PLAYER_ROOTS:
        return None
    for container in chain:
        tag = config.route_tags.get(f"{container[0]}:{container[1]}")
        if tag:
            return Route(RouteKind.TAGGED, tag, chain)
    if root_kind == "ascension_perks":
        return Route(RouteKind.PERK, root_key, chain)
    if root_kind == "traditions":
        return Route(RouteKind.TRADITION, root_key, chain)
    for position, container in enumerate(chain):
        if container[0] != "event" or position + 1 >= len(chain):
            continue
        if chain[position + 1] == ("on_actions", "on_tech_increased"):
            definition = index.definitions.get(container)
            if definition and definition.trigger_technologies:
                return Route(RouteKind.RESEARCH, definition.trigger_technologies[0], chain)
    if root_kind == "on_actions" and root_key.startswith("on_game_start"):
        return Route(RouteKind.START, None, chain)
    return Route(RouteKind.EVENT, None, chain)


#: Card tags, as a player reads them.
TAG_EVENT = "Event"
TAG_START = "Starting"


def tag_for(record, found: tuple[Route, ...], config: UnlockConfig) -> str | None:
    """The one word a card has room for about how its technology arrives.

    Only an undrawable technology gets one: anything the research pool offers
    needs no explanation. A route a card already shows another way -- a perk or
    tradition gate, or a plain research unlock -- takes no tag at all.
    """
    if record.key in config.technology_tags:
        return config.technology_tags[record.key] or None
    if not record.is_undrawable:
        return None
    if record.start_tech:
        return TAG_START
    kinds = {r.kind for r in found}
    if kinds & {RouteKind.PERK, RouteKind.TRADITION}:
        return None
    tagged = [r.key for r in found if r.kind is RouteKind.TAGGED]
    if tagged:
        return tagged[0]
    if RouteKind.RESEARCH in kinds:
        return None
    if RouteKind.EVENT in kinds:
        return TAG_EVENT
    if RouteKind.START in kinds:
        return TAG_START
    # No grant found anywhere. Most likely an event in a source whose events
    # are not indexed, so the old default still stands.
    return TAG_EVENT


def build(records: dict, index: UnlockIndex, config: UnlockConfig) -> dict[str, tuple[Route, ...]]:
    """Routes for every undrawable technology that has any."""
    found: dict[str, tuple[Route, ...]] = {}
    for key, record in records.items():
        if not record.is_undrawable:
            continue
        technology_routes = routes(key, index, config)
        if technology_routes:
            found[key] = technology_routes
    return found
