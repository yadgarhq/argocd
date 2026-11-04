"""The shared `.git/config` guard behind `scripts/tests/conftest.py` (yaadgaar 498).

A test must never leave a `user.*` key in the shared repository config. That file belongs to the
real checkout, and a worktree shares it, so one stray `user.name=t` rewrites the identity of every
later commit. A test builds its repositories under `tmp_path` and gives git an identity with
`-c` or `GIT_CONFIG_*`, never `git config`.

TWO EVASIONS ARE CLOSED TOO (yaadgaar 560). An `include.path` or `includeIf.*.path` key can pull a
`user.*` from another file, and `extensions.worktreeConfig` lets `git config --worktree` write a
`config.worktree` file beside the shared config. So the guard watches `user.*`, `include.*`,
`includeIf.*` and `extensions.worktreeConfig` (WATCHED_KEYS), in the shared config and in every
`config.worktree` under the shared dir: the main checkout's and each `worktrees/<name>/`'s. Any
change to a watched entry fails. Entries that were already there are not a change.

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
    """A watched key (`user.*`, `include.*`, `includeIf.*`, `extensions.worktreeConfig`) in the shared config changed."""


# Matched by `git config --get-regexp` against the canonical key: section and name lower case, a
# subsection (`includeIf.<condition>`) as written.
WATCHED_KEYS = r"^(user\.|include\.|includeif\.|extensions\.worktreeconfig$)"


def common_dir(start: Path, env: Mapping[str, str] | None = None) -> Path:
    """The `.git` directory a worktree SHARES. Under a hook, GIT_DIR in `env` points at it already."""
    env = dict(os.environ if env is None else env)
    # `git --git-dir=.git commit` runs a hook with GIT_DIR=.git, relative to the caller's cwd. Under
    # `-C start` git would read it relative to `start`, so anchor each relative path at the cwd.
    for name in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        if env.get(name) and not os.path.isabs(env[name]):
            env[name] = os.path.abspath(env[name])
    try:
        done = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise GuardUnavailable(f"cannot locate the shared git dir from {start}: {detail.strip()}") from error
    return Path(done.stdout.strip()).resolve()


def user_entries(config: Path) -> list[tuple[str, str]]:
    """Every watched entry (WATCHED_KEYS) of one config file, as sorted (key, value). Git does the parsing.

    The file itself only, never `--includes`: an include is watched as a key, so a test that adds one
    fails, and a pre-existing include of a file outside the repository is not this guard's subject.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run(
        ["git", "config", "--file", str(config), "--get-regexp", WATCHED_KEYS],
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
    _raise_if_moved(config, old, new, label)


def _raise_if_moved(config: Path, old: list[tuple[str, str]], new: list[tuple[str, str]], label: str) -> None:
    if old == new:
        return
    lines = [f"  removed or old: {key}={value}" for key, value in old if (key, value) not in new]
    lines += [f"  added or new:   {key}={value}" for key, value in new if (key, value) not in old]
    raise SharedConfigDirty(
        f"{label} left user.*, include.*, includeIf.* or extensions.worktreeConfig changed in the shared git config {config}.\n"
        + "\n".join(lines)
        + "\nBuild the repository under tmp_path and pass the identity with `git -c user.name=... -c user.email=...`"
        " or GIT_CONFIG_GLOBAL; never run `git config` against the checkout (yaadgaar 498, 560)."
    )


def config_files(common: Path) -> list[Path]:
    """The shared config and every `config.worktree` under the shared dir that exists now."""
    found = [common / "config", common / "config.worktree", *sorted(common.glob("worktrees/*/config.worktree"))]
    return [path for path in found if path.is_file()]


def snapshot(common: Path) -> dict[Path, bytes]:
    """The bytes of each file `config_files` names. The shared config must exist: fail closed if not."""
    if not (common / "config").is_file():
        raise GuardUnavailable(f"no shared git config at {common / 'config'}")
    return {path: path.read_bytes() for path in config_files(common)}


def assert_snapshot_unchanged(common: Path, before: Mapping[Path, bytes], label: str) -> None:
    """Raise SharedConfigDirty if a watched entry moved in any file of `before` or of the shared dir now.

    A file that appeared is compared against no entries; a file that went away, against none after.
    """
    for path in sorted(set(before) | set(config_files(common))):
        raw = path.read_bytes() if path.is_file() else b""
        if before.get(path) == raw:
            continue
        old = _entries_of_bytes(before[path]) if path in before else []
        new = user_entries(path) if path.is_file() else []
        _raise_if_moved(path, old, new, label)
