"""Fetch mod sources from git, pinned to an exact commit.

Why not just read the Steam Workshop copy: a local install is not a reproducible
input. It can be stale because Steam has not re-downloaded it, it can be a
maintainer's working copy sitting on a feature branch, and it carries no
identifier anyone else can check a build against. Gigastructures publishes its
shipping content on a git branch, so pinning a commit gives every build a
verifiable, quotable source.

Efficiency matters here. Gigastructures is ~1.9 GB, of which ~1.8 GB is artwork
this project never opens. A blobless, sparse, shallow checkout of just the
needed directories fetches roughly 49 MB instead.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: Directories a tech-tree build actually reads from a mod. Anything else is
#: artwork, audio or map data.
DEFAULT_SPARSE_PATHS = (
    "common",
    "localisation",
    "gfx/interface/icons/technologies",
)


class VendorError(RuntimeError):
    """A source could not be fetched or verified."""


@dataclass(frozen=True)
class GitSource:
    """A mod published on a git remote."""

    key: str
    repo: str
    #: Branch or tag to track. Used when ``commit`` is unset, and reported by
    #: the upstream watch.
    ref: str = "HEAD"
    #: Exact commit to check out. Leave unset only for exploratory builds; a
    #: published dataset should always name one.
    commit: str | None = None
    name: str | None = None
    sparse_paths: tuple[str, ...] = DEFAULT_SPARSE_PATHS

    @property
    def display_name(self) -> str:
        return self.name or self.key


@dataclass
class VendorResult:
    """Where a source landed and what was actually checked out."""

    source: GitSource
    path: Path
    commit: str
    #: True when the build pinned no commit and simply took the branch tip.
    floating: bool = False
    fetched: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def short_commit(self) -> str:
        return self.commit[:12]


def _git(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        command = " ".join(["git", *args])
        raise VendorError(f"{command} failed ({result.returncode}):\n{result.stderr.strip()}")
    return result


def remote_head(repo: str, ref: str) -> str:
    """Current commit of ``ref`` on the remote, without cloning anything.

    This is the primitive the scheduled upstream watch uses to notice that a
    pinned source has moved.
    """
    result = _git("ls-remote", repo, ref)
    for line in result.stdout.splitlines():
        sha, _, name = line.partition("\t")
        if name.strip() in (ref, f"refs/heads/{ref}", f"refs/tags/{ref}"):
            return sha.strip()
    raise VendorError(f"ref {ref!r} not found on {repo}")


def current_commit(path: Path) -> str | None:
    """Commit currently checked out in a vendored directory, if it is a repo."""
    if not (path / ".git").exists():
        return None
    result = _git("rev-parse", "HEAD", cwd=path, check=False)
    return result.stdout.strip() or None


def sync(
    source: GitSource,
    vendor_root: Path,
    *,
    force: bool = False,
) -> VendorResult:
    """Ensure ``source`` is checked out under ``vendor_root`` at its pinned commit.

    Returns without touching the network when the correct commit is already
    present, so repeated builds are cheap.
    """
    vendor_root = Path(vendor_root)
    target = vendor_root / source.key
    notes: list[str] = []

    wanted = source.commit
    floating = wanted is None
    if floating:
        wanted = remote_head(source.repo, source.ref)
        notes.append(
            f"no commit pinned; took {source.ref} tip {wanted[:12]}. "
            "Pin this in config for a reproducible build."
        )

    if not force and current_commit(target) == wanted:
        return VendorResult(source, target, wanted, floating, fetched=False, notes=notes)

    if force and target.exists():
        shutil.rmtree(target)

    target.mkdir(parents=True, exist_ok=True)
    if not (target / ".git").exists():
        _git("init", "-q", cwd=target)
        _git("remote", "add", "origin", source.repo, cwd=target)
    else:
        _git("remote", "set-url", "origin", source.repo, cwd=target)

    # Cone-mode sparse checkout keeps the working tree to the directories we
    # read; --filter=blob:none means the other blobs are never downloaded.
    _git("sparse-checkout", "init", "--cone", cwd=target)
    _git("sparse-checkout", "set", *source.sparse_paths, cwd=target)

    fetched = _git(
        "fetch", "--depth", "1", "--filter=blob:none", "origin", wanted, cwd=target, check=False
    )
    if fetched.returncode != 0:
        # Some remotes refuse fetch-by-sha. Fall back to the ref, then verify.
        notes.append(f"fetch by commit failed; fell back to ref {source.ref}")
        _git("fetch", "--depth", "1", "--filter=blob:none", "origin", source.ref, cwd=target)

    _git("checkout", "-q", "--detach", "FETCH_HEAD", cwd=target)

    actual = current_commit(target)
    if actual is None:
        raise VendorError(f"{source.key}: checkout produced no HEAD")
    if source.commit and actual != source.commit:
        raise VendorError(
            f"{source.key}: pinned commit {source.commit[:12]} but checked out "
            f"{actual[:12]}. The remote may have force-pushed; re-pin deliberately."
        )

    return VendorResult(source, target, actual, floating, fetched=True, notes=notes)


def verify_layout(result: VendorResult) -> list[str]:
    """Sanity-check that a vendored source looks like a Stellaris mod."""
    problems: list[str] = []
    if not (result.path / "common").is_dir():
        problems.append(f"{result.source.key}: no common/ directory at {result.path}")
    descriptor = result.path / "descriptor.mod"
    if not descriptor.is_file():
        problems.append(
            f"{result.source.key}: no descriptor.mod "
            "(add it to sparse_paths, or the repo root is not the mod root)"
        )
    return problems
