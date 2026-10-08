# argocd — the GitOps control plane

Argo CD's own configuration. **Argo manages Argo:** the chart is applied once by
hand to bootstrap, and from then on every change here arrives the same way every
other change does.

The cluster lives in the nix repo. `make bootstrap` and `make secrets` — the
one-time Argo CD install and the secrets it needs before anything else can
sync — live here too (ADR-0803; moved from
[`yadgarhq/deploy`](https://github.com/yadgarhq/deploy)). Decisions are in
[`yadgarhq/docs`](https://github.com/yadgarhq/docs) — D54 especially.

| Path                           |                                                    |
| ------------------------------ | -------------------------------------------------- |
| `Makefile`                     | `make bootstrap`/`make secrets` (ADR-0803)         |
| `install/values.yaml`          | Helm values for the `argo-cd` chart                |
| `projects/root.yaml`           | app-of-apps root — hand-applied, not self-managed  |
| `applications/yadgar.yaml`     | the parent chart that deploys the estate's modules |
| `applications/<operator>.yaml` | the six operator Applications root adopted (E3)    |
| `applications/<child>.yaml`    | the five Applications `infra` released (M3)        |
| `manifests/<name>/`            | directory sources; outside root's include glob     |
| `scripts/gates/`               | gates that need helm or the network (CI only)      |
| `MIGRATION_NOTES.md`           | what a change here needs from a person             |
| `scripts/verify_handover.py`   | read-only post-merge verifier (below)              |
| `scripts/project_probe.py`     | the 785 refusal-counter probe (ADR-0841)           |
| `verifier/manifests/`          | their identity, ClusterRole and egress policy      |

`projects/root.yaml` is applied by hand, once, at `make bootstrap` — it sits
outside its own `directory.include` glob, so it never selects itself and
nothing re-applies it on a git change. A change to this one file needs a
hand `kubectl apply -f projects/root.yaml` to go live; every other file root
selects reaches the cluster through root's normal automated sync.

`applications/` holds single `Application` resources. It used to sit beside
`applicationsets/`, which held generators that produced many — `yadgar-modules`
discovered each module repo and generated one Application per module.
Ledger 1270b retired that ApplicationSet and the directory with it, once
`applications/yadgar.yaml`'s parent chart (ADR-0786) took over deploying the
estate's modules as one Application.

## What lives where

|                  |                                                                                   |
| ---------------- | --------------------------------------------------------------------------------- |
| **this repo**    | Argo itself and every Application this organisation runs, `yadgar.yaml` included  |
| **module repos** | each carries its own `chart/`, deployed by the parent chart `yadgar.yaml` sources |

Infrastructure `Application` resources used to live in `deploy`, beside the
manifests and values they pointed at, rather than being centralised here —
the same argument D54 makes against an umbrella chart: a single place
listing everything becomes the thing every change has to touch. Argo's own
configuration was the one exception, because it had nowhere else to live.
Both exceptions below ran their course: `deploy` owns no Application any
more (ADR-0803, ADR-0828), and every Application this organisation runs is
declared here instead.

**The operators are the second exception, and the first step of retiring
`deploy`.** ADR-0803 moves what this organisation still needs from
`yadgarhq/deploy` to here, and ADR-0824 keeps one Application per operator:
`cert-manager`, `keda`, `mariadb-operator`, `envoy-gateway` and `prometheus`.
E2 deleted them, and `mariadb-operator-crds`, from `deploy`'s `infra/`. E3
declared the same six specs under `applications/`, and `root` adopted each live
object by name, so every uid was kept. `scripts/tests/test_operator_applications.py`
pinned each spec. D4 and D7.3 moved all five to the `platform` chart, and the
same test now pins that shape. D7.1 deleted `mariadb-operator-crds`. Its 12
CRDs stayed in the cluster, untracked, until D7.3 sourced `mariadb-operator`
from `platform`, which renders them. The same test holds the deleted
Application absent.

**`infra`'s five children followed (M3, option A, ADR-0828).** `arc`,
`estate-front`, `estate-front-runner`, `tls` and `yadgar` left `deploy`'s
`infra/` and root adopted each live object by name. `yadgar` is the parent
chart's own `example/application.yaml` with this organisation's values inlined
as `valuesObject`. The two directory sources, `tls` and `estate-front`, read
`manifests/<name>/` here. `manifests/` sits outside root's `applications/*.yaml`
include on purpose: that glob lets `*` cross `/`, so a file under
`applications/` would be applied by root as well.
`scripts/tests/test_infra_children.py` pins the five offline;
`scripts/gates/` holds the two-owners gate and the `yadgar` example and render
gates, which need helm and run in CI's `two-owners` job.
| `applications/argocd.yaml` | Argo managing Argo — what makes `install/values.yaml` actually apply |

## Argo manages Argo

`install/values.yaml` is not read by the bootstrap. `make bootstrap`, here,
passes only the few `--set` flags needed to _reach_ the server; the real
values arrive through `applications/argocd.yaml`, one sync later.

Without that Application the values file would apply to nothing while looking
authoritative, which is worse than not having it.

Its sync is **not** automated, deliberately. A self-managing Argo that
auto-syncs its own Deployment can restart itself mid-sync and leave the
operation in an unknown state — reviewing the diff and syncing on purpose is the
safer default for the one component that would have to fix itself.

## How modules get deployed

`applications/yadgar.yaml` is `yadgarhq/chart`'s parent chart, with this
organisation's values inlined as `valuesObject` (ADR-0786). One Application,
one sync, deploys every module as a subchart of the published parent chart
version — a module release bumps that version rather than editing anything
here.

**Before this (D54, retired at ledger 1270b).** `applicationsets/modules.yaml`
used Argo's **SCM Provider generator** over the `yadgarhq` organisation: a repo
deployed when it had a `chart/` directory and carried the
**`yadgar-deployable`** topic, so nothing central listed the modules. That
generated an Application per module to avoid an umbrella chart pinning every
module's version in one `Chart.yaml`, where a module release would edit the
umbrella and every module would wait on it. The parent chart cutover
(`plans/dogfooding-the-parent-chart.md` in `yadgarhq/docs`) moved every module
under the one published chart instead, and the generator's last live job —
reading `versions/<module>.yaml` for a per-module image pin — moved with it,
so the ApplicationSet generated zero Applications and was retired along with
`versions/`, `scripts/versions_pinned.py` and the `github-scm` Secret its
generator authenticated with.

## Post-merge verification

`scripts/verify_handover.py` proves a merge recreated nothing. It is read-only:
every kubectl call goes through one function that requires `--context` (there
is no default, and the operator's default context is production), allows only
`get` with an allowlist of flags (`-n`, `-A`, one `-o json`, `--no-headers`), and
reads a Secret only through one exact argv: name, uid and resourceVersion columns.

```bash
S=scripts/verify_handover.py
python3 $S snapshot --context kind-yadgar --out before.json
# ... merge ...
python3 $S wait --context kind-yadgar --app root --revision <merge sha>
python3 $S wait --context kind-yadgar --settled --since <time root reached the sha>
python3 $S snapshot --context kind-yadgar --out after.json
python3 $S diff before.json after.json   # 0 pass, 1 a failure, 2 a refusal
```

Wait, then settle, then snapshot. A snapshot taken before root syncs the merge
compares the old state with itself.

**What it reads**, for every Application under `root` (root's own Applications,
and those its ApplicationSets generate): uid, finalizers, syncPolicy, sync and
health; every CRD's uid and generation, with the same name=uid hash the
handover scripts printed; every object those Applications track, except Secrets;
every instance of every CRD they track, cluster-wide (cert-manager's
CertificateRequests, Orders and Challenges excepted); the Deployments,
StatefulSets, DaemonSets, PVCs and pods in their destination namespaces; root's
prune result. `--secrets-namespace` adds Secret
name/uid/resourceVersion, and `--edge-url`/`--edge-ca` add a verified-TLS probe
of the edge. There is no unverified probe.

**`diff` fails** on a changed or vanished uid, a Deployment or CRD generation
change, any deletionTimestamp, a changed finalizer list, an Application not
Healthy, an automated one not Synced, a manual one that went Synced to
OutOfSync (`argocd` is manual and OutOfSync by design), a failed last
operation, a Secret resourceVersion change, or a wrong edge status. It **warns**
on a syncPolicy change, a restart, other generation changes and a root prune.
It **refuses** snapshots from two clusters or with nothing in them.

**Destruction always fails; other changes fail only on root's objects.** Each
custom resource, workload and pod carries a scope. An Argo tracking-id names its
Application; without one it takes its owner's scope through ownerReferences.
An object is root's when EITHER snapshot says so. A custom resource or workload
that vanishes or gets a deletionTimestamp is a FAIL whoever owns it: a
root-managed operator upgrade that deletes yadgar's MariaDBs, Users and Grants,
or the `envoy-yadgar-edge` proxy, is exactly the damage this exists to catch.
Non-destructive changes to another Application's object (a new uid, a new
generation) and a foreign pod coming or going are WARN. Hook resources are
skipped: Argo recreates them on every sync.

**A merge that means to roll something goes red, by design.** A resource or
image change bumps a Deployment's generation and replaces its pods. The diff
names each change. Red means "read this", and the reviewer decides whether it
was the intent.

`wait --revision` accepts a later commit on `main` as well as the sha itself.
Root resolves `main` once per poll, so two quick merges take it straight past
the first. It needs the git history to see that: `--ancestry-repo`, by default
the current directory.

**Not in this repository's CI, on purpose.** This repository is public, so a
self-hosted runner registered against it would run whatever workflow a
collaborator pushes, with the verifier's cluster-read token. The scheduled
workflow lives in a private repository, `yadgarhq/argocd-verify`, whose runner
registers against that repository alone; it checks out this repository at
`main`'s sha and runs the script above as the `post-merge-verifier`
ServiceAccount, which `applications/post-merge-verifier.yaml` syncs from
`verifier/manifests/`. `MIGRATION_NOTES.md`, "The post-merge verifier", holds
that repository's exact files and the steps to create it. In that workflow the
Secret check and the edge probe do not run: the role cannot read Secrets, and
the edge CA is not in the cluster.
