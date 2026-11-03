"""The shared `.git/config` guard behind `scripts/tests/conftest.py` (yaadgaar 498).

A test must never leave a `user.*` key in the shared repository config. That file belongs to the
real checkout, and a worktree shares it, so one stray `user.name=t` rewrites the identity of every
later commit. A test builds its repositories under `tmp_path` and gives git an identity with
`-c` or `GIT_CONFIG_*`, never `git config`.

Stdlib only. `conftest.py` wires it in; `test_git_config_guard.py` proves it.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path


class GuardUnavailable(RuntimeError):
    """The shared config cannot be located. Fail closed: a guard that cannot look has not passed."""


class SharedConfigDirty(AssertionError):
    """A `user.*` key in the shared repository config changed."""


def common_dir(start: Path, env: Mapping[str, str] | None = None) -> Path:
    """The `.git` directory a worktree SHARES. Under a hook, GIT_DIR in `env` points at it already."""
    try:
        done = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=True,
            env=dict(os.environ if env is None else env),
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise GuardUnavailable(f"cannot locate the shared git dir from {start}: {detail.strip()}") from error
    return Path(done.stdout.strip()).resolve()


def user_entries(config: Path) -> list[tuple[str, str]]:
    """Every `user.*` entry of one config file, as sorted (key, value). Git does the parsing."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run(
        ["git", "config", "--file", str(config), "--get-regexp", r"^user\."],
        capture_output=True,
        text=True,
        env=env,
    )
    if done.returncode == 1:  # git: no key matched
        return []
    if done.returncode != 0:
        raise GuardUnavailable(f"cannot read {config}: {done.stderr.strip()}")
    entries = []
    for line in done.stdout.splitlines():
        key, _, value = line.partition(" ")
        entries.append((key, value))
    return sorted(entries)


def _entries_of_bytes(raw: bytes) -> list[tuple[str, str]]:
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "config"
        copy.write_bytes(raw)
        return user_entries(copy)


def assert_unchanged(config: Path, before: bytes, label: str) -> None:
    """Raise SharedConfigDirty, naming each key and value that moved, if `user.*` differs from `before`.

    Unchanged bytes return at once. A changed file is parsed on both sides, so a write outside `user.*` passes.
    """
    after = config.read_bytes()
    if after == before:
        return
    old, new = _entries_of_bytes(before), user_entries(config)
    if old == new:
        return
    lines = [f"  removed or old: {key}={value}" for key, value in old if (key, value) not in new]
    lines += [f"  added or new:   {key}={value}" for key, value in new if (key, value) not in old]
    raise SharedConfigDirty(
        f"{label} left user.* in the shared git config {config}.\n"
        + "\n".join(lines)
        + "\nBuild the repository under tmp_path and pass the identity with `git -c user.name=... -c user.email=...`"
        " or GIT_CONFIG_GLOBAL; never run `git config` against the checkout (yaadgaar 498)."
    )
