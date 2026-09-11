"""Token kinds and the token record produced by the lexer."""

from __future__ import annotations

import enum
from typing import NamedTuple


class TokenKind(enum.Enum):
    LBRACE = "{"
    RBRACE = "}"
    OPERATOR = "op"
    STRING = "string"
    BARE = "bare"


class Token(NamedTuple):
    """A single lexical unit.

    ``offset`` is the index into the *decoded* source text. Line and column are
    derived from it on demand rather than tracked per token, because the corpus
    is large and the overwhelming majority of tokens never appear in an error.
    """

    kind: TokenKind
    #: For STRING this is the *decoded* value with quotes and escapes removed.
    #: For every other kind it is the literal source text.
    value: str
    offset: int


#: Comparison operators Clausewitz permits between a key and its value.
#: ``=`` dominates, but ``<``/``>``/``<=``/``>=`` appear in ``check_variable``
#: blocks and ``!=`` in a handful of triggers.
OPERATORS = frozenset({"=", "==", "!=", "?=", "<", ">", "<=", ">="})
