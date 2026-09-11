"""Exceptions raised while reading Clausewitz script.

Every error carries enough location information to point a human at the exact
byte that broke, because the corpus is ~100 MB of third-party text and a bare
"unexpected token" is useless at that scale.
"""

from __future__ import annotations


class ClausewitzError(Exception):
    """Base class for all parse-time failures."""


class LexError(ClausewitzError):
    """The byte stream could not be split into tokens."""

    def __init__(self, message: str, *, path: str | None, line: int, column: int) -> None:
        self.path = path
        self.line = line
        self.column = column
        where = f"{path or '<string>'}:{line}:{column}"
        super().__init__(f"{where}: {message}")


class ParseError(ClausewitzError):
    """The token stream was not a valid document."""

    def __init__(self, message: str, *, path: str | None, line: int, column: int) -> None:
        self.path = path
        self.line = line
        self.column = column
        where = f"{path or '<string>'}:{line}:{column}"
        super().__init__(f"{where}: {message}")


class RoundtripError(ClausewitzError):
    """serialize() produced text that does not re-parse to the same tree.

    This is the project's correctness oracle; it firing means the lexer, the
    parser or the serializer disagree about the language.
    """
