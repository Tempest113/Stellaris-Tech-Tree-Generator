"""Turn Clausewitz source text into a flat token stream.

Design notes
------------
* Comments (``#`` to end of line) are discarded here. The roundtrip oracle is
  ``parse(serialize(parse(x))) == parse(x)``, which is idempotence of the
  parse/serialize pair rather than byte-for-byte fidelity, so comments do not
  need to survive. Nothing downstream reads them.
* A single master regex drives the scan. Per-character Python loops are far too
  slow for a corpus this size.
* Line/column are computed only when an error is raised.
"""

from __future__ import annotations

import re

from .errors import LexError
from .tokens import Token, TokenKind

# Order matters: two-character operators must be tried before "=", "<" and ">".
# A bare word is "anything that is not whitespace, a brace, an operator
# character, a comment marker or a quote", which is permissive enough for
# @variables, $PARAMS$, gfx/paths, key:0 and negative numbers.
#
# Built by concatenation rather than with re.VERBOSE: in verbose mode an
# unescaped "#" inside a character class is silently treated as the start of a
# regex comment, and the resulting failure points at the wrong line entirely.
_MASTER = re.compile(
    r"(?P<ws>[\s﻿]+)"
    r"|(?P<comment>#[^\n]*)"
    r"|(?P<lbrace>\{)"
    r"|(?P<rbrace>\})"
    r"|(?P<op>[<>!?]=|==|[=<>])"
    r'|(?P<string>"(?:[^"\\]|\\.)*")'
    r'|(?P<bare>[^\s{}=<>#"]+)'
)

_STRING_ESCAPE = re.compile(r"\\(.)")


def line_column(text: str, offset: int) -> tuple[int, int]:
    """1-based line and column for a character offset."""
    line = text.count("\n", 0, offset) + 1
    last_newline = text.rfind("\n", 0, offset)
    column = offset - last_newline
    return line, column


def tokenize(text: str, *, path: str | None = None) -> list[Token]:
    """Scan ``text`` into tokens, raising :class:`LexError` on the first junk byte."""
    tokens: list[Token] = []
    append = tokens.append
    pos = 0
    length = len(text)
    match_at = _MASTER.match

    while pos < length:
        match = match_at(text, pos)
        if match is None:
            line, column = line_column(text, pos)
            raise LexError(
                f"unexpected character {text[pos]!r}", path=path, line=line, column=column
            )

        kind = match.lastgroup
        if kind == "ws" or kind == "comment":
            pos = match.end()
            continue

        if kind == "bare":
            append(Token(TokenKind.BARE, match.group(), pos))
        elif kind == "string":
            raw = match.group()[1:-1]
            append(Token(TokenKind.STRING, _STRING_ESCAPE.sub(r"\1", raw), pos))
        elif kind == "op":
            append(Token(TokenKind.OPERATOR, match.group(), pos))
        elif kind == "lbrace":
            append(Token(TokenKind.LBRACE, "{", pos))
        else:
            append(Token(TokenKind.RBRACE, "}", pos))

        pos = match.end()

    return tokens
