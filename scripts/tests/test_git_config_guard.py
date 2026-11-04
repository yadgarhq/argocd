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
  4. The evasions (yaadgaar 560): an added, changed or removed `include.*`, `includeIf.*`,
     `author.*` or `committer.*` key, or `extensions.worktreeConfig`, is reported; a shared config
     deleted during the run is an error; a `config.worktree` removed during the run is reported; a `user.*` key written to a `config.worktree`
     (the main checkout's or a linked worktree's) is reported; pre-existing entries of those keys
     are not a change; and a relative GIT_DIR, as under `git --git-dir=.git commit`, still
     locates the shared config.

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


# ── 4. the evasions (yaadgaar 560) ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "value", "reported"),
    [
        ("include.path", "elsewhere", "include.path=elsewhere"),
        ("includeIf.gitdir:/.path", "elsewhere", "includeif.gitdir:/.path=elsewhere"),
        ("includeIf.onbranch:main.path", "elsewhere", "includeif.onbranch:main.path=elsewhere"),
        ("extensions.worktreeConfig", "true", "extensions.worktreeconfig=true"),
    ],
    ids=["include", "includeif-gitdir", "includeif-onbranch", "worktree-config"],
)
def test_an_added_include_or_worktree_config_key_is_reported(tmp_path: Path, key: str, value: str, reported: str) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    before = gcg.snapshot(repo / ".git")
    git(repo, "config", key, value)
    with pytest.raises(gcg.SharedConfigDirty) as caught:
        gcg.assert_snapshot_unchanged(repo / ".git", before, "test_x")
    assert reported in str(caught.value)


def test_an_include_that_sets_user_name_is_reported(tmp_path: Path) -> None:
    """The live evasion: `include.path` to a file holding `[user] name = x` changes the effective identity."""
    repo = tmp_path / "repo"
    make_repo(repo)
    (tmp_path / "identity").write_text("[user]\n\tname = x\n")
    before = gcg.snapshot(repo / ".git")
    git(repo, "config", "include.path", str(tmp_path / "identity"))
    with pytest.raises(gcg.SharedConfigDirty) as caught:
        gcg.assert_snapshot_unchanged(repo / ".git", before, "test_x")
    assert f"include.path={tmp_path / 'identity'}" in str(caught.value)


@pytest.mark.parametrize("change", ["value", "remove"])
def test_a_changed_or_removed_include_is_reported(tmp_path: Path, change: str) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    git(repo, "config", "include.path", "/home/someone/.config/git/personal")
    before = gcg.snapshot(repo / ".git")
    if change == "value":
        git(repo, "config", "include.path", "/elsewhere")
    else:
        git(repo, "config", "--unset", "include.path")
    with pytest.raises(gcg.SharedConfigDirty) as caught:
        gcg.assert_snapshot_unchanged(repo / ".git", before, "test_x")
    assert "include.path=/home/someone/.config/git/personal" in str(caught.value)


def test_preexisting_include_and_worktree_config_entries_are_not_a_change(tmp_path: Path) -> None:
    """Max's real shared config already holds include.path; a test that leaves it alone passes."""
    repo = tmp_path / "repo"
    make_repo(repo)
    git(repo, "config", "include.path", "/home/someone/.config/git/personal")
    git(repo, "config", "extensions.worktreeConfig", "true")
    git(repo, "config", "--worktree", "core.sparseCheckout", "false")
    before = gcg.snapshot(repo / ".git")
    git(repo, "config", "core.hooksPath", "elsewhere")
    gcg.assert_snapshot_unchanged(repo / ".git", before, "test_x")


def test_a_user_key_in_the_main_config_is_still_reported_through_the_snapshot(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    before = gcg.snapshot(repo / ".git")
    git(repo, "config", "user.name", "t")
    with pytest.raises(gcg.SharedConfigDirty) as caught:
        gcg.assert_snapshot_unchanged(repo / ".git", before, "test_x")
    assert "user.name=t" in str(caught.value)


@pytest.mark.parametrize("where", ["main", "linked"])
def test_a_user_key_in_a_config_worktree_is_reported(tmp_path: Path, where: str) -> None:
    """The live evasion: with worktreeConfig already on, `git config --worktree user.name x` writes config.worktree."""
    main = tmp_path / "main"
    make_repo(main)
    git(main, "commit", "-q", "--allow-empty", "-m", "c")
    linked = tmp_path / "linked"
    git(main, "worktree", "add", "-q", "-b", "side", str(linked))
    git(main, "config", "extensions.worktreeConfig", "true")
    before = gcg.snapshot(main / ".git")
    git(main if where == "main" else linked, "config", "--worktree", "user.name", "x")
    with pytest.raises(gcg.SharedConfigDirty) as caught:
        gcg.assert_snapshot_unchanged(main / ".git", before, "test_x")
    assert "user.name=x" in str(caught.value)
    assert "config.worktree" in str(caught.value)


def test_a_user_key_in_a_worktree_made_during_the_test_is_reported(tmp_path: Path) -> None:
    main = tmp_path / "main"
    make_repo(main)
    git(main, "commit", "-q", "--allow-empty", "-m", "c")
    git(main, "config", "extensions.worktreeConfig", "true")
    before = gcg.snapshot(main / ".git")
    linked = tmp_path / "linked"
    git(main, "worktree", "add", "-q", "-b", "side", str(linked))
    git(linked, "config", "--worktree", "user.email", "late@t")
    with pytest.raises(gcg.SharedConfigDirty) as caught:
        gcg.assert_snapshot_unchanged(main / ".git", before, "test_x")
    assert "user.email=late@t" in str(caught.value)


def test_a_relative_git_dir_still_locates_the_shared_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`git --git-dir=.git commit` runs hooks with GIT_DIR=.git, relative to the caller's cwd, not to `-C`."""
    repo = tmp_path / "repo"
    make_repo(repo)
    (repo / "scripts" / "tests").mkdir(parents=True)
    monkeypatch.chdir(repo)
    shared = gcg.common_dir(repo / "scripts" / "tests", env={**clean_env(), "GIT_DIR": ".git"})
    assert shared == (repo / ".git").resolve()


def test_wiring_an_include_evasion_fails_and_is_named(tmp_path: Path) -> None:
    done = nested(tmp_path, NESTED_INCLUDE)
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert "test_nested.py::test_culprit left user.*" in out and "include.path=" in out, out


NESTED_INCLUDE = '''
import subprocess
from pathlib import Path


def test_culprit(tmp_path):
    (tmp_path / "identity").write_text("[user]\\n\\tname = x\\n")
    subprocess.run(["git", "config", "include.path", str(tmp_path / "identity")], check=True)
'''


def test_a_snapshot_of_a_dir_with_no_shared_config_is_an_error_not_a_pass(tmp_path: Path) -> None:
    with pytest.raises(gcg.GuardUnavailable):
        gcg.snapshot(tmp_path)


def test_a_shared_config_deleted_during_the_run_is_an_error_not_a_pass(tmp_path: Path) -> None:
    """Fail closed at teardown as at setup: a guard that cannot read the shared config has not passed."""
    repo = tmp_path / "repo"
    make_repo(repo)
    before = gcg.snapshot(repo / ".git")
    (repo / ".git" / "config").unlink()
    with pytest.raises(gcg.GuardUnavailable):
        gcg.assert_snapshot_unchanged(repo / ".git", before, "test_x")


@pytest.mark.parametrize(
    ("key", "value", "reported"),
    [
        ("author.name", "x", "author.name=x"),
        ("author.email", "x@example.org", "author.email=x@example.org"),
        ("committer.name", "x", "committer.name=x"),
        ("committer.email", "x@example.org", "committer.email=x@example.org"),
    ],
    ids=["author-name", "author-email", "committer-name", "committer-email"],
)
def test_an_author_or_committer_key_is_reported(tmp_path: Path, key: str, value: str, reported: str) -> None:
    """`author.*` and `committer.*` override `user.*` for a commit's identity (`git var GIT_AUTHOR_IDENT`)."""
    repo = tmp_path / "repo"
    make_repo(repo)
    before = gcg.snapshot(repo / ".git")
    git(repo, "config", key, value)
    with pytest.raises(gcg.SharedConfigDirty) as caught:
        gcg.assert_snapshot_unchanged(repo / ".git", before, "test_x")
    assert reported in str(caught.value)


def test_an_author_key_really_overrides_user_for_the_commit_identity(tmp_path: Path) -> None:
    """Why `author.*` is watched: with `user.name=t` passed by `-c`, a config `author.name` still wins."""
    repo = tmp_path / "repo"
    make_repo(repo)
    subprocess.run(["git", "-C", str(repo), "config", "author.name", "x"], check=True, env=clean_env())
    assert git(repo, "var", "GIT_AUTHOR_IDENT").startswith("x <")


@pytest.mark.parametrize("where", ["main", "linked"])
def test_a_config_worktree_removed_during_the_run_is_reported(tmp_path: Path, where: str) -> None:
    """A removed file is compared against no entries after, so its `user.*` shows as removed."""
    main = tmp_path / "main"
    make_repo(main)
    git(main, "commit", "-q", "--allow-empty", "-m", "c")
    linked = tmp_path / "linked"
    git(main, "worktree", "add", "-q", "-b", "side", str(linked))
    git(main, "config", "extensions.worktreeConfig", "true")
    git(main if where == "main" else linked, "config", "--worktree", "user.name", "kept")
    before = gcg.snapshot(main / ".git")
    removed = [p for p in before if p.name == "config.worktree"]
    assert len(removed) == 1, before
    removed[0].unlink()
    with pytest.raises(gcg.SharedConfigDirty) as caught:
        gcg.assert_snapshot_unchanged(main / ".git", before, "test_x")
    assert "user.name=kept" in str(caught.value) and "removed or old" in str(caught.value)
