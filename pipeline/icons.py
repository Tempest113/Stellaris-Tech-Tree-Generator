"""Resolve technology and ascension perk icons.

Technology icons are never declared. A technology carries no ``icon`` field
anywhere in the corpus, so the file is found purely by convention:
``gfx/interface/icons/technologies/<key>.dds``. A ``technology_swap`` changes
which key is used, via ``inherit_icon``:

* ``inherit_icon = no``  -> ``<swap name>.dds``
* ``inherit_icon = yes`` -> the parent technology's icon
* omitted                -> behaves as inherit

Some icons are simply absent, in both vanilla and mods. Twelve cases exist in
the current corpus and none of them are near-misses under another name, so
guessing would be worse than admitting it: resolution falls back to vanilla's
own placeholder, ``technologies/unknown.dds``, which the game registers as
``GFX_technology_unknown`` in ``interface/technology_view.gfx``.

Every fallback is recorded rather than applied silently. A missing icon is
usually an upstream data bug worth reporting back, and an icon that silently
becomes a placeholder looks identical to a sparse checkout that failed to fetch
anything.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path

from .loadorder import LoadOrder

#: Vanilla's placeholder, by icon stem. Registered as GFX_technology_unknown.
PLACEHOLDER_STEM = "unknown"

TECHNOLOGY_ICON_DIR = "gfx/interface/icons/technologies"
ASCENSION_PERK_ICON_DIR = "gfx/interface/icons/ascension_perks"


class Fallback(enum.Enum):
    """Why a resolved icon is not the one that was asked for."""

    #: A swap declared ``inherit_icon = no`` but shipped no icon of its own, so
    #: the parent technology's icon is used instead.
    SWAP_TO_PARENT = "swap-to-parent"
    #: Nothing matched; vanilla's placeholder is used.
    PLACEHOLDER = "placeholder"
    #: Nothing matched and no placeholder exists either.
    NONE_AVAILABLE = "none-available"


@dataclass(frozen=True)
class IconRef:
    """A resolved icon, and how honestly it was resolved."""

    #: Key that was asked for.
    requested: str
    #: Icon stem actually used. Differs from ``requested`` when a fallback fired.
    stem: str | None
    path: Path | None
    fallback: Fallback | None = None

    @property
    def is_exact(self) -> bool:
        return self.fallback is None

    @property
    def is_missing(self) -> bool:
        return self.path is None


@dataclass
class IconIndex:
    """Icon files available across a load order, later sources shadowing earlier."""

    technologies: dict[str, Path] = field(default_factory=dict)
    ascension_perks: dict[str, Path] = field(default_factory=dict)
    #: Distinct resolutions that needed a fallback, in first-seen order.
    #: Deduplicated because this is an actionable list of upstream data bugs,
    #: not a call log: a technology whose swap inherits its icon resolves the
    #: parent twice and should still be reported once.
    _fallbacks: dict[tuple[str, "Fallback"], IconRef] = field(default_factory=dict)

    @property
    def placeholder(self) -> Path | None:
        return self.technologies.get(PLACEHOLDER_STEM)

    @property
    def fallbacks(self) -> list[IconRef]:
        return list(self._fallbacks.values())

    def _record(self, ref: IconRef) -> IconRef:
        if ref.fallback is not None:
            self._fallbacks.setdefault((ref.requested, ref.fallback), ref)
        return ref

    def missing_icon_keys(self) -> list[str]:
        """Keys that resolved to the placeholder: the upstream-bug worklist."""
        return sorted(
            ref.requested
            for ref in self._fallbacks.values()
            if ref.fallback in (Fallback.PLACEHOLDER, Fallback.NONE_AVAILABLE)
        )

    def _placeholder_ref(self, requested: str) -> IconRef:
        placeholder = self.placeholder
        if placeholder is None:
            return self._record(
                IconRef(requested, None, None, Fallback.NONE_AVAILABLE)
            )
        return self._record(
            IconRef(requested, PLACEHOLDER_STEM, placeholder, Fallback.PLACEHOLDER)
        )

    def technology(self, tech_key: str) -> IconRef:
        """Resolve a technology's own icon."""
        path = self.technologies.get(tech_key)
        if path is not None:
            return IconRef(tech_key, tech_key, path)
        return self._placeholder_ref(tech_key)

    def swap(self, tech_key: str, swap_name: str, *, inherit_icon: bool) -> IconRef:
        """Resolve the icon shown when a ``technology_swap`` is active.

        ``inherit_icon = yes`` is the common case and simply reuses the parent's
        icon, which is why most swaps ship no art of their own.
        """
        if inherit_icon:
            return self.technology(tech_key)

        path = self.technologies.get(swap_name)
        if path is not None:
            return IconRef(swap_name, swap_name, path)

        # The swap claimed its own art and did not ship it. Preferring the
        # parent's icon over the placeholder keeps the card recognisable, and is
        # what the reader expects from a renamed variant of the same technology.
        parent = self.technologies.get(tech_key)
        if parent is not None:
            return self._record(
                IconRef(swap_name, tech_key, parent, Fallback.SWAP_TO_PARENT)
            )
        return self._placeholder_ref(swap_name)

    def ascension_perk(self, perk_key: str) -> IconRef:
        """Resolve an ascension perk icon, for gate badges in the detail popup."""
        path = self.ascension_perks.get(perk_key)
        if path is not None:
            return IconRef(perk_key, perk_key, path)
        return self._placeholder_ref(perk_key)

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for ref in self._fallbacks.values():
            counts[ref.fallback.value] = counts.get(ref.fallback.value, 0) + 1
        detail = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "none"
        return (
            f"{len(self.technologies)} technology icons, "
            f"{len(self.ascension_perks)} ascension perk icons; fallbacks -> {detail}"
        )


def build_index(load_order: LoadOrder) -> IconIndex:
    """Index icon files across a load order.

    Later sources shadow earlier ones by filename, mirroring how the engine
    resolves assets.
    """
    index = IconIndex()
    for source in load_order:
        for subdir, target in (
            (TECHNOLOGY_ICON_DIR, index.technologies),
            (ASCENSION_PERK_ICON_DIR, index.ascension_perks),
        ):
            directory = source.root / subdir
            if not directory.is_dir():
                continue
            for path in directory.glob("*.dds"):
                target[path.stem] = path
    return index


def load_image(path: Path):
    """Open an icon as an RGBA image.

    Stellaris ships these as uncompressed BGRA8, which Pillow reads directly.
    Converting explicitly means a future switch to a compressed format still
    produces a usable image rather than a surprise further down the pipeline.
    """
    from PIL import Image

    with Image.open(path) as image:
        return image.convert("RGBA")
