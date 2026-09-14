"""Gigastructures' settings presets: which of its global flags each one leaves set.

Gigastructures configures itself through global flags -- ``warplanet_disabled``,
``vanilla_dyson_capped_1`` -- chosen from a menu at the start of a game. Its
three main presets are scripted effects that clear every menu flag and set
their own: ``giga_preset_arcade`` and the rest. A technology that tests one of
those flags in its ``potential``, its weight or an event on the way to it
depends on the preset.

This reads a preset the way the game runs it at the start of a game, before
``giga_game_started`` is set, with every DLC owned: it follows scripted effect
calls, applies ``remove_global_flag`` and ``set_global_flag`` in order, and
reads each ``if`` limit against the flags set so far. A flag set only under a
condition that cannot be settled -- ``is_multiplayer``, ``any_country`` -- is
left unknown, like anything else the tree cannot tell.

Which effects are presets, and what to call them, is configured in
``config/presets.toml``; nothing about Gigastructures is written here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .clausewitz import Block, Scalar
from .loadorder import LoadOrder, merge_keys, resolve_files
from .profiles import TV, Definitions, Evaluator, Profile, _and, _not

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

DEFAULT_PRESETS_CONFIG = Path("config/presets.toml")

#: How deep scripted effects may call one another.
MAX_DEPTH = 16
#: Effect blocks that run in order, unconditionally, where they stand.
_SEQUENTIAL = frozenset({"hidden_effect", "while", "effect"})


@dataclass(frozen=True)
class Preset:
    key: str
    #: The scripted effect that applies it, or None for settings changed by
    #: hand, which leaves every settings flag open.
    effect: str | None
    #: What a player calls it.
    name: str


@dataclass(frozen=True)
class PresetConfig:
    presets: tuple[Preset, ...] = ()
    #: The preset shown first.
    default: str | None = None
    #: Scripted effects whose global flags no preset settles, and further such
    #: flags: settings left to the player whatever preset they pick.
    unsettled_effects: tuple[str, ...] = ()
    unsettled_flags: tuple[str, ...] = ()


def load_config(path: Path | str = DEFAULT_PRESETS_CONFIG) -> PresetConfig:
    path = Path(path)
    if not path.is_file():
        return PresetConfig()
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    presets = tuple(Preset(p["key"], p.get("effect"), p["name"]) for p in data.get("preset", []))
    unsettled = data.get("unsettled", {})
    return PresetConfig(
        presets=presets,
        default=data.get("default"),
        unsettled_effects=tuple(unsettled.get("effects", ())),
        unsettled_flags=tuple(unsettled.get("flags", ())),
    )


def flags_named(names, effects: dict[str, Block]) -> set[str]:
    """Every global flag the named scripted effects set or clear, through the effects they call."""
    found: set[str] = set()
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
            if key.lower() in ("set_global_flag", "remove_global_flag") and isinstance(value, Scalar):
                found.add(value.value)
            elif key in effects and key not in seen and depth < MAX_DEPTH:
                seen.add(key)
                walk(effects[key], depth + 1)
            elif isinstance(value, Block):
                walk(value, depth)

    for name in names:
        if name in effects and name not in seen:
            seen.add(name)
            walk(effects[name], 0)
    return found


def flags_after(effect: str, effects: dict[str, Block], definitions: Definitions) -> dict[str, TV]:
    """Every global flag ``effect`` touches, and whether it is set afterwards."""
    state: dict[str, TV] = {}
    reader = Evaluator(Profile("regular"), definitions)
    reader.global_flags = state
    # Before the game has started, which is when the menu is used.
    state["giga_game_started"] = TV.FALSE

    def apply(flag: str, value: TV, when: TV) -> None:
        if when is TV.FALSE:
            return
        if when is TV.TRUE:
            state[flag] = value
        elif state.get(flag) is not value:
            state[flag] = TV.UNKNOWN

    def run(block: Block, when: TV, depth: int) -> None:
        chain: list[TV] | None = None
        for item in block.items:
            if isinstance(item, Block):
                run(item, when, depth)
                chain = None
                continue
            key = getattr(item, "key", None)
            value = getattr(item, "value", None)
            if key is None:
                continue
            lowered = key.lower()
            if lowered in ("set_global_flag", "remove_global_flag") and isinstance(value, Scalar):
                apply(value.value, TV.TRUE if lowered == "set_global_flag" else TV.FALSE, when)
                chain = None
                continue
            if key in effects and depth < MAX_DEPTH:
                # A scripted effect, called bare or with parameters.
                if isinstance(value, Block) or (isinstance(value, Scalar) and value.value == "yes"):
                    run(effects[key], when, depth + 1)
                chain = None
                continue
            if not isinstance(value, Block):
                chain = None
                continue
            if lowered in ("if", "else_if", "else"):
                earlier = chain if lowered != "if" and chain is not None else []
                if lowered != "if" and chain is None:
                    # An else with no chain to follow may or may not run.
                    earlier = [TV.UNKNOWN]
                limit = value.get_first("limit")
                holds = reader.trigger(limit) if isinstance(limit, Block) and lowered != "else" else TV.TRUE
                runs = _and([when, *(_not(e) for e in earlier), holds])
                run(Block(items=[i for i in value.items if getattr(i, "key", None) != "limit"]), runs, depth)
                chain = None if lowered == "else" else [*earlier, holds]
                continue
            chain = None
            if lowered in _SEQUENTIAL or lowered.startswith("event_target:"):
                run(value, when, depth)
            elif lowered == "fire_on_action":
                continue
            else:
                # A scope that may not exist, a random list: it may or may not run.
                run(value, _and([when, TV.UNKNOWN]), depth)

    body = effects.get(effect)
    if body is not None:
        run(body, TV.TRUE, 0)
    del state["giga_game_started"]
    return state


def load_presets(load_order: LoadOrder, config: PresetConfig, definitions: Definitions) -> dict[str, dict[str, TV]]:
    """Preset key -> the global flags it leaves, read from the load order."""
    if not config.presets:
        return {}
    merged = merge_keys(resolve_files(load_order, "common/scripted_effects")).blocks
    effects = {k: v for k, v in merged.items() if isinstance(v, Block)}
    unsettled = flags_named(config.unsettled_effects, effects) | set(config.unsettled_flags)
    found = {}
    for preset in config.presets:
        flags = flags_after(preset.effect, effects, definitions) if preset.effect else {}
        found[preset.key] = {flag: value for flag, value in flags.items() if flag not in unsettled}
    return found


def _flags_tested(block: Block | None, triggers, depth: int = 0, seen: frozenset[str] = frozenset()) -> list[str]:
    """Global flags a trigger block tests, through scripted triggers too."""
    found: list[str] = []
    if block is None:
        return found
    for item in block.items:
        if isinstance(item, Block):
            found += _flags_tested(item, triggers, depth, seen)
            continue
        key = getattr(item, "key", None)
        value = getattr(item, "value", None)
        if key is None:
            continue
        if isinstance(value, Block):
            found += _flags_tested(value, triggers, depth, seen)
        elif isinstance(value, Scalar):
            if key.lower() == "has_global_flag":
                found.append(value.value)
            elif value.value in ("yes", "no") and key not in seen and depth < MAX_DEPTH:
                found += _flags_tested(triggers.get(key), triggers, depth + 1, seen | {key})
    return list(dict.fromkeys(found))


def report(graph, views, extraction, localisation) -> str:
    """A reviewable account of what each preset hides, and the flags that decide it.

    Written to ``build/presets.md`` on every build. A technology is listed under
    a preset when every empire loses it there but not without a preset; each
    line names the global flags its own conditions test and what the preset
    leaves them as, or the prerequisite it goes with.
    """
    config = extraction.preset_config
    definitions = extraction.profile_definitions
    profiles = extraction.profiles
    records = graph.records
    lines = [
        "# Settings presets",
        "",
        "Generated by `tools/build_dataset.py`. Technologies every empire loses under a",
        "Gigastructures settings preset, with the global flags each one's own conditions",
        "test and what the preset leaves them as. A technology whose conditions test no",
        "flag goes with a prerequisite, a way in, or a weight that does.",
        "",
    ]
    if not config.presets:
        return "\n".join(lines + ["No presets are configured.", ""])

    def everyone(key: str, preset: str) -> bool:
        bits = [bit for bit, p in enumerate(profiles) if p.preset == preset]
        return all(views.technology_hidden[key] >> bit & 1 for bit in bits)

    for preset in config.presets:
        flags = definitions.presets.get(preset.key, {})
        hidden = sorted(
            (key for key in records if everyone(key, preset.key)),
            key=lambda k: localisation.name(k),
        )
        lines.append(f"## {preset.name} ({len(hidden)})")
        lines.append("")
        for key in hidden:
            record = records[key]
            blocks = [record.potential, *record.weight_modifiers]
            tested = []
            for block in blocks:
                tested += _flags_tested(block, definitions.triggers)
            tested = list(dict.fromkeys(tested))
            reasons = []
            if tested:
                shown = ", ".join(
                    f"`{flag}` {({TV.TRUE: 'set', TV.FALSE: 'not set', TV.UNKNOWN: 'unknown'})[flags.get(flag, TV.UNKNOWN)]}"
                    for flag in tested
                )
                reasons.append(f"tests {shown}")
            gone = [
                option
                for group in record.prerequisites
                for option in group.options
                if option in records and everyone(option, preset.key)
            ]
            if gone:
                reasons.append("needs " + ", ".join(localisation.name(o) for o in dict.fromkeys(gone)))
            why = "; ".join(reasons) or "no flag in its own conditions; see its ways in"
            lines.append(f"- **{localisation.name(key)}** `{key}` -- {why}")
        lines.append("")
    return "\n".join(lines)

