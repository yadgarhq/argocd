"""Which migration a -db pin may add without a human (ADR-1099, car C10 part 3).

PURE: a string in, a verdict out. No file, network or database is read here;
`pin_bump.migration_audit` (E4) hands in each added migration's Rust `sql:`
expression and the seed allowlist, and records the verdict.

A migration is admitted only when ALL of these hold:

1. its `sql:` is a Rust string literal (`"…".into()`, `.to_string()`,
   `.to_owned()`, `String::from("…")`, raw `r#"…"#`). `format!`, `concat!`, a
   const or anything else is non-literal: the SQL that runs is not the text
   read here;
2. the decoded SQL is ONE statement. The lexer splits on `;` outside quotes,
   backticks and comments; a single trailing `;` is allowed, any other `;` is
   red (Max, 2026-10-31: no statement-wise admission);
3. that statement is one of the four classes below, read by a positive
   grammar. A token the grammar does not expect is red, never skipped.

| Class | Statement |
| --- | --- |
| A | `CREATE TABLE [IF NOT EXISTS] t (…) [ENGINE=InnoDB] [CHARSET] [COLLATE] [COMMENT]`: no OR REPLACE, TEMPORARY, SELECT, LIKE, VALUES, TABLE, WITH |
| B | `CREATE INDEX [IF NOT EXISTS] i ON t (cols) [USING …] [COMMENT '…']`: non-unique |
| C | `ALTER TABLE t ADD COLUMN …[, ADD COLUMN …]`: each nullable or `NOT NULL DEFAULT <literal>` |
| D | `INSERT [IGNORE] INTO t (cols) VALUES (literals)[, …]` whose (module, table, cols) is allowlisted |

Class D (car 3) takes one value per column in every row, each a number, a
SINGLE-quoted ASCII string (a double-quoted one is an identifier under
ANSI_QUOTES), NULL, TRUE or FALSE; no repeated column. An allowlist entry of
the same module and the EXACT table name (MariaDB table names are
case-sensitive on Linux) admits it when the INSERT's columns are a SUBSET of
the entry's, compared case-insensitively as MariaDB compares column names.
The subset rule is the default Max has not ruled otherwise on. `pin_bump`
parses the allowlist from the base; when it is unusable, every INSERT is red
with that reason and the DDL classes are unaffected.

Class A is a NEW table, so its own CHECK, FOREIGN KEY and UNIQUE KEY touch no
existing row and stay green; LIKE is red only as `CREATE TABLE t LIKE s` or
`(LIKE s)`, never as an operator inside a CHECK; SELECT is red anywhere in the
statement, and after the column list only `ENGINE [=] InnoDB`, `[DEFAULT]
{CHARSET | CHARACTER SET} [=] name`, `[DEFAULT] COLLATE [=] name` and
`COMMENT [=] '…'` are allowed, each once: DATA / INDEX DIRECTORY, CONNECTION,
another engine and PARTITION BY are red. Class C allows per spec only: a type from `COLUMN_TYPES` and its
literal arguments, UNSIGNED / SIGNED / ZEROFILL, CHARACTER SET
/ CHARSET / COLLATE, NULL or NOT NULL, DEFAULT <literal>, COMMENT '<literal>',
AFTER <column> / FIRST, and IF NOT EXISTS, each attribute once; a TIMESTAMP
needs an explicit NULL or a literal DEFAULT; an inline UNIQUE, PRIMARY KEY,
CHECK, REFERENCES, AUTO_INCREMENT, a generated column, ON UPDATE and an
ALGORITHM= / LOCK= clause are red.

FAIL CLOSED in the lexer. It is ASCII, as MariaDB's is: whitespace is
`\t\n\v\f\r` and space only, `-- ` needs an ASCII space or control character
after it, digits are `[0-9]`, and any other non-ASCII character outside a
string, a quoted identifier or a comment is red. A backslash inside a string literal is red, because
whether it escapes the quote depends on the server's `NO_BACKSLASH_ESCAPES`,
and an under-split could hide a second statement. An executable comment
(`/*!…*/`, `/*M!…*/`) is red: the server runs what this lexer would skip.

The checksum is SHA-256 of the decoded literal's UTF-8 bytes, lowercase hex:
byte for byte what `yadgar-store` records in its ledger (`Migration::checksum`,
`store/src/migrate/checksum.rs`).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Sequence

CLASSES = {
    "A": "CREATE TABLE",
    "B": "non-unique CREATE INDEX",
    "C": "ALTER TABLE ADD COLUMN, nullable or NOT NULL DEFAULT <literal>",
    "D": "allowlisted literal INSERT",
}

MULTI_STATEMENT = "multi-statement migration; ruled red (ADR-1099)"
NON_LITERAL = "non-literal sql (not one Rust string literal: format!, concat!, a const or an expression); the SQL that runs cannot be read"


@dataclass(frozen=True, kw_only=True)
class SeedEntry:
    """One entry of `scripts/gates/migration_seed_allowlist.json` (ADR-1099), parsed from the BASE by `pin_bump`."""

    module: str
    table: str
    columns: tuple[str, ...]
    reason: str
    adr: str


@dataclass(frozen=True, kw_only=True)
class StatementVerdict:
    text: str
    statement_class: str | None
    reason: str


@dataclass(frozen=True, kw_only=True)
class MigrationVerdict:
    """`reason` is None when admitted; `sql` and `checksum` are None when the literal could not be read; `seed_entry`
    is the allowlist entry that admitted a class D INSERT (the first matching one in file order), else None."""

    sql: str | None
    checksum: str | None
    statements: tuple[StatementVerdict, ...]
    statement_class: str | None
    reason: str | None
    seed_entry: SeedEntry | None = None

    @property
    def admitted(self) -> bool:
        return self.reason is None


class Red(Exception):
    """A statement outside every class; the message is the reason."""


# ── the Rust literal ────────────────────────────────────────────────────────

_PLAIN = r'"(?P<plain>(?:[^"\\]|\\.)*)"'
# A raw literal ends at the FIRST `"` followed by its own hashes, as rustc ends it. A lazy `.*?` is not enough: under
# `fullmatch` it backtracks past that delimiter, so `r#"a"#.to_string() + X + r#"b"#` would read as one literal.
_RAW = r'r(?P<hashes>#*)"(?P<raw>(?:(?!"(?P=hashes)).)*)"(?P=hashes)'
_CONVERSION = r"(?:\s*\.\s*(?:into|to_string|to_owned)\s*\(\s*\))?"
LITERAL_EXPRESSION = re.compile(rf"(?:{_PLAIN}|{_RAW}){_CONVERSION}", re.DOTALL | re.ASCII)
STRING_FROM = re.compile(rf"String\s*::\s*from\s*\(\s*(?:{_PLAIN}|{_RAW})\s*\)", re.DOTALL | re.ASCII)
SIMPLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "0": "\0", "'": "'", '"': '"'}


def _unescape(body: str) -> str | None:
    out: list[str] = []
    i = 0
    while i < len(body):
        char = body[i]
        if char != "\\":
            out.append(char)
            i += 1
            continue
        nxt = body[i + 1] if i + 1 < len(body) else ""
        if nxt in SIMPLE_ESCAPES:
            out.append(SIMPLE_ESCAPES[nxt])
            i += 2
        elif nxt == "\n":
            # A line continuation: rustc skips the newline and every whitespace character after it.
            i += 2
            while i < len(body) and body[i] in " \t\n\r":
                i += 1
        elif nxt == "x" and re.fullmatch(r"[0-7][0-9a-fA-F]", body[i + 2 : i + 4]):
            out.append(chr(int(body[i + 2 : i + 4], 16)))
            i += 4
        elif nxt == "u" and (match := re.match(r"u\{([0-9a-fA-F_]{1,8})\}", body[i + 1 :])):
            point = int(match.group(1).replace("_", ""), 16)
            if 0xD800 <= point <= 0xDFFF or point > 0x10FFFF:
                return None  # rustc refuses a surrogate or an out-of-range scalar
            out.append(chr(point))
            i += 1 + match.end()
        else:
            return None
    return "".join(out)


def rust_literal(expression: str) -> str | None:
    """The string the Rust `expression` builds when it is one string literal (converted to `String`); else None."""
    text = expression.strip().replace("\r\n", "\n")
    match = LITERAL_EXPRESSION.fullmatch(text) or STRING_FROM.fullmatch(text)
    if not match:
        return None
    if match.group("raw") is not None:
        return match.group("raw")
    return _unescape(match.group("plain"))


# ── the lexer ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Token:
    kind: str  # word, ident, string, number, punct
    value: str  # a word upper-cased; an identifier or string unquoted
    raw: str = field(default="", compare=False)  # the source text, quotes included: a word's case, a string's quote

    def is_word(self, *words: str) -> bool:
        return self.kind == "word" and self.value in words


WORD = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*", re.ASCII)
NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?", re.ASCII)
# MariaDB's lexer is ASCII: only these are whitespace, and `-- ` needs an ASCII space or control character after it.
# A Unicode space (U+00A0, U+2028) is no separator to the server, so it is red here, never skipped.
WHITESPACE = " \t\n\v\f\r"
PUNCT = set("(),;=.-+*<>!%/&|^~:")


def _quoted(sql: str, start: int) -> tuple[str, int]:
    quote, i, out = sql[start], start + 1, []
    while i < len(sql):
        if sql[i] == "\\" and quote != "`":
            raise Red("unparseable: a backslash in a string literal (its meaning depends on NO_BACKSLASH_ESCAPES)")
        if sql[i] == quote:
            if sql.startswith(quote * 2, i):
                out.append(quote)
                i += 2
                continue
            return "".join(out), i + 1
        out.append(sql[i])
        i += 1
    raise Red(f"unparseable: unterminated {quote} literal")


def tokens(sql: str) -> list[Token]:
    """MariaDB's lexing of `sql`: comments dropped, literals unquoted. Raises `Red` on anything it cannot read."""
    found: list[Token] = []
    i = 0
    while i < len(sql):
        char = sql[i]
        if char in WHITESPACE:
            i += 1
        elif sql.startswith("--", i) and (i + 2 == len(sql) or sql[i + 2] == " " or ord(sql[i + 2]) < 32):
            stop = sql.find("\n", i)
            i = len(sql) if stop < 0 else stop
        elif char == "#":
            stop = sql.find("\n", i)
            i = len(sql) if stop < 0 else stop
        elif sql.startswith("/*", i):
            if sql.startswith("/*!", i) or sql.startswith("/*M!", i):
                raise Red("executable comment (/*! */): the server runs what a comment hides")
            stop = sql.find("*/", i + 2)
            if stop < 0:
                raise Red("unparseable: unterminated /* comment")
            i = stop + 2
        elif char in "'\"`":
            value, stop = _quoted(sql, i)
            found.append(Token("ident" if char == "`" else "string", value, sql[i:stop]))
            i = stop
        elif match := NUMBER.match(sql, i):
            found.append(Token("number", match.group(), match.group()))
            i = match.end()
        elif match := WORD.match(sql, i):
            found.append(Token("word", match.group().upper(), match.group()))
            i = match.end()
        elif char in PUNCT:
            found.append(Token("punct", char))
            i += 1
        else:
            raise Red(f"unparseable: unexpected character {char!r}")
    return found


def statements(sql: str) -> list[list[Token]]:
    """`sql`'s statements, split on `;` outside literals and comments; one trailing `;` ends the last one."""
    pieces: list[list[Token]] = [[]]
    for token in tokens(sql):
        if token == Token("punct", ";"):
            pieces.append([])
        else:
            pieces[-1].append(token)
    if len(pieces) > 1 and not pieces[-1]:
        pieces.pop()
    return pieces


# ── the grammar ─────────────────────────────────────────────────────────────


class Cursor:
    def __init__(self, items: list[Token]) -> None:
        self.items, self.at = items, 0

    def peek(self, offset: int = 0) -> Token | None:
        index = self.at + offset
        return self.items[index] if index < len(self.items) else None

    def done(self) -> bool:
        return self.at >= len(self.items)

    def take(self) -> Token:
        token = self.peek()
        if token is None:
            raise Red("unparseable: the statement ends early")
        self.at += 1
        return token

    def accept(self, *words: str) -> bool:
        """Consume the words in sequence when they all come next."""
        if all((t := self.peek(k)) is not None and t.is_word(w) for k, w in enumerate(words)):
            self.at += len(words)
            return True
        return False

    def expect(self, *words: str, why: str) -> None:
        if not self.accept(*words):
            raise Red(why)

    def punct(self, char: str) -> bool:
        if self.peek() == Token("punct", char):
            self.at += 1
            return True
        return False

    def name(self, what: str) -> str:
        token = self.take()
        if token.kind not in ("word", "ident"):
            raise Red(f"unparseable: expected a {what} name")
        if self.peek() == Token("punct", "."):
            raise Red(f"a qualified {what} name is not admitted")
        return token.value.lower() if token.kind == "word" else token.value

    def group(self) -> list[Token]:
        """The tokens between a `(` at the cursor and its matching `)`, both consumed."""
        if not self.punct("("):
            raise Red("unparseable: expected (")
        depth, start = 1, self.at
        while depth:
            token = self.take()
            depth += {Token("punct", "("): 1, Token("punct", ")"): -1}.get(token, 0)
        return self.items[start : self.at - 1]


def _balanced(items: list[Token]) -> bool:
    depth = 0
    for token in items:
        depth += {Token("punct", "("): 1, Token("punct", ")"): -1}.get(token, 0)
        if depth < 0:
            return False
    return depth == 0


def _split_commas(items: list[Token]) -> list[list[Token]]:
    parts: list[list[Token]] = [[]]
    depth = 0
    for token in items:
        if token == Token("punct", ",") and depth == 0:
            parts.append([])
            continue
        depth += {Token("punct", "("): 1, Token("punct", ")"): -1}.get(token, 0)
        parts[-1].append(token)
    return parts


def _literal(cursor: Cursor) -> bool:
    """Consume one SQL literal (single-quoted string, signed number, NULL, TRUE, FALSE); False, consuming nothing, if
    none. A double-quoted text is an identifier under ANSI_QUOTES, so it is no literal (yaadgaar 511)."""
    token = cursor.peek()
    if token is None:
        return False
    if token.kind == "string" and not token.raw.startswith("'"):
        return False
    if token.kind in ("string", "number") or token.is_word("NULL", "TRUE", "FALSE"):
        cursor.at += 1
        return True
    nxt = cursor.peek(1)
    if token in (Token("punct", "-"), Token("punct", "+")) and nxt is not None and nxt.kind == "number":
        cursor.at += 2
        return True
    return False


def _create_table(cursor: Cursor) -> str:
    cursor.accept("IF", "NOT", "EXISTS")
    cursor.name("table")
    if cursor.peek() is None or cursor.peek() != Token("punct", "("):
        raise Red("CREATE TABLE without a column list (LIKE, AS SELECT) is not admitted")
    if any(token.is_word("SELECT") for token in cursor.items):
        raise Red("CREATE TABLE with a SELECT is not admitted (it copies existing rows)")
    if any(token.kind == "string" and not token.raw.startswith("'") for token in cursor.items):
        raise Red("CREATE TABLE with a double-quoted text is not admitted (an identifier under ANSI_QUOTES, not a string)")
    body = cursor.group()
    if not body or body[0].is_word("LIKE", "VALUES", "TABLE", "WITH") or body[0] == Token("punct", "("):
        raise Red(f"CREATE TABLE ({body[0].value if body else ''} …) is not admitted; only column and key definitions")
    _table_options(cursor)
    return "A"


def _table_options(cursor: Cursor) -> None:
    """A positive list, each at most once, in any order: ENGINE [=] InnoDB, [DEFAULT] {CHARSET | CHARACTER SET} [=]
    name, [DEFAULT] COLLATE [=] name, COMMENT [=] '…'. Anything else (DATA / INDEX DIRECTORY, CONNECTION, another
    engine, PARTITION BY, a comma) is red: those reach the filesystem, another server, or a non-transactional
    engine."""
    seen: set[str] = set()

    def once(option: str) -> None:
        if option in seen:
            raise Red(f"CREATE TABLE option {option} given twice")
        seen.add(option)

    while not cursor.done():
        if cursor.accept("ENGINE"):
            once("ENGINE")
            cursor.punct("=")
            if not cursor.accept("INNODB"):
                raise Red(f"CREATE TABLE ENGINE {cursor.take().value} is not admitted; only InnoDB")
            continue
        default = cursor.accept("DEFAULT")
        if cursor.accept("CHARSET") or cursor.accept("CHARACTER", "SET"):
            once("CHARSET")
        elif cursor.accept("COLLATE"):
            once("COLLATE")
        elif not default and cursor.accept("COMMENT"):
            once("COMMENT")
            cursor.punct("=")
            if cursor.take().kind != "string":
                raise Red("CREATE TABLE COMMENT without a string")
            continue
        else:
            token = cursor.take()
            raise Red(f"CREATE TABLE option {token.value} is not admitted (ADR-1099: ENGINE=InnoDB, CHARSET, COLLATE, COMMENT)")
        cursor.punct("=")
        cursor.name("character set or collation")


def _create_index(cursor: Cursor) -> str:
    cursor.accept("IF", "NOT", "EXISTS")
    cursor.name("index")
    cursor.expect("ON", why="unparseable: CREATE INDEX without ON")
    cursor.name("table")
    for part in _split_commas(cursor.group()):
        column = Cursor(part)
        column.name("column")
        if column.peek() == Token("punct", "("):
            prefix = column.group()
            if len(prefix) != 1 or prefix[0].kind != "number":
                raise Red("an index key part other than a column or a column prefix is not admitted")
        column.accept("ASC") or column.accept("DESC")
        if not column.done():
            raise Red("an index key part other than a column or a column prefix is not admitted")
    while not cursor.done():
        if cursor.accept("USING"):
            if not (cursor.accept("BTREE") or cursor.accept("HASH")):
                raise Red("unparseable: USING without BTREE or HASH")
        elif cursor.accept("COMMENT"):
            if cursor.take().kind != "string":
                raise Red("unparseable: COMMENT without a string")
        else:
            raise Red(f"index option {cursor.take().value} is not admitted")
    return "B"


COLUMN_ATTRIBUTES = ("UNSIGNED", "SIGNED", "ZEROFILL")
# A positive list: an alias such as SERIAL (BIGINT UNSIGNED NOT NULL AUTO_INCREMENT UNIQUE) is not a plain type.
COLUMN_TYPES = frozenset(
    "TINYINT SMALLINT MEDIUMINT INT INTEGER BIGINT DECIMAL DEC NUMERIC FLOAT DOUBLE REAL BIT BOOL BOOLEAN "
    "CHAR VARCHAR BINARY VARBINARY TINYTEXT TEXT MEDIUMTEXT LONGTEXT TINYBLOB BLOB MEDIUMBLOB LONGBLOB JSON "
    "DATE DATETIME TIMESTAMP TIME YEAR ENUM SET".split()
)


def _add_column(spec: list[Token]) -> None:
    cursor = Cursor(spec)
    if not cursor.accept("ADD", "COLUMN"):
        head = " ".join(t.value for t in spec[: 2 if spec and spec[0].is_word("ADD") else 1]) or "an empty clause"
        raise Red(f"ALTER TABLE clause {head} is not ADD COLUMN")
    cursor.accept("IF", "NOT", "EXISTS")
    if cursor.peek() == Token("punct", "("):
        raise Red("ADD COLUMN (…) is not admitted; one column per ADD COLUMN")
    column = cursor.name("column")
    kind = cursor.take()
    if kind.kind != "word" or kind.value not in COLUMN_TYPES:
        raise Red(f"column {column}: type {kind.value} is not an admitted column type")
    if cursor.peek() == Token("punct", "("):
        for argument in _split_commas(cursor.group()):
            if len(argument) != 1 or argument[0].kind not in ("number", "string"):
                raise Red(f"column {column}: a type argument that is not a literal is not admitted")
    not_null = explicit_null = has_default = False
    seen: set[str] = set()

    def once(attribute: str) -> None:
        if attribute in seen:
            raise Red(f"column {column}: {attribute} given twice")
        seen.add(attribute)

    while not cursor.done():
        if cursor.accept("NOT", "NULL"):
            once("NULL / NOT NULL")
            not_null = True
        elif cursor.accept("NULL"):
            once("NULL / NOT NULL")
            explicit_null = True
        elif cursor.accept("DEFAULT"):
            once("DEFAULT")
            if cursor.peek() is not None and cursor.peek().is_word("NULL"):
                cursor.at += 1
            elif _literal(cursor):
                has_default = True
            else:
                raise Red(f"column {column}: NOT NULL needs DEFAULT <literal>; a non-literal DEFAULT is not admitted")
        elif cursor.accept("CHARACTER", "SET") or cursor.accept("CHARSET"):
            once("CHARACTER SET")
            cursor.name("character set")
        elif cursor.accept("COLLATE"):
            once("COLLATE")
            cursor.name("collation")
        elif cursor.accept("COMMENT"):
            once("COMMENT")
            if cursor.take().kind != "string":
                raise Red(f"column {column}: COMMENT without a string")
        elif cursor.accept("AFTER"):
            once("AFTER / FIRST")
            cursor.name("column")
        elif cursor.accept("FIRST"):
            once("AFTER / FIRST")
        elif word := next((w for w in COLUMN_ATTRIBUTES if cursor.accept(w)), None):
            once(word)
        else:
            raise Red(f"column {column}: {cursor.take().value} is not admitted in ADD COLUMN")
    if not_null and not has_default:
        raise Red(f"column {column}: NOT NULL without DEFAULT <literal> is not admitted")
    # Without explicit_defaults_for_timestamp a bare TIMESTAMP becomes NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE
    # CURRENT_TIMESTAMP, which rewrites every existing row; say NULL or give a literal DEFAULT.
    if kind.value == "TIMESTAMP" and not (explicit_null or has_default):
        raise Red(f"column {column}: TIMESTAMP needs an explicit NULL or a literal DEFAULT")


def _alter_table(cursor: Cursor) -> str:
    cursor.name("table")
    for spec in _split_commas(cursor.items[cursor.at :]):
        _add_column(spec)
    cursor.at = len(cursor.items)
    return "C"


def _insert_value(cursor: Cursor) -> bool:
    """One class D value: a number, a SINGLE-quoted ASCII string, NULL, TRUE or FALSE. A double-quoted text is an
    identifier under ANSI_QUOTES, so it is no literal here; non-ASCII inside a string is red too (ADR-1099: ASCII)."""
    token = cursor.peek()
    if token is not None and token.kind == "string" and not token.value.isascii():
        return False
    return _literal(cursor)


def _insert(cursor: Cursor, module: str, allowlist: Sequence[SeedEntry], allowlist_problem: str | None) -> SeedEntry:
    """Class D: `INSERT [IGNORE] INTO t (cols) VALUES (literals)[, (literals)…]`, its table on the allowlist for
    `module` (exact name: MariaDB table names are case-sensitive on Linux) with the INSERT's columns a SUBSET of the
    entry's (column names compare case-insensitively, as MariaDB's do). Returns the first matching entry."""
    cursor.accept("IGNORE")
    cursor.expect("INTO", why="unparseable: INSERT without INTO")
    token = cursor.peek()
    cursor.name("table")
    table = token.raw if token.kind == "word" else token.value
    columns: list[str] = []
    for part in _split_commas(cursor.group()):
        if len(part) != 1 or part[0].kind not in ("word", "ident"):
            raise Red("an INSERT column list that is not plain column names is not admitted")
        # BEFORE any case fold: Python lowercases U+212A KELVIN SIGN to ASCII `k` (argocd#216 security review).
        if not part[0].value.isascii():
            raise Red(f"INSERT column {part[0].value} is not an ASCII name")
        column = part[0].value.lower()
        if column in columns:
            raise Red(f"INSERT column {column} given twice")
        columns.append(column)
    cursor.expect("VALUES", why="an INSERT that is not VALUES (literals) is not admitted (INSERT … SELECT, SET)")
    while True:
        row = _split_commas(cursor.group())
        for value in row:
            item = Cursor(value)
            if not _insert_value(item) or not item.done():
                raise Red("an INSERT value that is not a literal (a single-quoted ASCII string, a number, NULL, TRUE, FALSE) is not admitted")
            if len(value) == 0:
                raise Red("unparseable: an empty INSERT value")
        if len(row) != len(columns):
            raise Red(f"an INSERT row of {len(row)} values for {len(columns)} columns is not admitted")
        if not cursor.punct(","):
            break
    if not cursor.done():
        raise Red(f"INSERT … {cursor.take().value} is not admitted (ON DUPLICATE KEY, RETURNING)")
    if allowlist_problem is not None:
        raise Red(f"seed INSERT into {table} ({', '.join(columns)}) is not admitted: the seed allowlist is unusable ({allowlist_problem})")
    for entry in allowlist:
        if entry.module == module and entry.table == table and set(columns) <= {c.lower() for c in entry.columns}:
            return entry
    raise Red(f"seed INSERT into {table} ({', '.join(columns)}) is not on the seed allowlist for {module}")


def classify_statement(
    items: list[Token], *, module: str, allowlist: Sequence[SeedEntry], allowlist_problem: str | None = None
) -> str:
    """The class of one statement; raises `Red` with the reason otherwise."""
    return _classify_statement(items, module=module, allowlist=allowlist, allowlist_problem=allowlist_problem)[0]


def _classify_statement(
    items: list[Token], *, module: str, allowlist: Sequence[SeedEntry], allowlist_problem: str | None
) -> tuple[str, SeedEntry | None]:
    """`(class, the admitting seed entry for class D else None)`; raises `Red` with the reason otherwise."""
    entry = None
    if not items:
        raise Red("unparseable: an empty statement")
    if not _balanced(items):
        raise Red("unparseable: unbalanced parentheses")
    cursor = Cursor(items)
    if cursor.accept("CREATE", "TABLE"):
        found = _create_table(cursor)
    elif cursor.accept("CREATE", "INDEX"):
        found = _create_index(cursor)
    elif cursor.accept("ALTER", "TABLE"):
        found = _alter_table(cursor)
    elif cursor.accept("INSERT"):
        entry = _insert(cursor, module, allowlist, allowlist_problem)
        found = "D"
    else:
        raise Red(f"{items[0].value} is not an admitted statement (ADR-1099 classes A-D)")
    if not cursor.done():
        raise Red(f"unparseable: unexpected {cursor.take().value}")
    return found, entry


def shown(text: str) -> str:
    """`text` fit for a reason, the PR body and stderr (yaadgaar 511): control characters and line breaks become
    spaces, backticks are escaped, and `::` is broken so no `::error::` workflow command can form. A quoted name or
    literal in a statement is upstream text."""
    return re.sub(r"[\x00-\x1f\x7f\u0085\u2028\u2029]", " ", text).replace("`", "\\`").replace("::", ": :")


def _text(items: list[Token]) -> str:
    return shown(" ".join(t.value for t in items))


def classify(
    expression: str, *, module: str, allowlist: Sequence[SeedEntry], allowlist_problem: str | None = None
) -> MigrationVerdict:
    """The verdict on one added migration, from its Rust `sql:` expression. `allowlist_problem` says why the seed
    allowlist could not be used (malformed); every class D INSERT is then red with that reason, the DDL classes not."""
    sql = rust_literal(expression)
    if sql is None:
        return MigrationVerdict(sql=None, checksum=None, statements=(), statement_class=None, reason=NON_LITERAL)
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()

    def red(reason: str, found: tuple[StatementVerdict, ...] = ()) -> MigrationVerdict:
        return MigrationVerdict(sql=sql, checksum=checksum, statements=found, statement_class=None, reason=shown(reason))

    try:
        pieces = statements(sql)
    except Red as error:
        return red(str(error))
    if len(pieces) == 1 and not pieces[0]:
        return red("unparseable: no statement")
    judged, entries = [], []
    for piece in pieces:
        try:
            c, entry = _classify_statement(piece, module=module, allowlist=allowlist, allowlist_problem=allowlist_problem)
            judged.append(StatementVerdict(text=_text(piece), statement_class=c, reason=CLASSES[c]))
            entries.append(entry)
        except Red as error:
            judged.append(StatementVerdict(text=_text(piece), statement_class=None, reason=shown(str(error))))
    found = tuple(judged)
    if len(found) > 1:
        return red(MULTI_STATEMENT, found)
    only = found[0]
    if only.statement_class is None:
        return red(only.reason, found)
    return MigrationVerdict(
        sql=sql, checksum=checksum, statements=found, statement_class=only.statement_class, reason=None, seed_entry=entries[0]
    )
