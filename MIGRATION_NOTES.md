# Migration notes

Steps this repository needs from a person. Argo manages Argo, so a change here
is not live when it merges — `applications/argocd.yaml` is deliberately not
`automated`. Its first sync ran once, 2026-10-02 06:51Z, by hand under a
throwaway kubeconfig minified to the `kind-yadgar` context with its namespace
set to `argocd` (`argocd app sync argocd --core`; the form in "The sync
timeout is raised to 25200 s" § "Apply" below), but that does not make later
changes land on their own: the Application stays manual, so every change here
still needs an operator to sync it by hand. Anything below that says "after
the sync" is waiting on that step, which belongs to the operator and is argued
in `plans/argocd-adoption-sync.md` in `yadgarhq/docs`.

## The ARC scale-set health rule (ledger 745)

`install/values.yaml` gained
`resource.customizations.health.actions.github.com_AutoscalingRunnerSet` under
`configs.cm`, in #22 (`afc5c08`) — well before `Application/argocd`'s first
sync (above). **It is live, not inert.** MEASURED 2026-10-02: the live
`argocd-cm` already carries the Lua for this key, not empty, and
`Application/estate-front-runner`'s `AutoscalingRunnerSet` resource already
reads `Healthy` with a message — the pass this section's own "Verify" steps
below describe. Both landed with that first sync, since this key predates it.

Landing this rolls `argocd-server`, `argocd-repo-server` and
`argocd-application-controller`, because each carries a `checksum/cm` annotation
over the rendered `argocd-cm`. That is the mechanism the adoption brief measured,
and it applies to **every** `configs.cm` edit forever, not to this one specially.
It does not roll `argocd-redis` or the ApplicationSet controller.

### Verify the rule

`argocd admin settings resource-overrides health` evaluates the rule exactly as
the controller would, against a file. **It does contact the cluster anyway**
— measured 2026-10-02: it starts configmap/secret and cluster-cache informers
against whatever context is current even when every input named below is a
local file, so every invocation here carries `--kube-context kind-yadgar` to
keep it off this host's default (production) context. INFERRED, not measured,
that it applies nothing: the informers it starts are read-only in every run
observed, but no log line proves it writes nothing. Run this anyway — it is
the check that turns "the Lua looks right" into "the Lua returns what I
expect".

```bash
# The rule, as a ConfigMap the CLI can read. Rendering it is what proves the
# chart puts the key where Argo looks for it. Purely local, but pinned anyway:
# `helm template` takes `--kube-context` too.
helm template argocd argo-cd --repo https://argoproj.github.io/argo-helm \
  --version 8.6.1 -n argocd -f install/values.yaml --kube-context kind-yadgar \
  | yq 'select(.kind == "ConfigMap" and .metadata.name == "argocd-cm")' > /tmp/argocd-cm.yaml

# The live scale set, read-only.
kubectl --context kind-yadgar -n estate-front get autoscalingrunnerset estate-front -o yaml > /tmp/ars.yaml

# Takes the two files above, but contacts the cluster too (see above) — pinned.
argocd admin settings resource-overrides health /tmp/ars.yaml \
  --argocd-cm-path /tmp/argocd-cm.yaml --kube-context kind-yadgar
# STATUS: Healthy
# MESSAGE: phase Running: the listener exists and 0 runner(s) are up. Zero is
#          the correct idle state under minRunners: 0.
```

Then edit `/tmp/ars.yaml` — set `status.phase` to `Pending`, or delete the
`status` block entirely — and run it again. It must report **Progressing**, with
a message naming the phase. A rule that reports Healthy for both is a rule that
has not been installed; check the key name, which is one string with dots in the
group and a single underscore before the kind.

`argocd` IS installed on this host now (v3.4.6), and the command above WAS run
this session (2026-10-02): `STATUS: Healthy` / `MESSAGE: phase Running: the
listener exists and 0 runner(s) are up. Zero is the correct idle state under
minRunners: 0.` — matching the expected output above exactly. It cannot
distinguish whether that came from the `--argocd-cm-path` file or from the
live cluster's `argocd-cm`, though: both carry the identical rule (measured
above), and the command contacts the cluster regardless of its file inputs.
Earlier, when this section was first written, `argocd` was not installed, and
what was run instead was the Lua extracted back out of `install/values.yaml`
and evaluated with a stock Lua interpreter against the real live object's JSON
and six mutations of it. That proved the logic and the phase values; it did
not prove Argo loads the key, because only Argo can prove that — which the
command above, now run for real, does.

### Checks after any sync

```bash
# The key reached the live ConfigMap.
kubectl --context kind-yadgar -n argocd get cm argocd-cm \
  -o jsonpath='{.data.resource\.customizations\.health\.actions\.github\.com_AutoscalingRunnerSet}'
# the Lua, not empty

# The scale set now carries a health status. It carried NONE before this.
kubectl --context kind-yadgar -n argocd get application estate-front-runner \
  -o jsonpath='{range .status.resources[?(@.kind=="AutoscalingRunnerSet")]}{.health.status}{" — "}{.health.message}{"\n"}{end}'
# Healthy — phase Running: the listener exists and 0 runner(s) are up. …
```

**`Healthy` with a MESSAGE is the pass, and the message is the whole point.**
Before this change that field was absent, and an absent health status is what
Argo aggregates as green. A `Healthy` with no message means the rule did not
load and nothing changed.

### What this rule does not tell you

`phase: Running` means the `AutoscalingListener` **resource** exists, not that
its pod is working. A listener that exists and crash-loops leaves this rule
reporting Healthy. That case cannot be reached from this object by any Argo
health rule: the listener lives in `arc-systems`, carries no ownerReferences,
and does not appear in the Application's resource list at all. It wants an alert
on the listener pod, beside the ones in `deploy/infra/prometheus.yaml`, and this
change does not build one.

There is deliberately **no rule for `AutoscalingListener`**. Its CRD status
schema is `type: object` with no properties, so the API server prunes everything
the controller writes and `.status` is permanently `{}`. There is nothing for a
rule to read.

### Do not make the `argocd` Application automated

Already refused by `applications/argocd.yaml`'s own comment and by
`bootstrap-automation.md`. With `selfHeal` on, the `checksum/cm` mechanism turns
every edit to this file into an unattended restart of the component that would
have to fix itself.

## E3 — root adopts the six operator Applications (ADR-0824)

**What the merge does.** It adds `applications/cert-manager.yaml`,
`keda.yaml`, `mariadb-operator.yaml`, `mariadb-operator-crds.yaml`,
`envoy-gateway.yaml` and `prometheus.yaml`. Each `spec` is byte-identical to
`yadgarhq/deploy`'s `infra/<name>.yaml` at `fa7ccb5`. The only metadata change
is that E1's `Prune=false` annotation is absent. `root` selects the files through
its `{applications,applicationsets}/*.yaml` include and auto-syncs them.

**How the adoption works.** `root` applies each Application to the live object
with the same name in `argocd`. The apply keeps the uid and writes root's
tracking-id, `root:argoproj.io/Application:argocd/<name>`. The live objects
were last applied client-side by `infra`, and their
`kubectl.kubernetes.io/last-applied-configuration` holds
`argocd.argoproj.io/sync-options: Prune=false` (read 2026-10-01). So root's
three-way merge deletes the annotation, because the new copy does not declare
it.

**ORDERING CONTRACT.** Merge only AFTER `yadgarhq/deploy`'s E2 has merged and
its post-merge checks passed. Then merge promptly. Between the two merges the
six Applications are unowned, and only `Prune=false` protects them. If E3 merged
while `infra` still declared the six, both Applications would claim them and
overwrite each other's tracking-id on every sync.

### Before this merge — read-only

`--context kind-yadgar` on every line. This host's default context is a
production cluster.

```bash
# 1. E2 has synced: each of the six still exists, still carries Prune=false,
#    and its tracking-id still names infra. infra is OutOfSync with exactly
#    these six requiring a prune. Any other state → STOP.
for app in cert-manager keda mariadb-operator mariadb-operator-crds envoy-gateway prometheus; do
  kubectl --context kind-yadgar -n argocd get application "$app" \
    -o jsonpath='{.metadata.name} {.metadata.uid} {.metadata.annotations.argocd\.argoproj\.io/sync-options} {.metadata.annotations.argocd\.argoproj\.io/tracking-id}{"\n"}'
done
kubectl --context kind-yadgar -n argocd get application infra -o json \
  | jq -r '.status.resources[] | select(.requiresPruning == true) | "\(.kind)/\(.name)"' | sort

# 2. root Synced/Healthy, sourcing yadgarhq/argocd main, prune on.
kubectl --context kind-yadgar -n argocd get application root \
  -o jsonpath='{.status.sync.status}/{.status.health.status} {.status.sync.revision} {.spec.source.repoURL}@{.spec.source.targetRevision} prune={.spec.syncPolicy.automated.prune}{"\n"}'
```

Expected uids, recorded at E1 and unchanged since:

| Application             | uid                                    |
| ----------------------- | -------------------------------------- |
| `cert-manager`          | `803a0a98-29a9-4ef0-b556-3a1a3754a7ea` |
| `keda`                  | `1ffb7cc6-cb58-4185-90b0-7eb00d854e89` |
| `mariadb-operator`      | `6e907a55-222c-45b7-8aa4-4ce5a2102bc6` |
| `mariadb-operator-crds` | `47326d8d-6b64-45c7-92c2-05e170a32631` |
| `envoy-gateway`         | `c997c5d8-220b-481f-b23f-e01dbbad549d` |
| `prometheus`            | `97638c3b-9b12-431f-a2f3-4e96f8ae7a7b` |

Read 2026-10-01, before E2: `root` was `Synced`/`Healthy` at `819d5f8`, with
`automated: {prune: true, selfHeal: true}` and two resources,
`Application/argocd` and `ApplicationSet/yadgar-modules`. It does not track the
six yet, so its prune cannot touch them in the window.

### After this merge — read-only

`root` polls git, so allow a few minutes for it to see the merge.

```bash
# 1. root synced the merge: Synced/Healthy at the merge sha, eight resources.
kubectl --context kind-yadgar -n argocd get application root \
  -o jsonpath='{.status.sync.status}/{.status.health.status} {.status.sync.revision}{"\n"}'
kubectl --context kind-yadgar -n argocd get application root -o json \
  | jq -r '.status.resources[] | "\(.kind)/\(.name) \(.status)"'
# expect: Application/argocd, ApplicationSet/yadgar-modules and the six
#         Applications, each Synced.

# 2. Each of the six: SAME uid as the table, tracking-id now
#    root:argoproj.io/Application:argocd/<name>, NO sync-options annotation,
#    and the Application itself Synced/Healthy.
for app in cert-manager keda mariadb-operator mariadb-operator-crds envoy-gateway prometheus; do
  kubectl --context kind-yadgar -n argocd get application "$app" \
    -o jsonpath='{.metadata.name} {.metadata.uid} [{.metadata.annotations.argocd\.argoproj\.io/sync-options}] {.metadata.annotations.argocd\.argoproj\.io/tracking-id} {.status.sync.status}/{.status.health.status}{"\n"}'
done

# 3. infra let go: Synced again, nothing requiring a prune.
kubectl --context kind-yadgar -n argocd get application infra \
  -o jsonpath='{.status.sync.status}/{.status.health.status}{"\n"}'
kubectl --context kind-yadgar -n argocd get application infra -o json \
  | jq -r '.status.resources[] | select(.requiresPruning == true) | "\(.kind)/\(.name)"'
# expect: Synced/Healthy and no line from the second command.

# 4. No SharedResourceWarning on root or infra.
for a in root infra; do
  kubectl --context kind-yadgar -n argocd get application "$a" \
    -o jsonpath='{.metadata.name} {.status.conditions[*].type}{"\n"}'
done

# 5. K4: still 14 Applications in argocd.
kubectl --context kind-yadgar -n argocd get applications --no-headers | wc -l
```

Also re-run reads 4 and 5 of `yadgarhq/deploy`'s `## E2` notes: K6 still 52
CRDs with the same hash, and every operator Deployment's uid and generation
unchanged.

If the `sync-options` annotation survives on a live object, STOP. Do not add
`Prune=false` to the copy here, because a later step must be able to prune the
old Application. Report it instead.

**Rollback — NOT a plain revert.** `root` runs `automated.prune: true`, and the
copies here carry no `Prune=false`. Reverting this merge makes `root` prune the
six live `Application` objects, which loses their uids. Their operators keep
running, because no Application carries a finalizer, but they run unowned. Do
not revert `yadgarhq/deploy`'s E2 after this merge either: `infra` and `root`
would both declare the six. To undo a bad copy, fix it forward here. If the
Applications must leave `root`, first put `Prune=false` on the six copies
in one merge, and delete them in a second merge.

## The post-merge verifier

`scripts/verify_handover.py` replaces the per-merge scratch scripts of the
operators handover. README.md, "Post-merge verification", says what it checks.
This repository holds the script, its tests and its read-only identity. **It
holds no workflow and no runner, deliberately**: the scheduled run lives in a
private repository, `yadgarhq/argocd-verify`, whose files are below.

### What merging this does on its own

`root` creates `Application/post-merge-verifier`, which syncs
`verifier/manifests/`: Namespace `post-merge-verifier`, ServiceAccount
`post-merge-verifier`, and ClusterRole and ClusterRoleBinding
`post-merge-verifier`. The role is get/list/watch, names every group and
resource, and has no `secrets`. It does read ConfigMap data. Nothing runs as
the ServiceAccount until the steps below are done. No pod rolls.

```bash
# After the merge, read-only:
kubectl --context kind-yadgar -n argocd get application post-merge-verifier \
  -o jsonpath='{.status.sync.status}/{.status.health.status}{"\n"}'
kubectl --context kind-yadgar auth can-i list secrets \
  --as=system:serviceaccount:post-merge-verifier:post-merge-verifier -A   # expect: no
kubectl --context kind-yadgar auth can-i list deployments.apps \
  --as=system:serviceaccount:post-merge-verifier:post-merge-verifier -A   # expect: yes
```

### Why not the existing runner, and why not this repository

Read on kind-yadgar, 2026-10-01, with `get` only:

- The only scale set, `estate-front/estate-front`, registers against
  `https://github.com/yadgarhq/estate`. A workflow elsewhere cannot target it.
- Its namespace's `estate-front-egress` policy excludes `10.96.0.0/16` and
  `10.89.4.0/24`, so it denies the API server at `10.96.0.1:443` and
  `10.89.4.2:6443`. That is the declared intent only (see "Egress" below).
- Its pods hold the `estate` environment's secrets. Binding cluster read there
  widens the most sensitive pod in the estate.

**A runner registered against `yadgarhq/argocd` is not safe either.** This
repository is public, on the free plan. Any workflow can name a self-hosted
label, and a collaborator's branch push runs it with no approval; a
repository-level runner cannot be limited to one workflow, and a test that
greps `runs-on:` guards nothing. The only real guards would be the fork
approval policy (`all_external_contributors`, which must stay at least that
strict) and trust in every collaborator. So the runner registers against a
private repository that runs this verifier and nothing else, and only its
collaborators can push a workflow to it. Disable forking on it.

### The trigger: polling, not a dispatch

`yadgarhq/argocd`'s `GITHUB_TOKEN` cannot dispatch a workflow in another
repository. A dispatch would need a GitHub App key stored in this public
repository's secrets, with write on the private one, reachable from every
workflow here. Polling needs no credential outside the private repository:
its own `GITHUB_TOKEN` reads this public repository's `main`. The workflow
runs every 10 minutes, compares `main`'s sha with the sha stored beside the
last snapshot, and does nothing more when it has not moved. Costs: up to 10
minutes plus GitHub's schedule delay of latency, one runner pod per poll (the
self-hosted runner bills no minutes), and several merges between two polls are
verified together, which `wait --revision` accepts because it takes a later
commit on `main`.

### Steps, in order (each needs a person)

1. **Max creates the private repository and the GitHub App.**
   - `yadgarhq/argocd-verify`, private, forking disabled.
   - A GitHub App installed on that repository only. Repository permissions,
     per ARC's "Authenticating ARC to the GitHub API"
     (<https://docs.github.com/en/actions/tutorials/use-actions-runner-controller/authenticate-to-the-api>,
     read 2026-10-01): **Administration: Read and write** ("only required when
     configuring Actions Runner Controller to register at the repository
     scope") and **Metadata: Read-only**. No organization permissions.
   - **Administration read and write is high-value**: it can change the
     repository's settings and delete it. Its key lives in the cluster Secret
     below, so anyone who can read Secrets in `post-merge-verifier` holds it.
     Installing it on the one private repository bounds that to that
     repository.

2. **The Secret**, created by hand:

   ```bash
   kubectl --context kind-yadgar -n post-merge-verifier create secret generic argocd-verify-github \
     --from-literal=github_app_id=<APP_ID> \
     --from-literal=github_app_installation_id=<INSTALLATION_ID> \
     --from-file=github_app_private_key=<KEY_FILE>
   ```

3. **A reviewed PR in this repository** adding the runner Application and the
   role addition, together. `root` then also tracks the AutoscalingRunnerSet,
   so the verifier lists it, and the role must grant that in the SAME PR or
   the next snapshot fails as Forbidden.

   `applications/post-merge-verifier-runner.yaml` (pin the runner image by
   digest, read off the registry when writing the file):

   ```yaml
   apiVersion: argoproj.io/v1alpha1
   kind: Application
   metadata:
     name: post-merge-verifier-runner
     namespace: argocd
   spec:
     project: default
     source:
       repoURL: ghcr.io/actions/actions-runner-controller-charts
       chart: gha-runner-scale-set
       targetRevision: 0.14.2
       helm:
         valuesObject:
           githubConfigUrl: https://github.com/yadgarhq/argocd-verify
           githubConfigSecret: argocd-verify-github
           runnerScaleSetName: argocd-verify # = runs-on and actionlint.yaml
           controllerServiceAccount: # see deploy's estate-front-runner for why
             namespace: arc-systems
             name: arc-gha-rs-controller
           minRunners: 0
           maxRunners: 1
           template:
             spec:
               # The read-only identity; the chart then creates no
               # no-permission ServiceAccount of its own.
               serviceAccountName: post-merge-verifier
               containers:
                 - name: runner
                   image: ghcr.io/actions/actions-runner@sha256:<DIGEST>
                   command: ["/home/runner/run.sh"]
     destination:
       server: https://kubernetes.default.svc
       namespace: post-merge-verifier
     syncPolicy:
       automated:
         selfHeal: true
       retry:
         limit: 6
         backoff: { duration: 15s, factor: 2, maxDuration: 5m }
   ```

   Add to `verifier/manifests/clusterrole.yaml`, and to `REQUIRED` in
   `scripts/tests/test_verify_handover.py`:

   ```yaml
   # All four ARC plurals. The verifier lists the AutoscalingRunnerSet root
   # tracks; it never lists EphemeralRunner, EphemeralRunnerSet or
   # AutoscalingListener instances (UNLISTED_INSTANCE_CRDS: they churn on
   # every poll), but naming them keeps a later tracked one from failing as
   # Forbidden.
   - apiGroups: [actions.github.com]
     resources:
       - autoscalingrunnersets
       - ephemeralrunnersets
       - ephemeralrunners
       - autoscalinglisteners
     verbs: [get, list, watch]
   ```

   **git in the runner image.** `wait --require-ancestry` refuses to start
   without git. The upstream `actions/runner` `images/Dockerfile` on `main`
   installs git from the git-core PPA (read 2026-10-01); confirm it for the
   digest you pin with `docker run --rm <image> git --version`.

4. **Create the private repository's files** (below), then set its variable
   `VERIFY_ENABLED` to `true` once the scale set's listener is up:

   ```bash
   gh-personal variable set VERIFY_ENABLED --body true --repo yadgarhq/argocd-verify
   ```

   The first run has no baseline. Its summary says "BASELINE ONLY", and it
   compares nothing. The next move of `main` is the first real check.

   **A red run stays red.** Its snapshot is not uploaded, so the next poll
   compares against the last passing snapshot again and fails again, every
   10 minutes, until the cause is fixed. When the change was intended (a
   roll), accept it as the new baseline by hand:

   ```bash
   gh-personal workflow run verify.yaml --repo yadgarhq/argocd-verify -f accept=true
   ```

### Egress: not a control yet

An egress NetworkPolicy for `post-merge-verifier` is worth declaring (DNS to
kube-dns; `10.96.0.1/32:443` and `10.89.4.2/32:6443` for the API server, the
latter kind's node IP, which moves if the cluster is recreated; `443` to
`0.0.0.0/0` except the cluster ranges for GitHub, `dl.k8s.io` and the Python
download). **Do not count it as a control.** The CNI is kindnet, image
`docker.io/kindest/kindnetd:v20260528-9350166c` (read 2026-10-01), and
whether it enforces NetworkPolicy has not been measured here;
`yadgarhq/estate`'s `.github/actionlint.yaml` says it enforces none. If the
policy is added, the ClusterRole also needs `networking.k8s.io:
[networkpolicies]`, for the reason step 3 gives.

### What is visible to whom

The snapshot artifact, the run log and the step summary carry object names,
namespaces and uids of kind-yadgar, and the names (never the data) of the
Secrets root's Applications track. In the private repository they are visible
to its collaborators only. That is why they stay unredacted.

### The private repository's files

`.github/workflows/verify.yaml`:

````yaml
name: verify-argocd

# Polls yadgarhq/argocd's `main` and, when it moved since the last verified
# sha, runs that sha's `scripts/verify_handover.py` against kind-yadgar as the
# read-only `post-merge-verifier` ServiceAccount.
#
# POLLING, NOT A DISPATCH FROM ARGOCD. argocd's GITHUB_TOKEN cannot dispatch to
# another repository, and the alternative (an App key in argocd's secrets) puts
# a credential with write on this repository into a PUBLIC repository's
# workflows. Polling needs no credential outside this repository: reading a
# public branch works with this repository's own GITHUB_TOKEN. The cost is
# latency (up to the cron period plus GitHub's schedule delay) and a runner pod
# per poll; when several merges land between two polls, one run covers them.
#
# THE BASELINE IS THE SNAPSHOT OF THE LAST SUCCESSFUL RUN of THIS workflow in
# THIS repository: same repository and head repository, the default branch, a
# `schedule` or `workflow_dispatch` event, this file's path, and
# `conclusion == success`. A snapshot is uploaded only after its diff passed,
# so a red run never becomes the next baseline: it stays red on every poll
# until the cause is fixed, or until a person accepts the new state with
# `workflow_dispatch` and `accept: true` (an intended roll, say).
#
# NOT CHECKED HERE: the edge probe (`--edge-url`; the edge CA is not in the
# cluster) and Secret metadata (`--secrets-namespace`; the role cannot read
# Secrets). Both stay operator-run checks with the operator's kubeconfig.

on:
  schedule:
    - cron: "*/10 * * * *"
  workflow_dispatch:
    inputs:
      accept:
        description: "Record the current state as the new baseline even if the diff fails (an intended change)"
        type: boolean
        default: false

permissions: {}

concurrency:
  group: verify-argocd
  cancel-in-progress: false

jobs:
  verify:
    name: verify argocd main
    # Inert until the scale set exists (MIGRATION_NOTES step 4).
    if: vars.VERIFY_ENABLED == 'true'
    runs-on: argocd-verify
    timeout-minutes: 45
    permissions:
      contents: read
      actions: read # list and download this workflow's previous snapshot
    env:
      CONTEXT: in-cluster-verifier
      WORKFLOW_PATH: .github/workflows/verify.yaml
    steps:
      - name: read argocd main and find the baseline
        id: state
        uses: actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3 # v9.0.0
        with:
          script: |
            const { owner, repo } = context.repo;
            const self = (await github.rest.repos.get({ owner, repo })).data;
            const branch = await github.rest.repos.getBranch({ owner: "yadgarhq", repo: "argocd", branch: "main" });
            const sha = branch.data.commit.sha;
            if (!/^[0-9a-f]{40}$/.test(sha)) { core.setFailed(`unexpected sha ${sha}`); return; }
            core.setOutput("sha", sha);
            const artifacts = await github.paginate(github.rest.actions.listArtifactsForRepo, {
              owner, repo, name: "argocd-snapshot", per_page: 100,
            });
            const candidates = artifacts
              .filter((a) => !a.expired && a.workflow_run
                && a.workflow_run.id !== context.runId
                && a.workflow_run.repository_id === self.id
                && a.workflow_run.head_repository_id === self.id
                && a.workflow_run.head_branch === self.default_branch)
              .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
            for (const a of candidates) {
              const run = (await github.rest.actions.getWorkflowRun({ owner, repo, run_id: a.workflow_run.id })).data;
              if (run.path === process.env.WORKFLOW_PATH
                && run.conclusion === "success"
                && ["schedule", "workflow_dispatch"].includes(run.event)
                && run.head_repository && run.head_repository.id === self.id) {
                core.info(`baseline: artifact ${a.id} from run ${run.id} (${run.event}), ${a.created_at}`);
                core.setOutput("found", "true");
                core.setOutput("artifact_id", String(a.id));
                core.setOutput("run_id", String(run.id));
                return;
              }
            }
            core.warning("no snapshot from a successful run of this workflow: this run records a baseline only");
            core.setOutput("found", "false");

      - name: download the baseline
        if: steps.state.outputs.found == 'true'
        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          artifact-ids: ${{ steps.state.outputs.artifact_id }}
          run-id: ${{ steps.state.outputs.run_id }}
          github-token: ${{ github.token }}
          path: ${{ runner.temp }}/before

      - name: skip when the last passing snapshot is already at main
        id: decide
        env:
          SHA: ${{ steps.state.outputs.sha }}
          FOUND: ${{ steps.state.outputs.found }}
          ACCEPT: ${{ inputs.accept }}
        run: |
          set -euo pipefail
          if [ "$ACCEPT" != "true" ] && [ "$FOUND" = "true" ] && [ "$(cat "$RUNNER_TEMP/before/argocd-sha" 2>/dev/null)" = "$SHA" ]; then
            echo "argocd main $SHA already has a passing snapshot; nothing new to verify" >> "$GITHUB_STEP_SUMMARY"
            echo "skip=true" >> "$GITHUB_OUTPUT"
          else
            echo "skip=false" >> "$GITHUB_OUTPUT"
          fi

      - name: check out argocd at that sha, read-only
        if: steps.decide.outputs.skip != 'true'
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          repository: yadgarhq/argocd
          ref: ${{ steps.state.outputs.sha }}
          path: argocd
          persist-credentials: false
          # Full history: `wait --revision` accepts a later commit on main only
          # when `git merge-base --is-ancestor` can see both.
          fetch-depth: 0

      - if: steps.decide.outputs.skip != 'true'
        uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.x"

      - name: install kubectl
        if: steps.decide.outputs.skip != 'true'
        env:
          KUBECTL_VERSION: v1.36.1
          KUBECTL_SHA256: 629d3f410e09bf49b64ae7079f7f0bda1191efed311f7d37fdbab0ad5b0ec2b7
        run: |
          set -euo pipefail
          mkdir -p "$RUNNER_TEMP/bin"
          curl -sSfL -o "$RUNNER_TEMP/bin/kubectl" "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
          echo "${KUBECTL_SHA256}  $RUNNER_TEMP/bin/kubectl" | sha256sum -c -
          chmod +x "$RUNNER_TEMP/bin/kubectl"
          echo "$RUNNER_TEMP/bin" >> "$GITHUB_PATH"

      # A named context over the pod's own ServiceAccount token, and NO
      # current-context, so a kubectl call without --context fails here too.
      - name: write kubeconfig
        if: steps.decide.outputs.skip != 'true'
        run: |
          set -euo pipefail
          sa=/var/run/secrets/kubernetes.io/serviceaccount
          test -r "$sa/token" || { echo "::error::no ServiceAccount token; is the runner pod's serviceAccountName post-merge-verifier?"; exit 1; }
          KUBECONFIG="$RUNNER_TEMP/verifier.kubeconfig"
          echo "KUBECONFIG=$KUBECONFIG" >> "$GITHUB_ENV"
          cat > "$KUBECONFIG" <<EOF
          apiVersion: v1
          kind: Config
          clusters:
            - name: in-cluster
              cluster:
                server: https://kubernetes.default.svc
                certificate-authority: $sa/ca.crt
          users:
            - name: post-merge-verifier
              user:
                tokenFile: $sa/token
          contexts:
            - name: $CONTEXT
              context:
                cluster: in-cluster
                user: post-merge-verifier
          EOF

      - name: wait for root at that sha, then for every Application to settle
        if: steps.decide.outputs.skip != 'true'
        env:
          SHA: ${{ steps.state.outputs.sha }}
        run: |
          set -euo pipefail
          python3 argocd/scripts/verify_handover.py wait --context "$CONTEXT" --app root --revision "$SHA" \
            --ancestry-repo argocd --require-ancestry --timeout 900
          since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
          python3 argocd/scripts/verify_handover.py wait --context "$CONTEXT" --settled --since "$since" --timeout 900

      - name: snapshot
        if: steps.decide.outputs.skip != 'true'
        env:
          SHA: ${{ steps.state.outputs.sha }}
        run: |
          set -euo pipefail
          mkdir -p "$RUNNER_TEMP/after"
          python3 argocd/scripts/verify_handover.py snapshot --context "$CONTEXT" --out "$RUNNER_TEMP/after/snapshot.json"
          echo "$SHA" > "$RUNNER_TEMP/after/argocd-sha"

      - name: diff against the baseline
        if: steps.decide.outputs.skip != 'true'
        env:
          FOUND: ${{ steps.state.outputs.found }}
          ACCEPT: ${{ inputs.accept }}
        run: |
          set -euo pipefail
          if [ "$FOUND" != "true" ]; then
            echo "## verify-argocd: BASELINE ONLY, nothing compared" >> "$GITHUB_STEP_SUMMARY"
            echo "::warning::baseline only: nothing was compared"
            exit 0
          fi
          set +e
          python3 argocd/scripts/verify_handover.py diff "$RUNNER_TEMP/before/snapshot.json" "$RUNNER_TEMP/after/snapshot.json" > "$RUNNER_TEMP/diff.txt"
          status=$?
          set -e
          cat "$RUNNER_TEMP/diff.txt"
          {
            echo "## verify-argocd: exit $status"
            echo
            echo '```'
            cat "$RUNNER_TEMP/diff.txt"
            echo '```'
          } >> "$GITHUB_STEP_SUMMARY"
          if [ "$status" -ne 0 ] && [ "$ACCEPT" = "true" ]; then
            echo "## ACCEPTED by $GITHUB_ACTOR as the new baseline despite exit $status" >> "$GITHUB_STEP_SUMMARY"
            echo "::warning::diff exit $status accepted as the new baseline"
            exit 0
          fi
          exit "$status"
      - name: keep the snapshot as the next run's baseline
        # Only after the diff passed (or was accepted): a red snapshot must
        # never become the next baseline.
        if: success() && steps.decide.outputs.skip != 'true'
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: argocd-snapshot
          path: |
            ${{ runner.temp }}/after/snapshot.json
            ${{ runner.temp }}/after/argocd-sha
          if-no-files-found: error
          retention-days: 90
````

`.github/actionlint.yaml`:

```yaml
# `argocd-verify` is the runnerScaleSetName of yadgarhq/argocd's
# applications/post-merge-verifier-runner.yaml. The two must agree.
self-hosted-runner:
  labels:
    - argocd-verify
```

`README.md`:

```markdown
# argocd-verify

Runs `yadgarhq/argocd`'s `scripts/verify_handover.py` against kind-yadgar
after `main` moves, on a self-hosted ARC runner registered against this
repository only.

This repository is private ON PURPOSE. Its runner's pod holds a cluster-read
token (ServiceAccount `post-merge-verifier`, no Secrets). Only this
repository's collaborators can push a workflow that runs there. Keep it
private, keep forking off, and keep `.github/workflows/` to `verify.yaml`.

- Trigger: every 10 minutes; nothing happens unless argocd's `main` moved.
- Baseline: this workflow's previous `argocd-snapshot` artifact.
- Switch: repository variable `VERIFY_ENABLED=true`.
- Setup and the reasons: `yadgarhq/argocd`'s MIGRATION_NOTES.md, "The
  post-merge verifier".
```

### Rollback

Revert the merge. `root` prunes `Application/post-merge-verifier`, which has no
finalizer, so the Namespace, ServiceAccount, ClusterRole and ClusterRoleBinding
stay behind, unowned. Delete them by hand if they must go:

```bash
kubectl --context kind-yadgar delete clusterrolebinding post-merge-verifier
kubectl --context kind-yadgar delete clusterrole post-merge-verifier
kubectl --context kind-yadgar delete namespace post-merge-verifier
```

## M3 — root adopts `infra`'s five children (retiring `infra`, option A, ADR-0828)

**What the merge does.** It adds `applications/arc.yaml`, `estate-front.yaml`,
`estate-front-runner.yaml`, `tls.yaml` and `yadgar.yaml`, and
`manifests/tls/` and `manifests/estate-front/`, byte copies of
`yadgarhq/deploy`'s `infra/tls/` and `infra/estate-front/` at `05b160b`. Root
selects the five Applications through its `{applications,applicationsets}/*.yaml`
include. `manifests/` is outside that include on purpose: Argo's include glob
lets `*` cross `/`, so a file under `applications/` would be applied by root
itself as well as by its Application.

Each spec differs from what runs today ONLY by:

- S0's `syncPolicy` on all five: `automated.prune` removed, and
  `retry: {limit: 6, backoff: {duration: 15s, factor: 2, maxDuration: 5m}}`.
  On `tls` this replaces deploy's 60-attempt budget (see `applications/tls.yaml`).
- `tls` and `estate-front`: `source` is this repository's `manifests/<name>`.
- `yadgar`: two `sources` (the OCI chart, plus `ref: self` on deploy for
  `$self/infra/yadgar/values.yaml`) became one `source`, the same chart at the
  same `0.3.13`, with that values file inlined as `valuesObject`.
- `arc` and `estate-front-runner`: the `helm.values` string became
  `valuesObject`, parsed-equal.

The render gate, run from the committed files with helm 3.18.4 and the
cluster's api-versions, found 0 differing objects for all five (arc 10,
estate-front 2, estate-front-runner 4, tls 5, yadgar 88). So each child stays
Synced: no child operation runs, and no hook runs. The ONE operation is root's,
which applies five Application objects. The metadata loses the `Prune=false`
annotation M1 put on the live objects, by the same three-way merge E3 relied on.

**ORDERING CONTRACT.** Merge only AFTER `yadgarhq/deploy`'s M2 has merged and
its post-merge checks passed: `infra` OutOfSync with exactly these five
requiring a prune, each still carrying `Prune=false` and an `infra:`
tracking-id. Then merge promptly: until this merge the five are unowned.

### Before this merge — read-only

`--context kind-yadgar` on every line. This host's default context is a
production cluster.

```bash
# 1. M2 has synced. Any other state → STOP.
for app in arc estate-front estate-front-runner tls yadgar; do
  kubectl --context kind-yadgar -n argocd get application "$app" \
    -o jsonpath='{.metadata.name} {.metadata.uid} [{.metadata.annotations.argocd\.argoproj\.io/sync-options}] {.metadata.annotations.argocd\.argoproj\.io/tracking-id} {.status.sync.status}/{.status.health.status} {.status.operationState.startedAt}{"\n"}'
done
kubectl --context kind-yadgar -n argocd get application infra -o json \
  | jq -r '.status.resources[] | select(.requiresPruning == true) | "\(.kind)/\(.name)"' | sort

# 2. The snapshot this merge is checked against. Keep the output.
kubectl --context kind-yadgar get crd -o jsonpath='{range .items[*]}{.metadata.name}={.metadata.uid}{"\n"}{end}' \
  | sort | sha256sum | cut -c1-16                      # K6: a8026323ea9d31c2
kubectl --context kind-yadgar -n argocd get applications --no-headers | wc -l   # K4: 14
for ns in yadgar arc-systems; do
  kubectl --context kind-yadgar -n "$ns" get deploy,statefulset \
    -o jsonpath='{range .items[*]}{.kind}/{.metadata.name} {.metadata.uid} gen={.metadata.generation}{"\n"}{end}'
done
kubectl --context kind-yadgar -n yadgar get secret valkey-password nats-auth nats-auth-gateway admin-bootstrap-token iam-keys \
  -o jsonpath='{range .items[*]}{.metadata.name} {.metadata.creationTimestamp} {.metadata.resourceVersion}{"\n"}{end}'
```

Expected uids, read 2026-10-01 after M1:

| Application           | uid                                    |
| --------------------- | -------------------------------------- |
| `arc`                 | `ff9e2c3a-52ec-41ed-a32f-8138f29f86c3` |
| `estate-front`        | `521f859e-f888-4c4f-95dc-48728acf11ab` |
| `estate-front-runner` | `4414811c-f7c9-47b3-b86e-b47a287cfdba` |
| `tls`                 | `89f9542e-34ae-4d01-9a76-41c7dd58bcf0` |
| `yadgar`              | `6181bd1c-3d73-4316-a864-b4e188dc6460` |

Each child's last operation started at: `arc` 2026-09-05T12:40:28Z,
`estate-front` 2026-09-05T12:40:18Z, `estate-front-runner` 2026-09-06T09:37:45Z,
`tls` 2026-09-27T16:51:07Z, `yadgar` 2026-10-01T08:09:19Z.

### After this merge — read-only

`root` polls git, so allow a few minutes for it to see the merge.

```bash
# 1. Each of the five: SAME uid as the table, tracking-id
#    root:argoproj.io/Application:argocd/<name>, NO sync-options annotation,
#    Synced/Healthy, and operationState.startedAt UNCHANGED (no child operation).
for app in arc estate-front estate-front-runner tls yadgar; do
  kubectl --context kind-yadgar -n argocd get application "$app" \
    -o jsonpath='{.metadata.name} {.metadata.uid} [{.metadata.annotations.argocd\.argoproj\.io/sync-options}] {.metadata.annotations.argocd\.argoproj\.io/tracking-id} {.status.sync.status}/{.status.health.status} {.status.operationState.startedAt}{"\n"}'
done

# 2. THE OPEN QUESTION: root's client-side apply must REMOVE `spec.sources`
#    from yadgar now that the file sets `spec.source`. last-applied holds
#    `sources`, so the three-way merge should delete it. If `sources` survived,
#    Argo would keep reading it (multi-source wins) and deploy's later deletion
#    of infra/yadgar/values.yaml would break yadgar. Expect: no `sources`, and
#    `source.chart` = yadgar.
kubectl --context kind-yadgar -n argocd get application yadgar -o json \
  | jq '{sources: .spec.sources, chart: .spec.source.chart, rev: .spec.source.targetRevision}'
for app in tls estate-front; do
  kubectl --context kind-yadgar -n argocd get application "$app" \
    -o jsonpath='{.metadata.name} {.spec.source.repoURL} {.spec.source.path}{"\n"}'
done

# 3. Workloads untouched: re-run the snapshot of "Before" step 2. Every uid
#    and generation, K6 a8026323ea9d31c2, K4 14, and each Secret's
#    creationTimestamp and resourceVersion unchanged.

# 4. infra let go: it lists only itself.
kubectl --context kind-yadgar -n argocd get application infra -o json \
  | jq -r '.status.sync.status, (.status.resources[] | "\(.kind)/\(.name)")'
# expect: Synced, then Application/infra alone.

# 5. The edge still serves. Expect HTTP 405 and ssl_verify_result=0.
curl -sS -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' \
  --cacert <(kubectl --context kind-yadgar -n cert-manager get secret yadgar-dev-ca \
    -o jsonpath='{.data.tls\.crt}' | base64 -d) \
  --resolve gateway.yadgar.internal:18443:127.0.0.1 https://gateway.yadgar.internal:18443/
```

K4 IS 14 HERE, NOT 13: argocd#52 added `post-merge-verifier`. M4, which
deletes `infra` by hand, takes it to 13.

THE POST-MERGE VERIFIER (#52) DOES NOT GATE THIS MERGE. It sees the five as
newly added Applications (INFO) and does not compare them across the merge, so
the manual snapshot above ("Before" step 2, re-read in "After" step 3) is M3's
gate.

If `yadgar.spec.sources` survives (step 2), STOP before deploy deletes
`infra/yadgar/values.yaml`, and report it.

**Rollback — NOT a plain revert.** `root` runs `automated.prune: true` and the
copies here carry no `Prune=false`, so reverting this merge makes `root` prune
the five live Application objects, `yadgar` among them. None carries a
finalizer, so the workloads would keep running, unowned. Fix forward here. If
an Application must leave `root`, first put `Prune=false` on its copy in one
merge, and delete it in a second.

## The sync timeout (ledger 1208)

**HISTORICAL, SUPERSEDED.** This section records the hand `kubectl patch` plus
controller restart that landed `"24000"`, run while `Application/argocd` was
still unsynced. The Application has since been adopted: its first sync ran
2026-10-02, right after argocd#56 (`f4da219`) merged (see "The sync timeout is
raised to 25200 s" below). The "Do not sync it" guidance a few lines down no
longer holds — syncing the Application is now the normal way to land a change
here, not an exception to avoid.

`install/values.yaml` gained `controller.sync.timeout.seconds: "24000"` under
`configs.params`. **Apply this only after M3 has moved `Application/tls` here
with its limit-6 retry.** The tls Application on the cluster before M3 retries 60
times, about ten hours. M3 has since moved it here with the limit-6 shape.

**Merging changes nothing live.** `Application/argocd` is manual and has never
synced. **Do not sync it to land this.** That sync applies every other drift in
the Application as well. Land the one key with a targeted
patch and a controller restart instead. Every command names
`--context kind-yadgar`. The default context is not this cluster.

The controller reads the key at start only. The StatefulSet maps it to the env
var `ARGOCD_APPLICATION_CONTROLLER_SYNC_TIMEOUT` with a `configMapKeyRef` to
`argocd-cmd-params-cm` (measured 2026-10-01). So a patch without a restart does
nothing.

### Before — read-only

```bash
# 1. The live value. Expect: 0
kubectl --context kind-yadgar -n argocd get configmap argocd-cmd-params-cm \
  -o jsonpath='{.data.controller\.sync\.timeout\.seconds}'; echo

# 2. The controller reads the key from the ConfigMap. Expect one entry, with
#    configMapKeyRef name argocd-cmd-params-cm, key controller.sync.timeout.seconds.
kubectl --context kind-yadgar -n argocd get statefulset argocd-application-controller -o json \
  | jq '.spec.template.spec.containers[].env[]? | select(.name=="ARGOCD_APPLICATION_CONTROLLER_SYNC_TIMEOUT")'

# 3. No operation in flight. Expect: no rows. The restart resumes an operation
#    in flight, and the new timeout terminates any operation older than 6.7 h at once.
#    Any row → STOP.
kubectl --context kind-yadgar get applications -A -o json \
  | jq -r '.items[] | select(.status.operationState.phase == "Running" or .status.operationState.phase == "Terminating")
           | [.metadata.name, .status.operationState.phase, .status.operationState.startedAt] | @tsv'

# 4. M3 is live: tls retries with the limit-6 shape. Expect: 6. Any other value → STOP.
kubectl --context kind-yadgar -n argocd get application tls \
  -o jsonpath='{.spec.syncPolicy.retry.limit}'; echo

# 5. The pod as it is now, to compare after the restart.
kubectl --context kind-yadgar -n argocd get pod argocd-application-controller-0 \
  -o jsonpath='{.metadata.uid} {.status.startTime}{"\n"}'
```

### Apply

```bash
# (a) The one key.
kubectl --context kind-yadgar -n argocd patch configmap argocd-cmd-params-cm \
  --type merge -p '{"data":{"controller.sync.timeout.seconds":"24000"}}'

# (b) Restart the controller: delete its one pod, and the StatefulSet recreates it.
#     Use this, not `kubectl rollout restart`. A rollout restart writes a
#     `kubectl.kubernetes.io/restartedAt` annotation into the pod template, which is
#     one more field git does not hold.
kubectl --context kind-yadgar -n argocd delete pod argocd-application-controller-0
# The StatefulSet recreates the pod a moment later, and `kubectl wait` fails on a
# pod that does not exist yet. Wait for it to exist first.
# Bounded: 150 tries, 2 s apart. No `exit`, so a pasted block does not close the
# shell; a pod still missing prints STOP, and then do not continue.
for _ in $(seq 1 150); do
  kubectl --context kind-yadgar -n argocd get pod argocd-application-controller-0 >/dev/null 2>&1 && break
  sleep 2
done
if kubectl --context kind-yadgar -n argocd get pod argocd-application-controller-0 >/dev/null 2>&1; then
  kubectl --context kind-yadgar -n argocd wait --for=condition=Ready \
    pod/argocd-application-controller-0 --timeout=300s
else
  echo "argocd-application-controller-0 not recreated after 300 s: STOP" >&2
fi
```

### After — verify

```bash
# 1. The ConfigMap holds the value. Expect: 24000
kubectl --context kind-yadgar -n argocd get configmap argocd-cmd-params-cm \
  -o jsonpath='{.data.controller\.sync\.timeout\.seconds}'; echo

# 2. A new pod: the uid and startTime differ from "Before" step 5.
kubectl --context kind-yadgar -n argocd get pod argocd-application-controller-0 \
  -o jsonpath='{.metadata.uid} {.status.startTime}{"\n"}'

# 3. The process has the value. Expect: 24000. This is an exec, but it only reads.
kubectl --context kind-yadgar -n argocd exec argocd-application-controller-0 \
  -c application-controller -- printenv ARGOCD_APPLICATION_CONTROLLER_SYNC_TIMEOUT

# 4. Every Application is as it was before the restart: no new Running operation.
kubectl --context kind-yadgar get applications -A -o json \
  | jq -r '.items[] | [.metadata.name, .status.sync.status, .status.health.status, (.status.operationState.phase // "-")] | @tsv'
```

The controller does not log the value at start. Do NOT use the text "due to
application controller sync timeout" in an operation message as evidence. v3.1.8
adds that text to every retry, whatever the cause
(`controller/appcontroller.go:1486`). The only log line that shows this timeout
fired is `Terminating in-progress operation due to timeout`.

### Live against git after the patch

The `argocd-cmd-params-cm` that argo-helm 8.6.1 renders from `install/values.yaml`
was identical to live in all 39 keys before this change (measured 2026-10-01).
After the patch, the two are identical again, the new key included. So this
ConfigMap leaves the Application's drift list.

The patch adds one difference elsewhere. The rendered StatefulSet's
`checksum/cmd-params` annotation is a hash of the new ConfigMap. The live annotation
is still the hash of the old one. That StatefulSet is already OutOfSync, because its
`checksum/cm` also differs. When the adoption sync runs, it writes both annotations
and restarts the controller once more. That restart is harmless.

### Rollback

Repeat the Apply (b) sequence with the old value, then verify the same way.

```bash
kubectl --context kind-yadgar -n argocd patch configmap argocd-cmd-params-cm \
  --type merge -p '{"data":{"controller.sync.timeout.seconds":"0"}}'
kubectl --context kind-yadgar -n argocd delete pod argocd-application-controller-0
# Bounded: 150 tries, 2 s apart. No `exit`, so a pasted block does not close the
# shell; a pod still missing prints STOP, and then do not continue.
for _ in $(seq 1 150); do
  kubectl --context kind-yadgar -n argocd get pod argocd-application-controller-0 >/dev/null 2>&1 && break
  sleep 2
done
if kubectl --context kind-yadgar -n argocd get pod argocd-application-controller-0 >/dev/null 2>&1; then
  kubectl --context kind-yadgar -n argocd wait --for=condition=Ready \
    pod/argocd-application-controller-0 --timeout=300s
else
  echo "argocd-application-controller-0 not recreated after 300 s: STOP" >&2
fi
# Expect: 0
kubectl --context kind-yadgar -n argocd exec argocd-application-controller-0 \
  -c application-controller -- printenv ARGOCD_APPLICATION_CONTROLLER_SYNC_TIMEOUT
```

Then revert the commit here, so that git does not hold a value the cluster does not run.

## The sync timeout is raised to 25200 s (ledger 1224)

`install/values.yaml` raises `controller.sync.timeout.seconds` from `24000` to
`25200` under `configs.params`. `yadgarhq/platform#24` is open, not merged, and
adds `activeDeadlineSeconds` to the four estate hook Jobs; its arithmetic
raises this repo's own floor to 24255 s
(`scripts/tests/test_install_values.py`). ADR-0830 holds ONE value across this
org and `yadgarhq/chart`'s kind installs, whose own floor is 24755 s, so 25200 s
clears both. The value is raised now, ahead of platform#24 merging, because
platform#24's release lands as a pin straight to `yadgarhq/chart` main with no
PR CI (`parent_bump.py`).

**`Application/argocd` is still not `automated`.** `"24000"` reached the live
`argocd-cmd-params-cm` on 2026-10-01 by the hand `kubectl patch` plus
controller restart above (ledger 1208) — the Application was not yet adopted
then. Its FIRST sync ran 2026-10-02 06:51:46Z, right after argocd#56
(`f4da219`) merged: by hand, `argocd app sync argocd --core` under a throwaway
kubeconfig minified to the `kind-yadgar` context with its namespace set to
`argocd` (the form in "Apply" below). It Succeeded and
recreated `argocd-application-controller`, `argocd-repo-server`,
`argocd-server` and `argocd-applicationset-controller`; `argocd-redis` was
untouched. So the Application IS adopted now, and the ledger-1208 "do not sync
it" guidance no longer holds: **an operator applies this by syncing
`Application/argocd`**, pinned to `kind-yadgar` (below) — not by repeating the
hand patch. A patch would still land the one key, but it would leave the other three
workloads' (`argocd-repo-server`, `argocd-server`,
`argocd-applicationset-controller`) `checksum/cmd-params` annotation drifted,
since the ledger-1208 steps restart only the controller's StatefulSet; sync
instead and let Argo restart all four together. 3 of its 39 resources carry no
sync status at all (measured 2026-10-02): `ServiceAccount`/`Role`/`RoleBinding`
`argocd-redis-secret-init`, each `requiresPruning: true`. They are the
`argocd-redis-secret-init` PreSync hook's own objects (helm
`before-hook-creation`): each sync deletes and recreates them, and they keep
reading `requiresPruning: true` with no sync status. A sync does not prune
them — there is no prune option here, no `--prune` passed. Read the diff
before syncing regardless, since a sync reconciles anything else that has
since drifted.

**The sync restarts four pods, not one.** The `argocd-cmd-params-cm` change
flips the `checksum/cmd-params` annotation on every workload that mounts it:
`argocd-application-controller` (StatefulSet), `argocd-repo-server`,
`argocd-server` and `argocd-applicationset-controller` (Deployments) — all four
carry the same `checksum/cmd-params` hash (measured 2026-10-02). `argocd-redis`
carries no such annotation and is not touched. This is the same four-pod
recreation the Application's first sync (above) already produced.

**Do NOT run this sync.** This note documents the step for the operator; it is
not applied by this change.

### Before — read-only

```bash
# 1. The live value. Expect: 24000
kubectl --context kind-yadgar -n argocd get configmap argocd-cmd-params-cm \
  -o jsonpath='{.data.controller\.sync\.timeout\.seconds}'; echo

# 2. No operation in flight. Expect: no rows. Any row → STOP.
kubectl --context kind-yadgar get applications -A -o json \
  | jq -r '.items[] | select(.status.operationState.phase == "Running" or .status.operationState.phase == "Terminating")
           | [.metadata.name, .status.operationState.phase, .status.operationState.startedAt] | @tsv'

# 3. Anything else not reading Synced, so the operator knows what the sync
#    will also touch (the redis-secret-init PreSync hook's own objects
#    included: they carry no status field at all and are not pruned by a
#    sync). Compare against the diff before syncing.
kubectl --context kind-yadgar -n argocd get application argocd -o json \
  | jq -r '.status.resources[] | select(.status != "Synced") | "\(.kind)/\(.name) \(.status // "no-status, requiresPruning=" + (.requiresPruning | tostring))"'

# 4. The four pods, to compare ages after the sync. argocd-redis is excluded on
#    purpose: it does not read this ConfigMap.
kubectl --context kind-yadgar -n argocd get pods \
  -l 'app.kubernetes.io/name in (argocd-application-controller,argocd-repo-server,argocd-server,argocd-applicationset-controller)' \
  -o jsonpath='{range .items[*]}{.metadata.name} {.metadata.uid} {.status.startTime}{"\n"}{end}'
```

### Apply

`--core` DOES take `--kube-context`, but that alone is not enough here: `argocd
app get argocd --core --kube-context kind-yadgar` (read-only, measured)
fails with `configmap "argocd-cm" not found`, because `--core` looks up the
control-plane `argocd-cm` in whatever namespace the kubeconfig CONTEXT itself
names, not `-N`/`--app-namespace` (that flag only scopes which namespace to
look up the Application, and adding it does not fix the failure, measured) and
not the `ARGOCD_NAMESPACE` env var (tried too, same failure, measured).
`kind-yadgar`'s context names namespace `yadgar`, not `argocd`
(`kubectl config get-contexts`). The default context on this host is a
production cluster, so an unpinned command here is a hazard.

Measured working form: a throwaway kubeconfig, minified to just the
`kind-yadgar` context, with that one field changed. It touches no file outside
`/tmp` and leaves the real kubeconfig alone. The whole body runs in a
subshell, `( set -eu; … )`, so `set -eu`, `exit` and the `EXIT` trap are all
scoped to that subshell: pasted into an interactive shell, a failure here ends
only the subshell, never the paster's own session. `[ -s "$TMPKC" ]` sits
directly after the line that can leave it empty.

```bash
(
  set -eu
  TMPKC=$(mktemp)
  trap 'rm -f "$TMPKC"' EXIT
  kubectl config view --minify --flatten --context kind-yadgar > "$TMPKC"
  [ -s "$TMPKC" ] || exit 1
  kubectl --kubeconfig "$TMPKC" config set-context kind-yadgar --namespace argocd
  KUBECONFIG="$TMPKC" argocd app sync argocd --core --kube-context kind-yadgar
)
```

Verified (2026-10-02): pasting this block, with `kind-yadgar` swapped for a
nonexistent context, into an interactive `bash -i` session — `kubectl config
view` fails loud, `set -e` ends the subshell there (the `[ -s ]` line is never
reached), the parent shell's next prompt comes back normally, and the `EXIT`
trap still ran: the temp file it reported was gone afterward. A `bash -c`
invocation would not have caught this class of bug, because `-c` already runs
the whole block in its own process.

### After — verify

```bash
# 1. The ConfigMap holds the value. Expect: 25200
kubectl --context kind-yadgar -n argocd get configmap argocd-cmd-params-cm \
  -o jsonpath='{.data.controller\.sync\.timeout\.seconds}'; echo

# 2. The four pods are new: uid and startTime differ from "Before" step 4.
kubectl --context kind-yadgar -n argocd get pods \
  -l 'app.kubernetes.io/name in (argocd-application-controller,argocd-repo-server,argocd-server,argocd-applicationset-controller)' \
  -o jsonpath='{range .items[*]}{.metadata.name} {.metadata.uid} {.status.startTime}{"\n"}{end}'

# 3. argocd-redis is unchanged: same uid and startTime as before the sync.
kubectl --context kind-yadgar -n argocd get pod -l app.kubernetes.io/name=argocd-redis \
  -o jsonpath='{.items[0].metadata.uid} {.items[0].status.startTime}{"\n"}'

# 4. The process has the value. Expect: 25200. This is an exec, but it only reads.
kubectl --context kind-yadgar -n argocd exec argocd-application-controller-0 \
  -c application-controller -- printenv ARGOCD_APPLICATION_CONTROLLER_SYNC_TIMEOUT

# 5. The Application itself: Synced/Healthy.
kubectl --context kind-yadgar -n argocd get application argocd \
  -o jsonpath='{.status.sync.status}/{.status.health.status}{"\n"}'
```

### Rollback

Revert the commit here, then run the same throwaway-kubeconfig sync from
"Apply" again and re-run the "After" checks against `"24000"`.
