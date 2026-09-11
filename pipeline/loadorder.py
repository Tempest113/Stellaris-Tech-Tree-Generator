"""Model the engine's load order over a stack of mods.

Stellaris resolves content in **two** stages, and conflating them gets real
mods wrong:

1. **File replacement.** If a later source ships a file at the same relative
   path as an earlier one, the later file replaces the earlier one wholesale.
   The earlier file's contents are gone, not merged. ``Oops! All Engineering!``
   works entirely this way: it ships 32 files named exactly like vanilla's and
   thereby rewrites 663 technologies.

2. **Key merge.** Across the files that survive stage 1, top-level keys merge in
   load order and the last declaration of a key wins **as a whole block** --
   never field-by-field. This is how ``zz_giga_tech_overwrites.txt`` replaces
   ``tech_mega_engineering``: a new filename, so no file replacement, but its
   key lands last.

Within one source, files load in case-insensitive filename order, which is why
mods prefix override files with ``zz_``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .clausewitz import Block, Scalar, parse_file
from .clausewitz.nodes import Node


@dataclass(frozen=True)
class Descriptor:
    """Contents of a ``descriptor.mod`` / ``*.mod`` file."""

    name: str
    version: str | None = None
    supported_version: str | None = None
    remote_file_id: str | None = None
    #: Set on loose ``*.mod`` files in the user mod directory, which point at a
    #: folder elsewhere rather than containing the content themselves.
    path: str | None = None
    tags: tuple[str, ...] = ()


def _text(block: Block, key: str) -> str | None:
    value = block.get_first(key)
    return value.value if isinstance(value, Scalar) else None


def read_descriptor(path: Path) -> Descriptor:
    """Parse a ``descriptor.mod``. The format is ordinary Clausewitz script."""
    block = parse_file(path)
    tags_block = block.block_at("tags")
    return Descriptor(
        name=_text(block, "name") or path.parent.name,
        version=_text(block, "version"),
        supported_version=_text(block, "supported_version"),
        remote_file_id=_text(block, "remote_file_id"),
        path=_text(block, "path"),
        tags=tuple(s.value for s in tags_block.scalars()) if tags_block else (),
    )


@dataclass(frozen=True)
class Source:
    """One layer of content: the base game, or a single mod."""

    #: Short stable identifier used in provenance output and config files.
    key: str
    #: Human-readable name, from descriptor.mod where available.
    name: str
    root: Path
    version: str | None = None
    is_base_game: bool = False

    def __str__(self) -> str:
        return f"{self.name} ({self.version})" if self.version else self.name


def base_game_source(game_dir: Path, version: str | None = None) -> Source:
    return Source(
        key="vanilla", name="Stellaris", root=game_dir, version=version, is_base_game=True
    )


def mod_source(root: Path, *, key: str | None = None, name: str | None = None) -> Source:
    """Build a :class:`Source` from a mod directory containing ``descriptor.mod``.

    ``name`` overrides the descriptor's own label. Worth having: Gigastructures'
    git repository ships a descriptor reading "Gigastructural Engineering DEV"
    even on the branch that matches the public Workshop release, so the config's
    name is the one users should see.
    """
    root = Path(root)
    descriptor_path = root / "descriptor.mod"
    if descriptor_path.is_file():
        descriptor = read_descriptor(descriptor_path)
        return Source(
            key=key or descriptor.remote_file_id or root.name,
            name=name or descriptor.name,
            root=root,
            version=descriptor.version,
        )
    return Source(key=key or root.name, name=name or root.name, root=root)


def resolve_mod_reference(reference: Path, *, user_data: Path | None) -> Path:
    """Resolve a ``*.mod`` file or a directory to the directory holding content.

    Loose ``*.mod`` files in the user mod directory are pointers: they carry a
    ``path=`` field naming the folder that actually holds ``common/``.
    """
    reference = Path(reference)
    if reference.is_dir():
        return reference
    if reference.suffix == ".mod" and reference.is_file():
        descriptor = read_descriptor(reference)
        if descriptor.path:
            target = Path(descriptor.path)
            if not target.is_absolute() and user_data is not None:
                target = user_data / descriptor.path
            if target.is_dir():
                return target
        # Some .mod files sit next to an identically named folder.
        sibling = reference.with_suffix("")
        if sibling.is_dir():
            return sibling
    raise FileNotFoundError(f"cannot resolve mod reference: {reference}")


@dataclass
class LoadOrder:
    """An ordered stack of sources. Index 0 loads first and loses conflicts."""

    sources: list[Source] = field(default_factory=list)

    def add(self, source: Source) -> "LoadOrder":
        self.sources.append(source)
        return self

    def __iter__(self):
        return iter(self.sources)

    def __len__(self) -> int:
        return len(self.sources)


@dataclass(frozen=True)
class ResolvedFile:
    """A file that survived stage-1 replacement, with the source that won it."""

    relative: str
    path: Path
    source: Source
    #: Sources that shipped this same relative path and were overridden.
    shadowed: tuple[Source, ...] = ()


def resolve_files(
    load_order: LoadOrder,
    subdir: str,
    *,
    pattern: str = "*.txt",
    recursive: bool = False,
) -> list[ResolvedFile]:
    """Apply stage-1 file replacement across ``<source>/<subdir>``.

    Returns files in engine load order: source order first, then case-insensitive
    filename order within each source.

    ``recursive`` must stay off for ``common/technology``, whose ``category/``
    and ``tier/`` subdirectories hold a different kind of definition entirely
    and would otherwise be merged in with the technologies. It must be on for
    ``common/inline_scripts``, which is genuinely a tree.
    """
    # relative path (lowercased) -> (winning source, real path, shadowed sources)
    winners: dict[str, tuple[Source, Path, list[Source]]] = {}
    # Preserve the relative path's original casing from whichever source won.
    for source in load_order:
        directory = source.root / subdir
        if not directory.is_dir():
            continue
        walk = directory.rglob if recursive else directory.glob
        for path in walk(pattern):
            if not path.is_file():
                continue
            relative = path.relative_to(directory).as_posix()
            key = relative.lower()
            if key in winners:
                previous_source, _, shadowed = winners[key]
                winners[key] = (source, path, shadowed + [previous_source])
            else:
                winners[key] = (source, path, [])

    order = {source.key: index for index, source in enumerate(load_order)}
    resolved = [
        ResolvedFile(
            relative=path.relative_to(source.root / subdir).as_posix(),
            path=path,
            source=source,
            shadowed=tuple(shadowed),
        )
        for source, path, shadowed in winners.values()
    ]
    resolved.sort(key=lambda f: (order[f.source.key], f.relative.lower()))
    return resolved


@dataclass(frozen=True)
class KeyOrigin:
    """Where a top-level key's winning definition came from."""

    source: Source
    relative: str
    #: Earlier ``(source, relative)`` pairs that declared the same key and lost.
    overridden: tuple[tuple[Source, str], ...] = ()


@dataclass
class MergedKeys:
    """Stage-2 result: the winning block for each top-level key, plus provenance."""

    blocks: dict[str, Node] = field(default_factory=dict)
    origins: dict[str, KeyOrigin] = field(default_factory=dict)
    #: Top-level ``@variable`` declarations found inline among the definitions.
    inline_variables: dict[str, str] = field(default_factory=dict)

    def overridden_keys(self) -> dict[str, KeyOrigin]:
        return {k: o for k, o in self.origins.items() if o.overridden}


_VARIABLE_KEY = re.compile(r"^@\w")


def merge_keys(files: list[ResolvedFile]) -> MergedKeys:
    """Apply stage-2 whole-key replacement over already-resolved files.

    Inline ``@variable = value`` declarations are captured separately rather
    than treated as definitions: vanilla declares some of them *mid-file*
    between technology blocks (``00_soc_tech.txt:2740`` and ``:3140``), so a
    parser that only scans a file header misses them.
    """
    merged = MergedKeys()

    for resolved in files:
        block = parse_file(resolved.path)
        for pair in block.pairs():
            key = pair.key
            if _VARIABLE_KEY.match(key):
                if isinstance(pair.value, Scalar):
                    merged.inline_variables[key[1:]] = pair.value.value
                continue

            previous = merged.origins.get(key)
            overridden = (
                previous.overridden + ((previous.source, previous.relative),)
                if previous
                else ()
            )
            merged.blocks[key] = pair.value
            merged.origins[key] = KeyOrigin(
                source=resolved.source, relative=resolved.relative, overridden=overridden
            )

    return merged
