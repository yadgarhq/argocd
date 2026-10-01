# Migration notes

Steps this repository needs from a person. Argo manages Argo, so a change here
is not live when it merges — `applications/argocd.yaml` is deliberately not
`automated`, and its adoption sync has never been run. Anything below that says
"after the sync" is waiting on that one ruling, which belongs to the operator
and is argued in `plans/argocd-adoption-sync.md` in `yadgarhq/docs`.

## The ARC scale-set health rule (ledger 745)

`install/values.yaml` gained
`resource.customizations.health.actions.github.com_AutoscalingRunnerSet` under
`configs.cm`. **It is inert today**, and that is not a defect in this change —
it is the state the adoption brief describes and the reason that brief exists.
Nothing in the cluster behaves differently until `argocd app sync argocd` is
run, and `Application/estate-front-runner` keeps reading Synced and Healthy with
a scale set Argo has no opinion about until then.

Landing this rolls `argocd-server`, `argocd-repo-server` and
`argocd-application-controller`, because each carries a `checksum/cm` annotation
over the rendered `argocd-cm`. That is the mechanism the adoption brief measured,
and it applies to **every** `configs.cm` edit forever, not to this one specially.
It does not roll `argocd-redis` or the ApplicationSet controller.

### Verify the rule before syncing anything

`argocd admin settings resource-overrides health` evaluates the rule exactly as
the controller would, against a file, with no cluster contact and nothing
applied. Do this first — it is the check that turns "the Lua looks right" into
"the Lua returns what I expect".

```bash
# The rule, as a ConfigMap the CLI can read. Rendering it is what proves the
# chart puts the key where Argo looks for it.
helm template argocd argo-cd --repo https://argoproj.github.io/argo-helm \
  --version 8.6.1 -n argocd -f install/values.yaml \
  | yq 'select(.kind == "ConfigMap" and .metadata.name == "argocd-cm")' > /tmp/argocd-cm.yaml

# The live scale set, read-only.
kubectl -n estate-front get autoscalingrunnerset estate-front -o yaml > /tmp/ars.yaml

argocd admin settings resource-overrides health /tmp/ars.yaml \
  --argocd-cm-path /tmp/argocd-cm.yaml
# STATUS: Healthy
# MESSAGE: phase Running: the listener exists and 0 runner(s) are up. Zero is
#          the correct idle state under minRunners: 0.
```

Then edit `/tmp/ars.yaml` — set `status.phase` to `Pending`, or delete the
`status` block entirely — and run it again. It must report **Progressing**, with
a message naming the phase. A rule that reports Healthy for both is a rule that
has not been installed; check the key name, which is one string with dots in the
group and a single underscore before the kind.

`argocd` is not installed on the machine this change was written on, so the
command above has **not** been run. What was run instead: the Lua was extracted
back out of `install/values.yaml` and evaluated with a stock Lua interpreter
against the real live object's JSON and six mutations of it. That proves the
logic and the phase values; it does not prove Argo loads the key, because only
Argo can prove that. The command above is the step that closes the gap, and it
costs nothing.

### After the sync

```bash
# The key reached the live ConfigMap.
kubectl -n argocd get cm argocd-cm \
  -o jsonpath='{.data.resource\.customizations\.health\.actions\.github\.com_AutoscalingRunnerSet}'
# the Lua, not empty

# The scale set now carries a health status. It carried NONE before this.
kubectl -n argocd get application estate-front-runner \
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
