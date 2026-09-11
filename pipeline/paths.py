"""Canonical relative paths the pipeline reads from a game or mod root.

Kept in one place because the same list drives three things that must not drift
apart: which directories get vendored from git, which get swept by the roundtrip
oracle, and which get scanned for assets. A mismatch shows up as a mod whose
icons are silently missing, or a source that parses clean only because half of
it was never fetched.
"""

from __future__ import annotations

#: Script directories, relative to a source root. Order is irrelevant; these are
#: read independently.
SCRIPT_SUBDIRS = (
    "common/technology",
    "common/scripted_variables",
    "common/scripted_triggers",
    "common/inline_scripts",
)

#: Asset directories, relative to a source root.
#:
#: Technology icons are resolved purely by convention -- a technology never
#: declares an ``icon`` field, so the file is always
#: ``technologies/<key>.dds``. Ascension perk icons are needed because perks are
#: the highest-priority gate mechanism and a gated technology has to be able to
#: show what gates it.
ICON_SUBDIRS = (
    "gfx/interface/icons/technologies",
    "gfx/interface/icons/ascension_perks",
)

#: Everything a build needs from a source. Used as the sparse-checkout set for
#: vendored mods; ``localisation`` is whole-tree because language files sit
#: directly under it alongside a ``replace/`` override directory.
SOURCE_SUBDIRS = ("common", "localisation", *ICON_SUBDIRS)


def icon_directories(root) -> list:
    """Existing icon directories under ``root``, in declaration order."""
    from pathlib import Path

    root = Path(root)
    return [root / sub for sub in ICON_SUBDIRS if (root / sub).is_dir()]
