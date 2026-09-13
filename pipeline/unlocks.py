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
import fnmatch
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
#: ``add_tech_progress`` counts: it puts the technology in front of the empire
#: part-researched, which for a technology never drawn is how it arrives -- the
#: Precursor "secrets" technologies and several Extreme Frontiers ones work so.
GRANT_EFFECTS = frozenset(
    {"give_technology", "add_research_option", "research_technology", "add_tech_progress"}
)

#: A ``$PARAM$`` placeholder, as a whole value.
_PLACEHOLDER = re.compile(r"^\$(\w+)(?:\|[^$]*)?\$$")

#: Effect keys that fire an event by id.
EVENT_KINDS = frozenset(
    {
        "event", "country_event", "planet_event", "fleet_event", "ship_event", "pop_event",
        # carrier_event alone is over a thousand vanilla events, among them the
        # colony event that offers the Null Void Beam.
        "carrier_event", "colony_event", "pop_group_event", "bypass_event",
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

#: ``common/`` directories that cannot grant a technology or fire an event.
#: Scripted triggers are the one that matters: they mention event ids in tests
#: like ``has_seen_event``, which must not read as firing them.
NON_EFFECT_DIRS = frozenset({"scripted_triggers", "script_values", "technology"})

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
    #: Reaching a level on a crisis path, such as Cosmogenesis Level 3.
    CRISIS = "crisis"
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
    #: The perk, tradition, crisis level or technology involved, or the
    #: configured tag.
    key: str | None
    #: The grant's container first, its root last.
    chain: tuple[Container, ...]
    #: For a tagged route, the position of the rule that tagged it. Rules are
    #: tried in file order, so an earlier rule is a stronger statement.
    rank: int = 0


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
    #: Calls that pass parameters, as ``(callee, {PARAM: value})``. A generic
    #: effect like ``add_tech_option_or_research_effect`` grants ``$TECH$``; the
    #: technology is known only at the call site, so the grant is resolved there.
    param_calls: list[tuple[Container, dict[str, str]]] = field(default_factory=list)


@dataclass
class UnlockIndex:
    definitions: dict[Container, Definition] = field(default_factory=dict)
    unparsed: list[str] = field(default_factory=list)
    #: Technologies that some ship component names as a prerequisite. A
    #: technology no effect grants but a component requires is usually learned
    #: from the debris of ships that carry it -- space monster weapons, mostly.
    component_prerequisites: set[str] = field(default_factory=set)
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
                    if "$" in technology:
                        continue  # a parameter, resolved at the call site
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
    #: ``("kind:key" pattern, tag)`` in precedence order, for routes recognised
    #: by what they pass through. Patterns are shell-style: ``anomalies:*``.
    route_tags: list[tuple[str, str]] = field(default_factory=list)
    #: Tag for an undrawable technology no effect grants but a ship component
    #: requires: learned from debris.
    debris_tag: str = "Debris"
    #: ``("kind:key" pattern, PARAM)``: calls that hand out the technology named
    #: in PARAM even though the callee never grants ``$PARAM$`` itself. Astral
    #: rift rewards are offered this way and granted by a later event that reads
    #: a flag, which static reading cannot follow.
    parameter_grants: list[tuple[str, str]] = field(default_factory=list)
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
        route_tags=[(r["via"], r["tag"]) for r in data.get("route", [])],
        debris_tag=data.get("debris", {}).get("tag", "Debris"),
        parameter_grants=[(g["via"], g["param"]) for g in data.get("parameter_grant", [])],
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


def _scan(
    node: Block,
    definition: Definition,
    effect_names: set[str],
    event_ids: frozenset[str] | set[str] = frozenset(),
) -> None:
    """Record what ``node`` grants, sets and fires.

    Anything whose value is a known event id counts as firing that event. The
    engine fires events from far more places than ``country_event = { id = ...
    }``: an anomaly's ``on_success = distar.50``, an archaeological site
    stage's ``event = giga_blokkat.3321``, a special project's
    ``EVENT_ID = grand_archive.10030`` passed to an inline script. Matching on
    the id itself catches all of them without enumerating the forms.
    """
    for item in node.items:
        if isinstance(item, Block):
            _scan(item, definition, effect_names, event_ids)
            continue
        if isinstance(item, Scalar):
            if item.value in event_ids:
                definition.calls.append(("event", item.value))
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
        if key == "inline_script":
            script, params = _inline_call(value)
            if script:
                callee = ("inline_scripts", script)
                definition.calls.append(callee)
                if params:
                    definition.param_calls.append((callee, params))
                for param in params.values():
                    if param in event_ids:
                        definition.calls.append(("event", param))
            continue
        if key in effect_names:
            callee = ("scripted_effects", key)
            definition.calls.append(callee)
            if isinstance(value, Block):
                params = {
                    p.key: p.value.value for p in value.pairs() if isinstance(p.value, Scalar)
                }
                if params:
                    definition.param_calls.append((callee, params))
                for param in params.values():
                    if param in event_ids:
                        definition.calls.append(("event", param))
                continue
        if isinstance(value, Scalar):
            if value.value in event_ids:
                definition.calls.append(("event", value.value))
            continue
        if isinstance(value, Block) and not _is_foreign(key):
            _scan(value, definition, effect_names, event_ids)


def _inline_call(value) -> tuple[str | None, dict[str, str]]:
    """``inline_script = path`` or ``inline_script = { script = path PARAM = v }``."""
    if isinstance(value, Scalar):
        return value.value, {}
    if isinstance(value, Block):
        script = value.get_first("script")
        if isinstance(script, Scalar):
            params = {
                p.key: p.value.value
                for p in value.pairs()
                if p.key != "script" and isinstance(p.value, Scalar)
            }
            return script.value, params
    return None, {}


def _resolve_parameterised(index: UnlockIndex, config: UnlockConfig | None = None) -> None:
    """Attribute each parameterised grant to the call site that names the technology."""
    declared = config.parameter_grants if config else []

    def grants_of(callee: Container, params: dict[str, str], depth: int) -> list[str]:
        definition = index.definitions.get(callee)
        if definition is None or depth > MAX_DEPTH:
            return []
        found: list[str] = []
        for grant in definition.grants:
            match = _PLACEHOLDER.match(grant)
            if match and match.group(1) in params and "$" not in params[match.group(1)]:
                found.append(params[match.group(1)])
        for inner, inner_params in definition.param_calls:
            passed = {}
            for name, value in inner_params.items():
                match = _PLACEHOLDER.match(value)
                passed[name] = params.get(match.group(1), value) if match else value
            found.extend(grants_of(inner, passed, depth + 1))
        return found

    for definition in index.definitions.values():
        for callee, params in definition.param_calls:
            technologies = grants_of(callee, params, 0)
            name = f"{callee[0]}:{callee[1]}"
            technologies += [
                params[param]
                for pattern, param in declared
                if param in params and fnmatch.fnmatchcase(name, pattern)
            ]
            for technology in technologies:
                if technology not in definition.grants:
                    definition.grants.append(technology)


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


def build_index(load_order: LoadOrder, config: UnlockConfig | None = None) -> UnlockIndex:
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

    event_ids: set[str] = set()
    for resolved in events:
        if resolved.path.name in NON_SCRIPT_FILENAMES:
            continue
        block = _parse(resolved.path)
        if block is None:
            continue
        for pair in block.pairs():
            if pair.key in EVENT_KINDS and isinstance(pair.value, Block):
                event_id = pair.value.get_first("id")
                if isinstance(event_id, Scalar):
                    event_ids.add(event_id.value)
            elif pair.key == "inline_script":
                _, params = _inline_call(pair.value)
                event_ids.update(v for k, v in params.items() if k.endswith("EVENT_ID"))
    # Most of common/ -- name lists, planet classes, graphical cultures -- can
    # neither grant a technology nor fire anything that does. Parsing it all
    # costs most of the build, so a file is only parsed when its text mentions
    # something that could matter. The effect-name test is a set intersection
    # over the file's keys; an alternation of a thousand names is slower than
    # just parsing.
    effect_tokens = {name.encode() for name in effect_names}

    def relevant(data: bytes) -> bool:
        if any(word in data for word in (b"technology", b"research_option", b"event", b"EVENT", b"on_success", b"special_project")):
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
            if pair.key == "inline_script":
                # An event written as an inline script: the Covenant marks are
                # granted by one, its id passed in as EVENT_ID.
                script, params = _inline_call(pair.value)
                event_id_value = params.get("EVENT_ID")
                if script and event_id_value:
                    definition = Definition(
                        calls=[("inline_scripts", script)],
                        param_calls=[(("inline_scripts", script), params)],
                    )
                    # The same script often takes the id of the event it fires
                    # next, as CONFIRM_EVENT_ID and the like.
                    definition.calls.extend(
                        ("event", value)
                        for name, value in params.items()
                        if name != "EVENT_ID" and value in event_ids
                    )
                    index.definitions[("event", event_id_value)] = definition
                continue
            if pair.key not in EVENT_KINDS or not isinstance(pair.value, Block):
                continue
            event_id = pair.value.get_first("id")
            if not isinstance(event_id, Scalar):
                continue
            definition = Definition(trigger_technologies=_trigger_technologies(pair.value))
            _scan(pair.value, definition, effect_names, event_ids)
            index.definitions[("event", event_id.value)] = definition

    for resolved in common:
        if resolved.path.name in NON_SCRIPT_FILENAMES:
            continue
        kind = resolved.relative.split("/", 1)[0]
        if kind in NON_EFFECT_DIRS:
            continue
        if kind == "component_templates":
            block = _parse(resolved.path)
            if block is not None:
                _collect_prerequisites(block, index.component_prerequisites)
            continue
        if kind == "inline_scripts":
            block = _parse(resolved.path)
            if block is None:
                index.unparsed.append(f"common/{resolved.relative}")
                continue
            # An inline script is a whole-file body, addressed by its path. When
            # that body is an event definition, what matters is inside it.
            script = resolved.relative.split("/", 1)[1].rsplit(".", 1)[0]
            definition = Definition()
            for item in block.items:
                key = getattr(item, "key", None)
                if key in EVENT_KINDS and isinstance(item.value, Block):
                    _scan(item.value, definition, effect_names, event_ids)
                else:
                    _scan(Block(items=[item]), definition, effect_names, event_ids)
            if definition.grants or definition.calls or definition.flags:
                index.definitions[("inline_scripts", script)] = definition
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
            if key == "inline_script":
                # A definition written as an inline script. The Grand Archive's
                # mutation projects are special projects defined this way, with
                # their completion event passed in as EVENT_ID.
                script, params = _inline_call(pair.value)
                if not script:
                    continue
                named = params.get("KEY") or params.get("NAME") or script
                kind_key = "special_project" if kind == "special_projects" else kind
                definition = Definition(
                    calls=[("inline_scripts", script)],
                    param_calls=[(("inline_scripts", script), params)] if params else [],
                )
                definition.calls.extend(
                    ("event", value) for value in params.values() if value in event_ids
                )
                index.definitions[(kind_key, named)] = definition
                continue
            if kind == "special_projects":
                named = pair.value.get_first("key")
                if not isinstance(named, Scalar):
                    continue
                key = named.value
                kind_key = "special_project"
            else:
                kind_key = kind
            definition = Definition()
            _scan(pair.value, definition, effect_names, event_ids)
            if kind == "on_actions":
                for list_key in ("events", "random_events"):
                    listed = pair.value.get_first(list_key)
                    if isinstance(listed, Block):
                        for scalar in listed.scalars():
                            if not re.fullmatch(r"-?\d+(\.\d+)?", scalar.value):
                                definition.calls.append(("event", scalar.value))
            if not (definition.grants or definition.calls or definition.flags):
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
    _resolve_parameterised(index, config)
    return index


def _collect_prerequisites(block: Block, into: set[str]) -> None:
    for item in block.items:
        if isinstance(item, Block):
            _collect_prerequisites(item, into)
            continue
        key = getattr(item, "key", None)
        if key == "prerequisites" and isinstance(item.value, Block):
            into.update(s.value for s in item.value.scalars())
        elif isinstance(item.value, Block):
            _collect_prerequisites(item.value, into)


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
            kept = found.get((route.kind, route.key))
            if kept is None or route.rank < kept.rank:
                found[(route.kind, route.key)] = route
    order = list(RouteKind)
    return tuple(sorted(found.values(), key=lambda r: (order.index(r.kind), r.key or "")))


def _classify(
    chain: tuple[Container, ...], index: UnlockIndex, config: UnlockConfig
) -> Route | None:
    root_kind, root_key = chain[-1]
    if root_kind in NON_PLAYER_ROOTS:
        return None
    if root_kind == "ascension_perks":
        return Route(RouteKind.PERK, root_key, chain)
    if root_kind == "traditions":
        return Route(RouteKind.TRADITION, root_key, chain)
    # Research before configured rules: a chain that reaches the player on
    # researching something is a research unlock whatever events it passes
    # through on the way.
    for position, container in enumerate(chain):
        if container[0] != "event" or position + 1 >= len(chain):
            continue
        if chain[position + 1] == ("on_actions", "on_tech_increased"):
            definition = index.definitions.get(container)
            if definition and definition.trigger_technologies:
                return Route(RouteKind.RESEARCH, definition.trigger_technologies[0], chain)
    names = [f"{kind}:{key}" for kind, key in chain]
    for rank, (pattern, tag) in enumerate(config.route_tags):
        if any(fnmatch.fnmatchcase(name, pattern) for name in names):
            return Route(RouteKind.TAGGED, tag, chain, rank)
    # After every rule: a crisis level belongs to one path's campaign, so a
    # route open to everyone that the rules recognise says more.
    if root_kind == "crisis_levels":
        return Route(RouteKind.CRISIS, root_key, chain)
    if root_kind == "on_actions" and root_key.startswith("on_game_start"):
        return Route(RouteKind.START, None, chain)
    return Route(RouteKind.EVENT, None, chain)


#: Card tags, as a player reads them.
TAG_EVENT = "Event"
TAG_START = "Starting"
#: A crisis level with no name to hand.
TAG_CRISIS = "Crisis Level"
#: Never drawn, and nothing the build can read hands it out.
TAG_UNKNOWN = "Unknown"


def tag_for(
    record,
    found: tuple[Route, ...],
    config: UnlockConfig,
    index: UnlockIndex | None = None,
    crisis_names: dict[str, str] | None = None,
) -> str | None:
    """The one word a card has room for about how its technology arrives.

    Only an undrawable technology gets one: anything the research pool offers
    needs no explanation. A route a card already shows another way -- a perk or
    tradition gate, or a plain research unlock -- takes no tag at all.

    Precedence, strongest first: a manual override; a starting technology; a
    perk or tradition route (no tag, the gate says it); a research unlock (no
    tag -- it arrives as a research option the moment its trigger is
    researched); a configured route tag, earliest rule first; a crisis level,
    named by ``crisis_names`` -- "Cosmogenesis Level 5" -- and the earliest
    level first; a plain event; game start; debris, for a technology no effect
    grants but a component requires.
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
    if RouteKind.RESEARCH in kinds:
        return None
    tagged = sorted((r for r in found if r.kind is RouteKind.TAGGED), key=lambda r: r.rank)
    if tagged:
        return tagged[0].key
    levels = sorted((r.key for r in found if r.kind is RouteKind.CRISIS and r.key), key=_level_order)
    if levels:
        return (crisis_names or {}).get(levels[0], TAG_CRISIS)
    if RouteKind.EVENT in kinds:
        return TAG_EVENT
    if RouteKind.START in kinds:
        return TAG_START
    if index is not None and record.key in index.component_prerequisites:
        return config.debris_tag
    # No grant found anywhere, and no component to learn it from. Saying
    # "Event" here would be a guess dressed as a finding.
    return TAG_UNKNOWN


def _level_order(key: str) -> tuple[int, str]:
    """Crisis levels by how early on their path they come."""
    match = re.search(r"(\d+)$", key)
    return (int(match.group(1)) if match else 0, key)


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


def report(
    records: dict,
    found: dict,
    config: UnlockConfig,
    index: UnlockIndex,
    localisation,
    crisis_names: dict[str, str] | None = None,
) -> str:
    """A reviewable account of every card tag and what decided it.

    Written to ``build/unlock-tags.md`` on every build. Most tags are inferred
    from the containers a route passes through, so each one names the rule and
    the route that produced it; a wrong tag is corrected in
    ``config/unlocks.toml``, either by editing a rule or with a per-technology
    override.
    """
    by_tag: dict[str, list[str]] = defaultdict(list)
    for key, record in sorted(records.items()):
        tag = tag_for(record, found.get(key, ()), config, index, crisis_names)
        if tag is None:
            continue
        technology_routes = found.get(key, ())
        if key in config.technology_tags:
            why = "manual override in config/unlocks.toml"
        elif record.start_tech:
            why = "start_tech = yes"
        else:
            tagged = sorted(
                (r for r in technology_routes if r.kind is RouteKind.TAGGED), key=lambda r: r.rank
            )
            levels = sorted(
                (r for r in technology_routes if r.kind is RouteKind.CRISIS),
                key=lambda r: _level_order(r.key or ""),
            )
            if tagged:
                route = tagged[0]
                pattern = config.route_tags[route.rank][0]
                path = " <- ".join(f"{kind}:{name}" for kind, name in route.chain)
                why = f"rule `{pattern}`: {path}"
            elif levels:
                path = " <- ".join(f"{kind}:{name}" for kind, name in levels[0].chain)
                why = f"crisis level: {path}"
            elif tag == config.debris_tag:
                why = "no effect grants it; a ship component requires it"
            elif tag == TAG_UNKNOWN:
                why = "no effect grants it and no component requires it"
            else:
                kinds = sorted({r.kind.value for r in technology_routes})
                why = f"routes: {', '.join(kinds) or 'none'}"
        others = sorted(
            {r.key for r in technology_routes if r.kind is RouteKind.TAGGED and r.key != tag}
        )
        also = f" (also: {', '.join(others)})" if others else ""
        by_tag[tag].append(f"- **{localisation.name(key)}** `{key}`{also} -- {why}")

    lines = [
        "# Unlock tags",
        "",
        "Generated by `tools/build_dataset.py`. Every never-drawable technology with a",
        "card tag, grouped by tag, with the rule and route that decided it. Correct a",
        "wrong tag in `config/unlocks.toml`.",
        "",
    ]
    for tag in sorted(by_tag, key=lambda t: (-len(by_tag[t]), t)):
        lines.append(f"## {tag} ({len(by_tag[tag])})")
        lines.append("")
        lines.extend(by_tag[tag])
        lines.append("")
    return "\n".join(lines)
