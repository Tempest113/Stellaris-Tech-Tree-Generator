"""Expand ``inline_script`` directives.

Why this matters more here than it looks: in Gigastructures, an inline script
does not merely inject a ``modifier`` block, it emits **entire technology
bodies**. Fifty of Gigastructures' 301 technologies have no local fields at all
-- their ``area``, ``tier``, ``levels``, ``prerequisites`` and ``potential`` all
arrive from ``technology/giga_mega_repeatable``. Any component that reads a
technology before expansion sees a body with almost nothing in it and draws a
confident wrong conclusion. That single mistake produced three separate silent
bugs in the previous attempt at this project.

Mechanics
---------
Parameters embed *mid-token* in the script body (``giga_tech_repeatable_$name$_cap``,
``"giga_$name$_capacity_increase_title"``), so substitution has to happen on the
script's **text**, before it is tokenized. The call site itself is ordinary
parseable script, so the flow is:

1. parse the caller normally;
2. find ``inline_script`` entries at any depth;
3. read the referenced file as text and substitute ``$ARG$`` / ``$ARG|default$``;
4. parse the substituted text;
5. splice the resulting items in place of the directive;
6. repeat, because scripts may themselves call scripts.

Expansion must reach **any depth**: Gigastructures calls inline scripts from
inside ``weight_modifier``, from inside a ``cost = { }`` block, and from inside
``technology_swap``.

Expanded items are spliced as siblings, which means a body can legitimately end
up with two ``weight_modifier`` blocks -- one from the template and one written
beside the call. That is why the tree model keeps duplicate keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .clausewitz import Block, Pair, Scalar, parse, serialize
from .clausewitz.lexer import tokenize
from .clausewitz.nodes import Item, Node
from .clausewitz.tokens import TokenKind
from .loadorder import LoadOrder, resolve_files

#: Guards against a script that includes itself, directly or in a ring.
MAX_DEPTH = 16

#: ``$NAME$`` or ``$NAME|default$``. The default form is rare (three uses in the
#: whole corpus, all planet-class arguments) but silently dropping it would
#: corrupt those bodies.
_PARAM = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)(?:\|([^$]*))?\$")

#: Key naming the script to include, inside the block form.
_SCRIPT_KEY = "script"


class InlineScriptError(RuntimeError):
    """An inline script could not be resolved or expanded."""


@dataclass
class ScriptIndex:
    """Available inline scripts, keyed by their path without extension."""

    files: dict[str, Path] = field(default_factory=dict)
    #: Script paths referenced by callers but not present in the load order.
    missing: set[str] = field(default_factory=set)

    def get(self, name: str) -> Path | None:
        path = self.files.get(name.strip().strip('"').lower())
        if path is None:
            self.missing.add(name)
        return path


def build_index(load_order: LoadOrder) -> ScriptIndex:
    """Index ``common/inline_scripts`` across the load order.

    File replacement applies, so a mod shipping ``technology/foo.txt`` shadows
    the base game's script of the same name.
    """
    index = ScriptIndex()
    for resolved in resolve_files(load_order, "common/inline_scripts", recursive=True):
        key = resolved.relative[:-4] if resolved.relative.endswith(".txt") else resolved.relative
        index.files[key.lower()] = resolved.path
    return index


def _argument_text(value: Node) -> str:
    """Render an argument value for textual substitution."""
    if isinstance(value, Scalar):
        return value.value
    return serialize(value).strip()


def _directive_target(value: Node) -> tuple[str, dict[str, str]] | None:
    """Read a directive's script path and arguments, in either supported form."""
    if isinstance(value, Scalar):
        # inline_script = "technology/archaeotech_weight"
        return value.value, {}

    script = value.get_first(_SCRIPT_KEY)
    if not isinstance(script, Scalar):
        return None

    arguments = {
        pair.key: _argument_text(pair.value)
        for pair in value.pairs()
        if pair.key != _SCRIPT_KEY
    }
    return script.value, arguments


def _substitute(text: str, arguments: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        if name in arguments:
            return arguments[name]
        if default is not None:
            return default
        # An unsupplied parameter with no default is left verbatim so the
        # resulting parse error names it, rather than silently vanishing.
        return match.group(0)

    return _PARAM.sub(replace, text)


@dataclass
class ExpansionStats:
    """What an expansion pass did, for the build report."""

    directives_expanded: int = 0
    max_depth_reached: int = 0
    missing_scripts: set[str] = field(default_factory=set)
    unsupplied_parameters: set[str] = field(default_factory=set)


class Expander:
    """Expands ``inline_script`` directives against a script index."""

    def __init__(self, index: ScriptIndex) -> None:
        self.index = index
        self.stats = ExpansionStats()
        self._cache: dict[tuple[str, tuple[tuple[str, str], ...]], list[Item]] = {}

    def expand(self, node: Node, *, depth: int = 0) -> Node:
        """Return ``node`` with every directive replaced by the script's items."""
        if isinstance(node, Scalar):
            return node
        return Block(self._expand_items(node.items, depth))

    def _expand_items(self, items: list[Item], depth: int) -> list[Item]:
        out: list[Item] = []
        for item in items:
            if isinstance(item, Pair) and item.key == "inline_script":
                out.extend(self._expand_directive(item, depth))
                continue
            if isinstance(item, Pair):
                out.append(
                    Pair(item.key, item.op, self.expand(item.value, depth=depth), item.key_quoted)
                )
            elif isinstance(item, Block):
                out.append(self.expand(item, depth=depth))  # type: ignore[arg-type]
            else:
                out.append(item)
        return out

    def _expand_directive(self, directive: Pair, depth: int) -> list[Item]:
        if depth >= MAX_DEPTH:
            raise InlineScriptError(
                f"inline_script nesting exceeded {MAX_DEPTH} levels; likely a cycle"
            )

        target = _directive_target(directive.value)
        if target is None:
            raise InlineScriptError("inline_script block has no 'script' key")
        name, arguments = target

        path = self.index.get(name)
        if path is None:
            self.stats.missing_scripts.add(name)
            return []

        cache_key = (name.lower(), tuple(sorted(arguments.items())))
        cached = self._cache.get(cache_key)
        if cached is not None:
            self.stats.directives_expanded += 1
            return list(cached)

        text = _substitute(_read(path), arguments)
        for parameter in _unsupplied_parameters(text, path):
            self.stats.unsupplied_parameters.add(f"{name}:${parameter}$")

        try:
            body = parse(text, path=f"{path} (inline_script)")
        except Exception as exc:  # noqa: BLE001 - re-raised with context
            raise InlineScriptError(f"expanding {name!r} from {path}: {exc}") from exc

        expanded = self._expand_items(body.items, depth + 1)
        self.stats.directives_expanded += 1
        self.stats.max_depth_reached = max(self.stats.max_depth_reached, depth + 1)
        self._cache[cache_key] = expanded
        return list(expanded)


def _unsupplied_parameters(text: str, path: Path) -> set[str]:
    """Parameters still present after substitution, ignoring commented-out ones.

    Substitution deliberately runs over raw text, so it also touches comments.
    Scanning that same text for leftovers produces false alarms: Gigastructures'
    ``neighbor_spread_tech_weight_bonus`` documents an abandoned
    ``$OWNED_AND_RUINED_MEGA_LIST$`` parameter inside a commented block. Re-lexing
    drops comments, so only parameters that will really reach the parser count.
    """
    try:
        tokens = tokenize(text, path=str(path))
    except Exception:  # noqa: BLE001 - the parse below will report this properly
        return set()
    return {
        match.group(1)
        for token in tokens
        if token.kind in (TokenKind.BARE, TokenKind.STRING)
        for match in _PARAM.finditer(token.value)
    }


_ENCODINGS = ("utf-8-sig", "cp1252")


def _read(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AssertionError("cp1252 decodes any byte sequence; this is unreachable")
