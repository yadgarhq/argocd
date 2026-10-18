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
  changed under a matching digest, a helm disagreement, a single helm, a
  platform move, and a module image changed with no module version moved;
- E3: a new `env_required` key the render lacks is NEEDS-HUMAN, and the same
  key rendered is green; a new `env::var` / `var_os` / lookup-closure read and a
  new const passed to `env_required` are NEEDS-HUMAN; a new error-code literal
  is green; braces inside string, char and comment text do not cut a
  `#[cfg(test)] mod` wrong; a local closure wrapping a lookup (seeder's
  `required`) and a path-qualified const are followed; a new call site whose
  key cannot be resolved (`format!`) and a moved `yadgar-*` crate pin are
  NEEDS-HUMAN; a DaemonSet, an initContainer and a swap inside the registry are
  NEEDS-HUMAN; platform moves and migrations carry the ADR-0948 R5 interim text;
- E3 fail-closed tripwires (re-review at aad2e9d): a new shared-crate env entry
  point, every unknown reader shape (use-as, bare use, module alias, dotenvy,
  vars scan, generic and boxed `Fn(&str)`, clap `env =`), a new key through a fn
  or macro wrapper, a duplicate unresolved site in a new fn, an unpinnable or
  overridden shared crate, a moved Cargo.lock commit, a missing Cargo.lock: each
  NEEDS-HUMAN; a key rendered on one container of two is not rendered;
- E4: a moved `yadgar-store` pin on a -db module, a schema change outside the
  blocks and an `include_str!` are NEEDS-HUMAN;
- E4: an added migration version is NEEDS-HUMAN; an unchanged schema is green;
- the body carries the template's five headings and one Conventional Commits
  bullet, a rollback with `argocd app terminate-op`, and no MIGRATION_NOTES.
"""

from __future__ import annotations

import copy
import importlib.util
import re
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


def test_the_chart_pin_moves_its_tag_only() -> None:
    text = '{\n  "chart_tag": "v0.25.14",\n  "platform_version": "0.2.6"\n}\n'
    assert pb.rewrite_chart_pin(text, "0.25.27") == '{\n  "chart_tag": "v0.25.27",\n  "platform_version": "0.2.6"\n}\n'


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


def test_a_platform_move_is_needs_human() -> None:
    """The #103 shape: platform 0.2.4 -> 0.2.5 moves the operator Applications."""
    old = {**OLD_MODULES, "platform": "0.2.4"}
    new = {**NEW_MODULES, "platform": "0.2.5"}
    verdict = classify(two_helms(render_at("0.12.10"), render_at("0.12.11")), new_modules=new, old_modules=old)
    assert not verdict.accept
    assert any("platform moved" in r and "0.2.4" in r and "0.2.5" in r for r in verdict.reasons), verdict.reasons


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



def test_a_platform_move_is_an_interim_block() -> None:
    """ADR-0948 R5 (Max, 2026-10-18): platform moves auto-merge when green, once car C10 lands."""
    old = {**OLD_MODULES, "platform": "0.2.4"}
    new = {**NEW_MODULES, "platform": "0.2.5"}
    verdict = classify(two_helms(render_at("0.12.10"), render_at("0.12.11")), new_modules=new, old_modules=old)
    assert any("platform moved" in r and R5 in r for r in verdict.reasons), verdict.reasons


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


def test_an_added_migration_version_is_needs_human() -> None:
    reasons = pb.migration_audit("iam-db", tree("e4-old"), tree("e4-new"))
    assert reasons and any("iam-db" in r and "7" in r for r in reasons), reasons


def test_an_unchanged_schema_is_green() -> None:
    assert pb.migration_audit("iam-db", tree("e4-old"), tree("e4-old")) == []


def test_a_changed_migration_is_needs_human() -> None:
    new = tree("e4-old")
    new["src/schema/policy.rs"] = new["src/schema/policy.rs"].replace("VARCHAR(64)", "VARCHAR(128)")
    reasons = pb.migration_audit("iam-db", tree("e4-old"), new)
    assert reasons and any("2" in r and "changed" in r for r in reasons), reasons


def test_the_migration_versions_read_both_files_and_skip_tests() -> None:
    assert set(pb.migration_blocks(tree("e4-new"))) == {1, 2, 7}



def test_an_added_migration_is_an_interim_block() -> None:
    reasons = pb.migration_audit("iam-db", tree("e4-old"), tree("e4-new"))
    assert any(R5 in r for r in reasons), reasons


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


def test_a_needs_human_body_names_every_reason() -> None:
    body = pb.pr_body(evidence(False, ["platform moved 0.2.4 -> 0.2.5"]))
    assert "NEEDS-HUMAN" in body and "platform moved 0.2.4 -> 0.2.5" in body
