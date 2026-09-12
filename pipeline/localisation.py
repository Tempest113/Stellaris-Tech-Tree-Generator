"""Read and resolve Stellaris localisation.

The file format is YAML-shaped but is not YAML. Entries look like::

     tech_mega_engineering:0 "Mega-Engineering"
     tech_corvette_hull_effect:1 "$corvette_hull_effect$\\n$frigate_hull_effect$"

Two things make resolution harder than it looks, and both were flagged as
project requirements:

**References must resolve to text, not to the key.** A value may embed
``$other_key$``, and that key's value may embed more of them. Resolution has to
iterate to a fixpoint, and -- this is the part that bites -- it has to replace
*every* token on a line rather than the first. The previous attempt at this
project replaced only the first token per hop and shipped 16.4% of names and
22.8% of descriptions with raw ``$tokens$`` still visible.

**Colour markup must be stripped.** Stellaris writes ``§G+10%§!`` for a green
"+10%". The renderer draws its own colours, so the markup is removed and the
text reads as plain white.

Also handled: ``£resource£`` icon references, ``\\n`` escapes, and
``localisation/replace/``, which overrides matching keys from anywhere else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .loadorder import LoadOrder, Source

#: Guards against a reference loop. Real chains are 2-3 deep; the corpus has
#: ``tech_corvette_hull_effect`` -> ``corvette_hull_effect`` ->
#: ``mod_shipsize_corvette_hull_mult``.
MAX_RESOLUTION_DEPTH = 12

#: ``key:version "value"``. The value is matched greedily to the last quote on
#: the line, because values legitimately contain ``#`` and unescaped inner
#: punctuation but never a trailing comment after the closing quote.
_ENTRY = re.compile(r'^\s*([^\s:#"][^:]*):(\d*)\s*"(.*)"\s*$')

#: ``$key$`` or ``$key|format$``. The pipe form carries a runtime formatting
#: directive (``$VALUE|0$``) and has no localisation key behind it.
_TOKEN = re.compile(r"\$([^$|\[\]]*)(\|[^$]*)?\$")

#: ``§`` plus one letter starts a colour run; ``§!`` ends it.
_COLOUR = re.compile(r"§.")

#: ``£resource£`` and ``£resource|icon£`` embed an icon.
_ICON = re.compile(r"£([^£\s]*)£?")

#: ``[Scope.GetName]`` is resolved by the game at runtime against live state.
_COMMAND = re.compile(r"\[[^\]]*\]")


@dataclass
class LocEntry:
    raw: str
    source: Source | None = None
    #: True when the entry came from ``localisation/replace/``.
    is_replacement: bool = False


@dataclass
class Localisation:
    """A resolved localisation table for one language."""

    entries: dict[str, LocEntry] = field(default_factory=dict)
    #: Reference keys that no entry defines. Usually runtime values.
    unresolved: set[str] = field(default_factory=set)
    _cache: dict[str, str] = field(default_factory=dict)

    def __contains__(self, key: str) -> bool:
        return key in self.entries

    def __len__(self) -> int:
        return len(self.entries)

    def raw(self, key: str) -> str | None:
        entry = self.entries.get(key)
        return entry.raw if entry else None

    def get(self, key: str, default: str | None = None) -> str | None:
        """Fully resolved, markup-free text for ``key``."""
        if key in self._cache:
            return self._cache[key]
        entry = self.entries.get(key)
        if entry is None:
            return default
        text = clean(self._expand(entry.raw, depth=0, seen={key}))
        self._cache[key] = text
        return text

    def name(self, key: str) -> str:
        """Display name, falling back to the key so nothing renders blank."""
        return self.get(key) or key

    def description(self, key: str) -> str:
        return self.get(f"{key}_desc") or ""

    # -- resolution --------------------------------------------------------

    def _expand(self, text: str, *, depth: int, seen: set[str]) -> str:
        """Replace every reference token, recursively.

        ``seen`` breaks reference cycles along the current chain rather than
        globally, so two siblings may both reference the same key.
        """
        if depth >= MAX_RESOLUTION_DEPTH or "$" not in text:
            return text

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if not key:
                return match.group(0)
            if key in seen:
                return ""  # cycle; drop rather than loop
            entry = self.entries.get(key)
            if entry is None:
                self.unresolved.add(key)
                # A formatting token such as $VALUE|0$ has no key behind it and
                # is a runtime value; dropping it beats showing the reader
                # internal syntax.
                return ""
            return self._expand(entry.raw, depth=depth + 1, seen=seen | {key})

        return _TOKEN.sub(replace, text)


def clean(text: str) -> str:
    """Strip markup and normalise escapes, leaving plain readable text."""
    text = _COLOUR.sub("", text)
    text = _COMMAND.sub("", text)
    text = _ICON.sub("", text)
    text = text.replace("\\n", "\n").replace('\\"', '"')
    # Collapse the runs of spaces that dropping tokens tends to leave behind,
    # without touching the newlines that structure a description.
    text = re.sub(r"[ \t]{2,}", " ", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def parse_file(path: Path) -> dict[str, str]:
    """Read one ``*_l_<language>.yml`` file into raw key/value pairs."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - cp1252 accepts anything
        return {}

    entries: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.endswith(":"):
            continue
        match = _ENTRY.match(line)
        if match:
            entries[match.group(1).strip()] = match.group(3)
    return entries


def load(load_order: LoadOrder, *, language: str = "english") -> Localisation:
    """Build the localisation table for a load order.

    Read in two passes. Ordinary files first, in load order, then
    ``localisation/replace/``, which overrides matching keys from anywhere --
    that is how Gigastructures rewrites vanilla strings without shipping a file
    of the same name.
    """
    table = Localisation()

    for replacement in (False, True):
        for source in load_order:
            root = source.root / "localisation"
            directory = root / "replace" / language if replacement else root / language
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*.yml")):
                for key, value in parse_file(path).items():
                    table.entries[key] = LocEntry(
                        raw=value, source=source, is_replacement=replacement
                    )

    return table


def coverage(table: Localisation, keys: list[str]) -> dict[str, list[str]]:
    """Report which of ``keys`` have no name, and which resolve to nothing.

    Separated because they are different problems: a missing key means the
    technology has no localisation at all, while an empty resolution means the
    entry exists but every token in it dropped out.
    """
    missing = [k for k in keys if k not in table]
    empty = [k for k in keys if k in table and not table.get(k)]
    return {"missing": missing, "empty": empty}
