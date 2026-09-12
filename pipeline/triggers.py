"""How a Stellaris trigger block is read.

Three rules that every consumer of a ``potential`` or ``weight_modifier`` block
needs and none of them owns: what negates, what changes scope, and what a
``scripted_trigger`` name stands for. They live here so the dependency graph and
the gate resolver cannot drift apart on the semantics of the same block.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .clausewitz import Block, parse_file
from .loadorder import LoadOrder, resolve_files

#: Boolean wrappers that invert the meaning of what they contain. Case varies:
#: vanilla writes ``NOT``, Gigastructures writes ``not``.
NEGATING = {"not", "nor", "nand"}

#: Trigger keys that move evaluation off the researching country and onto some
#: other scope. A ``has_technology`` underneath one of these asks whether
#: *somebody else* holds the technology, which is a condition on the state of
#: the galaxy rather than a dependency of the technology being defined.
#:
#: Four such references exist in the corpus, all in the E.H.O.F. sentient metal
#: chain. ``tech_ehof_sentient_tier_1`` becomes available when
#: ``any_country = { has_technology = tech_ehof_sentient_tier_4 }`` -- once
#: anyone in the galaxy reaches tier 4, tier 1 is unlocked for everybody.
#: Reading that as a dependency inverts the chain and places tier 1 to the
#: right of tier 4.
#:
#: Matched by prefix because the scope families are open-ended; the named set
#: covers the fixed scopes that appear without one. ``this``, ``root`` and
#: ``prev`` are deliberately absent: inside a technology's ``potential`` they
#: still refer to the researching country, so treating them as a scope change
#: would discard real dependencies.
SCOPE_PREFIXES = ("any_", "every_", "random_", "all_", "count_")
SCOPE_KEYS = {"owner", "controller", "capital_scope", "space_owner", "from", "fromfrom"}


def changes_scope(key: str) -> bool:
    """Whether ``key`` evaluates its contents against a different scope.

    Public because gate detection needs the same rule: an ascension perk held
    by ``any_country`` is no more a gate on this technology than a technology
    held by one is a dependency.
    """
    lowered = key.lower()
    if lowered in NEGATING:
        return False
    return lowered.startswith(SCOPE_PREFIXES) or lowered in SCOPE_KEYS



@dataclass
class TriggerIndex:
    """``common/scripted_triggers`` definitions, by name.

    Resolved only far enough to answer questions about a trigger's contents: a
    scripted trigger is a named block substituted wherever ``<name> = yes``
    appears, so following one is a lookup and a recursive walk, never
    evaluation. Nothing here decides whether a trigger passes.
    """

    definitions: dict[str, Block] = field(default_factory=dict)
    #: Files that would not parse. Recorded rather than raised: a broken trigger
    #: in some unrelated mod file must not fail a tech-tree build.
    unparsed: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.definitions)

    def __contains__(self, name: str) -> bool:
        return name in self.definitions

    def get(self, name: str) -> Block | None:
        return self.definitions.get(name)

    def summary(self) -> str:
        broken = f", {len(self.unparsed)} unparsed" if self.unparsed else ""
        return f"{len(self.definitions)} scripted triggers{broken}"


def build_index(load_order: LoadOrder) -> TriggerIndex:
    """Index every scripted trigger the load order defines.

    Later sources win, matching the engine: a mod redefining a vanilla trigger
    replaces it wholesale.
    """
    index = TriggerIndex()
    for resolved in resolve_files(load_order, "common/scripted_triggers"):
        try:
            block = parse_file(resolved.path)
        except Exception as error:  # noqa: BLE001
            index.unparsed.append(f"{resolved.path.name}: {error}")
            continue
        for pair in block.pairs():
            if isinstance(pair.value, Block):
                index.definitions[pair.key] = pair.value
    return index
