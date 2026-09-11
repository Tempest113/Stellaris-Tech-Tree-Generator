"""The project's correctness oracle.

``parse(serialize(parse(text))) == parse(text)`` must hold for every file in the
corpus. This is idempotence of the parse/serialize pair rather than byte-for-byte
fidelity (comments and whitespace are deliberately not preserved), but it is
still a strong check: it fails whenever the lexer, the parser and the serializer
disagree about what the language means.

It is cheap to run over the whole corpus and it catches the failure mode that
matters most here, which is a parser that silently drops or mangles constructs
in ~100 MB of third-party text nobody is going to read by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import ClausewitzError, RoundtripError
from .nodes import Block
from .parser import parse, parse_file
from .serializer import serialize
from ..paths import SCRIPT_SUBDIRS

#: Files that sit in ``common/`` but are prose, not script. Paradox ships
#: developer notes alongside real data and they do not parse by design; see
#: docs/KNOWN-CORPUS-DEFECTS.md.
NON_SCRIPT_FILENAMES = frozenset(
    {
        "HOW_TO_MAKE_NEW_SHIPS.txt",
        "000_documentation.txt",
        "00_documentation.txt",
        "00_README.txt",
        "README.txt",
        "readme.txt",
    }
)

#: The ``common/`` subdirectories the tech-tree pipeline reads, as bare names.
#: Derived from pipeline.paths so there is a single source of truth.
PIPELINE_SUBDIRS = tuple(sub.split("/", 1)[1] for sub in SCRIPT_SUBDIRS)


def check_roundtrip(text: str, *, path: str | None = None) -> Block:
    """Parse ``text``, assert the roundtrip property, and return the tree."""
    tree = parse(text, path=path)
    reparsed = parse(serialize(tree), path=f"{path or '<string>'}:<serialized>")
    if reparsed != tree:
        raise RoundtripError(
            f"{path or '<string>'}: re-parsing serialized output produced a different tree"
        )
    return tree


@dataclass
class CorpusReport:
    """Outcome of sweeping a directory tree."""

    files_checked: int = 0
    files_skipped: int = 0
    bytes_checked: int = 0
    failures: list[tuple[Path, str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.failures is None:
            self.failures = []

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        megabytes = self.bytes_checked / 1_000_000
        head = (
            f"{self.files_checked} files / {megabytes:.1f} MB checked, "
            f"{self.files_skipped} skipped, {len(self.failures)} failed"
        )
        if self.ok:
            return head
        lines = [head, ""]
        for path, message in self.failures[:40]:
            lines.append(f"  {path}: {message}")
        if len(self.failures) > 40:
            lines.append(f"  ... and {len(self.failures) - 40} more")
        return "\n".join(lines)


def check_tree(
    root: Path | str,
    *,
    pattern: str = "**/*.txt",
    skip_non_script: bool = True,
) -> CorpusReport:
    """Run the oracle over every matching file under ``root``."""
    root = Path(root)
    report = CorpusReport()

    for path in sorted(root.glob(pattern)):
        if not path.is_file():
            continue
        if skip_non_script and path.name in NON_SCRIPT_FILENAMES:
            report.files_skipped += 1
            continue
        report.files_checked += 1
        report.bytes_checked += path.stat().st_size
        try:
            tree = parse_file(path)
            reparsed = parse(serialize(tree), path=f"{path}:<serialized>")
            if reparsed != tree:
                report.failures.append((path, "roundtrip mismatch"))
        except ClausewitzError as exc:
            report.failures.append((path, str(exc)))

    return report
