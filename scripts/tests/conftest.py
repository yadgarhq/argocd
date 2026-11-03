"""Test-suite guard: no test may leave `user.*` in the shared `.git/config` (yaadgaar 498).

Two autouse fixtures, both from `git_config_guard.py`:

  - per test: snapshot the shared config at setup, compare at teardown, so the report names the
    test that wrote it (pytest shows an ERROR at teardown for that test);
  - per session: the same across the whole run, which catches a write made outside any test
    (a session fixture's teardown, a collection-time import).

"Shared" is `git rev-parse --git-common-dir`, so a linked worktree guards the main checkout's file.
If git cannot locate it the session errors; a guard that cannot look has not passed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("git_config_guard", _HERE / "git_config_guard.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


@pytest.fixture(scope="session", autouse=True)
def shared_git_config():
    """The shared config path; fails the session at teardown if `user.*` moved since the start."""
    config = guard.common_dir(_HERE) / "config"
    before = config.read_bytes()
    yield config
    guard.assert_unchanged(config, before, "the test session")


@pytest.fixture(autouse=True)
def _no_user_keys_left_in_shared_git_config(request: pytest.FixtureRequest, shared_git_config: Path):
    before = shared_git_config.read_bytes()
    yield
    guard.assert_unchanged(shared_git_config, before, request.node.nodeid)
