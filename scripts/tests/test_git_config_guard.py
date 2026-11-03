"""The shared-`.git/config` guard of `scripts/tests/conftest.py` (yaadgaar 498, ruled 2026-11-03).

Some test, once, left `user.name=t` in the REAL checkout's `.git/config`. Nobody found which. This
is the tripwire instead: a test that leaves a `user.*` key in the shared repo config fails, naming
the key and the value.

Every repository built here is a throwaway under `tmp_path`. Identity reaches git through `-c`
only, never through `git config`, and the environment drops every `GIT_*` (a hook's GIT_DIR would
override `-C`) and the user's own git config.

WHAT IS ASSERTED, each with a red case:

  1. A written `user.name=t` is reported, key and value; a changed value shows both values; a
     removed key is reported; a change to a key outside `user.*` is not; unchanged bytes pass.
  2. `common_dir` of a linked worktree is the main repository's `.git`: a worktree shares it.
  3. The conftest wiring, in a nested pytest run on a throwaway repository: a test that runs
     `git config user.name t` fails and is named; a clean test passes; a write made by a
     session-scoped fixture's teardown (outside any test) fails the session.

Run: python3 -m pytest scripts/tests/test_git_config_guard.py -q
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("git_config_guard", TESTS / "git_config_guard.py")
gcg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gcg)


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return env


def git(repo: Path, *args: str) -> str:
    command = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false", *args]
    return subprocess.run(command, capture_output=True, text=True, check=True, env=clean_env()).stdout.strip()


def make_repo(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    return path / ".git" / "config"


# ── 1. what counts as a change ───────────────────────────────────────────────


def test_a_written_user_name_is_reported_with_key_and_value(tmp_path: Path) -> None:
    config = make_repo(tmp_path / "repo")
    before = config.read_bytes()
    git(tmp_path / "repo", "config", "user.name", "t")
    with pytest.raises(gcg.SharedConfigDirty) as caught:
        gcg.assert_unchanged(config, before, "test_x")
    assert "user.name=t" in str(caught.value)
    assert "test_x" in str(caught.value)


def test_a_changed_value_shows_both_values(tmp_path: Path) -> None:
    config = make_repo(tmp_path / "repo")
    git(tmp_path / "repo", "config", "user.email", "a@example.org")
    before = config.read_bytes()
    git(tmp_path / "repo", "config", "user.email", "b@example.org")
    with pytest.raises(gcg.SharedConfigDirty) as caught:
        gcg.assert_unchanged(config, before, "test_x")
    assert "user.email=a@example.org" in str(caught.value)
    assert "user.email=b@example.org" in str(caught.value)


def test_a_removed_key_is_reported(tmp_path: Path) -> None:
    config = make_repo(tmp_path / "repo")
    git(tmp_path / "repo", "config", "user.name", "kept")
    before = config.read_bytes()
    git(tmp_path / "repo", "config", "--unset", "user.name")
    with pytest.raises(gcg.SharedConfigDirty) as caught:
        gcg.assert_unchanged(config, before, "test_x")
    assert "user.name=kept" in str(caught.value)


def test_a_key_outside_user_is_not_reported(tmp_path: Path) -> None:
    config = make_repo(tmp_path / "repo")
    before = config.read_bytes()
    git(tmp_path / "repo", "config", "core.hooksPath", "elsewhere")
    assert config.read_bytes() != before, "the fixture must change the file for this case to mean anything"
    gcg.assert_unchanged(config, before, "test_x")


def test_unchanged_bytes_pass_and_a_preexisting_user_key_is_not_a_change(tmp_path: Path) -> None:
    config = make_repo(tmp_path / "repo")
    git(tmp_path / "repo", "config", "user.name", "already-there")
    gcg.assert_unchanged(config, config.read_bytes(), "test_x")


# ── 2. the shared config of a worktree ───────────────────────────────────────


def test_common_dir_of_a_linked_worktree_is_the_main_git_dir(tmp_path: Path) -> None:
    main = tmp_path / "main"
    make_repo(main)
    git(main, "commit", "-q", "--allow-empty", "-m", "c")
    linked = tmp_path / "linked"
    git(main, "worktree", "add", "-q", "-b", "side", str(linked))
    shared = gcg.common_dir(linked, env=clean_env())
    assert shared == (main / ".git").resolve()
    config = shared / "config"
    before = config.read_bytes()
    git(linked, "config", "user.name", "t")
    with pytest.raises(gcg.SharedConfigDirty) as caught:
        gcg.assert_unchanged(config, before, "test_x")
    assert "user.name=t" in str(caught.value)


def test_common_dir_outside_a_repository_is_an_error_not_a_pass(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    with pytest.raises(gcg.GuardUnavailable):
        gcg.common_dir(tmp_path / "plain", env={**clean_env(), "GIT_CEILING_DIRECTORIES": str(tmp_path)})


# ── 3. the wiring, in a nested run ───────────────────────────────────────────

NESTED_TESTS = '''
import subprocess


def test_clean():
    pass


def test_culprit():
    subprocess.run(["git", "config", "user.name", "t"], check=True)


def test_zlast():
    pass
'''

NESTED_SESSION_ONLY = '''
import subprocess

import pytest


@pytest.fixture(scope="session")
def leak_at_the_end():
    yield
    subprocess.run(["git", "config", "user.email", "late@t"], check=True)


def test_uses_it(leak_at_the_end):
    pass
'''


def nested(tmp_path: Path, test_source: str) -> subprocess.CompletedProcess[str]:
    repo = tmp_path / "nested"
    make_repo(repo)
    for name in ("conftest.py", "git_config_guard.py"):
        shutil.copy(TESTS / name, repo / name)
    (repo / "test_nested.py").write_text(test_source)
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(repo), str(repo)]
    return subprocess.run(command, capture_output=True, text=True, cwd=repo, env=clean_env())


def test_wiring_a_culprit_test_fails_and_is_named(tmp_path: Path) -> None:
    done = nested(tmp_path, NESTED_TESTS)
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert "user.name=t" in out, out
    assert "test_nested.py::test_culprit left user.*" in out, out
    assert "3 passed" in out and "2 errors" in out, out


def test_wiring_a_write_outside_any_test_fails_the_session(tmp_path: Path) -> None:
    done = nested(tmp_path, NESTED_SESSION_ONLY)
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert "user.email=late@t" in out, out
    assert "1 passed" in out and "1 error" in out, out
