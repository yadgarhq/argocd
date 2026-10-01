# argocd — the GitOps control plane

Argo CD's own configuration. **Argo manages Argo:** the chart is applied once by
hand to bootstrap, and from then on every change here arrives the same way every
other change does.

The cluster and the infrastructure live in
[`yadgarhq/deploy`](https://github.com/yadgarhq/deploy). Decisions are in
[`yadgarhq/docs`](https://github.com/yadgarhq/docs) — D54 especially.

| Path                           |                                                 |
| ------------------------------ | ----------------------------------------------- |
| `install/values.yaml`          | Helm values for the `argo-cd` chart             |
| `projects/root.yaml`           | app-of-apps root, applied once during bootstrap |
| `applicationsets/modules.yaml` | discovers module repos across the organisation  |
| `applications/<operator>.yaml` | the six operator Applications root adopted (E3) |
| `MIGRATION_NOTES.md`           | what a change here needs from a person          |
| `scripts/verify_handover.py`   | read-only post-merge verifier (below)           |
| `verifier/manifests/`          | its read-only ServiceAccount and ClusterRole    |

`applications/` holds single `Application` resources; `applicationsets/` holds
generators that produce many. Keeping them apart matters because the two are
read differently — one names a thing, the other names a rule.

## What lives where

|                       |                                                             |
| --------------------- | ----------------------------------------------------------- |
| **this repo**         | Argo itself, and how Argo discovers work                    |
| **`yadgarhq/deploy`** | what Argo deploys that is not a module — the infrastructure |
| **module repos**      | each carries its own `chart/`, found by the ApplicationSet  |

Infrastructure `Application` resources live in `deploy` beside the manifests and
values they point at, rather than being centralised here. That is the same
argument D54 makes against an umbrella chart: a single place listing everything
becomes the thing every change has to touch. Argo's own configuration is the one
exception, because it has nowhere else to live.

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
| `applications/argocd.yaml` | Argo managing Argo — what makes `install/values.yaml` actually apply |

## Argo manages Argo

`install/values.yaml` is not read by the bootstrap. `make bootstrap` in
`yadgarhq/deploy` passes only the few `--set` flags needed to _reach_ the
server; the real values arrive through `applicationsets/argocd-self.yaml`, one
sync later.

Without that Application the values file would apply to nothing while looking
authoritative, which is worse than not having it.

Its sync is **not** automated, deliberately. A self-managing Argo that
auto-syncs its own Deployment can restart itself mid-sync and leave the
operation in an unknown state — reviewing the diff and syncing on purpose is the
safer default for the one component that would have to fix itself.

## How modules get deployed

`applicationsets/modules.yaml` uses Argo's **SCM Provider generator** over the
`yadgarhq` organisation. A repo is deployed when it has a `chart/` directory and
carries the **`yadgar-deployable`** topic. Nothing central lists the modules — a
new module is a new repo, and it deploys.

That is D54: an umbrella chart would pin every module's version in one
`Chart.yaml`, so a module release would edit the umbrella and every module would
wait on it. At 69 repos that is the worst instance of the coupling shape D7, D21
and D51 already refuse.

**Opt-in, never opt-out.** A generator that deploys anything appearing in the
organisation is aimed at the next scratch repo.

## Setup it needs

The generator enumerates the organisation through the GitHub API. Unauthenticated
works for public repos but is rate-limited hard, so:

```bash
kubectl -n argocd create secret generic github-scm \
  --from-literal=token=<PAT, read-only repo scope>
```

There is no `argocd` CLI dependency — the server runs in the cluster, and
`kubectl` on its CRDs does the same job.

## Post-merge verification

`scripts/verify_handover.py` proves a merge recreated nothing. It is read-only:
every kubectl call goes through one function that requires `--context` (there
is no default, and the operator's default context is production), allows only
`get`, and reads a Secret only as metadata columns.

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

**A merge that means to roll something goes red, by design.** A resource or
image change bumps a Deployment's generation and replaces its pods. The diff
names each change. Red means "read this", and the reviewer decides whether it
was the intent.

`wait --revision` accepts a later commit on `main` as well as the sha itself.
Root resolves `main` once per poll, so two quick merges take it straight past
the first. It needs the git history to see that: `--ancestry-repo`, by default
the current directory.

**In CI**, `.github/workflows/post-merge-verify.yaml` runs the same sequence on
each push to `main`, as the `post-merge-verifier` ServiceAccount that
`applications/post-merge-verifier.yaml` syncs from `verifier/manifests/`. Its
baseline is the previous run's AFTER snapshot, kept as an artifact, so each diff
covers everything since the last run. It is switched off until a runner exists:
`MIGRATION_NOTES.md`, "The post-merge verifier", says why the existing ARC
runner cannot serve it and what a dedicated one needs. In CI the Secret check
and the edge probe do not run: the role cannot read Secrets, and the edge CA is
not in the cluster.
