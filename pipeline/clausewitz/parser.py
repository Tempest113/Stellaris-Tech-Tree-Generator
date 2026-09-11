"""Build a :class:`~pipeline.clausewitz.nodes.Block` from Clausewitz source.

The grammar is small:

    document := item*
    item     := pair | scalar | block
    pair     := (bare | string) operator value
    value    := scalar | block
    block    := '{' item* '}'

Everything awkward about the language lives in the lexer (operators, comments,
bare-word charset) or in the tree model (ordered items, duplicate keys). The
parser itself stays deliberately dull.
"""

from __future__ import annotations

from pathlib import Path

from .errors import ParseError
from .lexer import line_column, tokenize
from .nodes import Block, Item, Node, Pair, Scalar
from .tokens import Token, TokenKind

#: Tried in order when a file is not valid UTF-8. Paradox shipped Windows-1252
#: for years and some older mods still do; cp1252 accepts any byte sequence, so
#: it terminates the list.
_ENCODINGS = ("utf-8-sig", "cp1252")


def _fail(message: str, tokens: list[Token], index: int, text: str, path: str | None):
    offset = tokens[index].offset if index < len(tokens) else len(text)
    line, column = line_column(text, offset)
    raise ParseError(message, path=path, line=line, column=column)


def parse(
    text: str, *, path: str | None = None, allow_unclosed_blocks: bool = False
) -> Block:
    """Parse a whole document into a top-level block.

    ``allow_unclosed_blocks`` opts into the engine's lenient handling of a file
    that ends with blocks still open. It is off by default because a truncated
    file is normally a real problem worth halting on; base game
    ``scripted_loc/scripted_loc_ruloc.txt`` is the one known case that needs it.
    """
    tokens = tokenize(text, path=path)
    items, index = _parse_items(
        tokens, 0, text, path, at_top_level=True, allow_unclosed=allow_unclosed_blocks
    )
    if index != len(tokens):
        _fail("unexpected '}' with no matching '{'", tokens, index, text, path)
    return Block(items)


def parse_file(path: Path | str, *, allow_unclosed_blocks: bool = False) -> Block:
    """Read and parse a file, tolerating Paradox's mixed encodings."""
    path = Path(path)
    raw = path.read_bytes()
    for encoding in _ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        return parse(text, path=str(path), allow_unclosed_blocks=allow_unclosed_blocks)
    raise AssertionError("cp1252 decodes any byte sequence; this is unreachable")


def _parse_items(
    tokens: list[Token],
    index: int,
    text: str,
    path: str | None,
    *,
    at_top_level: bool,
    allow_unclosed: bool = False,
) -> tuple[list[Item], int]:
    items: list[Item] = []
    total = len(tokens)

    while index < total:
        token = tokens[index]

        if token.kind is TokenKind.RBRACE:
            if at_top_level:
                return items, index
            return items, index + 1

        if token.kind is TokenKind.LBRACE:
            inner, index = _parse_items(
                tokens, index + 1, text, path, at_top_level=False, allow_unclosed=allow_unclosed
            )
            items.append(Block(inner))
            continue

        if token.kind is TokenKind.OPERATOR:
            _fail(f"operator {token.value!r} with no key before it", tokens, index, text, path)

        # A bare word or string. It is a pair if an operator follows, and a
        # plain list element otherwise.
        following = tokens[index + 1] if index + 1 < total else None
        if following is not None and following.kind is TokenKind.OPERATOR:
            value, index = _parse_value(
                tokens, index + 2, text, path, allow_unclosed=allow_unclosed
            )
            items.append(
                Pair(
                    key=token.value,
                    op=following.value,
                    value=value,
                    key_quoted=token.kind is TokenKind.STRING,
                )
            )
            continue

        items.append(Scalar(token.value, quoted=token.kind is TokenKind.STRING))
        index += 1

    if not at_top_level and not allow_unclosed:
        _fail("unclosed '{' at end of file", tokens, index, text, path)
    return items, index


def _parse_value(
    tokens: list[Token],
    index: int,
    text: str,
    path: str | None,
    *,
    allow_unclosed: bool = False,
) -> tuple[Node, int]:
    if index >= len(tokens):
        _fail("expected a value but reached end of file", tokens, index, text, path)

    token = tokens[index]
    if token.kind is TokenKind.LBRACE:
        inner, index = _parse_items(
            tokens, index + 1, text, path, at_top_level=False, allow_unclosed=allow_unclosed
        )
        return Block(inner), index
    if token.kind in (TokenKind.BARE, TokenKind.STRING):
        return Scalar(token.value, quoted=token.kind is TokenKind.STRING), index + 1

    _fail(f"expected a value, found {token.value!r}", tokens, index, text, path)
