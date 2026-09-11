"""Build configuration.

One file describes a whole build: which game install, which mods in which
order, and which sources are read for reference only. Nothing about
Gigastructures is hard-coded in the pipeline -- it is just the mod this
repository happens to ship a config for.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from .vendor import DEFAULT_SPARSE_PATHS, GitSource

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

DEFAULT_CONFIG = Path("config/build.toml")


class ConfigError(RuntimeError):
    """The build configuration is missing or malformed."""


@dataclass(frozen=True)
class WorkshopSource:
    """A mod read from a local Steam Workshop install.

    Less reproducible than a git source -- it carries no commit anyone else can
    verify -- so it is a fallback for mods that publish no repository.
    """

    key: str
    workshop_id: int
    name: str | None = None

    @property
    def display_name(self) -> str:
        return self.name or self.key


@dataclass(frozen=True)
class LocalSource:
    """A mod read from a directory on disk. For development only."""

    key: str
    path: Path
    name: str | None = None

    @property
    def display_name(self) -> str:
        return self.name or self.key


Source = GitSource | WorkshopSource | LocalSource


@dataclass
class BuildConfig:
    """Everything needed to produce a dataset."""

    #: Explicit game directory, or ``None`` to auto-discover.
    game_path: Path | None = None
    #: Mods layered over the base game, in load order.
    sources: list[Source] = field(default_factory=list)
    #: Sources read only to resolve references reaching out of the load order.
    #: Their own overrides are deliberately *not* applied.
    reference_sources: list[Source] = field(default_factory=list)
    vendor_root: Path = Path("vendor")
    output_root: Path = Path("build")
    #: Localisation language to extract.
    language: str = "english"
    path: Path | None = None


def _source_from_table(table: dict, *, where: str) -> Source:
    key = table.get("key")
    if not key:
        raise ConfigError(f"{where}: every source needs a 'key'")

    kind = table.get("kind", "git")
    name = table.get("name")

    if kind == "git":
        repo = table.get("repo")
        if not repo:
            raise ConfigError(f"{where} ({key}): a git source needs a 'repo'")
        sparse = table.get("sparse_paths")
        return GitSource(
            key=key,
            repo=repo,
            ref=table.get("ref", "HEAD"),
            commit=table.get("commit"),
            name=name,
            sparse_paths=tuple(sparse) if sparse else DEFAULT_SPARSE_PATHS,
        )

    if kind == "workshop":
        workshop_id = table.get("workshop_id")
        if workshop_id is None:
            raise ConfigError(f"{where} ({key}): a workshop source needs a 'workshop_id'")
        return WorkshopSource(key=key, workshop_id=int(workshop_id), name=name)

    if kind == "local":
        path = table.get("path")
        if not path:
            raise ConfigError(f"{where} ({key}): a local source needs a 'path'")
        return LocalSource(key=key, path=Path(path), name=name)

    raise ConfigError(f"{where} ({key}): unknown kind {kind!r}; expected git, workshop or local")


def load(path: Path | str = DEFAULT_CONFIG) -> BuildConfig:
    """Read a build configuration from TOML."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"no build config at {path}")

    with path.open("rb") as handle:
        data = tomllib.load(handle)

    game = data.get("game", {})
    game_path = game.get("path")

    config = BuildConfig(
        game_path=Path(game_path) if game_path else None,
        sources=[
            _source_from_table(t, where=f"{path} [[sources]]")
            for t in data.get("sources", [])
        ],
        reference_sources=[
            _source_from_table(t, where=f"{path} [[reference_sources]]")
            for t in data.get("reference_sources", [])
        ],
        vendor_root=Path(data.get("build", {}).get("vendor_root", "vendor")),
        output_root=Path(data.get("build", {}).get("output_root", "build")),
        language=data.get("build", {}).get("language", "english"),
        path=path,
    )

    keys = [s.key for s in (*config.sources, *config.reference_sources)]
    duplicates = {k for k in keys if keys.count(k) > 1}
    if duplicates:
        raise ConfigError(f"{path}: duplicate source keys: {', '.join(sorted(duplicates))}")

    return config
