"""Assign technologies to rows, including crisis rows.

By default a technology's row comes from its ``(area, category)``. Some groups
of technologies are better read as a self-contained mini-tree, though:
Gigastructures' crises each have their own research chain, and a player facing
the Blokkats wants to see everything standing between them and survival in one
band rather than scattered across three research areas.

Crisis membership is **not** derivable from the technology data. Nothing in a
technology says "I belong to the Blokkat crisis". The signals available are the
file it was declared in, its key prefix, its category, and what it hangs off in
the graph -- all heuristics, and the last one especially so. So membership lives
in ``config/rows.toml``, where a human can read and correct it, and anything the
rules cannot place confidently is reported rather than guessed at.

Gigastructures does name its own crises, in the ``giga_category_*`` localisation
keys it uses for situation log headings: Katzenartig Imperium, Sirenalia,
Aeternum, The Blokkats, The Compound, and E.H.O.F. Those names are the ones used
here.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from .graph import TechGraph
from .records import Extraction

DEFAULT_ROWS_CONFIG = Path("config/rows.toml")


class RowConfigError(RuntimeError):
    """The row configuration is malformed, or names something that does not exist."""


@dataclass(frozen=True)
class CrisisRow:
    """A named group of technologies lifted out of the normal category rows."""

    key: str
    name: str
    #: Source files whose technologies all belong to this crisis. The strongest
    #: signal available, since Gigastructures keeps each crisis in its own file.
    source_files: tuple[str, ...] = ()
    #: Explicit technology keys. The escape hatch for anything the rules miss.
    keys: tuple[str, ...] = ()
    #: Key prefixes. Convenient but weak; several crises do not name their
    #: technologies after themselves.
    key_prefixes: tuple[str, ...] = ()
    #: Technology categories belonging wholly to this crisis.
    categories: tuple[str, ...] = ()
    #: Roots whose graph descendants belong to this crisis. The weakest signal,
    #: and the one most likely to need review -- it follows real dependencies,
    #: which may lead somewhere unrelated.
    reachable_from: tuple[str, ...] = ()
    #: Keys to keep out, whatever the rules above say.
    exclude: tuple[str, ...] = ()
    #: Free-text note carried into the report, for recording judgement calls.
    note: str = ""


@dataclass
class RowAssignment:
    """Which crisis each technology belongs to, and why."""

    crises: tuple[CrisisRow, ...] = ()
    #: technology key -> crisis key
    assigned: dict[str, str] = field(default_factory=dict)
    #: technology key -> the rule that placed it, for the report
    reasons: dict[str, str] = field(default_factory=dict)
    #: Technologies that plausibly belong to a crisis but were not placed.
    #: These are the ones a human should look at.
    uncertain: dict[str, list[str]] = field(default_factory=dict)

    def crisis_of(self, technology: str) -> str | None:
        return self.assigned.get(technology)

    def members(self, crisis_key: str) -> list[str]:
        return sorted(k for k, v in self.assigned.items() if v == crisis_key)

    def report(self) -> str:
        lines = ["# Crisis row assignment", ""]
        for crisis in self.crises:
            members = self.members(crisis.key)
            lines.append(f"## {crisis.name} ({crisis.key}) - {len(members)} technologies")
            if crisis.note:
                lines.append(f"\n> {crisis.note}")
            lines.append("")
            for key in members:
                lines.append(f"- `{key}` - {self.reasons.get(key, 'unknown rule')}")
            lines.append("")

        if self.uncertain:
            lines += [
                "## Needs review",
                "",
                "These technologies look like they may belong to a crisis but no rule",
                "placed them. Add them to `config/rows.toml` under the right crisis, or",
                "leave them where they are if they genuinely belong in a category row.",
                "",
            ]
            for key, why in sorted(self.uncertain.items()):
                lines.append(f"- `{key}` - {'; '.join(why)}")
            lines.append("")
        return "\n".join(lines)


def load_config(path: Path | str = DEFAULT_ROWS_CONFIG) -> tuple[CrisisRow, ...]:
    path = Path(path)
    if not path.is_file():
        return ()
    with path.open("rb") as handle:
        data = tomllib.load(handle)

    crises: list[CrisisRow] = []
    for table in data.get("crisis", []):
        key = table.get("key")
        if not key:
            raise RowConfigError(f"{path}: every [[crisis]] needs a 'key'")
        crises.append(
            CrisisRow(
                key=key,
                name=table.get("name", key),
                source_files=tuple(table.get("source_files", ())),
                keys=tuple(table.get("keys", ())),
                key_prefixes=tuple(table.get("key_prefixes", ())),
                categories=tuple(table.get("categories", ())),
                reachable_from=tuple(table.get("reachable_from", ())),
                exclude=tuple(table.get("exclude", ())),
                note=table.get("note", ""),
            )
        )

    seen = [c.key for c in crises]
    duplicates = {k for k in seen if seen.count(k) > 1}
    if duplicates:
        raise RowConfigError(f"{path}: duplicate crisis keys: {', '.join(sorted(duplicates))}")
    return tuple(crises)


def assign(
    extraction: Extraction,
    graph: TechGraph,
    crises: tuple[CrisisRow, ...],
    *,
    uncertainty_hints: dict[str, list[str]] | None = None,
) -> RowAssignment:
    """Place technologies into crisis rows.

    A technology is assigned to the **first** crisis whose rules match, so
    config order resolves overlaps. Rules are tried strongest first: explicit
    keys, then source file, then category, then prefix, then graph reachability.

    Fails if a rule names a technology or file that does not exist. A stale rule
    left over from a mod update is exactly the kind of thing that should stop a
    build rather than silently place nothing.
    """
    assignment = RowAssignment(crises=crises)
    technologies = extraction.technologies

    files = {r.origin.relative for r in extraction if r.origin}
    for crisis in crises:
        for key in (*crisis.keys, *crisis.exclude, *crisis.reachable_from):
            if key not in technologies:
                raise RowConfigError(
                    f"crisis {crisis.key!r} names technology {key!r}, which does not "
                    "exist in this load order. Remove it or fix the key."
                )
        for name in crisis.source_files:
            if name not in files:
                raise RowConfigError(
                    f"crisis {crisis.key!r} names source file {name!r}, which no "
                    "technology in this load order came from."
                )

    for crisis in crises:
        excluded = set(crisis.exclude)
        reachable: set[str] = set()
        for root in crisis.reachable_from:
            reachable |= {root} | graph.descendants(root)

        for key, record in technologies.items():
            if key in assignment.assigned or key in excluded:
                continue
            reason = _match(crisis, key, record, reachable)
            if reason:
                assignment.assigned[key] = crisis.key
                assignment.reasons[key] = reason

    for key, hints in (uncertainty_hints or {}).items():
        if key not in assignment.assigned:
            assignment.uncertain[key] = hints

    return assignment


def _match(crisis: CrisisRow, key: str, record, reachable: set[str]) -> str | None:
    if key in crisis.keys:
        return "listed explicitly"
    if record.origin and record.origin.relative in crisis.source_files:
        return f"declared in {record.origin.relative}"
    if record.category and record.category in crisis.categories:
        return f"category {record.category}"
    for prefix in crisis.key_prefixes:
        if key.startswith(prefix):
            return f"key prefix {prefix!r}"
    if key in reachable:
        return "reachable from " + ", ".join(crisis.reachable_from)
    return None
