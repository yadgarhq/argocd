"""`scripts/migration_classifier.py`: which added -db migration auto-pin admits (ADR-1099, car C10 part 3).

PURE. The classifier reads the Rust `sql:` expression of one migration and
nothing else, so every case here is a string.

One constructed case per arm:

- the Rust literal: a plain, a continued (`\\` + newline), an escaped and a raw
  literal decode to the bytes rustc builds; `format!`, `concat!` and a const
  are non-literal and red;
- the lexer: a `;` inside a string, a backtick identifier or a comment does not
  split; a second statement is red, one trailing `;` is not; an executable
  comment, an unterminated literal and a backslash in a string are red;
- class A: CREATE TABLE green, with its own CHECK, FOREIGN KEY and UNIQUE KEY
  and a LIKE inside a CHECK; OR REPLACE, TEMPORARY, a SELECT anywhere, `LIKE
  t`, `(LIKE t)`, `(VALUES …)`, `(TABLE t)`, `(WITH …)` red; a name qualified by another database is red in every class;
- class B: a non-unique CREATE INDEX green; UNIQUE, FULLTEXT, ALGORITHM and
  LOCK red;
- class C: ADD COLUMN nullable or NOT NULL DEFAULT <literal> green, every spec;
  NOT NULL without a default, a non-literal default, AUTO_INCREMENT, a
  generated column, an inline UNIQUE / CHECK / REFERENCES, ALGORITHM=, a type
  outside the allowlist (SERIAL is BIGINT AUTO_INCREMENT UNIQUE) and a spec
  that is not ADD COLUMN red;
- class D: an INSERT is red with an empty allowlist, green only for an entry of
  its module and exact table whose columns hold the INSERT's (the subset rule),
  with one single-quoted ASCII string, number, NULL, TRUE or FALSE per column in
  every row; INSERT ... SELECT, a subquery, ON DUPLICATE, SET, RETURNING, a
  qualified name, a repeated column, a count mismatch and any other value red;
  an unusable allowlist makes every INSERT red with its reason, DDL untouched;
- everything else (UPDATE, DELETE, DROP, RENAME, MODIFY, ADD CONSTRAINT,
  unparseable) red;
- security review of argocd#214: class A table options are a positive list
  (ENGINE=InnoDB, CHARSET, COLLATE, COMMENT, each once); the lexer is ASCII (a
  Unicode space, a non-ASCII digit or letter outside a literal is red); a
  TIMESTAMP needs explicit NULL or a literal DEFAULT; a repeated column
  attribute is red; an invalid `\\u{…}` escape is non-literal, not a crash.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("migration_classifier", REPOSITORY / "scripts" / "migration_classifier.py")
mc = importlib.util.module_from_spec(_spec)
sys.modules["migration_classifier"] = mc
_spec.loader.exec_module(mc)


def lit(sql: str) -> str:
    """`sql` as the Rust expression a -db module writes: `"…".into()`."""
    return '"' + sql.replace("\\", "\\\\").replace('"', '\\"') + '".into()'


def verdict(sql: str, allowlist: tuple = ()) -> "mc.MigrationVerdict":
    return mc.classify(lit(sql), module="iam-db", allowlist=allowlist)


def green(sql: str, allowlist: tuple = ()) -> str:
    found = verdict(sql, allowlist)
    assert found.admitted, found.reason
    return found.statement_class


def red(sql: str, allowlist: tuple = ()) -> str:
    found = verdict(sql, allowlist)
    assert not found.admitted, (sql, found.statement_class)
    return found.reason


# ── the Rust literal ────────────────────────────────────────────────────────


def test_a_plain_literal_decodes_to_its_bytes_and_checksum() -> None:
    found = mc.classify('"CREATE TABLE t (id INT)"\n            .into()', module="iam-db", allowlist=())
    assert found.sql == "CREATE TABLE t (id INT)"
    assert found.checksum == hashlib.sha256(b"CREATE TABLE t (id INT)").hexdigest()


@pytest.mark.parametrize(
    ("expression", "decoded"),
    [
        pytest.param('"a\\\n      b".into()', "ab", id="continuation-skips-the-newline-and-indent"),
        pytest.param('"a\\n\\t\\\\\\"\\x41\\u{e9}".to_string()', 'a\n\t\\"Aé', id="escapes"),
        pytest.param('r#"a "quoted" b"#.into()', 'a "quoted" b', id="raw-hashes"),
        pytest.param('r"a\\nb".to_owned()', "a\\nb", id="raw-keeps-backslash"),
        pytest.param('r##"a"#b"##.into()', 'a"#b', id="raw-longer-hash-delimiter"),
        pytest.param('String::from("x")', "x", id="string-from"),
        pytest.param('"line one\r\n  two".into()', "line one\n  two", id="crlf-is-lf"),
    ],
)
def test_the_rust_literal_decodes_as_rustc_does(expression: str, decoded: str) -> None:
    assert mc.rust_literal(expression) == decoded


@pytest.mark.parametrize(
    "expression",
    [
        pytest.param('format!("ALTER TABLE task ADD CONSTRAINT {TITLE_CHECK} CHECK (1)")', id="format"),
        pytest.param('concat!("CREATE TABLE ", "t (id INT)").into()', id="concat"),
        pytest.param("CREATE_T.into()", id="const"),
        pytest.param("CREATE_T", id="bare-const"),
        pytest.param('"CREATE TABLE t (id INT)".into() // trailing', id="comment"),
        pytest.param('b"CREATE TABLE t (id INT)".into()', id="byte-string"),
        pytest.param('"CREATE TABLE t (id INT)".replace("t", "u")', id="method"),
        pytest.param('"unknown \\q escape".into()', id="bad-escape"),
        pytest.param('r#"a"#.to_string() + X + r#"b"#', id="raw-plus-raw"),
        pytest.param('"a".to_string() + &x + "b"', id="plain-plus-plain"),
        pytest.param('concat!("a", X)', id="concat-const"),
        pytest.param('r#"a"# r#"b"#', id="adjacent-raw"),
        pytest.param('r#"a"#r#"b"#.into()', id="adjacent-raw-no-space"),
        pytest.param('String::from(r#"a"#.to_string() + X + r#"b"#)', id="string-from-raw-plus"),
        pytest.param('"a\\" + X + "b".into()', id="escaped-quote-plus"),
    ],
)
def test_a_non_literal_sql_is_red(expression: str) -> None:
    found = mc.classify(expression, module="task-db", allowlist=())
    assert not found.admitted and "non-literal" in found.reason, found.reason
    assert found.checksum is None


# ── the lexer ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("CREATE TABLE t (a VARCHAR(8) NOT NULL DEFAULT 'x;y')", id="string"),
        pytest.param("CREATE TABLE `t;u` (a INT)", id="backtick"),
        pytest.param("CREATE TABLE t (a INT) -- a; b\n", id="dash-comment"),
        pytest.param("CREATE TABLE t (a INT) # a; b\n", id="hash-comment"),
        pytest.param("CREATE TABLE t (a INT /* a; b */)", id="block-comment"),
        pytest.param("CREATE TABLE t (a VARCHAR(8) NOT NULL DEFAULT 'it''s;')", id="doubled-quote"),
    ],
)
def test_a_semicolon_in_a_string_comment_or_backtick_does_not_split(sql: str) -> None:
    assert green(sql) == "A"


def test_a_semicolon_in_a_double_quoted_text_does_not_split() -> None:
    """Moved out of the green list above (yaadgaar 511): a double-quoted text is red now (an identifier under
    ANSI_QUOTES), but the lexer still reads it as one token, so the statement is one, not two."""
    sql = 'CREATE TABLE t (a VARCHAR(8) NOT NULL DEFAULT "x;y")'
    assert len(mc.statements(sql)) == 1
    assert "ANSI_QUOTES" in red(sql)


def test_one_trailing_semicolon_is_one_statement() -> None:
    assert green("CREATE TABLE t (a INT);\n") == "A"


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("CREATE TABLE t (a INT); CREATE TABLE u (a INT)", id="two-green"),
        pytest.param(
            "CREATE TABLE IF NOT EXISTS l (id TINYINT) ENGINE=InnoDB; INSERT IGNORE INTO l (id) VALUES (1)",
            id="project-db-5",
        ),
        pytest.param("CREATE TABLE t (a INT);;", id="double-trailing"),
        pytest.param("; CREATE TABLE t (a INT)", id="leading"),
    ],
)
def test_a_multi_statement_migration_is_red(sql: str) -> None:
    assert "multi-statement" in red(sql)


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("CREATE TABLE t (a INT) /*! , b INT */", id="mysql"),
        pytest.param("CREATE TABLE t (a INT) /*M!100100 , b INT */", id="mariadb"),
        pytest.param("CREATE TABLE t (a INT) /*!50001 ; DROP TABLE x */", id="hidden-drop"),
    ],
)
def test_executable_comments_are_red(sql: str) -> None:
    assert "executable comment" in red(sql)


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("CREATE TABLE t (a VARCHAR(8) DEFAULT 'open", id="unterminated-string"),
        pytest.param("CREATE TABLE t (a INT) /* open", id="unterminated-comment"),
        pytest.param("CREATE TABLE `t (a INT)", id="unterminated-backtick"),
        pytest.param("CREATE TABLE t (a VARCHAR(8) DEFAULT 'x\\'; DROP TABLE u; -- ')", id="backslash"),
        pytest.param("CREATE TABLE t (a INT", id="unbalanced-paren"),
        pytest.param("CREATE TABLE t (a INT))", id="extra-close-paren"),
        pytest.param("", id="empty"),
        pytest.param("   -- only a comment\n", id="comment-only"),
        pytest.param("CREATE TABLE t (a INT) \x00", id="stray-character"),
    ],
)
def test_an_unparseable_statement_is_red(sql: str) -> None:
    assert "unparseable" in red(sql)


# ── class A: CREATE TABLE ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("CREATE TABLE t (id BIGINT PRIMARY KEY)", id="plain"),
        pytest.param("create table if not exists t (id INT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4", id="if-not-exists"),
        pytest.param(
            "CREATE TABLE p (id TINYINT NOT NULL PRIMARY KEY, team VARCHAR(96), UNIQUE KEY uq (team),"
            " CONSTRAINT ck CHECK (id = 1), CONSTRAINT fk FOREIGN KEY (team) REFERENCES t (id) ON DELETE CASCADE)",
            id="own-constraints",
        ),
        pytest.param("CREATE TABLE p (path VARCHAR(9), CHECK ((path LIKE 'local/%')))", id="like-in-check"),
    ],
)
def test_create_table_is_class_a(sql: str) -> None:
    assert green(sql) == "A"


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("CREATE OR REPLACE TABLE t (id INT)", id="or-replace"),
        pytest.param("CREATE TEMPORARY TABLE t (id INT)", id="temporary"),
        pytest.param("CREATE TABLE t (id INT) SELECT id FROM u", id="select"),
        pytest.param("CREATE TABLE t (id INT) AS SELECT id FROM u", id="as-select"),
        pytest.param("CREATE TABLE t AS SELECT id FROM u", id="no-columns"),
        pytest.param("CREATE TABLE t LIKE u", id="like"),
        pytest.param("CREATE TABLE t (LIKE u)", id="paren-like"),
        pytest.param("CREATE TABLE t (SELECT id FROM u)", id="paren-select"),
        pytest.param("CREATE TABLE t (id INT, CHECK (id IN (SELECT 1)))", id="select-inside"),
        pytest.param("CREATE TABLE t (VALUES (1))", id="paren-values"),
        pytest.param("CREATE TABLE t (TABLE u)", id="paren-table"),
        pytest.param("CREATE TABLE t (WITH c AS (SELECT 1) SELECT * FROM c)", id="paren-with"),
        pytest.param("CREATE TABLE t ((SELECT 1))", id="nested-paren"),
        pytest.param("CREATE TABLE t (id INT) VALUES (1)", id="trailing-values"),
        pytest.param("CREATE TABLE t (id INT) TABLE u", id="trailing-table"),
    ],
)
def test_create_or_replace_select_like_are_red(sql: str) -> None:
    red(sql)


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("CREATE TABLE db.t (id INT)", id="create-table"),
        pytest.param("CREATE INDEX ix ON db.t (a)", id="create-index"),
        pytest.param("ALTER TABLE db.t ADD COLUMN a INT NULL", id="alter-table"),
    ],
)
def test_a_name_qualified_by_another_database_is_red(sql: str) -> None:
    assert "qualified" in red(sql)


# ── class B: CREATE INDEX ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("CREATE INDEX ix ON t (a)", id="plain"),
        pytest.param("CREATE INDEX IF NOT EXISTS ix ON t (a, b(16) DESC) USING BTREE COMMENT 'x'", id="options"),
    ],
)
def test_a_non_unique_create_index_is_class_b(sql: str) -> None:
    assert green(sql) == "B"


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("CREATE UNIQUE INDEX ix ON t (a)", id="unique"),
        pytest.param("CREATE FULLTEXT INDEX ix ON t (a)", id="fulltext"),
        pytest.param("CREATE OR REPLACE INDEX ix ON t (a)", id="or-replace"),
        pytest.param("CREATE INDEX ix ON t (a) ALGORITHM=INPLACE", id="algorithm"),
        pytest.param("CREATE INDEX ix ON t (a) LOCK=NONE", id="lock"),
        pytest.param("CREATE INDEX ix ON t ((a + 1))", id="expression"),
    ],
)
def test_a_unique_or_optioned_index_is_red(sql: str) -> None:
    red(sql)


# ── class C: ALTER TABLE ADD COLUMN ─────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("ALTER TABLE iam_user ADD COLUMN credential_epoch BIGINT UNSIGNED NOT NULL DEFAULT 0", id="iam-db-16"),
        pytest.param("ALTER TABLE t ADD COLUMN tags JSON NOT NULL DEFAULT '[]', ADD COLUMN links JSON NOT NULL DEFAULT '[]'", id="task-db-2"),
        pytest.param("ALTER TABLE t ADD COLUMN f BINARY(32) NULL DEFAULT NULL", id="nullable"),
        pytest.param("ALTER TABLE t ADD COLUMN f DECIMAL(10,2)", id="bare-nullable"),
        pytest.param("ALTER TABLE t ADD COLUMN f INT NOT NULL DEFAULT -1 COMMENT 'x' AFTER g", id="negative-after"),
        pytest.param(
            "ALTER TABLE t ADD COLUMN IF NOT EXISTS f VARCHAR(9) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL FIRST",
            id="charset",
        ),
        pytest.param("ALTER TABLE t ADD COLUMN f ENUM('a','b') NOT NULL DEFAULT 'a'", id="enum"),
    ],
)
def test_add_column_with_literal_default_is_green(sql: str) -> None:
    assert green(sql) == "C"


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("ALTER TABLE t ADD COLUMN f INT NOT NULL", id="no-default"),
        pytest.param("ALTER TABLE t ADD COLUMN f INT NOT NULL DEFAULT NULL", id="default-null"),
        pytest.param("ALTER TABLE t ADD COLUMN f TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP", id="function-default"),
    ],
)
def test_add_column_not_null_without_default_is_red(sql: str) -> None:
    assert "NOT NULL" in red(sql)


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL, ADD COLUMN b INT NOT NULL", id="second-not-null"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL, ADD COLUMN b INT AUTO_INCREMENT", id="auto-increment"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL, ADD COLUMN b INT AS (a + 1)", id="generated-as"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL, ADD COLUMN b INT GENERATED ALWAYS AS (a) STORED", id="generated"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL, ADD COLUMN b INT NULL UNIQUE", id="inline-unique"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL, ADD COLUMN b INT NULL CHECK (b > 0)", id="inline-check"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL, ADD COLUMN b INT NULL REFERENCES u (id)", id="inline-fk"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL, ADD COLUMN b INT NULL PRIMARY KEY", id="inline-pk"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL, ALGORITHM=INSTANT", id="algorithm"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL, LOCK=NONE", id="lock"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL, ADD INDEX ix (a)", id="add-index"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL, DROP COLUMN b", id="drop"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL, MODIFY b INT NULL", id="modify"),
        pytest.param("ALTER TABLE t ADD a INT NULL", id="no-column-keyword"),
        pytest.param("ALTER TABLE t ADD COLUMN (a INT NULL, b INT NULL)", id="parenthesised"),
        pytest.param("ALTER TABLE t ADD COLUMN a TIMESTAMP NULL ON UPDATE CURRENT_TIMESTAMP", id="on-update"),
        pytest.param("ALTER ONLINE TABLE t ADD COLUMN a INT NULL", id="online"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL, ADD COLUMN s SERIAL", id="serial"),
        pytest.param("ALTER TABLE t ADD COLUMN b AS (1)", id="as-in-the-type-slot"),
        pytest.param("ALTER TABLE t ADD COLUMN b NULL", id="no-type"),
        pytest.param("ALTER TABLE t ADD COLUMN b MY_DOMAIN NULL", id="unknown-type"),
    ],
)
def test_a_multi_spec_alter_with_one_bad_spec_is_red(sql: str) -> None:
    red(sql)


# ── class D: the seed INSERT, behind the allowlist hook ─────────────────────

SEED = "INSERT INTO iam_team_sync_lock (id) VALUES (1)"


def test_an_insert_is_red_while_the_allowlist_is_empty() -> None:
    """The allowlist ships empty (car 3), so iam-db 21's seed stays NEEDS-HUMAN until an entry lands on main."""
    assert "allowlist" in red(SEED)


def test_the_allowlist_hook_admits_a_listed_literal_insert() -> None:
    entry = mc.SeedEntry(module="iam-db", table="iam_team_sync_lock", columns=("id",), reason="sentinel row", adr="ADR-1099")
    assert green(SEED, (entry,)) == "D"
    assert green("INSERT IGNORE INTO iam_team_sync_lock (id) VALUES (1), (2)", (entry,)) == "D"
    other_module = mc.SeedEntry(module="task-db", table="iam_team_sync_lock", columns=("id",), reason="r", adr="ADR-1099")
    assert "allowlist" in red(SEED, (other_module,))
    assert "allowlist" in red("INSERT INTO iam_team_sync_lock (id, x) VALUES (1, 2)", (entry,))
    assert red("INSERT INTO iam_team_sync_lock (id) SELECT 1", (entry,))
    assert red("INSERT INTO iam_team_sync_lock (id) VALUES (1) ON DUPLICATE KEY UPDATE id = 1", (entry,))
    assert red("INSERT INTO iam_team_sync_lock (id) VALUES (NOW())", (entry,))


# ── class D with car 3's allowlist (PR B) ───────────────────────────────────

LOCK = mc.SeedEntry(module="iam-db", table="iam_team_sync_lock", columns=("id",), reason="sentinel row", adr="ADR-1099")
SETTING = mc.SeedEntry(
    module="iam-db", table="iam_org_setting", columns=("name", "value", "locked"), reason="org setting seed", adr="ADR-0522"
)


def test_an_insert_into_a_table_not_on_the_allowlist_is_red() -> None:
    reason = red("INSERT INTO iam_team (id) VALUES (1)", (LOCK, SETTING))
    assert reason == "seed INSERT into iam_team (id) is not on the seed allowlist for iam-db", reason


def test_an_allowlisted_insert_with_literals_is_green_and_names_its_entry() -> None:
    found = verdict("INSERT INTO iam_org_setting (name, value, locked)\n    VALUES ('owner_reads_own_record', 2, 1)", (LOCK, SETTING))
    assert found.admitted and found.statement_class == "D" and found.seed_entry == SETTING, found
    rows = verdict("INSERT IGNORE INTO iam_team_sync_lock (id) VALUES (1), (-2), (NULL), (TRUE), (FALSE), ('x')", (LOCK,))
    assert rows.admitted and rows.seed_entry == LOCK, rows.reason
    assert verdict("CREATE TABLE t (id INT)", (LOCK,)).seed_entry is None, "only class D names an entry"


def test_the_insert_columns_may_be_a_subset_of_the_entry_but_not_outside_it() -> None:
    """The chosen rule (Max has not ruled otherwise): the INSERT's columns are a SUBSET of the entry's columns."""
    assert green("INSERT INTO iam_org_setting (name, value) VALUES ('a', 1)", (SETTING,)) == "D"
    assert green("INSERT INTO iam_org_setting (locked, NAME) VALUES (1, 'a')", (SETTING,)) == "D", "column names are case-insensitive"
    reason = red("INSERT INTO iam_org_setting (name, value, owner) VALUES ('a', 1, 2)", (SETTING,))
    assert "not on the seed allowlist" in reason, reason


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("INSERT INTO iam_team_sync_lock (id) SELECT 1", id="insert-select"),
        pytest.param("INSERT INTO iam_team_sync_lock (id) VALUES ((SELECT 1))", id="subquery-value"),
        pytest.param("INSERT INTO iam_team_sync_lock (id) VALUES (1) ON DUPLICATE KEY UPDATE id = 1", id="on-duplicate"),
        pytest.param("INSERT INTO iam_team_sync_lock SET id = 1", id="set-form"),
        pytest.param("INSERT INTO iam_team_sync_lock (id) VALUES (1) RETURNING id", id="returning"),
        pytest.param("INSERT INTO iam_team_sync_lock VALUES (1)", id="no-column-list"),
        pytest.param("INSERT INTO iam_team_sync_lock (id) VALUE (1)", id="value-singular"),
        pytest.param("INSERT LOW_PRIORITY INTO iam_team_sync_lock (id) VALUES (1)", id="priority"),
        pytest.param("REPLACE INTO iam_team_sync_lock (id) VALUES (1)", id="replace"),
        pytest.param("INSERT INTO iam.iam_team_sync_lock (id) VALUES (1)", id="qualified-table"),
        pytest.param("INSERT INTO iam_team_sync_lock (iam_team_sync_lock.id) VALUES (1)", id="qualified-column"),
    ],
)
def test_an_insert_outside_the_values_grammar_is_red_even_when_allowlisted(sql: str) -> None:
    red(sql, (LOCK,))


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("NOW()", id="function-call"),
        pytest.param("@v", id="user-variable"),
        pytest.param("id", id="column-reference"),
        pytest.param("1 + 1", id="expression"),
        pytest.param("X'41'", id="hex-string"),
        pytest.param("_utf8'a'", id="introducer"),
        pytest.param("'a' 'b'", id="adjacent-strings"),
        pytest.param("1e5", id="exponent"),
        pytest.param("0x41", id="hex-number"),
        pytest.param("DEFAULT", id="default-keyword"),
        pytest.param('"a"', id="double-quoted-is-an-identifier-under-ANSI_QUOTES"),
        pytest.param("'café'", id="non-ascii-inside-a-string"),
        pytest.param("１", id="non-ascii-digit-outside-a-string"),
        pytest.param("1 ", id="non-ascii-space-outside-a-string"),
        pytest.param("", id="empty"),
    ],
)
def test_a_non_literal_or_non_ascii_insert_value_is_red(value: str) -> None:
    red(f"INSERT INTO iam_team_sync_lock (id) VALUES ({value})", (LOCK,))


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("INSERT INTO iam_team_sync_lock (id) VALUES (1, 2)", id="more-values"),
        pytest.param("INSERT INTO iam_org_setting (name, value) VALUES ('a')", id="fewer-values"),
        pytest.param("INSERT INTO iam_org_setting (name, value) VALUES ('a', 1), ('b')", id="second-row-short"),
    ],
)
def test_a_column_and_value_count_mismatch_is_red(sql: str) -> None:
    assert "values" in red(sql, (LOCK, SETTING))


def test_a_repeated_insert_column_is_red() -> None:
    assert "twice" in red("INSERT INTO iam_org_setting (name, NAME) VALUES ('a', 'b')", (SETTING,))


def test_the_insert_table_matches_the_entry_case_exactly() -> None:
    """Table names are case-sensitive on MariaDB under Linux (lower_case_table_names=0): IAM_TEAM_SYNC_LOCK is another table."""
    assert "not on the seed allowlist" in red("INSERT INTO IAM_TEAM_SYNC_LOCK (id) VALUES (1)", (LOCK,))
    assert green("INSERT INTO `iam_team_sync_lock` (`id`) VALUES (1)", (LOCK,)) == "D"


KEY_ENTRY = mc.SeedEntry(module="iam-db", table="iam_x", columns=("name", "value", "key"), reason="r", adr="ADR-1099")


@pytest.mark.parametrize("column", ["\u212aey", "k\u0131y", "\u00e9"], ids=["kelvin-sign-lowercases-to-k", "dotless-i", "e-acute"])
def test_a_non_ascii_backticked_insert_column_is_red_before_any_case_fold(column: str) -> None:
    """Security review of argocd#216: Python lowercases U+212A KELVIN SIGN to ASCII `k`, so `Key` matched `key`."""
    assert "ASCII" in red(f"INSERT INTO iam_x (`{column}`) VALUES (1)", (KEY_ENTRY,))
    assert green("INSERT INTO iam_x (`key`) VALUES (1)", (KEY_ENTRY,)) == "D"


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("INSERT INTO `t\n::error::x` (id) VALUES (1)", id="insert-table"),
        pytest.param("INSERT INTO iam_team_sync_lock (`a\r\n::error::x`) VALUES (1)", id="insert-column"),
        pytest.param("CREATE TABLE `t` (`a\n::error::x` INT) ENGINE `x\n::warning::y`", id="create-table-option"),
        pytest.param("ALTER TABLE t ADD COLUMN `c\n::error::x` INT NOT NULL", id="alter-column"),
        pytest.param("ALTER TABLE t ADD COLUMN `c``x` INT NOT NULL", id="alter-column-backtick"),
        pytest.param("UPDATE `t\u2028::error::x` SET a = 1", id="other-statement"),
        pytest.param("CREATE TABLE `t (a INT)", id="lexer-error-names-a-backtick"),
    ],
)
def test_a_reason_carries_no_line_break_workflow_command_or_raw_backtick(sql: str) -> None:
    """yaadgaar 511: a quoted name reaches the reason, the PR body and stderr; it must not break a line, start a
    `::error::` workflow command or close a markdown code span."""
    found = verdict(sql, (LOCK,))
    assert not found.admitted
    for text in (found.reason, *(s.reason for s in found.statements), *(s.text for s in found.statements)):
        assert not any(ord(c) < 32 or c in "\x7f\u0085\u2028\u2029" for c in text), repr(text)
        assert "::" not in text, repr(text)
        assert "`" not in text.replace("\\`", ""), repr(text)


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param('ALTER TABLE t ADD COLUMN c VARCHAR(8) NOT NULL DEFAULT "x"', id="class-c-default"),
        pytest.param('CREATE TABLE t (c VARCHAR(8) NOT NULL DEFAULT "x")', id="class-a-default"),
        pytest.param('CREATE TABLE t (c INT) COMMENT "x"', id="class-a-comment"),
    ],
)
def test_a_double_quoted_default_is_an_identifier_under_ansi_quotes_and_red(sql: str) -> None:
    """yaadgaar 511: under ANSI_QUOTES `"x"` is an identifier, not a string; single quotes stay green."""
    red(sql)
    assert green(sql.replace('"', "'")) in ("A", "C")


def test_an_unusable_allowlist_makes_every_insert_red_with_its_reason_and_leaves_ddl_alone() -> None:
    found = mc.classify(lit(SEED), module="iam-db", allowlist=(), allowlist_problem="scripts/gates/x.json: not JSON")
    assert not found.admitted and "scripts/gates/x.json: not JSON" in found.reason, found.reason
    ddl = mc.classify(lit("CREATE TABLE t (id INT)"), module="iam-db", allowlist=(), allowlist_problem="not JSON")
    assert ddl.admitted and ddl.statement_class == "A"


# ── everything else ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("ALTER TABLE task ADD CONSTRAINT ck CHECK (CHAR_LENGTH(title) <= 128)", id="add-constraint"),
        pytest.param("UPDATE task SET visibility = 1 WHERE visibility NOT IN (1, 2, 3)", id="update"),
        pytest.param("DELETE FROM task", id="delete"),
        pytest.param("DROP TABLE task", id="drop"),
        pytest.param("RENAME TABLE a TO b", id="rename"),
        pytest.param("ALTER TABLE project MODIFY path VARCHAR(255) NOT NULL", id="modify"),
        pytest.param("TRUNCATE task", id="truncate"),
        pytest.param("SET FOREIGN_KEY_CHECKS = 0", id="set"),
    ],
)
def test_check_constraint_update_delete_drop_rename_are_red(sql: str) -> None:
    red(sql)


def test_every_verdict_names_its_statements() -> None:
    found = verdict("CREATE TABLE t (a INT)")
    assert [(s.statement_class, s.reason) for s in found.statements] == [("A", mc.CLASSES["A"])]
    found = verdict("DROP TABLE t")
    assert found.statements[0].statement_class is None and "DROP" in found.statements[0].reason


# ── class A table options: a positive list (security review of argocd#214) ──


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("CREATE TABLE t (id INT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4", id="corpus-shape"),
        pytest.param("CREATE TABLE t (id INT) engine innodb COLLATE = utf8mb4_bin COMMENT = 'x'", id="no-equals-any-case"),
        pytest.param("CREATE TABLE t (id INT) CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_bin ENGINE=InnoDB", id="any-order"),
        pytest.param("CREATE TABLE t (id INT)", id="none"),
    ],
)
def test_the_admitted_table_options_are_green(sql: str) -> None:
    assert green(sql) == "A"


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("CREATE TABLE t (id INT) DATA DIRECTORY='/tmp/x'", id="data-directory"),
        pytest.param("CREATE TABLE t (id INT) INDEX DIRECTORY='/tmp/x'", id="index-directory"),
        pytest.param("CREATE TABLE t (id INT) ENGINE=CONNECT", id="connect"),
        pytest.param("CREATE TABLE t (id INT) ENGINE=MERGE UNION=(u)", id="merge-union"),
        pytest.param("CREATE TABLE t (id INT) ENGINE=FEDERATED CONNECTION='mysql://h/db/t'", id="federated"),
        pytest.param("CREATE TABLE t (id INT) ENGINE=InnoDB CONNECTION='x'", id="connection"),
        pytest.param("CREATE TABLE t (id INT) ENGINE=BLACKHOLE", id="blackhole"),
        pytest.param("CREATE TABLE t (id INT) ENGINE=MEMORY", id="memory"),
        pytest.param("CREATE TABLE t (id INT) PARTITION BY HASH(id) PARTITIONS 4", id="partition"),
        pytest.param("CREATE TABLE t (id INT) ENGINE=InnoDB ENGINE=MEMORY", id="engine-twice"),
        pytest.param("CREATE TABLE t (id INT) DEFAULT CHARSET=utf8mb4 CHARACTER SET latin1", id="charset-twice"),
        pytest.param("CREATE TABLE t (id INT) ENGINE=InnoDB ENGINE=InnoDB", id="innodb-twice"),
        pytest.param("CREATE TABLE t (id INT) ENGINE=InnoDB, DEFAULT CHARSET=utf8mb4", id="comma"),
        pytest.param("CREATE TABLE t (id INT) AUTO_INCREMENT=5", id="auto-increment"),
        pytest.param("CREATE TABLE t (id INT) COMMENT=x", id="comment-not-a-string"),
    ],
)
def test_any_other_table_option_is_red(sql: str) -> None:
    red(sql)


# ── the lexer is ASCII, as MariaDB's is ─────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("CREATE TABLE t (a INT) -- ; DROP TABLE u", id="nbsp-after-dashes"),
        pytest.param("CREATE TABLE t (a INT) ", id="line-separator-as-space"),
        pytest.param("CREATE TABLE t (a INT) ", id="nbsp-as-space"),
        pytest.param("ALTER TABLE t ADD COLUMN a VARCHAR(٣) NULL", id="arabic-digit"),
        pytest.param("CREATE TABLE té (a INT)", id="non-ascii-word"),
    ],
)
def test_a_non_ascii_character_outside_a_literal_is_red(sql: str) -> None:
    red(sql)


def test_non_ascii_inside_a_string_identifier_or_comment_is_kept() -> None:
    assert green("CREATE TABLE `té` (a VARCHAR(8) NOT NULL DEFAULT 'é') COMMENT ' ' -- é\n") == "A"
    assert green("CREATE TABLE t (a INT) --\t; x\n") == "A"


# ── ADD COLUMN: TIMESTAMP needs explicit NULL or a literal DEFAULT; no repeats ─


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("ALTER TABLE t ADD COLUMN at TIMESTAMP", id="bare-timestamp"),
        pytest.param("ALTER TABLE t ADD COLUMN at TIMESTAMP DEFAULT NULL", id="timestamp-default-null-only"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NOT NULL DEFAULT 1 DEFAULT NULL", id="default-twice"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL NOT NULL DEFAULT 1", id="nullness-twice"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT UNSIGNED UNSIGNED NULL", id="attribute-twice"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL COMMENT 'x' COMMENT 'y'", id="comment-twice"),
        pytest.param("ALTER TABLE t ADD COLUMN a INT NULL AFTER b FIRST", id="position-twice"),
    ],
)
def test_a_timestamp_without_explicit_null_or_a_repeated_attribute_is_red(sql: str) -> None:
    red(sql)


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("ALTER TABLE t ADD COLUMN at TIMESTAMP NULL DEFAULT NULL", id="timestamp-null"),
        pytest.param("ALTER TABLE t ADD COLUMN at TIMESTAMP NOT NULL DEFAULT '2026-01-01 00:00:00'", id="timestamp-literal"),
    ],
)
def test_a_timestamp_with_explicit_null_or_a_literal_default_is_green(sql: str) -> None:
    assert green(sql) == "C"


@pytest.mark.parametrize("escape", ["\\u{D800}", "\\u{DFFF}", "\\u{110000}"])
def test_an_invalid_unicode_escape_is_non_literal_not_a_crash(escape: str) -> None:
    found = mc.classify(f'"CREATE TABLE t (a VARCHAR(8) DEFAULT \'{escape}\')".into()', module="iam-db", allowlist=())
    assert not found.admitted and "non-literal" in found.reason, found.reason
