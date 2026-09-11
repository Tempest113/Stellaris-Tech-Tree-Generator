"""Locate a Stellaris installation and its mods.

Steam spreads content across an arbitrary number of library folders recorded in
``libraryfolders.vdf``, so hard-coding ``C:/Program Files (x86)/Steam`` finds
maybe half of real installs. Local mods live somewhere else again, under the
user's documents directory.

Nothing here is Stellaris-version-specific; it is all Steam layout.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

#: Stellaris on Steam.
APP_ID = "281990"

#: Default Steam roots by platform, tried in order.
_STEAM_ROOTS = {
    "win32": [
        Path("C:/Program Files (x86)/Steam"),
        Path("C:/Program Files/Steam"),
        Path("C:/Steam"),
    ],
    "darwin": [Path.home() / "Library/Application Support/Steam"],
    "linux": [
        Path.home() / ".steam/steam",
        Path.home() / ".local/share/Steam",
    ],
}

#: Matches the quoted "path" entries inside libraryfolders.vdf. The file is
#: Valve's KeyValues format; a real parser is overkill for one field.
_VDF_PATH = re.compile(r'"path"\s*"([^"]+)"')


class StellarisNotFound(RuntimeError):
    """No Stellaris installation could be located."""


@dataclass(frozen=True)
class Install:
    """A located Stellaris installation and the mod stores beside it."""

    game: Path
    workshop: Path | None
    user_data: Path | None

    @property
    def user_mods(self) -> Path | None:
        return self.user_data / "mod" if self.user_data else None

    def workshop_mod(self, mod_id: str | int) -> Path | None:
        """Path to a subscribed Workshop mod, or ``None`` if not installed."""
        if self.workshop is None:
            return None
        candidate = self.workshop / str(mod_id)
        return candidate if candidate.is_dir() else None

    @property
    def version(self) -> str | None:
        """Game version string from ``launcher-settings.json``, if readable."""
        settings = self.game / "launcher-settings.json"
        if not settings.is_file():
            return None
        match = re.search(r'"rawVersion"\s*:\s*"([^"]+)"', settings.read_text(encoding="utf-8"))
        return match.group(1) if match else None


def _platform_key() -> str:
    import sys

    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def steam_roots() -> list[Path]:
    """Candidate Steam installation roots, honouring ``STEAM_ROOT``."""
    roots: list[Path] = []
    override = os.environ.get("STEAM_ROOT")
    if override:
        roots.append(Path(override))
    roots.extend(_STEAM_ROOTS.get(_platform_key(), []))
    return [r for r in roots if r.is_dir()]


def library_folders() -> list[Path]:
    """Every Steam library folder, read from ``libraryfolders.vdf``."""
    found: list[Path] = []
    for root in steam_roots():
        found.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if not vdf.is_file():
            continue
        text = vdf.read_text(encoding="utf-8", errors="replace")
        for raw in _VDF_PATH.findall(text):
            # Paths are stored with escaped backslashes.
            candidate = Path(raw.replace("\\\\", "\\"))
            if candidate.is_dir():
                found.append(candidate)

    # Preserve order while removing duplicates.
    unique: list[Path] = []
    seen: set[str] = set()
    for path in found:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def user_data_dir() -> Path | None:
    """The Paradox user data directory holding local mods and ``dlc_load.json``."""
    candidates = [
        Path.home() / "Documents/Paradox Interactive/Stellaris",
        Path.home() / "OneDrive/Documents/Paradox Interactive/Stellaris",
        Path.home() / ".local/share/Paradox Interactive/Stellaris",
    ]
    override = os.environ.get("STELLARIS_USER_DIR")
    if override:
        candidates.insert(0, Path(override))
    return next((c for c in candidates if c.is_dir()), None)


def find_install() -> Install:
    """Locate Stellaris, or raise :class:`StellarisNotFound`.

    ``STELLARIS_PATH`` overrides discovery entirely, which is what CI and tests
    on machines without Steam should set.
    """
    override = os.environ.get("STELLARIS_PATH")
    searched: list[str] = []

    if override:
        game = Path(override)
        if not (game / "common").is_dir():
            raise StellarisNotFound(f"STELLARIS_PATH={override} has no common/ directory")
        return Install(game=game, workshop=None, user_data=user_data_dir())

    for library in library_folders():
        game = library / "steamapps" / "common" / "Stellaris"
        searched.append(str(game))
        if (game / "common").is_dir():
            workshop = library / "steamapps" / "workshop" / "content" / APP_ID
            return Install(
                game=game,
                workshop=workshop if workshop.is_dir() else None,
                user_data=user_data_dir(),
            )

    raise StellarisNotFound(
        "could not find Stellaris. Set STELLARIS_PATH to the game directory.\n"
        "Looked in:\n  " + "\n  ".join(searched or ["(no Steam libraries found)"])
    )


def try_find_install() -> Install | None:
    """Like :func:`find_install` but returns ``None`` instead of raising."""
    try:
        return find_install()
    except StellarisNotFound:
        return None
