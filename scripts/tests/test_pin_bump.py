"""`scripts/pin_bump.py`: the offline engine of the automatic pin (ADR-0946, ledger 1632 C1).

OFFLINE. Every render, registry digest and module source is handed to the
engine as a fixture here, so this file runs in the `operator-applications` hook
with pytest and pyyaml only. The live half (the #106, #107 and #109 replays
against the registry and helm) is `scripts/gates/test_pin_bump_replay.py`.

One constructed case per arm:

- version: target == pin is a no-op with its own exit code; a lower target and
  a prerelease target are refused; the highest release ignores prereleases and
  is the numeric maximum, never the lexical one;
- the `targetRevision` rewrite refuses a line matched twice or not at all, and
  never touches the comments that name the field;
- the classifier (E1) accepts only the image-only shape and names every other
  shape: an env entry added, an object added, a digest mismatch, a repository
  changed under a matching digest, a helm disagreement, a single helm, and a
  module image changed with no module version moved;
- E3: a new `env_required` key the render lacks is NEEDS-HUMAN, and the same
  key rendered is green; a new `env::var` / `var_os` / lookup-closure read and a
  new const passed to `env_required` are NEEDS-HUMAN; a new error-code literal
  is green; braces inside string, char and comment text do not cut a
  `#[cfg(test)] mod` wrong; a local closure wrapping a lookup (seeder's
  `required`) and a path-qualified const are followed; a new call site whose
  key cannot be resolved (`format!`) and a moved `yadgar-*` crate pin are
  NEEDS-HUMAN; a DaemonSet, an initContainer and a swap inside the registry are
  NEEDS-HUMAN; a platform move is left to the operator evidence (car C10,
  `test_pin_platform.py`);
- E3 fail-closed tripwires (re-review at aad2e9d): a new shared-crate env entry
  point, every unknown reader shape (use-as, bare use, module alias, dotenvy,
  vars scan, generic and boxed `Fn(&str)`, clap `env =`), a new key through a fn
  or macro wrapper, a duplicate unresolved site in a new fn, an unpinnable or
  overridden shared crate, a moved Cargo.lock commit, a missing Cargo.lock: each
  NEEDS-HUMAN; a key rendered on one container of two is not rendered;
- E3 closure scope (pin 0.27.7, gateway v0.12.17): a local closure reader
  (`let get = |suffix| lookup(…)`) is a reader only in the block that binds
  it, so axum's `get(handler)` / `post(handler)` / `.route(.., get(x))` in
  another fn is green, in another file or the same one (also at an offset
  inside the closure's block), after the inner block that binds it, and before
  the `let`; a new literal or unresolvable read through the closure in its own
  block, a real `std::env::var` / `env::var` read beside it, and a new key
  through a closure value that escapes (passed to a fn, returned) stay
  NEEDS-HUMAN;
- E4: a moved `yadgar-store` pin on a -db module (beside an admitted
  migration too), a schema change outside the blocks and an `include_str!` are
  NEEDS-HUMAN;
- E4 (car C10 part 3, ADR-1099; the classes are `test_migration_classifier.py`):
  an added migration the classifier admits is green and recorded with its
  sha256 and as forward-only; one it does not admit (an UPDATE, a non-literal
  `sql:`, two statements, a seed INSERT while the allowlist is empty) is
  NEEDS-HUMAN by version; the added fn with its doc lines and its list entry
  (one per line or appended as `, x()]`), and a new child file of migration fns
  with its `mod` line, leave no residual diff, while any other change beside
  them (an old block, a const, a renamed fn, a dropped or doubled entry, a fn
  body beyond the literal, an attribute, a helper in the child file, a bare
  `mod` line) is NEEDS-HUMAN; an unchanged schema is green; a whitespace-only
  edit inside an old migration's literal (the store checksums exact bytes) and
  an added version at or below the old maximum (`pending_after`) are
  NEEDS-HUMAN; a Migration literal other than exactly `version: <int>`,
  `name: <literal>`, `sql: <literal>` (a cfg attribute, `..base`, another
  field, a block expression) is NEEDS-HUMAN, and a name is sanitised;
- the body carries the template's five headings and one Conventional Commits
  bullet, a rollback with `argocd app terminate-op`, and no MIGRATION_NOTES; a
  pin that crossed a migration names it as forward-only (DatabaseAhead) with
  each admitted migration's sha256.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pin_bump"

# `scripts/` is not a package, so the module is loaded by path.
_spec = importlib.util.spec_from_file_location("pin_bump", REPOSITORY / "scripts" / "pin_bump.py")
pb = importlib.util.module_from_spec(_spec)
sys.modules["pin_bump"] = pb
_spec.loader.exec_module(pb)


R5 = "interim block; lifted by car C10 per ADR-0948 R5"


# ── versions ────────────────────────────────────────────────────────────────


def test_a_target_equal_to_the_pin_is_a_no_op() -> None:
    with pytest.raises(pb.NoOp):
        pb.decide("0.25.27", "0.25.27")


def test_a_lower_target_is_refused() -> None:
    with pytest.raises(pb.Refused, match="lower"):
        pb.decide("0.25.27", "0.25.14")


def test_a_prerelease_target_is_refused() -> None:
    with pytest.raises(pb.Refused, match="release"):
        pb.decide("0.25.27", "0.25.28-rc.1")


def test_a_higher_target_is_a_bump() -> None:
    assert pb.decide("0.25.14", "0.25.27") is None


def test_the_highest_release_ignores_prereleases() -> None:
    assert pb.highest_release(["v0.25.14", "v0.25.27", "v0.26.0-rc.1", "v0.25.28-beta"]) == "0.25.27"


def test_the_highest_release_is_numeric_not_lexical() -> None:
    """ADR-0690: a lexical maximum of these answers 0.25.9, a downgrade."""
    assert pb.highest_release(["v0.25.9", "v0.25.27", "v0.25.10", "not-a-version"]) == "0.25.27"


def test_the_no_op_exit_code_is_distinct() -> None:
    codes = {pb.EXIT_ACCEPT, pb.EXIT_ERROR, pb.EXIT_NEEDS_HUMAN, pb.EXIT_NO_OP}
    assert len(codes) == 4 and pb.EXIT_ACCEPT == 0


# ── the three mechanical files ──────────────────────────────────────────────

APPLICATION_TEXT = (REPOSITORY / "applications" / "yadgar.yaml").read_text()
PIN = re.search(r"^    targetRevision: (\S+)$", APPLICATION_TEXT, re.M).group(1)


def test_the_target_revision_line_alone_moves() -> None:
    rewritten, old = pb.rewrite_target_revision(APPLICATION_TEXT, "9.9.9")
    assert old == PIN
    before, after = APPLICATION_TEXT.splitlines(), rewritten.splitlines()
    assert len(before) == len(after) and rewritten.endswith("\n")
    changed = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
    assert changed == [(f"    targetRevision: {PIN}", "    targetRevision: 9.9.9")]


def test_a_target_revision_matched_twice_is_refused() -> None:
    doubled = APPLICATION_TEXT.replace(
        f"    targetRevision: {PIN}\n", f"    targetRevision: {PIN}\n    targetRevision: {PIN}\n"
    )
    with pytest.raises(pb.Refused, match="2"):
        pb.rewrite_target_revision(doubled, "9.9.9")


def test_a_target_revision_matched_nowhere_is_refused() -> None:
    with pytest.raises(pb.Refused, match="0"):
        pb.rewrite_target_revision(APPLICATION_TEXT.replace("    targetRevision:", "    revision:"), "9.9.9")


OLD_DIGEST = "sha256:" + "1" * 64
NEW_DIGEST = "sha256:" + "2" * 64
PIN_TEXT = '{{\n  "chart_tag": "v{tag}",\n  "chart_digest": "{digest}",\n  "platform_version": "0.2.6"\n}}\n'


def test_the_chart_pin_moves_its_tag_and_digest_only() -> None:
    text = PIN_TEXT.format(tag="0.25.14", digest=OLD_DIGEST)
    assert pb.rewrite_chart_pin(text, "0.25.27", digest=NEW_DIGEST) == PIN_TEXT.format(tag="0.25.27", digest=NEW_DIGEST)


def test_a_chart_pin_with_no_digest_line_is_refused_not_grown() -> None:
    """Yaadgaar 250: the writer moves the recorded digest; a pin without one is refused, never silently extended."""
    text = '{\n  "chart_tag": "v0.25.14",\n  "platform_version": "0.2.6"\n}\n'
    with pytest.raises(pb.Refused, match="chart_digest"):
        pb.rewrite_chart_pin(text, "0.25.27", digest=NEW_DIGEST)


@pytest.mark.parametrize("digest", ["", "2" * 64, "sha256:" + "Z" * 64, "sha256:abc"])
def test_a_malformed_digest_is_refused(digest: str) -> None:
    with pytest.raises(pb.Refused, match="digest"):
        pb.rewrite_chart_pin(PIN_TEXT.format(tag="0.25.14", digest=OLD_DIGEST), "0.25.27", digest=digest)


def test_the_chart_digest_is_resolved_through_the_module_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    class FakeNet:
        def ghcr_digest(self, module: str, tag: str) -> str:
            seen.append((module, tag))
            return NEW_DIGEST

    monkeypatch.setattr(pb, "Net", FakeNet)
    assert pb.chart_digest("0.25.27") == NEW_DIGEST
    assert seen == [("charts/yadgar", "0.25.27")]


# ── E1: the render classifier ───────────────────────────────────────────────

DIGEST = {
    ("gateway", "0.12.10"): "sha256:" + "1" * 64,
    ("gateway", "0.12.11"): "sha256:" + "2" * 64,
    ("iam", "0.11.8"): "sha256:" + "3" * 64,
}
OLD_MODULES = {"config": "0.2.1", "gateway": "0.12.10", "iam": "0.11.8", "platform": "0.2.6"}
NEW_MODULES = {**OLD_MODULES, "gateway": "0.12.11"}


def deployment(name: str, image: str, env: list | None = None) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name},
        "spec": {
            "template": {
                "spec": {
                    "containers": [{"name": name, "image": image, "env": env or [{"name": "LISTEN", "value": ":8080"}]}]
                }
            }
        },
    }


def config_map(name: str) -> dict:
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name}, "data": {"k": "v"}}


def render_at(gateway_version: str) -> list:
    return [
        deployment("gateway", f"ghcr.io/yadgarhq/gateway@{DIGEST[('gateway', gateway_version)]}"),
        deployment("iam", f"ghcr.io/yadgarhq/iam@{DIGEST[('iam', '0.11.8')]}"),
        config_map("audit"),
    ]


def two_helms(base: list, head: list) -> dict:
    return {"v3.18.4": (base, head), "v4.3.0": (copy.deepcopy(base), copy.deepcopy(head))}


def digest_of(module: str, version: str) -> str:
    return DIGEST[(module, version)]


def classify(renders: dict, new_modules: dict | None = None, old_modules: dict | None = None):
    return pb.classify(renders, old_modules or OLD_MODULES, new_modules or NEW_MODULES, digest_of)


def test_green_the_107_shape_gateway_image_only_with_its_tag_digest() -> None:
    verdict = classify(two_helms(render_at("0.12.10"), render_at("0.12.11")))
    assert verdict.reasons == []
    assert verdict.accept
    assert [c["object"] for c in verdict.changed] == ["apps/Deployment//gateway"]


def test_an_env_entry_added_names_the_object_and_field() -> None:
    head = render_at("0.12.11")
    head[0]["spec"]["template"]["spec"]["containers"][0]["env"].append({"name": "NEW_KEY", "value": "x"})
    verdict = classify(two_helms(render_at("0.12.10"), head))
    assert not verdict.accept
    assert any("apps/Deployment//gateway" in r and "env" in r for r in verdict.reasons), verdict.reasons


def test_an_object_added_is_needs_human() -> None:
    """The #104 seeder shape: a new object in the render."""
    head = render_at("0.12.11") + [config_map("seeder-registry")]
    verdict = classify(two_helms(render_at("0.12.10"), head))
    assert not verdict.accept
    assert any("added" in r and "/ConfigMap//seeder-registry" in r for r in verdict.reasons), verdict.reasons


def test_an_object_removed_is_needs_human() -> None:
    verdict = classify(two_helms(render_at("0.12.10"), render_at("0.12.11")[:2]))
    assert not verdict.accept
    assert any("removed" in r and "/ConfigMap//audit" in r for r in verdict.reasons), verdict.reasons


def test_a_digest_that_is_not_the_tag_digest_is_needs_human() -> None:
    head = render_at("0.12.11")
    head[0]["spec"]["template"]["spec"]["containers"][0]["image"] = "ghcr.io/yadgarhq/gateway@sha256:" + "9" * 64
    verdict = classify(two_helms(render_at("0.12.10"), head))
    assert not verdict.accept
    assert any("gateway:0.12.11" in r and "digest" in r for r in verdict.reasons), verdict.reasons


def test_a_repository_changed_under_a_matching_digest_is_needs_human() -> None:
    head = render_at("0.12.11")
    head[0]["spec"]["template"]["spec"]["containers"][0]["image"] = f"ghcr.io/elsewhere/gateway@{DIGEST[('gateway', '0.12.11')]}"
    verdict = classify(two_helms(render_at("0.12.10"), head))
    assert not verdict.accept
    assert any("repository" in r for r in verdict.reasons), verdict.reasons


def test_the_two_helms_disagreeing_by_one_byte_is_needs_human() -> None:
    renders = two_helms(render_at("0.12.10"), render_at("0.12.11"))
    renders["v4.3.0"][1][2]["data"]["k"] = "w"
    verdict = classify(renders)
    assert not verdict.accept
    assert any("disagree" in r for r in verdict.reasons), verdict.reasons


def test_a_single_helm_is_needs_human() -> None:
    verdict = classify({"v3.18.4": (render_at("0.12.10"), render_at("0.12.11"))})
    assert not verdict.accept
    assert any("one helm" in r for r in verdict.reasons), verdict.reasons


def test_a_platform_move_is_left_to_the_operator_evidence() -> None:
    """The #103 shape: platform 0.2.4 -> 0.2.5 moves the operator Applications. Car C10 (ruled 2026-10-31): the
    `yadgar` classifier no longer blocks it by category; `classify_platform` judges the five operator Applications
    (`test_pin_platform.py`)."""
    old = {**OLD_MODULES, "platform": "0.2.4"}
    new = {**NEW_MODULES, "platform": "0.2.5"}
    verdict = classify(two_helms(render_at("0.12.10"), render_at("0.12.11")), new_modules=new, old_modules=old)
    assert verdict.accept, verdict.reasons


def test_a_module_image_changed_without_its_version_moving_is_needs_human() -> None:
    verdict = classify(two_helms(render_at("0.12.10"), render_at("0.12.11")), new_modules=OLD_MODULES)
    assert not verdict.accept
    assert any("gateway" in r and "did not move" in r for r in verdict.reasons), verdict.reasons


def test_a_changed_config_map_is_needs_human() -> None:
    head = render_at("0.12.11")
    head[2]["data"]["k"] = "changed"
    renders = two_helms(render_at("0.12.10"), head)
    verdict = classify(renders)
    assert not verdict.accept
    assert any("/ConfigMap//audit" in r for r in verdict.reasons), verdict.reasons



def test_a_platform_move_carries_no_interim_block() -> None:
    """ADR-0948 R5 (Max, 2026-10-18): platform moves auto-merge when green; car C10 lifted the interim block."""
    old = {**OLD_MODULES, "platform": "0.2.4"}
    new = {**NEW_MODULES, "platform": "0.2.5"}
    verdict = classify(two_helms(render_at("0.12.10"), render_at("0.12.11")), new_modules=new, old_modules=old)
    assert not any(R5 in r for r in verdict.reasons), verdict.reasons


def workload(kind: str, name: str, image: str, container_list: str = "containers") -> dict:
    document = deployment(name, image)
    document["kind"] = kind
    pod = document["spec"]["template"]["spec"]
    if container_list != "containers":
        pod[container_list] = pod.pop("containers")
        pod["containers"] = [{"name": "main", "image": "busybox:1.37"}]
    return document


def test_a_daemon_set_image_change_is_needs_human() -> None:
    """Only a Deployment, StatefulSet or Job may change image, even to the moved module's own tag digest."""
    old = render_at("0.12.10")[1:] + [workload("DaemonSet", "gateway", f"ghcr.io/yadgarhq/gateway@{DIGEST[('gateway', '0.12.10')]}")]
    new = render_at("0.12.10")[1:] + [workload("DaemonSet", "gateway", f"ghcr.io/yadgarhq/gateway@{DIGEST[('gateway', '0.12.11')]}")]
    verdict = classify(two_helms(old, new))
    assert not verdict.accept
    assert any("DaemonSet" in r for r in verdict.reasons), verdict.reasons


def test_a_swap_inside_the_registry_is_needs_human() -> None:
    """iam's Deployment takes gateway's image at gateway's new tag digest: the repository moved, so red."""
    head = render_at("0.12.10")
    head[1]["spec"]["template"]["spec"]["containers"][0]["image"] = f"ghcr.io/yadgarhq/gateway@{DIGEST[('gateway', '0.12.11')]}"
    verdict = classify(two_helms(render_at("0.12.10"), head))
    assert not verdict.accept
    assert any("apps/Deployment//iam" in r and "repository" in r for r in verdict.reasons), verdict.reasons


def test_an_init_container_image_change_is_needs_human() -> None:
    """Only `containers[*].image` may change (the C1 dispatch), not an initContainer's."""
    def gateway_init(version: str) -> list:
        image = f"ghcr.io/yadgarhq/gateway@{DIGEST[('gateway', version)]}"
        return render_at("0.12.10")[1:] + [workload("Deployment", "gateway", image, "initContainers")]

    verdict = classify(two_helms(gateway_init("0.12.10"), gateway_init("0.12.11")))
    assert not verdict.accept
    assert any("initContainers" in r for r in verdict.reasons), verdict.reasons


# ── E3: the boot audit ──────────────────────────────────────────────────────


def tree(name: str) -> dict[str, str]:
    root = FIXTURES / name
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*.rs"))}


def test_the_boot_audit_reads_required_keys_in_every_call_shape_and_skips_tests() -> None:
    required, reads = pb.env_keys(tree("e3-new"))
    assert required == {"LISTEN", "DB_HOST", "NEW_KEY"}
    assert "NATS_TLS_DOMAIN" in reads and "YADGAR_TEST_ONLY_KEY" not in reads


def test_a_new_required_key_the_render_lacks_is_needs_human() -> None:
    reasons = pb.boot_audit("gateway", tree("e3-old"), tree("e3-new"), {"LISTEN", "DB_HOST"})
    assert reasons and any("NEW_KEY" in r and "gateway" in r for r in reasons), reasons


def test_a_new_required_key_the_render_carries_is_green() -> None:
    assert pb.boot_audit("gateway", tree("e3-old"), tree("e3-new"), {"LISTEN", "DB_HOST", "NEW_KEY"}) == []


def test_an_unchanged_source_is_green() -> None:
    assert pb.boot_audit("gateway", tree("e3-old"), tree("e3-old"), {"LISTEN", "DB_HOST"}) == []


def with_boot_line(name: str, line: str, prelude: str = "") -> dict[str, str]:
    """`name`'s tree with `line` added to `boot()` and `prelude` added at the top of the file."""
    source = tree(name)
    text = source["src/boot.rs"].replace("    Ok(())\n}", f"    {line}\n    Ok(())\n}}", 1)
    source["src/boot.rs"] = prelude + text
    return source


RENDERED = {"LISTEN", "DB_HOST"}


def test_a_new_error_code_literal_is_green() -> None:
    """Coordinator ruling, ledger 1632: NOT_FOUND (gateway 0.12.13) and PROJECT_ARCHIVED (0.12.10) are error
    codes, not env names; only a name at an env-read call site counts."""
    new = with_boot_line("e3-old", 'let _ = ("NOT_FOUND", "PROJECT_ARCHIVED");')
    assert pb.boot_audit("gateway", tree("e3-old"), new, RENDERED) == []


def test_a_new_env_var_read_the_render_lacks_is_needs_human() -> None:
    new = with_boot_line("e3-old", 'let _read = std::env::var("NEW_READ").ok();')
    reasons = pb.boot_audit("gateway", tree("e3-old"), new, RENDERED)
    assert reasons and any("NEW_READ" in r for r in reasons), reasons


def test_a_new_env_var_os_read_the_render_lacks_is_needs_human() -> None:
    new = with_boot_line("e3-old", 'let _read = std::env::var_os("NEW_READ_OS");')
    assert any("NEW_READ_OS" in r for r in pb.boot_audit("gateway", tree("e3-old"), new, RENDERED))


def test_a_new_const_passed_to_env_required_is_needs_human() -> None:
    new = with_boot_line("e3-old", "let _c = env_required(NEW_CONST_KEY)?;", 'const NEW_CONST_KEY: &str = "NEW_CONST";\n')
    reasons = pb.boot_audit("gateway", tree("e3-old"), new, RENDERED)
    assert reasons and any("NEW_CONST" in r and "required" in r for r in reasons), reasons


def test_a_new_key_read_through_a_lookup_closure_is_needs_human() -> None:
    """gateway's `from_lookup(lookup: impl Fn(&str) -> Option<String>)` reads `lookup(KEY)`."""
    prelude = 'const TRUST: &str = "NEW_LOOKUP";\nfn from_lookup(lookup: impl Fn(&str) -> Option<String>) {\n    let _ = lookup(TRUST);\n}\n'
    new = with_boot_line("e3-old", "", prelude)
    assert any("NEW_LOOKUP" in r for r in pb.boot_audit("gateway", tree("e3-old"), new, RENDERED))


def test_braces_inside_literals_and_comments_do_not_cut_the_test_module_wrong() -> None:
    required, reads = pb.env_keys(tree("e3-braces"))
    assert required == {"AFTER_TEST_MODULE", "AFTER_CONST_KEY"}
    assert reads == {"AFTER_TEST_MODULE", "AFTER_CONST_KEY", "LOOKUP_KEY"}



SEEDER_PRELUDE = """pub const REGISTRY_FILE: &str = "SEEDER_REGISTRY_FILE";
pub fn from_lookup(lookup: impl Fn(&str) -> Option<String>) -> Result<(), String> {
    let required = |var: &'static str| -> Result<String, String> {
        match lookup(var) {
            None => Err(format!("{var} is required")),
            Some(v) => Ok(v),
        }
    };
    let _file = required(REGISTRY_FILE)?;
    Ok(())
}
"""


def test_a_local_closure_wrapping_a_lookup_yields_required_keys() -> None:
    """seeder's `Config::from_lookup` reads through `let required = |var| match lookup(var) …`."""
    source = {"src/config.rs": SEEDER_PRELUDE}
    required, reads = pb.env_keys(source)
    assert required == {"SEEDER_REGISTRY_FILE"} and reads == {"SEEDER_REGISTRY_FILE"}


def test_a_new_seeder_required_key_is_needs_human() -> None:
    old = {"src/config.rs": SEEDER_PRELUDE}
    new = {
        "src/config.rs": SEEDER_PRELUDE.replace(
            "    let _file = required(REGISTRY_FILE)?;\n",
            "    let _file = required(REGISTRY_FILE)?;\n    let _x = required(NEW_SEEDER_KEY)?;\n",
        ).replace("pub fn", 'pub const NEW_SEEDER_KEY: &str = "SEEDER_NEW_KEY";\npub fn', 1)
    }
    reasons = pb.boot_audit("seeder", old, new, {"SEEDER_REGISTRY_FILE"})
    assert any("SEEDER_NEW_KEY" in r and "required" in r for r in reasons), reasons


def test_a_path_qualified_const_resolves_within_the_crate() -> None:
    source = {
        "src/config.rs": 'pub const HOST: &str = "QUALIFIED_HOST";\n',
        "src/boot.rs": "fn boot() -> Result<(), String> {\n    let _h = env_required(config::HOST)?;\n    Ok(())\n}\n",
    }
    assert pb.env_keys(source)[0] == {"QUALIFIED_HOST"}


def test_a_format_built_key_at_a_new_call_site_is_needs_human() -> None:
    new = with_boot_line("e3-old", 'let _t = std::env::var(&format!("{prefix}_NEW_TIMEOUT")).ok();', 'const prefix: &str = "X";\n')
    reasons = pb.boot_audit("gateway", tree("e3-old"), new, RENDERED)
    assert any("cannot resolve" in r and "format!" in r for r in reasons), reasons


def test_an_unresolved_call_site_present_at_both_tags_is_green() -> None:
    """The diff against the old tag is what keeps #107 and #109 green: their unresolved sites did not move."""
    both = with_boot_line("e3-old", 'let _t = std::env::var(&format!("{prefix}_TIMEOUT")).ok();')
    assert pb.boot_audit("gateway", both, copy.deepcopy(both), RENDERED) == []


def with_cargo(
    source: dict, lifecycle: str, store: str | None = None, commit: str = "0" * 40, extra: str = ""
) -> dict:
    """`source` with a Cargo.toml pinning yadgar-lifecycle (and yadgar-store) by tag, and the matching Cargo.lock."""
    lines = ["[package]", 'name = "yadgar-gateway"', "", "[dependencies]"]
    lines.append(f'yadgar-lifecycle = {{ git = "https://github.com/yadgarhq/lifecycle.git", tag = "{lifecycle}" }}')
    if store:
        lines.append(f'yadgar-store = {{ git = "https://github.com/yadgarhq/store.git", tag = "{store}" }}')
    lines.append('serde = "1"')
    lock = ["version = 4", "", "[[package]]", 'name = "yadgar-gateway"', 'version = "0.1.0"', ""]
    lock += ["[[package]]", 'name = "yadgar-lifecycle"', 'version = "0.2.0"']
    lock += [f'source = "git+https://github.com/yadgarhq/lifecycle.git?tag={lifecycle}#{commit}"', ""]
    if store:
        lock += ["[[package]]", 'name = "yadgar-store"', 'version = "0.4.0"']
        lock += [f'source = "git+https://github.com/yadgarhq/store.git?tag={store}#{commit}"', ""]
    return {**source, "Cargo.toml": "\n".join(lines) + "\n" + extra, "Cargo.lock": "\n".join(lock)}


def test_a_moved_yadgar_lifecycle_tag_is_needs_human() -> None:
    old, new = with_cargo(tree("e3-old"), "v0.2.17"), with_cargo(tree("e3-old"), "v0.2.18")
    reasons = pb.boot_audit("gateway", old, new, RENDERED)
    assert any("yadgar-lifecycle" in r and "v0.2.17" in r and "v0.2.18" in r for r in reasons), reasons


def test_an_unmoved_shared_crate_is_green() -> None:
    old, new = with_cargo(tree("e3-old"), "v0.2.17"), with_cargo(tree("e3-old"), "v0.2.17")
    assert pb.boot_audit("gateway", old, new, RENDERED) == []



# ── E3 fail-closed tripwires (re-review of argocd#110 at aad2e9d) ──────────


def grown(line: str, prelude: str = "") -> list[str]:
    """E3 on e3-old against e3-old grown by `line` in `boot()` and `prelude` at the top."""
    return pb.boot_audit("gateway", tree("e3-old"), with_boot_line("e3-old", line, prelude), RENDERED)


@pytest.mark.parametrize(
    "line",
    [
        "let _tls = yadgar_lifecycle::serve_tls::ServerTls::from_env(LISTEN_KEY, CHART_KEY);",
        "let _t = yadgar_dial::request_timeout_from_lookup(&env, TIMEOUT_VAR, 5);",
        "let _c = shared::configure(env, 3);",
    ],
    ids=["from_env", "from_lookup", "reader-passed-as-argument"],
)
def test_a_new_shared_crate_env_entry_point_is_needs_human(line: str) -> None:
    """lifecycle's `ServerTls::from_env` reads `{prefix}_TLS_*`; the keys it reads are invisible here."""
    assert any("entry point" in r for r in grown(line)), grown(line)


@pytest.mark.parametrize(
    ("line", "prelude"),
    [
        ('let _g = getenv("ALIASED_KEY");', "use std::env::var as getenv;\n"),
        ('let _v = var("BARE_KEY");', "use std::env::var;\n"),
        ('let _e = e::var("MODULE_ALIAS_KEY");', "use std::env as e;\n"),
        ('let _d = dotenvy::var("DOTENV_KEY");', ""),
        ("let _all: Vec<(String, String)> = std::env::vars().collect();", ""),
        ("", "fn read<F: Fn(&str) -> Option<String>>(f: F) -> Option<String> { f(\"GENERIC\") }\n"),
        ("", "struct Reader { get: Box<dyn Fn(&str) -> Option<String>> }\n"),
        ("", '#[derive(clap::Parser)]\nstruct Args {\n    #[arg(env = "CLAP_KEY")]\n    key: String,\n}\n'),
    ],
    ids=["use-as", "bare-use", "module-alias", "dotenvy", "vars-scan", "generic-fn", "boxed-fn", "clap-env"],
)
def test_an_unknown_reader_shape_trips_the_token_tripwire(line: str, prelude: str) -> None:
    assert any("tripwire" in r for r in grown(line, prelude)), grown(line, prelude)


FN_WRAPPER = "fn get(k: &str) -> Option<String> {\n    std::env::var(k).ok()\n}\n"
MACRO_WRAPPER = "macro_rules! req {\n    ($k:expr) => {\n        env_required($k)\n    };\n}\n"


def test_a_new_key_through_a_fn_wrapper_present_at_both_tags_is_needs_human() -> None:
    old = with_boot_line("e3-old", "", FN_WRAPPER)
    new = with_boot_line("e3-old", 'let _n = get("NEW_FN_KEY");', FN_WRAPPER)
    reasons = pb.boot_audit("gateway", old, new, RENDERED)
    assert any("NEW_FN_KEY" in r for r in reasons), reasons


def test_a_new_key_through_a_macro_wrapper_present_at_both_tags_is_needs_human() -> None:
    old = with_boot_line("e3-old", "", MACRO_WRAPPER)
    new = with_boot_line("e3-old", 'let _n = req!("NEW_MACRO_KEY");', MACRO_WRAPPER)
    reasons = pb.boot_audit("gateway", old, new, RENDERED)
    assert any("NEW_MACRO_KEY" in r and "required" in r for r in reasons), reasons


def test_a_duplicate_unresolved_site_in_a_new_fn_is_needs_human() -> None:
    site = 'std::env::var(&format!("{p}_X")).ok()'
    first = f"fn first(p: &str) -> Option<String> {{\n    {site}\n}}\n"
    second = f"fn second(p: &str) -> Option<String> {{\n    {site}\n}}\n"
    old = with_boot_line("e3-old", "", first)
    new = with_boot_line("e3-old", "", first + second)
    reasons = pb.boot_audit("gateway", old, new, RENDERED)
    assert any("cannot resolve" in r for r in reasons), reasons


def test_a_second_argument_const_after_an_upper_snake_first_argument_is_unresolved() -> None:
    """`env_parsed(K9, D)`: the first argument is the key, so D's value is never taken as it."""
    source = {"src/boot.rs": 'const K9: &str = "REAL_KEY_FROM_A_VARIABLE";\nconst D: &str = "NOT_THE_KEY";\nfn f() { let _ = env_parsed(K9, D); }\n'}
    required, reads = pb.env_keys(source)
    assert "NOT_THE_KEY" not in reads and "NOT_THE_KEY" not in required


# ── E3: a local closure reader is a reader in its own block only ────────────
# gateway v0.12.17 (pin 0.27.7): `upstream/tls.rs` `from_lookup` binds
# `let get = |suffix| lookup(&format!(…))`, and the new `http/whoami.rs` calls
# axum's `get(whoami)`. A `let` binding cannot be named outside its block, so
# the axum call is not an env read; before the fix it was judged one.


def closure_tls(name: str) -> str:
    """gateway's `upstream/tls.rs` shape: a closure `name` over a lookup parameter, read by literal suffix."""
    return (
        "pub fn from_lookup(prefix: &'static str, lookup: impl Fn(&str) -> Option<String>) -> Option<String> {\n"
        f'    let {name} = |suffix: &str| lookup(&format!("{{prefix}}_{{suffix}}"));\n'
        f'    {name}("TLS_ENABLED")\n'
        "}\n"
    )


AXUM_ROUTES = {
    "route-fallback": 'pub fn routes() -> Router {\n    Router::new().route("/auth/whoami", NAME(whoami).fallback(method_not_allowed))\n}\n',
    "bare-handler": "pub fn routes() -> Router {\n    let _r = NAME(handler);\n    Router::new()\n}\n",
    "inside-another-closure": 'pub fn routes() -> Router {\n    let mk = || Router::new().route("/", NAME(handler));\n    mk()\n}\n',
}


@pytest.mark.parametrize("name", ["get", "post"])
@pytest.mark.parametrize("shape", sorted(AXUM_ROUTES))
def test_an_axum_method_router_named_like_a_closure_reader_elsewhere_is_not_an_env_read(name: str, shape: str) -> None:
    old = {"src/upstream/tls.rs": closure_tls(name)}
    new = {**old, "src/http/whoami.rs": AXUM_ROUTES[shape].replace("NAME", name)}
    assert pb.boot_audit("gateway", old, new, set()) == []


def test_an_axum_get_in_another_fn_of_the_same_file_is_not_an_env_read() -> None:
    old = {"src/upstream/tls.rs": closure_tls("get")}
    new = {"src/upstream/tls.rs": closure_tls("get") + "pub fn routes() -> Router {\n    Router::new().route(\"/\", get(handler))\n}\n"}
    assert pb.boot_audit("gateway", old, new, set()) == []


def test_an_axum_get_in_another_file_at_an_offset_inside_the_closure_block_is_not_an_env_read() -> None:
    """The span is per file: a call whose offset falls inside the closure's block in tls.rs is still outside it."""
    tls = closure_tls("get")
    offset = tls.index('get("TLS_ENABLED")')
    routes = "fn routes() { let _r = get(handler); }\n"
    padding = offset - routes.index("get(handler)")
    old = {"src/upstream/tls.rs": tls}
    new = {**old, "src/http/whoami.rs": " " * padding + routes}
    assert pb.boot_audit("gateway", old, new, set()) == []


INNER_BLOCK = (
    "pub fn from_lookup(lookup: impl Fn(&str) -> Option<String>) -> Option<String> {\n"
    "    {\n"
    "        let get = |k: &str| lookup(k);\n"
    '        let _ = get("TLS_ENABLED");\n'
    "    }\n"
    "    None\n"
    "}\n"
)


def test_a_bare_get_after_the_inner_block_that_binds_the_closure_is_not_an_env_read() -> None:
    new = {"src/upstream/tls.rs": INNER_BLOCK + "fn other() {\n    let _r = get(handler);\n}\n"}
    assert pb.boot_audit("gateway", {"src/upstream/tls.rs": INNER_BLOCK}, new, set()) == []


def test_a_bare_get_before_the_closure_binding_is_not_an_env_read() -> None:
    new = {"src/upstream/tls.rs": "fn other() {\n    let _r = get(handler);\n}\n" + closure_tls("get")}
    assert pb.boot_audit("gateway", {"src/upstream/tls.rs": closure_tls("get")}, new, set()) == []


ESCAPE_PASSED = (
    "pub fn from_lookup(lookup: impl Fn(&str) -> Option<String>) {\n"
    "    let get = |k: &str| lookup(k);\n"
    "    helper(get);\n"
    "}\n"
    "fn helper<F: Fn(&str) -> Option<String>>(get: F) -> Option<String> {\n"
    "    get(OLD_K)\n"
    "}\n"
)
ESCAPE_RETURNED = (
    "fn mk(lookup: impl Fn(&str) -> Option<String>) -> impl Fn(&str) -> Option<String> {\n"
    "    let get = move |k: &str| lookup(k);\n"
    "    get\n"
    "}\n"
    "pub fn boot(l: impl Fn(&str) -> Option<String>) {\n"
    "    let get = mk(l);\n"
    "    let _ = get(OLD_K);\n"
    "}\n"
)


@pytest.mark.parametrize("source", [ESCAPE_PASSED, ESCAPE_RETURNED], ids=["passed-to-a-fn", "returned-from-a-fn"])
def test_a_new_key_through_an_escaping_closure_value_is_needs_human(source: str) -> None:
    """A closure value that leaves its block (passed, returned) can be called by the same name anywhere."""
    consts = 'const OLD_K: &str = "OLD_K";\nconst NEW_K: &str = "NEW_ESCAPED_KEY";\n'
    old = {"src/upstream/tls.rs": consts + source}
    new = {"src/upstream/tls.rs": consts + source.replace("get(OLD_K)", "get(OLD_K);\n    get(NEW_K)")}
    reasons = pb.boot_audit("gateway", old, new, set())
    assert any("NEW_ESCAPED_KEY" in r for r in reasons), reasons


def with_closure_line(line: str) -> dict[str, str]:
    """`closure_tls("get")` with `line` added inside `from_lookup`, after the closure."""
    text = closure_tls("get").replace('    get("TLS_ENABLED")', f'    {line}\n    get("TLS_ENABLED")')
    return {"src/upstream/tls.rs": text}


def test_a_new_unresolvable_read_through_the_closure_in_its_own_block_is_needs_human() -> None:
    reasons = pb.boot_audit("gateway", {"src/upstream/tls.rs": closure_tls("get")}, with_closure_line("let _ = get(&dynamic);"), set())
    assert any("cannot resolve" in r and "get(&dynamic)" in r for r in reasons), reasons


def test_a_new_literal_read_through_the_closure_in_its_own_block_is_needs_human() -> None:
    reasons = pb.boot_audit("gateway", {"src/upstream/tls.rs": closure_tls("get")}, with_closure_line('let _ = get("NEW_CLOSURE_KEY");'), set())
    assert any("NEW_CLOSURE_KEY" in r for r in reasons), reasons


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('let _r = std::env::var("NEW_STD_KEY").ok();', "NEW_STD_KEY"),
        ('let _r = env::var(&format!("{p}_NEW")).ok();', "cannot resolve"),
    ],
    ids=["std-env-var", "env-var-unresolvable"],
)
def test_a_real_env_read_beside_the_closure_reader_is_still_needs_human(line: str, expected: str) -> None:
    old = {"src/upstream/tls.rs": closure_tls("get")}
    new = {**old, "src/http/whoami.rs": f"pub fn routes(p: &str) {{\n    {line}\n    let _ = get(handler);\n}}\n"}
    reasons = pb.boot_audit("gateway", old, new, set())
    assert any(expected in r for r in reasons), reasons
    assert not any("get(handler)" in r for r in reasons), reasons


CARGO_LIFECYCLE = 'yadgar-lifecycle = { git = "https://github.com/yadgarhq/lifecycle.git", tag = "v0.2.17" }'


@pytest.mark.parametrize(
    "spec",
    [
        "yadgar-lifecycle = { workspace = true }",
        'yadgar-lifecycle = { git = "https://github.com/yadgarhq/lifecycle.git", branch = "main" }',
        'yadgar-lifecycle = { git = "https://github.com/yadgarhq/lifecycle.git" }',
        'yadgar-lifecycle = { path = "../lifecycle" }',
    ],
    ids=["workspace", "branch", "tagless-git", "path"],
)
def test_an_unpinnable_shared_crate_spec_is_needs_human(spec: str) -> None:
    old = with_cargo(tree("e3-old"), "v0.2.17")
    new = {**old, "Cargo.toml": old["Cargo.toml"].replace(CARGO_LIFECYCLE, spec)}
    old = {**old, "Cargo.toml": new["Cargo.toml"]}
    reasons = pb.boot_audit("gateway", old, new, RENDERED)
    assert any("yadgar-lifecycle" in r and "pin" in r for r in reasons), reasons


@pytest.mark.parametrize(
    "extra",
    [
        '\n[patch."https://github.com/yadgarhq/lifecycle.git"]\nyadgar-lifecycle = { path = "../lifecycle" }\n',
        '\n[replace]\n"yadgar-lifecycle:0.2.0" = { path = "../lifecycle" }\n',
        '\n[workspace]\nmembers = ["."]\n',
        '\n[[bin]]\nname = "x"\npath = "build/main.rs"\n',
    ],
    ids=["patch", "replace", "workspace", "bin-outside-src"],
)
def test_a_cargo_override_or_layout_change_is_needs_human(extra: str) -> None:
    old = with_cargo(tree("e3-old"), "v0.2.17")
    new = with_cargo(tree("e3-old"), "v0.2.17", extra=extra)
    assert pb.boot_audit("gateway", old, new, RENDERED), "an override or layout change passed green"


def test_a_moved_shared_crate_commit_in_cargo_lock_is_needs_human() -> None:
    """The Cargo.toml tag is unchanged, but the lock resolved another commit (a moved tag, or a transitive move)."""
    old = with_cargo(tree("e3-old"), "v0.2.17", commit="a" * 40)
    new = with_cargo(tree("e3-old"), "v0.2.17", commit="b" * 40)
    reasons = pb.boot_audit("gateway", old, new, RENDERED)
    assert any("Cargo.lock" in r and "yadgar-lifecycle" in r for r in reasons), reasons


def test_a_missing_cargo_lock_is_needs_human() -> None:
    old = with_cargo(tree("e3-old"), "v0.2.17")
    new = {k: v for k, v in old.items() if k != "Cargo.lock"}
    assert any("Cargo.lock" in r for r in pb.boot_audit("gateway", old, new, RENDERED))


def test_a_key_rendered_on_one_container_only_is_needs_human() -> None:
    """Two containers run the module's image; each is audited on its own env, not the union."""
    image = f"ghcr.io/yadgarhq/gateway@{DIGEST[('gateway', '0.12.11')]}"
    document = deployment("gateway", image, [{"name": "LISTEN", "value": ":1"}, {"name": "DB_HOST", "value": "h"}])
    document["spec"]["template"]["spec"]["containers"].append({"name": "sidecar", "image": image, "env": [{"name": "LISTEN", "value": ":2"}]})
    assert pb.rendered_env([document], "gateway") == {"LISTEN"}


# ── E4: the migration diff ──────────────────────────────────────────────────


def with_audit_sql(sql: str) -> dict:
    """e4-new with migration 7's `sql:` expression replaced by `sql` (Rust source text)."""
    new = tree("e4-new")
    new["src/schema.rs"] = new["src/schema.rs"].replace('"CREATE TABLE audit (id BIGINT PRIMARY KEY)".into()', sql)
    assert sql in new["src/schema.rs"]
    return new


def test_an_added_migration_version_is_needs_human() -> None:
    """Rewritten for car C10 part 3: an added migration outside the ADR-1099 classes stays NEEDS-HUMAN, by version."""
    reasons = pb.migration_audit("iam-db", tree("e4-old"), with_audit_sql('"UPDATE user SET id = 1".into()'))
    assert reasons == ["iam-db: migration 7 (create_audit) not admitted: UPDATE is not an admitted statement (ADR-1099 classes A-D)"], reasons


def test_an_unchanged_schema_is_green() -> None:
    assert pb.migration_audit("iam-db", tree("e4-old"), tree("e4-old")) == []


def test_a_changed_migration_is_needs_human() -> None:
    new = tree("e4-old")
    new["src/schema/policy.rs"] = new["src/schema/policy.rs"].replace("VARCHAR(64)", "VARCHAR(128)")
    reasons = pb.migration_audit("iam-db", tree("e4-old"), new)
    assert reasons and any("2" in r and "changed" in r for r in reasons), reasons


def test_a_whitespace_only_edit_inside_an_old_migrations_sql_is_needs_human() -> None:
    """The store checksums the exact bytes of `sql` (`Migration::checksum`), so a whitespace edit inside an old
    migration's literal fails boot with ChecksumMismatch; whitespace-collapsed text would call it unchanged."""
    new = tree("e4-old")
    new["src/schema/policy.rs"] = new["src/schema/policy.rs"].replace("(name VARCHAR(64) PRIMARY KEY)", "(name  VARCHAR(64) PRIMARY KEY)")
    assert new["src/schema/policy.rs"] != tree("e4-old")["src/schema/policy.rs"]
    reasons = pb.migration_audit("iam-db", tree("e4-old"), new)
    assert "iam-db: migration 2 changed" in reasons, reasons


def test_a_whitespace_edit_outside_an_old_literal_is_not_a_changed_migration() -> None:
    new = tree("e4-old")
    new["src/schema/policy.rs"] = new["src/schema/policy.rs"].replace("        version: 2,", "        version:   2,")
    assert pb.migration_audit("iam-db", tree("e4-old"), new) == []


def test_a_changed_non_literal_old_migration_is_compared_as_text() -> None:
    old, new = tree("e4-old"), tree("e4-old")
    for source, column in ((old, "name"), (new, "label")):
        source["src/schema/policy.rs"] = source["src/schema/policy.rs"].replace(
            '"CREATE TABLE setting (name VARCHAR(64) PRIMARY KEY)".into()', f'format!("CREATE TABLE setting ({column} INT)")'
        )
    assert pb.migration_audit("iam-db", old, old) == []
    assert "iam-db: migration 2 changed" in pb.migration_audit("iam-db", old, new)


def test_an_added_version_at_or_below_the_old_maximum_is_needs_human() -> None:
    """yadgar-store runs only `pending_after(applied)`, versions above the applied high-water mark, so an added
    version at or below the old maximum never runs on an existing database."""
    old = tree("e4-new")
    new = tree("e4-new")
    new["src/schema.rs"] = new["src/schema.rs"].replace("vec![user(), policy::setting(), audit()]", "vec![user(), policy::setting(), audit(), early()]") + (
        '\nfn early() -> Migration {\n    Migration {\n        version: 5,\n        name: "create_early".into(),\n'
        '        sql: "CREATE TABLE early (id INT)".into(),\n    }\n}\n'
    )
    found = pb.migration_evidence("iam-db", old, new)
    assert (
        "iam-db: migration 5 sits at or below the applied high-water mark 7; the store never runs it on an existing database (pending_after)"
        in found["reasons"]
    ), found["reasons"]
    assert found["status"] == "red"
    # Above the maximum it stays admitted: the same fn at version 8 is green.
    new["src/schema.rs"] = new["src/schema.rs"].replace("version: 5,", "version: 8,")
    assert pb.migration_audit("iam-db", old, new) == []


def test_the_migration_versions_read_both_files_and_skip_tests() -> None:
    assert set(pb.migration_blocks(tree("e4-new"))) == {1, 2, 7}



def test_an_added_migration_is_judged_by_the_classifier_not_an_interim_block() -> None:
    """Rewritten from `test_an_added_migration_is_an_interim_block`: car C10 part 3 (ADR-1099) lifts the ADR-0948 R5
    interim block. A CREATE TABLE added in its own fn and list entry is admitted; no reason carries the interim text."""
    found = pb.migration_evidence("iam-db", tree("e4-old"), tree("e4-new"))
    assert found["reasons"] == [] and found["status"] == "green", found["reasons"]
    assert not any(R5 in r for r in found["reasons"])


def test_an_admitted_migration_is_green_and_recorded_with_its_checksum() -> None:
    found = pb.migration_evidence("iam-db", tree("e4-old"), tree("e4-new"))
    checksum = hashlib.sha256(b"CREATE TABLE audit (id BIGINT PRIMARY KEY)").hexdigest()
    assert found["admitted"] == [{"version": 7, "name": "create_audit", "checksum": checksum, "statement_class": "A"}]
    assert found["forward_only"] == [7]


def test_an_unchanged_schema_records_no_admitted_and_no_forward_only_migration() -> None:
    found = pb.migration_evidence("iam-db", tree("e4-old"), tree("e4-old"))
    assert found == {"status": "green", "reasons": [], "admitted": [], "forward_only": []}


def test_a_non_literal_added_migration_is_needs_human_and_still_forward_only() -> None:
    found = pb.migration_evidence("iam-db", tree("e4-old"), with_audit_sql('format!("CREATE TABLE {T} (id INT)")'))
    assert found["status"] == "red" and found["admitted"] == [] and found["forward_only"] == [7]
    assert any(r.startswith("iam-db: migration 7 (create_audit) not admitted: non-literal sql") for r in found["reasons"]), found


def test_a_multi_statement_added_migration_is_needs_human() -> None:
    new = with_audit_sql('"CREATE TABLE audit (id INT);\n INSERT IGNORE INTO audit (id) VALUES (1)".into()')
    reasons = pb.migration_audit("iam-db", tree("e4-old"), new)
    assert reasons == ["iam-db: migration 7 (create_audit) not admitted: multi-statement migration; ruled red (ADR-1099)"], reasons


def test_an_insert_seed_is_needs_human_while_the_allowlist_is_empty() -> None:
    """Rewritten for car 3: no allowlist given is the empty one, so class D (a literal INSERT) is red by default, and
    the allowlist file ships empty (`[]`), so a seed stays NEEDS-HUMAN until a reviewed entry lands on main."""
    assert pb.NO_SEEDS == pb.SeedAllowlist(entries=(), problem=None)
    assert pb.parse_seed_allowlist((REPOSITORY / pb.SEED_ALLOWLIST_FILE).read_bytes()) == pb.NO_SEEDS
    assert (REPOSITORY / pb.SEED_ALLOWLIST_FILE).read_text() == "[]\n"
    reasons = pb.migration_audit("iam-db", tree("e4-old"), with_audit_sql('"INSERT INTO user (id) VALUES (1)".into()'))
    assert reasons == ["iam-db: migration 7 (create_audit) not admitted: seed INSERT into user (id) is not on the seed allowlist for iam-db"], reasons


def user_seed():
    """Built at test time: `test_migration_classifier` reloads the classifier module, and dataclass equality is per class."""
    return pb.classifier().SeedEntry(module="iam-db", table="user", columns=("id", "name"), reason="the first user", adr="ADR-1099")


def seeds_text(*entries: dict) -> str:
    return json.dumps(list(entries))


def entry_json(**overrides) -> dict:
    found = {"module": "iam-db", "table": "user", "columns": ["id", "name"], "reason": "the first user", "adr": "ADR-1099"}
    found.update(overrides)
    return found


def test_an_allowlisted_seed_is_green_and_records_the_entry_that_admitted_it() -> None:
    seeds = pb.parse_seed_allowlist(seeds_text(entry_json()))
    assert seeds == pb.SeedAllowlist(entries=(user_seed(),), problem=None)
    found = pb.migration_evidence("iam-db", tree("e4-old"), with_audit_sql('"INSERT INTO user (id, name) VALUES (1, \'root\')".into()'), seeds)
    checksum = hashlib.sha256(b"INSERT INTO user (id, name) VALUES (1, 'root')").hexdigest()
    assert found["status"] == "green" and found["reasons"] == [], found["reasons"]
    assert found["admitted"] == [
        {"version": 7, "name": "create_audit", "checksum": checksum, "statement_class": "D", "seed": {"module": "iam-db", "table": "user", "adr": "ADR-1099"}}
    ]
    other = pb.parse_seed_allowlist(seeds_text(entry_json(module="task-db")))
    assert pb.migration_evidence("iam-db", tree("e4-old"), with_audit_sql('"INSERT INTO user (id, name) VALUES (1, \'root\')".into()'), other)["status"] == "red"


def test_the_body_names_the_allowlist_entry_that_admitted_a_seed() -> None:
    seeded = evidence()
    seeded["migrations"] = {
        "iam-db": {
            "status": "green",
            "reasons": [],
            "admitted": [{"version": 21, "name": "seed_x", "checksum": "cd" * 32, "statement_class": "D", "seed": {"module": "iam-db", "table": "x", "adr": "ADR-1099"}}],
            "forward_only": [21],
        }
    }
    verification = pb.pr_body(seeded).split("## Verification\n", 1)[1].split("\n## ", 1)[0]
    line = "iam-db: migration 21 (seed_x) admitted, class D (allowlisted literal INSERT), sha256 `" + "cd" * 32 + "`"
    assert line + ", by the seed allowlist entry iam-db / x (ADR-1099)" in verification, verification


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("[", id="not-json"),
        pytest.param('[{"module": "iam-db", "module": "iam-db", "table": "user", "columns": ["id"], "reason": "r", "adr": "ADR-1099"}]', id="duplicate-key"),
        pytest.param("{}", id="not-a-list"),
        pytest.param(seeds_text("x"), id="entry-not-an-object"),
        pytest.param(seeds_text(entry_json(extra="x")), id="unknown-key"),
        pytest.param(seeds_text({k: v for k, v in entry_json().items() if k != "reason"}), id="missing-key"),
        pytest.param(seeds_text(entry_json(reason="")), id="empty-reason"),
        pytest.param(seeds_text(entry_json(reason=1)), id="reason-not-a-string"),
        pytest.param(seeds_text(entry_json(columns=[])), id="no-columns"),
        pytest.param(seeds_text(entry_json(columns="id")), id="columns-not-a-list"),
        pytest.param(seeds_text(entry_json(columns=["id", "id"])), id="repeated-column"),
        pytest.param(seeds_text(entry_json(columns=["id", True])), id="column-not-a-string"),
        pytest.param(seeds_text(entry_json(columns=["id; DROP"])), id="column-not-an-identifier"),
        pytest.param(seeds_text(entry_json(columns=["Id"])), id="column-not-lowercase"),
        pytest.param(seeds_text(entry_json(table="db.user")), id="qualified-table"),
        pytest.param(seeds_text(entry_json(table="User")), id="table-not-lowercase"),
        pytest.param(seeds_text(entry_json(table="us\u00e9r")), id="table-not-ascii"),
        pytest.param(seeds_text(entry_json(adr="ADR-99")), id="adr-short"),
        pytest.param(seeds_text(entry_json(adr="ADR-\uff11\uff10\uff19\uff19")), id="adr-unicode-digits"),
        pytest.param(seeds_text(entry_json(adr="ADR-1099\n")), id="adr-trailing-newline"),
        pytest.param(seeds_text(entry_json(module="gateway")), id="module-not-db"),
        pytest.param(seeds_text(entry_json(), entry_json(columns=["id"])), id="table-listed-twice"),
        pytest.param("\ufeff[]", id="byte-order-mark"),
        pytest.param(b"[\xff]", id="not-utf8"),
    ],
)
def test_a_malformed_allowlist_admits_nothing_and_says_why(text) -> None:
    seeds = pb.parse_seed_allowlist(text)
    assert seeds.entries == () and seeds.problem and pb.SEED_ALLOWLIST_FILE in seeds.problem, seeds


@pytest.mark.parametrize("depth", [100_000, 2_000_000], ids=["recursion", "deep"])
def test_a_deeply_nested_allowlist_is_a_problem_not_an_exception(depth: int) -> None:
    """Security review of argocd#216: `json` recurses per nesting level and raises RecursionError."""
    seeds = pb.parse_seed_allowlist("[" * depth + "]" * depth)
    assert seeds.entries == () and seeds.problem and pb.SEED_ALLOWLIST_FILE in seeds.problem, seeds


def test_a_malformed_allowlist_makes_a_seed_red_with_its_reason_and_never_raises() -> None:
    seeds = pb.parse_seed_allowlist('[{"module": "iam-db", "module": "x"}]')
    found = pb.migration_evidence("iam-db", tree("e4-old"), with_audit_sql('"INSERT INTO user (id) VALUES (1)".into()'), seeds)
    assert found["status"] == "red" and len(found["reasons"]) == 1, found["reasons"]
    assert "the seed allowlist is unusable" in found["reasons"][0] and "duplicate key 'module'" in found["reasons"][0], found["reasons"]
    ddl = pb.migration_evidence("iam-db", tree("e4-old"), tree("e4-new"), seeds)
    assert ddl["status"] == "green" and ddl["reasons"] == [], "a malformed allowlist touches class D only"


def test_the_allowlist_is_read_from_the_tree_pin_bump_checks(tmp_path: Path) -> None:
    """`check` reads `repo`'s copy: main's checkout in auto-pin, the base copy `pin_evidence.evidence` writes there."""
    assert pb.seed_allowlist(tmp_path) == pb.NO_SEEDS, "no file is the empty allowlist"
    (tmp_path / pb.SEED_ALLOWLIST_FILE).parent.mkdir(parents=True)
    (tmp_path / pb.SEED_ALLOWLIST_FILE).write_text(seeds_text(entry_json()))
    assert pb.seed_allowlist(tmp_path).entries == (user_seed(),)
    (tmp_path / pb.SEED_ALLOWLIST_FILE).unlink()
    (tmp_path / pb.SEED_ALLOWLIST_FILE).mkdir()
    unreadable = pb.seed_allowlist(tmp_path)
    assert unreadable.entries == () and "could not be read" in unreadable.problem, unreadable


AUDIT_LITERAL = """    Migration {
        version: 7,
        name: "create_audit".into(),
        sql: "CREATE TABLE audit (id BIGINT PRIMARY KEY)".into(),
    }"""


def with_audit_literal(literal: str) -> dict:
    """e4-new with migration 7's whole struct literal replaced by `literal` (Rust source text)."""
    new = tree("e4-new")
    assert AUDIT_LITERAL in new["src/schema.rs"]
    new["src/schema.rs"] = new["src/schema.rs"].replace(AUDIT_LITERAL, literal)
    return new


@pytest.mark.parametrize(
    "literal",
    [
        pytest.param(
            '    Migration {\n        version: 7,\n        name: "create_audit".into(),\n'
            '        #[cfg(test)]\n        sql: "CREATE TABLE audit (id INT)".into(),\n'
            '        #[cfg(not(test))]\n        sql: "DROP TABLE user".into(),\n    }',
            id="cfg-sql-twice",
        ),
        pytest.param(
            '    Migration {\n        version: 7,\n        name: "create_audit".into(),\n'
            '        sql: "DROP TABLE user".into(),\n        sql: "CREATE TABLE audit (id INT)".into(),\n    }',
            id="sql-twice",
        ),
        pytest.param(
            '    Migration {\n        #[cfg(test)]\n        version: 7,\n        #[cfg(not(test))]\n        version: 8,\n'
            '        name: "create_audit".into(),\n        sql: "CREATE TABLE audit (id INT)".into(),\n    }',
            id="cfg-version-twice",
        ),
        pytest.param(
            '    Migration {\n        version: 7,\n        name: { side_effect(); "create_audit" }.into(),\n'
            '        sql: "CREATE TABLE audit (id INT)".into(),\n    }',
            id="name-block-expression",
        ),
        pytest.param(
            '    Migration {\n        version: 7,\n        sql: "CREATE TABLE audit (id INT)".into(),\n        ..base()\n    }',
            id="struct-update",
        ),
        pytest.param(
            '    Migration {\n        version: 7,\n        name: "create_audit".into(),\n'
            '        sql: "CREATE TABLE audit (id INT)".into(),\n        extra: 1,\n    }',
            id="other-field",
        ),
        pytest.param(
            '    Migration {\n        version: SEVEN + 7,\n        name: "create_audit".into(),\n'
            '        sql: "CREATE TABLE audit (id INT)".into(),\n    }',
            id="version-expression",
        ),
        pytest.param(
            '    Migration {\n        version: g(version: 7,),\n        name: "create_audit".into(),\n'
            '        sql: "CREATE TABLE audit (id INT)".into(),\n    }',
            id="version-digits-only-inside-an-expression",
        ),
        pytest.param(
            '    Migration {\n        version: 7,\n        sql: "CREATE TABLE audit (id INT)".into(),\n    }',
            id="no-name",
        ),
    ],
)
def test_a_migration_literal_other_than_exactly_version_name_sql_is_red(literal: str) -> None:
    """Security review of argocd#214: the struct literal has a positive grammar too. Exactly `version: <int>`,
    `name: <string literal>` and `sql: <string literal>`, each once; an attribute, `..`, another field or an
    expression is red, whatever `sql:` the first match would have read."""
    found = pb.migration_evidence("iam-db", tree("e4-old"), with_audit_literal(literal))
    assert found["status"] == "red" and found["admitted"] == [], found
    # `version: SEVEN + 7` is no migration site at all, so its fn is a residual change instead; red either way.
    wanted = ("not exactly the fields version, name and sql", "changed outside the added migration(s)")
    assert any(w in r for r in found["reasons"] for w in wanted), found["reasons"]


def test_a_comment_inside_the_migration_literal_is_still_green() -> None:
    literal = AUDIT_LITERAL.replace("        sql:", "        // Why, as iam-db writes it.\n        sql:").replace(
        ".into(),\n    }", ".into(), // trailing\n    }"
    )
    assert pb.migration_audit("iam-db", tree("e4-old"), with_audit_literal(literal)) == []


def test_a_raw_string_sql_inside_the_migration_literal_is_green() -> None:
    literal = AUDIT_LITERAL.replace('"CREATE TABLE audit (id BIGINT PRIMARY KEY)".into()', 'r#"CREATE TABLE audit (id BIGINT PRIMARY KEY)"#.into()')
    found = pb.migration_evidence("iam-db", tree("e4-old"), with_audit_literal(literal))
    assert found["reasons"] == [] and found["admitted"][0]["checksum"] == hashlib.sha256(b"CREATE TABLE audit (id BIGINT PRIMARY KEY)").hexdigest()


def test_the_migration_name_is_sanitised_in_reasons_and_the_record() -> None:
    bad = with_audit_literal(AUDIT_LITERAL.replace('"create_audit"', '"a\\nb`c"').replace("CREATE TABLE audit (id BIGINT PRIMARY KEY)", "DROP TABLE x"))
    reasons = pb.migration_audit("iam-db", tree("e4-old"), bad)
    assert reasons and reasons[0].startswith("iam-db: migration 7 (a b\\`c) not admitted"), reasons
    good = with_audit_literal(AUDIT_LITERAL.replace('"create_audit"', '"a\\nb`c"'))
    assert pb.migration_evidence("iam-db", tree("e4-old"), good)["admitted"][0]["name"] == "a b\\`c"


DOCUMENTED_FN = """
/// Why the table exists, as every real -db module writes it.
/// A second doc line.
// and a plain comment
fn audit() -> Migration {
    Migration {
        version: 7,
        name: "create_audit".into(),
        // A comment inside the literal stays with it.
        sql: "CREATE TABLE audit (
                  id BIGINT PRIMARY KEY
              ) ENGINE=InnoDB"
            .into(),
    }
}
"""


def multi_line(source: dict, entries: list[str], tail: str = "") -> dict:
    """`source` with its list rewritten one entry per line, with trailing commas, as the real modules write it."""
    text = source["src/schema.rs"]
    start = text.index("vec![")
    stop = text.index("]", start) + 1
    body = "".join(f"        {e},\n" for e in entries)
    return {**source, "src/schema.rs": text[:start] + "vec![\n" + body + "    ]" + text[stop:] + tail}


def test_an_added_migration_leaves_no_residual_file_diff() -> None:
    """The fn (with its `///` and `//` lines) and its list entry are removed; the rest is the old file, so no reason."""
    old = multi_line(tree("e4-old"), ["user()", "policy::setting()"])
    new = multi_line(tree("e4-old"), ["user()", "policy::setting()", "audit()"], DOCUMENTED_FN)
    found = pb.migration_evidence("iam-db", old, new)
    assert found["reasons"] == [] and [m["version"] for m in found["admitted"]] == [7], found
    # Also with the e4-new shape: the entry appended without a trailing comma, `, audit()]`.
    assert pb.migration_audit("iam-db", tree("e4-old"), tree("e4-new")) == []


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(lambda t: t.replace("CREATE TABLE user (id BIGINT PRIMARY KEY)", "CREATE TABLE user (id INT)"), id="old-block"),
        pytest.param(lambda t: t + "\nconst EXTRA: &str = \"x\";\n", id="new-const"),
        pytest.param(lambda t: t.replace("pub fn migrations()", "pub fn migrations_renamed()"), id="renamed-fn"),
        pytest.param(lambda t: t.replace("vec![user(), policy::setting(), audit()]", "vec![user(), audit()]"), id="dropped-entry"),
        pytest.param(lambda t: t.replace("audit()]", "audit(), audit()]"), id="listed-twice"),
        pytest.param(
            lambda t: t.replace("fn audit() -> Migration {\n    Migration {", "fn audit() -> Migration {\n    let _x = 1;\n    Migration {"),
            id="fn-body-is-not-only-the-literal",
        ),
        pytest.param(lambda t: t.replace("fn audit() -> Migration {", "#[cfg(feature = \"x\")]\nfn audit() -> Migration {"), id="attribute"),
    ],
)
def test_a_residual_change_beside_an_added_migration_is_red(edit) -> None:
    """The schema file outside the added migration fns and their list entries must be the old one (ADR-1099)."""
    new = tree("e4-new")
    new["src/schema.rs"] = edit(new["src/schema.rs"])
    reasons = pb.migration_audit("iam-db", tree("e4-old"), new)
    assert "iam-db: schema source src/schema.rs changed outside the added migration(s)" in reasons, reasons


TEAM_RS = """//! Migrations 7 and 8, for teams.

use yadgar_store::migrate::Migration;

pub(super) fn team() -> Migration {
    Migration {
        version: 7,
        name: "create_team".into(),
        sql: "CREATE TABLE team (id INT PRIMARY KEY)".into(),
    }
}

/// Doc for the index.
pub(super) fn team_index() -> Migration {
    Migration {
        version: 8,
        name: "team_index".into(),
        sql: "CREATE INDEX ix_team ON team (id)".into(),
    }
}
"""


def with_child(team_rs: str = TEAM_RS) -> dict:
    new = tree("e4-old")
    new["src/schema.rs"] = new["src/schema.rs"].replace(
        "mod policy;\n", "mod policy;\n/// Migrations 7 and 8, for teams.\nmod team;\n"
    ).replace("vec![user(), policy::setting()]", "vec![user(), policy::setting(), team::team(), team::team_index()]")
    new["src/schema/team.rs"] = team_rs
    return new


def test_a_new_child_schema_file_with_only_migration_fns_is_green() -> None:
    found = pb.migration_evidence("iam-db", tree("e4-old"), with_child())
    assert found["reasons"] == [], found["reasons"]
    assert [(m["version"], m["statement_class"]) for m in found["admitted"]] == [(7, "A"), (8, "B")]
    assert found["forward_only"] == [7, 8]


@pytest.mark.parametrize(
    "team_rs",
    [
        pytest.param(TEAM_RS + "\nconst T: &str = \"team\";\n", id="const"),
        pytest.param(TEAM_RS + "\npub fn helper() -> u8 {\n    1\n}\n", id="helper-fn"),
    ],
)
def test_a_new_child_schema_file_with_more_than_migration_fns_is_red(team_rs: str) -> None:
    reasons = pb.migration_audit("iam-db", tree("e4-old"), with_child(team_rs))
    assert "iam-db: schema source src/schema/team.rs changed outside the added migration(s)" in reasons, reasons


def test_a_mod_line_without_a_new_child_file_is_red() -> None:
    new = tree("e4-new")
    new["src/schema.rs"] = new["src/schema.rs"].replace("mod policy;\n", "mod policy;\nmod other;\n")
    reasons = pb.migration_audit("iam-db", tree("e4-old"), new)
    assert "iam-db: schema source src/schema.rs changed outside the added migration(s)" in reasons, reasons


def test_a_moved_yadgar_store_tag_stays_red_after_c10() -> None:
    """ADR-1092 / ADR-1099: even beside an admitted migration, a moved store tag is NEEDS-HUMAN, its reason unchanged
    byte for byte (acknowledgements match reason strings)."""
    old, new = with_cargo(tree("e4-old"), "v0.2.20", "v0.4.0"), with_cargo(tree("e4-new"), "v0.2.20", "v0.4.1")
    found = pb.migration_evidence("iam-db", old, new)
    store = [r for r in found["reasons"] if "yadgar-store" in r]
    assert store == [
        f"{r}; store's migrate.rs owns the ledger DDL; it stays NEEDS-HUMAN after car C10 (plan E4)"
        for r in pb.crate_moves("iam-db", old, new, only="yadgar-store")
    ] and store, found["reasons"]
    assert found["status"] == "red" and [m["version"] for m in found["admitted"]] == [7]


def test_a_moved_yadgar_store_tag_on_a_db_module_is_needs_human() -> None:
    """yadgar-store's migrate.rs owns the ledger DDL, so its move is a schema change for the -db module."""
    old, new = with_cargo(tree("e4-old"), "v0.2.20", "v0.4.0"), with_cargo(tree("e4-old"), "v0.2.20", "v0.4.1")
    reasons = pb.migration_audit("iam-db", old, new)
    assert any("yadgar-store" in r and "v0.4.0" in r and "v0.4.1" in r for r in reasons), reasons



def test_a_schema_change_outside_the_migration_blocks_is_needs_human() -> None:
    """SQL from a const, or a migration dropped from the set while its block stays, changes the file, not a block."""
    new = tree("e4-old")
    new["src/schema.rs"] = new["src/schema.rs"].replace("vec![user(), policy::setting()]", "vec![user()]")
    reasons = pb.migration_audit("iam-db", tree("e4-old"), new)
    assert any("schema source" in r for r in reasons), reasons


def test_an_included_schema_file_is_needs_human() -> None:
    new = tree("e4-old")
    old = copy.deepcopy(new)
    for source in (old, new):
        source["src/schema/policy.rs"] = source["src/schema/policy.rs"].replace(
            '"CREATE TABLE setting (name VARCHAR(64) PRIMARY KEY)"', 'include_str!("setting.sql")'
        )
    reasons = pb.migration_audit("iam-db", old, new)
    assert any("include_str!" in r for r in reasons), reasons


# ── the body ────────────────────────────────────────────────────────────────

BULLET = re.compile(r"^- (build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)(\([^)]+\))?!?: \S")


def evidence(accept: bool = True, reasons: list | None = None) -> dict:
    return {
        "pin": "0.25.12",
        "target": "0.25.14",
        "modules": {"old": OLD_MODULES, "new": NEW_MODULES},
        "commits": [{"sha": "abc1234", "subject": "ci: pin gateway 0.12.11 in the parent chart"}],
        "helms": ["v3.18.4", "v4.3.0"],
        "objects": 94,
        "changed": [
            {
                "object": "apps/Deployment//gateway",
                "field": "spec.template.spec.containers[0].image",
                "module": "gateway",
                "version": "0.12.11",
                "digest": DIGEST[("gateway", "0.12.11")],
            }
        ],
        "boot_audit": {"gateway": {"status": "green", "reasons": []}},
        "migrations": {},
        "accept": accept,
        "reasons": reasons or [],
    }


def headings(body: str) -> list[str]:
    return [line[3:] for line in body.splitlines() if line.startswith("## ")]


def test_the_body_carries_the_template_headings_and_one_bullet() -> None:
    body = pb.pr_body(evidence())
    assert headings(body) == ["What", "Why", "Changelog", "Verification", "Risk"]
    changelog = body.split("## Changelog\n", 1)[1].split("\n## ", 1)[0]
    lines = [line for line in changelog.splitlines() if line.strip()]
    assert len(lines) == 1 and BULLET.match(lines[0]), lines
    assert "0.25.14" in lines[0] and "gateway 0.12.11" in lines[0]


def test_the_body_carries_the_commit_table_the_digest_and_the_rollback() -> None:
    body = pb.pr_body(evidence())
    assert "abc1234" in body and DIGEST[("gateway", "0.12.11")] in body
    assert "argocd app terminate-op yadgar" in body
    assert "MIGRATION_NOTES" not in body


def test_the_body_names_forward_only_for_a_crossed_migration() -> None:
    """ADR-1099: an admitted migration is recorded with its checksum, and the rollback says a revert is not clean."""
    crossed = evidence()
    checksum = "ab" * 32
    crossed["migrations"] = {
        "iam-db": {
            "status": "green",
            "reasons": [],
            "admitted": [{"version": 22, "name": "create_x", "checksum": checksum, "statement_class": "A"}],
            "forward_only": [22],
        }
    }
    body = pb.pr_body(crossed)
    verification = body.split("## Verification\n", 1)[1].split("\n## ", 1)[0]
    assert "iam-db: migration 22 (create_x) admitted, class A (CREATE TABLE), sha256 `" + checksum + "`" in verification
    risk = body.split("## Risk\n", 1)[1]
    assert "crossed migration(s) iam-db 22" in risk and "NOT clean" in risk and "DatabaseAhead" in risk, risk
    assert "forward-fix pin" in risk and "interim block" not in risk, risk
    assert "Revert this PR's commit" not in risk and "Do not revert this PR" in risk, risk
    plain = pb.pr_body(evidence()).split("## Risk\n", 1)[1]
    assert "crosses no migration" in plain and "DatabaseAhead" not in plain and "interim block" not in plain, plain
    assert "2. Revert this PR's commit by PR." in plain, plain


def test_a_needs_human_body_names_every_reason() -> None:
    body = pb.pr_body(evidence(False, ["platform moved 0.2.4 -> 0.2.5"]))
    assert "NEEDS-HUMAN" in body and "platform moved 0.2.4 -> 0.2.5" in body


def test_the_writer_loads_without_the_probe_in_its_tree(tmp_path: Path) -> None:
    """`write_files` runs the writer inside a tree that holds the mechanical files and the two writers, nothing
    else (the replay tests build exactly that). A writer that loads `scripts/project_probe.py` at module level dies
    there with FileNotFoundError; the probe is a test's input, never the `--write` path's."""
    gates = tmp_path / "scripts" / "gates"
    gates.mkdir(parents=True)
    for name in ("test_yadgar_application.py", "test_no_two_owners.py"):
        shutil.copyfile(REPOSITORY / "scripts" / "gates" / name, gates / name)
    writer = gates / "test_yadgar_application.py"
    finished = subprocess.run(
        [sys.executable, str(writer), "--write"], capture_output=True, text=True, check=False, cwd=tmp_path
    )
    assert "project_probe" not in finished.stderr, finished.stderr


# ── E3: an unresolved call site follows its fn across files (yaadgaar 238) ──


def keyed_fn(prefix: str, name: str = "timeout") -> str:
    return f'pub fn {name}() -> Option<String> {{\n    let prefix = "{prefix}";\n    std::env::var(&format!("{{prefix}}_X")).ok()\n}}\n'


REQUEST_FN = (
    "pub fn timeout(req: &str) -> Option<String> {\n"
    "    let prefix = req;\n"
    '    std::env::var(&format!("{prefix}_X")).ok()\n}\n'
)


def audit(old_files: dict[str, str], new_files: dict[str, str]) -> list[str]:
    old, new = tree("e3-old"), tree("e3-old")
    old.update(old_files)
    new.update(new_files)
    return pb.boot_audit("gateway", old, new, RENDERED)


def test_a_fn_moved_verbatim_between_files_is_green() -> None:
    assert audit({"src/a.rs": keyed_fn("APP")}, {"src/a.rs": "// gone\n", "src/b.rs": keyed_fn("APP")}) == []


def test_a_moved_fn_with_a_changed_binding_and_the_same_site_text_is_needs_human() -> None:
    reasons = audit({"src/a.rs": keyed_fn("APP")}, {"src/a.rs": "// gone\n", "src/b.rs": keyed_fn("SECRET")})
    assert any("cannot resolve" in r and "src/b.rs" in r for r in reasons), reasons


def test_a_changed_binding_with_the_same_site_text_in_the_same_file_is_needs_human() -> None:
    reasons = audit({"src/a.rs": keyed_fn("APP")}, {"src/a.rs": keyed_fn("SECRET")})
    assert any("cannot resolve" in r and "src/a.rs" in r for r in reasons), reasons


def test_a_different_fn_of_the_same_name_in_another_file_is_needs_human() -> None:
    reasons = audit({"src/a.rs": keyed_fn("APP")}, {"src/a.rs": "// gone\n", "src/b.rs": REQUEST_FN})
    assert any("cannot resolve" in r and "src/b.rs" in r for r in reasons), reasons


def test_a_rewritten_fn_of_the_same_name_in_the_same_file_is_needs_human() -> None:
    reasons = audit({"src/a.rs": keyed_fn("APP")}, {"src/a.rs": REQUEST_FN})
    assert any("cannot resolve" in r for r in reasons), reasons


def test_a_copy_of_a_fn_into_another_file_is_still_needs_human() -> None:
    assert audit({"src/a.rs": keyed_fn("APP")}, {"src/a.rs": keyed_fn("APP"), "src/b.rs": keyed_fn("APP")})


def test_one_site_moved_and_a_second_added_across_files_is_needs_human() -> None:
    new = {"src/a.rs": "// gone\n", "src/b.rs": keyed_fn("APP") + keyed_fn("APP")}
    assert audit({"src/a.rs": keyed_fn("APP")}, new)


def test_a_moved_fn_renamed_is_needs_human() -> None:
    assert audit({"src/a.rs": keyed_fn("APP")}, {"src/a.rs": "// gone\n", "src/b.rs": keyed_fn("APP", "timeout2")})


NESTED_AFTER = (
    "pub fn timeout() -> Option<String> {\n"
    '    fn inner() -> u8 {\n        1\n    }\n'
    '    let prefix = "{P}";\n'
    '    std::env::var(&format!("{prefix}_X")).ok()\n'
    "}\n"
)


def test_a_site_after_a_nested_fn_is_hashed_with_its_own_fn() -> None:
    old = {"src/a.rs": NESTED_AFTER.replace("{P}", "APP")}
    new = {"src/a.rs": NESTED_AFTER.replace("{P}", "KEY")}
    assert any("cannot resolve" in r for r in audit(old, new))


ARRAY_PARAM = (
    "pub fn timeout(key: [u8; 4]) -> Result<Option<String>, [u8; 4]>\n"
    "where\n    Vec<[u8; 4]>: Sized,\n{\n"
    '    let prefix = "{P}";\n'
    '    Ok(std::env::var(&format!("{prefix}_X")).ok())\n'
    "}\n"
)


def test_a_semicolon_in_an_array_parameter_does_not_hide_the_body() -> None:
    old = {"src/a.rs": ARRAY_PARAM.replace("{P}", "APP")}
    new = {"src/a.rs": ARRAY_PARAM.replace("{P}", "KEY")}
    assert any("cannot resolve" in r and "fn timeout" in r for r in audit(old, new))


def test_a_fn_with_an_array_signature_moved_verbatim_between_files_is_green() -> None:
    fn = ARRAY_PARAM.replace("{P}", "APP")
    assert audit({"src/a.rs": fn}, {"src/a.rs": "// gone\n", "src/b.rs": fn}) == []


TOP_LEVEL = 'static P: &str = "{P}";\nstatic T: Option<String> = std::env::var(&format!("{p}_X")).ok();\n'


def test_a_top_level_site_that_changes_is_needs_human() -> None:
    old = {"src/a.rs": TOP_LEVEL.replace("{P}", "APP")}
    new = {"src/a.rs": TOP_LEVEL.replace("{P}", "KEY")}
    assert any("cannot resolve" in r for r in audit(old, new))
