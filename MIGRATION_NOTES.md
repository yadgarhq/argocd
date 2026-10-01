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
operators handover. `.github/workflows/post-merge-verify.yaml` runs it after
each merge to `main`. README.md, "Post-merge verification", says what it checks.

### What this merge does on its own

`root` creates `Application/post-merge-verifier`, and that Application syncs
`verifier/manifests/`: Namespace `post-merge-verifier`, ServiceAccount
`post-merge-verifier`, and ClusterRole and ClusterRoleBinding
`post-merge-verifier`. The role is get/list/watch, names every group and
resource, and has no `secrets`. Nothing runs as that ServiceAccount yet.

The workflow is **skipped** on every push until the repository variable
`POST_MERGE_VERIFY` is `true`. No runner can serve it today.

```bash
# After the merge, read-only:
kubectl --context kind-yadgar -n argocd get application post-merge-verifier \
  -o jsonpath='{.status.sync.status}/{.status.health.status}{"\n"}'
kubectl --context kind-yadgar auth can-i list secrets \
  --as=system:serviceaccount:post-merge-verifier:post-merge-verifier -A   # expect: no
kubectl --context kind-yadgar auth can-i list deployments.apps \
  --as=system:serviceaccount:post-merge-verifier:post-merge-verifier -A   # expect: yes
```

### Why the existing ARC runner cannot run it

Read on kind-yadgar, 2026-10-01, with `get` only:

- The only scale set, `estate-front/estate-front`, registers against
  `https://github.com/yadgarhq/estate`. A workflow in this repository cannot
  target it.
- The namespace's `estate-front-egress` NetworkPolicy excludes `10.96.0.0/16`
  and `10.89.4.0/24`, so it denies the API server at `10.96.0.1:443` and at
  its endpoint `10.89.4.2:6443`. `yadgarhq/estate`'s `.github/actionlint.yaml`
  says kindnet enforces no NetworkPolicy, so this is the declared intent, not a
  measured block.
- Its pods run as `estate-front-gha-rs-no-permission` and hold the `estate`
  environment's secrets. Binding cluster read there widens the most sensitive
  pod in the estate.

### Proposal: a dedicated scale set (needs a person, in this order)

1. **A GitHub App credential for this repository only.** ARC registers a
   repository-level runner with an App that has Administration read and write
   and Metadata read on `yadgarhq/argocd`. Do not reuse `yadgarhq-bot`: it holds
   write on every repository (ADR-0563). Create the Secret by hand:

   ```bash
   kubectl --context kind-yadgar -n post-merge-verifier create secret generic argocd-verify-github \
     --from-literal=github_app_id=<APP_ID> \
     --from-literal=github_app_installation_id=<INSTALLATION_ID> \
     --from-file=github_app_private_key=<KEY_FILE>
   ```

2. **The scale set, as `applications/post-merge-verifier-runner.yaml`, in a
   reviewed PR after step 1.** `template.spec.serviceAccountName` makes the
   chart use the read-only ServiceAccount instead of creating a
   no-permission one. Pin the runner image by digest; read the digest off the
   registry when you write the file.

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
           githubConfigUrl: https://github.com/yadgarhq/argocd
           githubConfigSecret: argocd-verify-github
           runnerScaleSetName: argocd-verify # = runs-on and .github/actionlint.yaml
           controllerServiceAccount: # see deploy's estate-front-runner for why
             namespace: arc-systems
             name: arc-gha-rs-controller
           minRunners: 0
           maxRunners: 1
           template:
             spec:
               serviceAccountName: post-merge-verifier
               containers:
                 - name: runner
                   image: ghcr.io/actions/actions-runner@sha256:<DIGEST>
                   command: ["/home/runner/run.sh"]
     destination:
       server: https://kubernetes.default.svc
       namespace: post-merge-verifier
     syncPolicy:
       automated: { selfHeal: true }
   ```

   Add an egress NetworkPolicy to `verifier/manifests/` in the same PR: DNS to
   kube-dns, `10.96.0.1/32:443` and `10.89.4.2/32:6443` for the API server,
   and `443` to `0.0.0.0/0` except the cluster ranges for GitHub, `dl.k8s.io`
   and the Python download. The node IP is kind's and moves if the cluster is
   recreated.

3. **Switch the workflow on** once the scale set's listener is up:

   ```bash
   gh-personal variable set POST_MERGE_VERIFY --body true --repo yadgarhq/argocd
   ```

   The first run has no baseline. Its summary says "BASELINE ONLY", and it
   compares nothing. The second merge after that is the first real check.

### What the label cannot stop

Any workflow in this repository could name `runs-on: argocd-verify`, including
one on `pull_request`, which runs branch code with the cluster-read token. A
repository-level runner cannot be limited to one workflow.
`scripts/tests/test_verify_handover.py` fails when a workflow other than
`post-merge-verify.yaml` names the label, or when that workflow gains a
trigger other than push to `main`.

### Rollback

Revert the merge. `root` prunes `Application/post-merge-verifier`, which has no
finalizer, so the Namespace, ServiceAccount, ClusterRole and ClusterRoleBinding
stay behind, unowned. Delete them by hand if they must go:

```bash
kubectl --context kind-yadgar delete clusterrolebinding post-merge-verifier
kubectl --context kind-yadgar delete clusterrole post-merge-verifier
kubectl --context kind-yadgar delete namespace post-merge-verifier
```
