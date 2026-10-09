# Migration notes

Steps this repository needs from a person. Argo manages Argo, so a change here
is not live when it merges — `applications/argocd.yaml` is deliberately not
`automated`. Its first sync ran once, 2026-10-02 06:51Z, by hand under a
throwaway kubeconfig minified to the `kind-yadgar` context with its namespace
set to `argocd` (`argocd app sync argocd --core --kube-context kind-yadgar`;
the form in "The sync timeout is raised to 25200 s" § "Apply" below), but that
does not make later changes land on their own: the Application stays manual,
so every change here still needs an operator to sync it by hand. Each section
below says what it
needs that sync for; the ruling on when to run it belongs to the operator and
is argued in `plans/argocd-adoption-sync.md` in `yadgarhq/docs`.

## The ARC scale-set health rule (ledger 745)

`install/values.yaml` gained
`resource.customizations.health.actions.github.com_AutoscalingRunnerSet` under
`configs.cm`, in #22 (`afc5c08`) — well before `Application/argocd`'s first
sync (above). **It is live, not inert.** MEASURED 2026-10-02: the live
`argocd-cm` already carries the Lua for this key, not empty, and
`Application/estate-front-runner`'s `AutoscalingRunnerSet` resource already
reads `Healthy` with a message — the pass this section's own "Verify" steps
below describe. Both landed with that first sync, since this key predates it.

The pod restart already happened too: the 2026-10-02 06:51Z sync (above)
recreated `argocd-application-controller`, `argocd-repo-server`,
`argocd-server` and `argocd-applicationset-controller`, and kept
`argocd-redis`. The ledger-1224 sync that lands 25200 (below) recreates the
same four, the same way — through
`checksum/cmd-params`, which all four carry (measured 2026-10-02) and
`argocd-redis` does not.

`checksum/cm`, the mechanism a `configs.cm` edit like this one actually
exercises, is narrower: only `argocd-server`, `argocd-repo-server` and
`argocd-application-controller` carry it (measured 2026-10-02) — that is what
the adoption brief measured, and it applies to **every** `configs.cm` edit, not
to this one specially. `argocd-applicationset-controller` carries no
`checksum/cm` annotation, so an isolated `configs.cm` edit would not have
rolled it. The first sync recreated it anyway. Which pod-template difference
caused that was not isolated. It was not a `configs.params` data change: the
ConfigMap already matched git after the 2026-10-01 patch.

### Verify the rule

`argocd admin settings resource-overrides health` evaluates the rule exactly as
the controller would, against a file. It logs informer startup in every run,
but against a kubeconfig whose only server is unreachable it returns the same
verdict with no connection error (measured 2026-10-02), so it does not contact
the cluster; every invocation here carries `--kube-context kind-yadgar`
anyway. The same command against a kubeconfig holding no clusters at all also
returns the identical `STATUS`/`MESSAGE` for the healthy case below, and
`Progressing` for a `status.phase: Pending` input (both measured 2026-10-02).
INFERRED, not measured, that it applies nothing: no log line proves it writes
nothing, though never contacting the cluster makes that more likely. Run this
anyway — it is the check that turns "the Lua looks right" into "the Lua
returns what I expect".

```bash
# The rule, as a ConfigMap the CLI can read. Rendering it is what proves the
# chart puts the key where Argo looks for it. Purely local, but pinned anyway:
# `helm template` takes `--kube-context` too.
helm template argocd argo-cd --repo https://argoproj.github.io/argo-helm \
  --version 8.6.1 -n argocd -f install/values.yaml --kube-context kind-yadgar \
  | yq 'select(.kind == "ConfigMap" and .metadata.name == "argocd-cm")' > /tmp/argocd-cm.yaml

# The live scale set, read-only.
kubectl --context kind-yadgar -n estate-front get autoscalingrunnerset estate-front -o yaml > /tmp/ars.yaml

# Takes the two files above; does not contact the cluster (see above) —
# pinned anyway.
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
minRunners: 0.` — matching the expected output above exactly. That result came
from the `--argocd-cm-path` file: the same command against a kubeconfig with
no clusters returns the identical output (measured 2026-10-02). Earlier, when
this section was first written, `argocd` was not installed, and
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
# NOT a kubectl read of `.status.resources[].health`: Argo v3 does not persist
# per-resource health onto the Application CR by default
# (`controller.resource.health.persist` is unset here, measured 2026-10-02),
# so that field reads empty whether the rule loaded or not — a kubectl read of
# it would report a working rule as broken. `argocd app get --core` computes
# health live instead of reading the persisted (here, absent) field.
(
  set -eu
  TMPKC=$(mktemp)
  trap 'rm -f "$TMPKC"' EXIT
  kubectl config view --minify --flatten --context kind-yadgar > "$TMPKC"
  [ -s "$TMPKC" ] || exit 1
  kubectl --kubeconfig "$TMPKC" config set-context kind-yadgar --namespace argocd
  KUBECONFIG="$TMPKC" argocd app get estate-front-runner --core --kube-context kind-yadgar -o json \
    | jq -r '.status.resources[] | select(.kind=="AutoscalingRunnerSet") | .health | "\(.status) — \(.message)"'
)
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
#         Applications, each Synced. (Read at the time of this merge.
#         Ledger 1270b later retired ApplicationSet/yadgar-modules — a
#         verification run after that merge lists every Application root
#         selects, without the ApplicationSet.)

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
  `10.89.4.2:6443`. kindnet enforces it (ADR-0688; see "The verifier egress
  policy" below).
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

### The verifier egress policy

`verifier/manifests/networkpolicy.yaml` declares `post-merge-verifier-egress`
(ledger 785, ADR-0841; yadgarhq/docs `plans/settled-state-smoke-gate.md`
stage 6). It is egress only, over every pod in `post-merge-verifier`. It allows
kube-dns on 53, the API server (`10.96.0.1/32:443`, and `10.89.4.0/24:6443`:
every node, where only the control plane listens), Prometheus
(`10.96.63.35/32:80` and the server pods on 9090), the edge
(`10.96.0.100/32:443` and the envoy pods on 10443), and `443` to `0.0.0.0/0`
except the pod, Service and node ranges. The addresses were
measured on 2026-10-03 and move when the cluster is rebuilt.

**This section used to say the policy is "not a control yet" because kindnet
enforcement was unmeasured.** That is false: kindnet `v20260528-9350166c` runs
the kube-network-policies controller (ledger 684, ADR-0688). A wrong
allow-list is therefore a verifier outage, and it presents as a timeout.

**What merging does.** `post-merge-verifier` syncs the policy. On 2026-10-03
the namespace held no pod, so no running traffic changes until the runner
scale set exists. The ClusterRole needs `networking.k8s.io: [networkpolicies]`
for the snapshot to list it; that grant is argocd#62's (A-U10), not this
change's, and `estate-front-egress` already needs the same grant.

```bash
# Before the merge, read-only:
kubectl --context kind-yadgar apply --dry-run=server -f verifier/manifests/networkpolicy.yaml
# After the merge, read-only:
kubectl --context kind-yadgar -n post-merge-verifier get networkpolicy post-merge-verifier-egress
```

**Rollback needs a person.** The Application has `selfHeal: true` and no
`prune`, so a git revert leaves the live policy in place. After the revert
merges:

```bash
kubectl --context kind-yadgar -n post-merge-verifier delete networkpolicy post-merge-verifier-egress
```

**The probe itself.** `scripts/project_probe.py` (its docstring has the
steps, verdicts and exit codes) is what `project-probe.yaml` in argocd-verify
runs, as `python3 argocd/scripts/project_probe.py --edge-ca <estate ca/root.pem>`
with `ESTATE_PROBE_PASSWORD` and `GITHUB_TOKEN` in its environment. The two
red trials of stage 6 are `--trial unregistered-personal` (exit 3 when valid)
and `--trial no-token` (exit 3 when valid: a 401 and no counter moved).

**The live proof, in the first `project-probe.yaml` trial run** (A-U13). From
the runner pod, in one run, so the binary and the resolver are constants:

1. The allowed destinations connect at once: Prometheus 9090 and 80, the edge
   10443 and 443, DNS 53.
2. `nslookup iam.yadgar.svc.cluster.local` resolves (iam is headless, so the
   answer is pod addresses).
3. `nc -z -w 4 iam.yadgar.svc.cluster.local 50052` exits non-zero at the
   deadline. **This arm alone proves nothing about this policy:**
   `iam-ingress` admits only `app=gateway` on 50052, so it times out with no
   egress policy at all.
4. The discriminating arm: kube-dns metrics,
   `nc -z -w 4 <kube-dns pod IP> 9153`, must also time out at 4 s.
   `kube-system` had no NetworkPolicy on 2026-10-03, and this policy does not
   list 9153. Record in the same run, as
   the control that the port is live, Prometheus answering
   `up{instance=~".*:9153"} == 1` for every kube-dns pod. Prometheus scrapes
   that port and this runner cannot reach it, so the timeout is this policy.

`nc` and `nslookup` may be absent from the runner image. The same arms in
Python, which `setup-python` provides:

```bash
python3 - <<'PY'
import socket, time, urllib.parse, urllib.request
print(sorted({a[4][0] for a in socket.getaddrinfo("iam.yadgar.svc.cluster.local", 50052)}))
for host, port in [("prometheus-server.observability.svc", 80), ("10.96.0.100", 443),
                   ("iam.yadgar.svc.cluster.local", 50052), ("kube-dns.kube-system.svc", 9153)]:
    t = time.monotonic()
    try:
        socket.create_connection((host, port), timeout=4).close(); r = "connected"
    except OSError as e:
        r = type(e).__name__
    print(f"{host}:{port} {r} {time.monotonic() - t:.1f}s")
q = urllib.parse.urlencode({"query": 'up{instance=~".*:9153"}'})
print(urllib.request.urlopen(f"http://prometheus-server.observability.svc/api/v1/query?{q}", timeout=10).read().decode())
PY
```

`kube-dns.kube-system.svc:9153` dials the Service address, which rule (e)
excepts and no rule lists, so it times out before or after DNAT.

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
(`f4da219`) merged: by hand, `argocd app sync argocd --core --kube-context
kind-yadgar` under a throwaway kubeconfig minified to the `kind-yadgar` context
with its namespace set to `argocd` (the form in "Apply" below). It Succeeded
and recreated `argocd-application-controller`, `argocd-repo-server`,
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

Moved here byte-faithfully from `yadgarhq/deploy`'s `MIGRATION_NOTES.md`
(ADR-0803, this PR) because `Makefile`'s `secrets` target cites these three
sections by name in its own error messages, and a citation into a file this
repository does not carry resolves nowhere. `deploy`'s copy of these three
sections is unchanged by this move.

**The paths these three sections name are unchanged by the move, and some
no longer exist.** They describe `deploy`'s layout as it stood before the
`infra` retirement (ADR-0828, M1–M5), and the byte-faithful copy keeps that
wording rather than editing it in place — "The `estate-front` runner" in
particular still reads `infra/arc.yaml`, `infra/estate-front-app.yaml`,
`infra/estate-front-runner.yaml` and `infra/estate-front/`. Translate:

| Path this section names          | Where it lives now                      |
| -------------------------------- | --------------------------------------- |
| `infra/arc.yaml`                 | `applications/arc.yaml`                 |
| `infra/estate-front-app.yaml`    | `applications/estate-front.yaml`        |
| `infra/estate-front-runner.yaml` | `applications/estate-front-runner.yaml` |
| `infra/estate-front/`            | `manifests/estate-front/`               |
| `infra/tls/`                     | `manifests/tls/`                        |

All five right-hand paths exist in this repository today. `make secrets`
and `make bootstrap`, wherever this section's prose says `deploy`, now mean
this repository's own `Makefile`.

## The identity encryption keys (ledger 452)

`iam` encrypts stored names with AES-256-GCM and looks usernames up by an
HMAC-SHA256 blind index. It refuses to boot without both keys.

**Losing the encryption key is unrecoverable.** Every stored name becomes
permanently unreadable — not degraded, gone. Losing the blind-index key is nearly
as bad: no login can find its user again, because the index it computes no longer
matches the ones in the table.

**THIS ONE IS STILL MINTED BY HAND, AND THAT IS THE DECISION.** `valkey-password`,
`nats-auth` and `nats-auth-gateway` are generated by the parent chart's
`platform.bootstrap` hooks under ADR-0517; these keys deliberately are not
(`platform.bootstrap.iamKeys.create: false`, ADR-0810). ADR-0517 splits credentials into machine-only, where losing
one costs a rotation, and human-facing, which must be retrievable once. **These
are a third kind — data-bearing — and for that kind the rule inverts.**

Generate them automatically and a cluster whose Secret was lost but whose
database survived gets an `iam` that starts and cannot decrypt the rows it
already has: broken while reporting healthy. Leave them out of the bootstrap and
the same cluster gets a pod that refuses to start and names the missing file.
**The refusal is the feature.** It is also the only signal that the keys were
lost at all.

So a fresh cluster costs this one step, and it is the only one a first sync
needs. Run **once** per set of keys, and keep the same keys across cluster
rebuilds.

### 1. Mint both keys

```bash
cd "$(mktemp -d)"
umask 077
openssl rand -out encryption.key 32
openssl rand -out blind-index.key 32
```

32 bytes of raw material each — not base64, not a passphrase. `iam` refuses a key
of any other length rather than padding or truncating it into something that
silently does not match what encrypted the existing rows.

**Two keys, not one, and not the same key twice.** They are separate so that
compromising the lookup path does not also decrypt the data behind it.

### 2. Store them in 1Password

Raw bytes, so these go in as documents rather than text fields:

```bash
op document create encryption.key  --title "yadgar iam — encryption key"  --vault Private
op document create blind-index.key --title "yadgar iam — blind index key" --vault Private
```

1Password first, cluster second, so a cluster rebuild does not destroy them.

### 3. Load them into the cluster

Before the first sync, so `iam` never waits:

```bash
kubectl --context kind-yadgar create secret generic iam-keys \
  --namespace yadgar \
  --from-file=encryption.key \
  --from-file=blind-index.key
```

Then destroy the local copies:

```bash
shred -u encryption.key blind-index.key
cd - && rmdir "$OLDPWD" 2>/dev/null || true
```

### To restore them into a rebuilt cluster

**`make secrets` does this, and `make bootstrap` depends on it.** What follows is
the same commands by hand, kept because they are the authority on which 1Password
items are read. Do it **before** the first sync of the rebuilt cluster:

```bash
op document get "yadgar iam — encryption key"  --out-file encryption.key
op document get "yadgar iam — blind index key" --out-file blind-index.key

kubectl --context kind-yadgar create secret generic iam-keys \
  --namespace yadgar \
  --from-file=encryption.key \
  --from-file=blind-index.key \
  --dry-run=client -o yaml | kubectl --context kind-yadgar apply --server-side --field-manager=yadgar-deploy --force-conflicts -f -

shred -u encryption.key blind-index.key
```

### If a cluster already holds keys that were never backed up

Copy them out before anything else destroys them:

```bash
cd "$(mktemp -d)"
umask 077
kubectl --context kind-yadgar -n yadgar get secret iam-keys \
  -o jsonpath='{.data.encryption\.key}' | base64 -d > encryption.key
kubectl --context kind-yadgar -n yadgar get secret iam-keys \
  -o jsonpath='{.data.blind-index\.key}' | base64 -d > blind-index.key
```

Then step 2 above, and `shred -u` both files.

### Check it took

```bash
kubectl --context kind-yadgar -n yadgar get secret iam-keys -o jsonpath='{.data}' | grep -o 'encryption.key'
kubectl --context kind-yadgar -n yadgar logs deploy/iam | grep 'crypto keys loaded'
```

A pod that cannot read them does not start and says why — that is D69's rule
applied to key material, and it is deliberate: a service that cannot decrypt what
it stored is broken rather than degraded.

## The development TLS edge (ledger 454)

Establishes HTTPS in front of the gateway so an MCP client on this machine
reaches it the way a real client would, over a certificate that verifies
properly rather than one anything has been told to ignore.

Run **once**. Everything after it arrives through git.

### Why a root CA at all, and why it lives outside the cluster

Let's Encrypt cannot issue here: HTTP-01 needs Let's Encrypt's servers to reach
this machine on public port 80, and DNS-01 would mean pointing a real domain and
its API credentials at a laptop. Both buy public PKI for traffic that never
leaves the host.

So the trust anchor is local. It is generated **here**, not in the cluster,
because a CA that cert-manager mints for itself is regenerated whenever the
cluster is rebuilt — and then the root NixOS trusts silently stops matching the
certificate the gateway serves. That failure looks like a TLS error of unclear
origin, and the natural next move is disabling verification, which deletes the
thing this exists to exercise.

### 1. Mint the root CA

```bash
cd "$(mktemp -d)"

openssl genrsa -out yadgar-dev-ca.key 4096

openssl req -x509 -new -nodes -key yadgar-dev-ca.key -sha256 -days 3650 \
  -out yadgar-dev-ca.crt \
  -subj "/CN=yadgar development root CA/O=yadgar" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "nameConstraints=critical,permitted;DNS:yadgar.internal,excluded;IP:0.0.0.0/0.0.0.0,excluded;IP:::/::"
```

**The `nameConstraints` line is the important one and is easy to leave out.**
This root goes into the system trust store, and its private key then lives in a
kind cluster. Without constraints, anything holding that key can mint a
trusted certificate for _any_ hostname — your bank, your registry, your identity
provider. Constrained, it can only sign names under `yadgar.internal`, so the
blast radius of a leaked development key is the development environment.

`yadgar.internal`, not `yadgar.localhost`, and that is a correction rather than a
preference — see "Move the development domain to `yadgar.internal`" below for the
measurement that forced it.

The IP exclusions are there because name constraints apply per name type:
constraining DNS alone leaves certificates with an IP SAN unconstrained.

`pathlen:0` stops it issuing intermediate CAs.

Verify before going further — if the extensions are missing, stop and redo:

```bash
openssl x509 -in yadgar-dev-ca.crt -noout -text | grep -A3 "X509v3 Name Constraints"
```

### 2. Store it in 1Password

Store it as a **Secure Note with named fields**, not as an SSH Key item. A
1Password SSH Key item hands back OpenSSH format
(a `BEGIN OPENSSH PRIVATE KEY` banner); `openssl` and `kubectl create secret tls`
both want PKCS#8 (a plain `PRIVATE KEY` banner), and the mismatch surfaces as an
unhelpful parse error. 1Password can generate a keypair but not a certificate, so
the material is minted by `openssl` above either way and 1Password only stores it.

```bash
op item create --category "Secure Note" --vault Private \
  --title yadgar-dev-ca \
  "private key[password]=$(cat yadgar-dev-ca.key)" \
  "certificate[text]=$(cat yadgar-dev-ca.crt)"
```

Field names matter — they are what the secret references resolve:

```bash
op read "op://Private/yadgar-dev-ca/private key" | head -1   # BEGIN PRIVATE KEY
op read "op://Private/yadgar-dev-ca/certificate" | head -1   # BEGIN CERTIFICATE
```

If the first prints an `OPENSSH PRIVATE KEY` banner instead, the item is the wrong
type; recreate it as a Secure Note.

The key is the secret. The certificate is public — it is committed to the nix repo
in step 4 so the trust statement is declarative, and that is fine.

### 3. Hand the key to cert-manager

**`make secrets` does this too, and it does not wait**: it creates the
`cert-manager` namespace itself, so the Secret is in place before Argo syncs
rather than after. Run this by hand only when loading the CA on its own, into a
cluster that is already up.

Straight from 1Password, so this works identically after a cluster rebuild with
no local files in play:

```bash
kubectl --context kind-yadgar create secret tls yadgar-dev-ca \
  --namespace cert-manager \
  --cert <(op read "op://Private/yadgar-dev-ca/certificate") \
  --key  <(op read "op://Private/yadgar-dev-ca/private key")
```

Then destroy the local copies — 1Password is the durable one:

```bash
shred -u yadgar-dev-ca.key
rm -f yadgar-dev-ca.crt
cd - && rmdir "$OLDPWD" 2>/dev/null || true
```

This secret is deliberately **not** in git. Rebuilding the cluster means
recreating it from 1Password; the root itself survives, so nothing needs
re-trusting.

### 4. Trust the root, and name the host — nix repo

Both are host configuration, so they belong to the machine's own repo rather
than to `deploy`. Commit the **certificate only**.

```nix
# modules/nixos/yadgar-dev-tls.nix (or wherever host config lives)
{
  security.pki.certificateFiles = [ ./certs/yadgar-dev-ca.crt ];

  # .internal is reserved by ICANN (Board Resolution 2024.07.29.06) for
  # private use and no resolver treats it specially — so, unlike .localhost, the
  # same name can point at 127.0.0.1 here and at this host's bridge address from
  # a VM, with no client quietly deciding otherwise. Deliberately NOT .local,
  # which RFC 6762 reserves for mDNS — Avahi and systemd-resolved intercept it,
  # and the resulting resolution failures look like a cluster problem rather
  # than a naming one.
  networking.hosts."127.0.0.1" = [ "gateway.yadgar.internal" ];
}
```

Apply it yourself — `nixos-rebuild` is not run from here.

### 5. Check it end to end

```bash
curl -v https://gateway.yadgar.internal:18443/  # port 18443, per kind's mapping
```

A verified handshake with no `-k` is the pass condition. `-k` passing proves
nothing, since it is the check being skipped.

**Prove the check can fail**, per the invariant that a check which cannot fail
is worse than none:

```bash
curl -v https://127.0.0.1:18443/   # MUST fail: name constraint + SAN mismatch
```

If that succeeds, the certificate is not the one you think it is.

## The `estate-front` runner (ledger 610, `yadgarhq/estate` stage 1)

`infra/arc.yaml`, `infra/estate-front-app.yaml` and `infra/estate-front-runner.yaml`
declare actions-runner-controller and the scale set `yadgarhq/estate`'s
`smoke.yaml` runs on. Argo applies all three. **Two things it cannot carry are
below. Neither is an operator step any longer — CI publishes the image and
`make secrets` loads the credential — but both still need a person when the
image digest moves.**

### What this is, in plain terms

Skip this if you already know ARC; it exists because the rest of this section
explains HOW without ever saying WHAT.

`yadgarhq/estate` runs a smoke suite after every deploy. That suite has to talk
to services **inside** the kind cluster, which a GitHub-hosted runner on the
public internet cannot reach. So the runner has to live in the cluster instead.

**actions-runner-controller (ARC)** is the GitHub-published Kubernetes operator
that does this. The shape is:

- A **controller** in `arc-systems` watches for scale-set definitions. Installed
  and Running.
- For each scale set it starts a **listener** pod, which holds the credential and
  long-polls GitHub asking "any jobs waiting for the label `estate-front`?" The
  listener runs no workflow code — that separation is why ADR-0563 tolerates the
  App's broad permissions.
- When a job appears, the listener creates a **runner pod** from a container
  image, which registers itself with GitHub, executes the job, and is destroyed.
  `minRunners: 0`, so nothing exists between jobs and an idle cluster is correct.

Hence two things Argo cannot carry, and each maps to one of those bullets. Both
were operator steps once. Neither is now:

1. **The runner image** is what the runner pod is made from. The stock
   `ghcr.io/actions/actions-runner` image has no Rust toolchain, and the smoke
   suite is a Rust test binary — so the estate builds its own with rustup baked
   in, rather than curl-piping a toolchain into a pod that holds the `estate`
   environment's secrets at job time. **CI builds and publishes it** (ADR-0579);
   what a person still does is move the digest pin, which is §1.
2. **The `estate-runner-github` Secret** is what the listener authenticates with.
   Without it the listener cannot start, so nothing ever asks GitHub for jobs,
   so every dispatched run queues until GitHub gives up on it. **`make secrets`
   creates it** (ADR-0580).

Neither can live in git: one is a container image, the other is a private key.
That is why this section exists at all — not because either is done by hand.

### Where this actually stands — measured 2026-09-06, read this first

Argo has applied its half, `make secrets` takes the other, and CI builds the
image. Nothing in this section is an operator step any more:

| thing                                       | state                                                  |
| ------------------------------------------- | ------------------------------------------------------ |
| `arc-systems` and `estate-front` namespaces | **exist**                                              |
| `deploy/arc-gha-rs-controller`              | **Running**, 1/1                                       |
| `AutoscalingListener` for `estate-front`    | **Running** in `arc-systems`                           |
| `AutoscalingRunnerSet/estate-front`         | **exists** — min 0, max 2                              |
| Secret `estate-front/estate-runner-github`  | **`make secrets` creates it**                          |
| `ghcr.io/yadgarhq/estate-runner`            | **published by CI** — `yadgarhq/actions`, see §1       |
| runner pods in `estate-front`               | **created per job**, destroyed after — `minRunners: 0` |

A cluster with no job in flight has no runner pod, and that is correct rather
than a fault. The listener is the row to check when nothing happens: it holds the
credential and does the asking, so without it every dispatched run queues in
silence.

**Step 2 lands before step 1 matters.** The Secret is what unblocks registration
and it is testable on its own: create it, and an `AutoscalingListener` pod
appears in `arc-systems`. The image is not pulled until a job is actually
dispatched to a runner, which cannot happen until the listener exists.

**The symptom on the GitHub side, so it is recognisable.** While the credential
was missing, every `smoke` run queued against `runs-on: estate-front` and was
eventually cancelled — measured 2026-09-05: **0 successful.** That is the
historical shape and it is kept because it is what the diagnosis below explains.
It no longer describes today: measured 2026-09-06, `smoke` runs reach the runner
and return verdicts, successes and failures both.

**WHY SO MANY ARE CANCELLED DESPITE `cancel-in-progress: false`.** The obvious
reading — that the setting is being ignored — is wrong, and so is the reading
this section carried first, that "no run ever starts, so there is nothing to
protect". Both are refuted by the run data.

A run that passes the concurrency gate OCCUPIES the group even while its jobs sit
`status: queued` waiting for a runner. It is the protected occupant, and
`cancel-in-progress: false` is what protects it — run 33971568146 (14:21:27Z) was
still alive four hours and fourteen dispatches later. GitHub's concurrency-blocked
status is a different one, `pending`, and that is the single waiting slot. Each
arriving run takes that slot and cancels whoever held it.

The timestamps prove it rather than suggest it: every cancellation's `updated_at`
is one second after the NEXT run's `created_at` (18:24:57/18:24:56,
18:25:04/18:25:03, 18:25:35/18:25:34). And run 33977748368, dispatched at
16:24:09Z, was not cancelled until 18:24:44Z — by the following batch, two hours
later. So the shape is global, not per batch: **one occupant, one pending slot,
everything else cancelled.** An earlier revision here claimed "four cancelled and
one stuck per batch"; the 16:23 batch actually left five cancelled and no
survivor.

**The concurrency block is correct and needs no change.** Fixing the runner fixes
the cancellations, because the occupant will then finish and free the group.

**The controller's own error, so you can confirm the diagnosis rather than trust
this table:**

```bash
kubectl --context kind-yadgar -n arc-systems logs deploy/arc-gha-rs-controller --tail=300 | grep -i 'failed to resolve'
# Failed to initialize Actions service client for creating a new runner scale set
# failed to resolve app config: failed to get kubernetes secret
```

That message names the missing Secret and nothing else. When it stops appearing
and an `AutoscalingListener` pod is Running in `arc-systems`, step 2 worked.

`minRunners: 0`, so nothing is created until a job is queued. A cluster that has
synced this and stopped there is not broken.

**It does not LOOK broken either, and that is the part to know.** An earlier
revision of this section said `Application/estate-front-runner` goes Degraded
without the credential. It does not. Argo CD assesses a custom resource it has
no health check for as Healthy, and it has none for `actions.github.com` kinds:
v3.1.8 ships no `resource_customizations/actions.github.com/` directory, and
this cluster's `argocd-cm` declares no `resource.customizations.health.*` key at
all — both measured 2026-09-05. So a missing, misnamed or wrong-keyed Secret
leaves this Application reporting **Synced and Healthy**.

The evidence is in `arc-systems`, the controller's namespace, not in the runner's:

```bash
kubectl --context kind-yadgar -n arc-systems logs deploy/arc-gha-rs-controller
kubectl --context kind-yadgar -n arc-systems get pods   # an AutoscalingListener for estate-front, or none
```

What that failure looks like exactly is not written down here, because seeing it
now means BREAKING a working install: the listener is Running, measured
2026-09-06. Making the Degraded claim true would take
a `resource.customizations.health.actions.github.com_AutoscalingRunnerSet` entry
in `argocd-cm` — which is `yadgarhq/argocd`'s object
(`install/values.yaml`, `configs.cm`), not this repository's, so it is out of
scope here rather than declined.

### 1. Pin the digest CI published

**There is nothing to build by hand.** The Containerfile that used to sit in
this section as a fenced code block now lives at
`containers/estate-runner/Containerfile` in `yadgarhq/actions`, and
`.github/workflows/estate-runner-image.yaml` builds it. ADR-0579: an image the
estate depends on is built by CI, never by hand from a runbook. A Containerfile
that lives only in documentation is not a build — and a hand-pushed image is one
that passed no scan, carries no SBOM and holds no signature, which is the whole
apparatus bypassed at the one place it protects the build itself.

The push credential that used to be described here — a classic personal access
token with `write:packages`, used from a workstation — is no longer needed for
anything. The workflow authenticates with `GITHUB_TOKEN`. Do not mint one.

The visibility flip that used to be step three here is **done** (ledger 661), and
the workflow now re-verifies an anonymous pull on every run, so a package that
goes private reddens a build rather than surfacing as an ImagePullBackOff nobody
was told about.

**What a person still does, and it is one line.** The workflow's last step prints
the pin into the run's step summary:

```yaml
image: ghcr.io/yadgarhq/estate-runner@sha256:<digest>
```

Copy that line into `infra/estate-front-runner.yaml` and open a pull request.

**Read the digest off the run, never off a tag.** `:latest` and the dated tag
both move — the workflow promotes `:latest` onto each new build — so a tag names
whatever was published most recently rather than the thing anybody reviewed. The
digest is what cosign signed. Every pod in this estate reports `latest`
somewhere; comparing tags proves nothing.

**Bumping it is a deliberate edit, and that is the standing obligation.** The
workflow rebuilds weekly on cron (`17 4 * * 1`), so a new digest exists most
Mondays and none of them moves this repository. That is the same discipline
`image.digest` in `yadgarhq/argocd`'s `versions/<module>.yaml` used to carry,
before that mechanism retired at ledger 1270b: the pin moved when a person
moved it. A cluster left unbumped keeps running the digest in git, which is
correct rather than broken — it simply ages. One difference worth naming:
those files carried a tag alongside the digest and this one does not. Here
the digest is the whole pin, and nothing should add a tag back beside it.

**How to check the roll landed, because Argo will not tell you.** Argo CD has no
health check for `actions.github.com` kinds, so `Application/estate-front-runner`
reports Synced and Healthy whether or not a runner ever comes up — a green status
is not evidence here. What is evidence is the digest a runner pod actually
resolved. Dispatch a smoke run, and while it holds a pod:

```bash
kubectl --context kind-yadgar -n estate-front get pods \
  -o jsonpath='{range .items[*]}{.status.containerStatuses[*].imageID}{"\n"}{end}'
```

**Compare digests, never tags.** `.status.containerStatuses[].imageID` carries the
resolved `@sha256:` and must equal the pin in
`infra/estate-front-runner.yaml`. `.spec.containers[].image` only echoes what was
requested, and every pod in this estate says `latest` somewhere.

`minRunners: 0`, so there is no pod between jobs and nothing to check on an idle
cluster. That also means this change replaces no running pod when it syncs: the
next job builds its runner from the new pin.

The listener is a different object, and what happens to it is **expected rather
than observed** — said plainly so nobody takes it for a measurement. What IS
measured: the chart writes an `actions.github.com/values-hash` annotation over
the whole values block, and rendering the chart before and after shows that
changing the image line changes that hash. So the object Argo applies differs by
more than the image line. Whether the controller then recreates the
`AutoscalingListener` pod is an inference about how it reads that annotation,
and `helm template` cannot see it. Expect a new listener pod in `arc-systems`; a
job in flight during the sync is the case to avoid either way.

**The weekly rebuild does not bump the runner binary.** GitHub deprecates old
runner binaries and refuses registration for one far enough behind, so this
still has to be watched. The Containerfile's `FROM` is digest-pinned, which means
the cron refreshes the apt and rustup layers and leaves the runner version where
it is. Moving it is an edit to that `FROM` digest in `yadgarhq/actions`, then a
bump of the pin here. Two repositories, both reviewed — which is the point.

### 2. Create the `estate-runner-github` Secret — **`make secrets` does this now**

**There is nothing to run by hand any more.** The `secrets` target in the
Makefile creates this Secret alongside `yadgar-dev-ca` and `iam-keys`, reading
the key from the 1Password document `yadgar — yadgarhq-bot App private key`
(Private vault). `bootstrap` depends on `secrets`, so a recreated cluster gets
it without anyone remembering this section:

```bash
make secrets     # idempotent on its own
make bootstrap   # runs secrets first
```

It is idempotent the same way the other two are — `--dry-run=client -o yaml |
kubectl --context $(KUBE_CONTEXT) apply --server-side --field-manager=yadgar-deploy
--force-conflicts -f -`, the server-side form deploy#82 (`0e33e2d`) adopted, matching
the Makefile's `$(SECRET_APPLY)` — so re-running it on a cluster that already has the
Secret is a no-op rather than an error.

**"No-op" holds only while the 1Password copy is unchanged.** If the document
holds a DIFFERENT key, this is a rotation rather than a no-op: `--force-conflicts`
lets `yadgar-deploy` take the fields the first time from whichever manager wrote
them before (`kubectl-client-side-apply`, `kubectl-create`, or the bootstrap
Job's `curl`); after that it owns them and the apply updates the value. That is
the behaviour you want — but **the listener does
not re-read the Secret**. After any rotation, delete the listener pod in
`arc-systems` so it picks the new credential up; otherwise it keeps authenticating
with the old key and nothing says so. The private key goes through a `mktemp -d` file
created under `umask 077` and shredded by an `EXIT` trap; it never reaches argv
or shell history. `github_app_id` and `github_app_installation_id` are passed as
literals because neither is a secret.

**A `last-applied-configuration` annotation written before this switch still
holds a full copy of the Secret's old data, and server-side apply does not
remove it.** Strip it once wherever it is still present:

```bash
kubectl --context kind-yadgar -n yadgar annotate secret iam-keys kubectl.kubernetes.io/last-applied-configuration-
kubectl --context kind-yadgar -n cert-manager annotate secret yadgar-dev-ca kubectl.kubernetes.io/last-applied-configuration-
kubectl --context kind-yadgar -n estate-front annotate secret estate-runner-github kubectl.kubernetes.io/last-applied-configuration-
```

Ledger 1222 did this on `kind-yadgar`; a cluster recreated from an older
snapshot needs the same strip once.

**If `make secrets` fails on this step**, the 1Password document is missing or
renamed. Recreate it by the procedure below, keeping the title byte-identical —
the Makefile looks it up by title.

The rest of this section is the manual procedure, kept because it is what to do
when the 1Password document itself has to be recreated.

#### Recreating the 1Password document

The listener authenticates as the **`yadgarhq-bot`** App (app_id **4814165**,
installed organisation-wide). Not a PAT: a PAT carries a person's whole access
and outlives them.

**What the App holds**, measured 2026-09-05 —
`gh api /orgs/yadgarhq/installations` (installation **158692002**):
`actions: write`, `administration: write`, `contents: write`, `issues: write`,
`metadata: read`, `organization_self_hosted_runners: write`,
`pull_requests: write`, `workflows: write`.

**The permission this scale set needs is `administration: write`**, not
`organization_self_hosted_runners: write` as an earlier revision of this section
said. The latter registers a runner at the ORGANISATION.
`infra/estate-front-runner.yaml` sets
`githubConfigUrl: https://github.com/yadgarhq/estate`, so the registration is a
**repository** one, and GitHub asks for repository `administration: write` for
that. It is the reason that permission was granted.

**Say the cost out loud.** The same response reports
`repository_selection: "all"` — the installation is organisation-wide. So
`administration: write` reaches every repository in `yadgarhq`, not just
`estate`: settings, branch protection, collaborators, deletion. Registering one
repository's runner bought an organisation-wide administrative permission.
ADR-0563 is what makes that tolerable rather than fine — no job on this runner
ever receives the key, and the listener that does hold it runs in `arc-systems`
and executes no workflow code. That ADR's revisit trigger is the App being
SPLIT; it became broader instead, so the decision stands unchanged.

**The private key is never written to a file in this repository, and no manifest
references anything but the Secret's name.** It is the same key the release flow
uses (`RELEASE_APP_PRIVATE_KEY`), read out of 1Password.

The installation id is not a secret and can be re-derived:

```bash
# As an organisation owner. 158692002 on 2026-09-05.
gh api /orgs/yadgarhq/installations --jq '.installations[] | select(.app_id == 4814165) | .id'
```

**"WHAT KEY, AND WHY NOT JUST USE THE APP?" — it IS the App.** A GitHub App has
no password and no copyable token. It authenticates by signing a JWT with an RSA
**private key** and exchanging that JWT for a short-lived installation token, so
the private key IS the App credential; there is no App-without-a-key option. The
three fields below are all App identity — `github_app_id` (which App),
`github_app_installation_id` (which installation of it), `github_app_private_key`
(the proof).

ARC's `githubConfigSecret` accepts EITHER that App triple OR a single
`github_token` holding a PAT. This estate chose the App, for the reason in
`infra/estate-front-runner.yaml`: a PAT carries a person's whole access and
outlives them. A short-lived token minted per poll from a key held by a pod that
runs no workflow code is the narrower credential, which is also what makes
ADR-0563 tolerable.

**WHERE THE KEY IS: NOWHERE YOU CAN READ IT, so you will generate a new one.**
An earlier revision of this section said to `op read` it from 1Password and left
`<vault>/<item>` as a placeholder for the reader to fill in. There is nothing to
fill in. Searched 2026-09-05 across all 897 items in every vault: **no item holds
the `yadgarhq-bot` App private key.** The only copy is the organisation secret
`RELEASE_APP_PRIVATE_KEY`, and GitHub Actions secrets are write-only — neither
the API nor the web UI will show it back to you.

That is not a problem, because **a GitHub App may hold several private keys at
once**. Generating another does not invalidate the one the release flow uses.

1. Go to <https://github.com/organizations/yadgarhq/settings/apps/yadgarhq-bot>
   (App `yadgarhq-bot`, app_id `4814165`) — you must be an organisation owner.
2. Scroll to **Private keys** → **Generate a private key**. The browser downloads
   a `.pem` immediately; it is shown once and never again.
3. Move it to a path you control and keep the permissions tight.

**Then put it in 1Password, so the next person is not sent here again.** This is
the step whose absence made this section unrunnable:

```bash
op document create ./yadgarhq-bot.<date>.private-key.pem \
  --title 'yadgar — yadgarhq-bot App private key' --vault Private
```

The existing `yadgar iam — encryption key` and `yadgar iam — blind index key`
items are `DOCUMENT`-category entries in the `Private` vault; this follows them.
Once it is stored, the command below becomes
`op document get 'yadgar — yadgarhq-bot App private key' > ./yadgarhq-bot.pem`
and no download is needed again.

**If you would rather not add a key**, the alternative is to revoke and replace:
generate a new one, update the `RELEASE_APP_PRIVATE_KEY` organisation secret with
it, create the cluster Secret from it, and only then delete the old key from the
App. Do it in that order — deleting first breaks every release in the estate
until the secret is replaced.

```bash
# THE KEY GOES THROUGH A FILE, NOT argv AND NOT A PROCESS SUBSTITUTION. argv is
# visible in `ps` and lands in shell history, so `--from-literal` is out; a
# process substitution expands to `/dev/fd/63`, which `--from-file` handles
# inconsistently across kubectl versions. A real file with `umask 077`, deleted
# straight after, is the form that behaves the same everywhere.
umask 077
# Either the freshly downloaded file, or — once it is stored as above —
#   op document get 'yadgar — yadgarhq-bot App private key' > ./yadgarhq-bot.pem
cp ~/Downloads/yadgarhq-bot.*.private-key.pem ./yadgarhq-bot.pem

kubectl --context kind-yadgar create namespace estate-front --dry-run=client -o yaml | kubectl --context kind-yadgar apply -f -
kubectl --context kind-yadgar -n estate-front create secret generic estate-runner-github \
  --from-literal=github_app_id=4814165 \
  --from-literal=github_app_installation_id=158692002 \
  --from-file=github_app_private_key=./yadgarhq-bot.pem \
  --dry-run=client -o yaml | kubectl --context kind-yadgar apply --server-side --field-manager=yadgar-deploy --force-conflicts -f -

shred -u ./yadgarhq-bot.pem
```

`--from-file=github_app_private_key=./yadgarhq-bot.pem` names the Secret key
explicitly. Dropping the `github_app_private_key=` prefix would name it after the
file, and the listener would report a missing field rather than a wrong one.

Argo does not manage this Secret and will not prune it. Rotating the key is the
same `create secret` command with `--dry-run=client -o yaml | kubectl
--context kind-yadgar apply --server-side --field-manager=yadgar-deploy
--force-conflicts -f -` appended, then deleting the listener pod in
`arc-systems` so it re-reads it.

### The fork pull-request approval policy — **NOTHING TO RUN**

Defence in depth behind the controls already enforced in git — see "How a fork
is kept off this runner" in `infra/estate-front-runner.yaml`. `yadgarhq/estate`
is a **public** repository, so this policy is the setting that matters, and it
is already at its strictest. Measured 2026-09-05, it reads
`all_external_contributors`, as it does on all fifteen public repositories in
the organisation:

```bash
gh api /repos/yadgarhq/estate/actions/permissions/fork-pr-contributor-approval
```

An earlier revision of this section asked an operator to set it. Do not; it is
set.

**It is a per-repository value, not an inherited one.** The ORGANISATION default
still reads `first_time_contributors`
(`gh api /orgs/yadgarhq/actions/permissions/fork-pr-contributor-approval`,
same date), so nothing about this is self-maintaining. What keeps a new
repository correct is `apply.sh` in `yadgarhq/docs`, which sets the repository
value at creation. A repository made by hand, outside that script, starts at the
organisation default.

**The organisation setting the design named — "fork pull-request workflows must
not run on self-hosted runners" — governs PRIVATE repositories only**, and the
runner group it also named cannot be created: `yadgarhq` is on the free plan and
`GET /orgs/yadgarhq/actions/runner-groups` returns only `Default`. Custom runner
groups are a Team or Enterprise feature. What replaces both is a
repository-scoped registration, which GitHub enforces.

### Resolving `gateway.yadgar.internal` — decided, and NOT a CoreDNS change

**Nothing to run.** The suite dials the external name so that SNI, the leaf and
the name-constrained chain are the ones a real client validates (ADR-0562). No
CoreDNS rewrite exists — verified 2026-09-05, `kube-system/coredns`'s Corefile
has no `rewrite` line — and none is added.

What ships instead is two objects this repository already owns: a stable
`yadgar-edge` Service on a pinned ClusterIP
(`infra/estate-front/edge-service.yaml`), and a `hostAliases` entry on the
runner pod that maps the name to it. Only RESOLUTION is redirected. Trust is
not: the client still sends `gateway.yadgar.internal` as SNI and still validates
the same certificate.

**Why not the Corefile, under ADR-0480.** That ConfigMap is written by kubeadm
when kind creates the cluster, so an Argo-managed copy makes two writers of one
object — ADR-0480's stated failure mode, not an analogy to it. A `kind delete`
and recreate resets it, and the half-built states in between are exactly what
that ADR exists to prevent. A Service and a pod spec are, by the same ADR,
unambiguously "what runs inside the cluster".

**What this does not cover, said plainly:** the name resolves in the runner pod
and nowhere else in the cluster. Stage 3's `estate-annex` scale set gets the
same `hostAliases` entry. If a THIRD consumer ever needs it, the general answer
is the Corefile, and it is the nix repo's to own:

```
rewrite name gateway.yadgar.internal yadgar-edge.envoy-gateway-system.svc.cluster.local
```

### Removing it again — the order matters, and one commit is the wrong shape

**Do not delete `infra/arc.yaml` and `infra/estate-front-runner.yaml` in the
same commit.** The scale-set chart puts an ARC finalizer,
`actions.github.com/cleanup-protection`, on three of the four objects it
renders — read out of `helm template` of `gha-runner-scale-set` 0.14.2 with this
repository's values block, 2026-09-05:

| object         | name                                |
| -------------- | ----------------------------------- |
| ServiceAccount | `estate-front-gha-rs-no-permission` |
| Role           | `estate-front-gha-rs-manager`       |
| RoleBinding    | `estate-front-gha-rs-manager`       |

Only the controller clears those finalizers. Prune the controller and the
finalizers stay, and each object sits `Terminating` until somebody patches the
finalizer off by hand. The two live in **separate Applications**, so nothing
about sync waves orders their PRUNES — waves order a sync, and these are two
deletions in two Applications.

The order for a person, one step at a time:

1. Delete `infra/estate-front-runner.yaml`, commit, and let Argo prune it.
2. Confirm the namespace is actually empty before going on:

   ```bash
   kubectl --context kind-yadgar -n estate-front get autoscalingrunnersets,serviceaccounts,roles,rolebindings
   ```

   Anything still `Terminating` means the controller has not finished. Wait for
   it. Do not proceed while it is running, because it is the thing that will
   clear those finalizers.

3. Only then delete `infra/arc.yaml`, in a second commit.

If step 3 already happened by mistake, the recovery is to patch each stuck
object's finalizers to `[]` — which is a hand edit of cluster state, and the
reason this order is written down rather than discovered.

### What none of this proves

The runner registers and jobs land on it — both observed 2026-09-06, and an
earlier revision of this paragraph denied both. The claim that followed — that
the NetworkPolicy in `infra/estate-front/` is "evaluated by nothing, because
kindnet implements no NetworkPolicy" — is false and has been since ledger 684. It
is corrected here in ledger 896: kindnetd does evaluate NetworkPolicy. What
nothing has measured is the EGRESS half, which is all that policy contains, so
the confinement is neither a proven control nor a mere specification. It is
untested. Ledger 614 was filed on the premise that has gone, and re-deciding it
belongs to the record rather than to this file.

Two things here are reasoned rather than observed, and are named so nobody takes
them for measurements. The teardown order is read off the rendered finalizers;
watching it go wrong means deleting a controller. And **nothing here has
observed a runner pod come up on the digest this repository now pins.** The pod
measured on 2026-09-06 resolved
`sha256:0d20f8ff5940906a99511934e572b0c64fea8cf6eec14809164ea59f7e12de07` — the
hand-built image the pin replaces — because the pin had not been applied yet.
What is verified about the new digest is that it is what CI published, scanned,
asserted and signed, and that it survives templating into the
`AutoscalingRunnerSet`. That it runs a smoke suite successfully is the next
dispatched run's job to show, by the `imageID` check in §1.

## Retiring the yadgar-modules ApplicationSet (ledger 1270b)

This merge deletes `applicationsets/modules.yaml`, `versions/*.yaml` (all
seven), `scripts/versions_pinned.py`, the `versions-pinned` pre-commit hook,
and the `github-scm` Secret creation and its `GITHUB_TOKEN` gate from
`make bootstrap`. The ApplicationSet generated zero Applications before this
merge (its `NotIn` selector already excluded every `yadgar-deployable`
module, since the parent chart cutover, ADR-0786, took over deploying them),
so `kubectl --context kind-yadgar get applicationsets -A` going empty prunes
nothing live.

**NEEDS-MAX, after this merge syncs:**

1. `root` is NOT self-managed — it carries no
   `argocd.argoproj.io/tracking-id` (measured live: its `managedFields` name
   no Argo field manager at all) because `make bootstrap` applies
   `projects/root.yaml` by hand, client-side, and nothing in git ever
   re-applies `root`'s own object; `root`'s own `directory.include` lives
   outside the glob it reads, so it never selects itself. This merge's
   deletion of `applicationsets/modules.yaml` prunes
   `ApplicationSet/yadgar-modules` on its own (a file that stops matching the
   LIVE include, whatever that include's exact text is), but narrowing the
   `include` field itself — `{applications,applicationsets}/*.yaml` to
   `applications/*.yaml` — does not go live on its own. After the prune
   above is observed:
   ```bash
   kubectl --context kind-yadgar diff -f projects/root.yaml
   # expect exactly one field: include: applications/*.yaml
   kubectl --context kind-yadgar apply -f projects/root.yaml
   ```
2. Delete the live Secret: `kubectl --context kind-yadgar -n argocd delete
secret github-scm`. Nothing reads it any more — the ApplicationSet it
   authenticated is gone from git and, once this merge syncs, from the
   cluster too.
3. Revoke that Secret's PAT at the GitHub token source it was minted from
   (the read-only, repo-scoped token `make bootstrap`'s old `GITHUB_TOKEN`
   gate asked for). The token is in 1Password or wherever it was minted;
   this repository holds no copy of it and never did.

**Rollback — NOT a plain revert, and order matters.** A revert restores the
Makefile line, but `make bootstrap` is not re-run, so the Secret must be
recreated by hand:

1. Recreate the Secret by hand, with a FRESH PAT (the one named in step 3
   above is revoked, not recoverable):
   ```bash
   kubectl --context kind-yadgar -n argocd create secret generic github-scm \
     --from-literal=token=<fresh PAT, read-only repo scope>
   ```
2. Revert this merge. `applicationsets/modules.yaml`, `versions/*.yaml`,
   `scripts/versions_pinned.py`, the pre-commit hook and the Makefile lines
   are all preserved verbatim in its diff.
3. If step 1 of the NEEDS-MAX list above was already done — `projects/root.yaml`
   was hand-applied with the narrowed `include` — re-apply the REVERTED
   `projects/root.yaml` by hand too: `kubectl --context kind-yadgar apply -f
projects/root.yaml`. Until that hand apply runs, `root` keeps the
   narrowed, now-reverted-in-git `include` live, and the reinstated
   ApplicationSet starts reconciling under an include that still excludes
   it.

## The yadgar pin moves to parent chart 0.13.13

**What the merge does.** It moves `applications/yadgar.yaml`'s
`targetRevision` from 0.3.38 to 0.13.13, `scripts/chart_pin.json` to
`v0.13.13` / `0.1.36`, and the five platform-sourced operator Applications
(`cert-manager`, `keda`, `mariadb-operator`, `envoy-gateway`, `prometheus`)
from `platform` 0.1.26 to 0.1.36. `valuesObject` does not change: 0.13.13
renders today's values with no refusal and needs no new key.

Merging is the deploy. `root` applies the six Applications from `main`, and
`yadgar` syncs on its own (`selfHeal`, no `prune`). Measured 2026-10-08 with
helm 3.18.4, and with a server-side dry run against `kind-yadgar`:

- 88 → 90 objects. 0 removed. 2 added: `Certificate/nats-tls` and
  `Certificate/valkey-tls` (platform#36). Nothing mounts them at this pin.
- 13 changed: the image of all seven module Deployments; four new pool env
  lines on `iam-db`, `project-db` and `task-db` (store v0.4.0); the
  `Prune=false` annotation on `Certificate/gateway-tls`, `EnvoyProxy/edge`,
  `Gateway/edge` and `GatewayClass/eg` (platform#31, ADR-0851); the scripts of
  the `preflight` and `envoy-gateway-probe` hook Jobs (platform#32, ledger
  1263).
- The five operator Applications render byte-identically at 0.1.26 and
  0.1.36, CRDs included. Only their `targetRevision` changes.
- The dry run admits every object. No immutable field moves.

**What runs.** PreSync: `preflight`, then `bootstrap-secrets` and
`admin-bootstrap-token`. Sync: the seven Deployments roll, each
`maxSurge: 1 / maxUnavailable: 0`. `valkey` and `nats` do not roll.
cert-manager issues two new Secrets, `nats-tls` and `valkey-tls`. PostSync:
`envoy-gateway-probe`. No `-db` image runs a new migration. The `-db` pool
acquire timeout moves from 30 s to 25 s.

### Before this merge — read-only

`--context kind-yadgar` on every line. This host's default context is a
production cluster.

```bash
# 1. Every Application Synced/Healthy; yadgar at 0.3.38. Any other state → STOP.
kubectl --context kind-yadgar -n argocd get applications \
  -o custom-columns=NAME:.metadata.name,REV:.spec.source.targetRevision,SYNC:.status.sync.status,HEALTH:.status.health.status

# 2. The uids PB-1's acceptance checks after the merge. Expected, read 2026-10-08:
#    Certificate/gateway-tls ef87adbb-6fd1-4a0e-b9fd-8a986ca5edeb
#    EnvoyProxy/edge         6c45210d-e8a1-470e-9222-e9fb6239bbef
#    Gateway/edge            2235fee3-2a90-41c8-bb54-b0e964b790d6
#    GatewayClass/eg         9fb46cca-1d64-4b7d-b88a-7924f5da278c
kubectl --context kind-yadgar -n yadgar get certificate/gateway-tls envoyproxy/edge gateway/edge \
  -o custom-columns=KIND:.kind,NAME:.metadata.name,UID:.metadata.uid
kubectl --context kind-yadgar get gatewayclass eg -o custom-columns=NAME:.metadata.name,UID:.metadata.uid

# 3. The verifier snapshot. Its diff after the merge is RED BY DESIGN: seven
#    Deployments change generation and roll. Read every named change.
python3 scripts/verify_handover.py snapshot --context kind-yadgar --out before.json
```

### After this merge — read-only

```bash
# 1. yadgar Synced/Healthy at 0.13.13, and its last operation Succeeded.
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.spec.source.targetRevision} {.status.sync.status}/{.status.health.status} {.status.operationState.phase}{"\n"}'

# 2. The five operators Synced/Healthy at 0.1.36.
kubectl --context kind-yadgar -n argocd get applications \
  -o custom-columns=NAME:.metadata.name,REV:.spec.source.targetRevision,SYNC:.status.sync.status,HEALTH:.status.health.status

# 3. The hooks of this revision completed.
kubectl --context kind-yadgar -n yadgar get jobs

# 4. Seven Deployments rolled and are Ready; valkey did not roll.
kubectl --context kind-yadgar -n yadgar get deploy,pods

# 5. The two new Certificates are Ready.
kubectl --context kind-yadgar -n yadgar get certificate nats-tls valkey-tls

# 6. The four edge objects: SAME uid as "Before" step 2, now with Prune=false.
kubectl --context kind-yadgar -n yadgar get certificate/gateway-tls envoyproxy/edge gateway/edge \
  -o custom-columns=KIND:.kind,NAME:.metadata.name,UID:.metadata.uid,OPT:.metadata.annotations.argocd\.argoproj\.io/sync-options
kubectl --context kind-yadgar get gatewayclass eg \
  -o custom-columns=NAME:.metadata.name,UID:.metadata.uid,OPT:.metadata.annotations.argocd\.argoproj\.io/sync-options

# 7. The verifier diff. Expect the seven rolls, and no vanished uid.
python3 scripts/verify_handover.py wait --context kind-yadgar --app root --revision <merge sha>
python3 scripts/verify_handover.py wait --context kind-yadgar --settled --since <time root reached the sha>
python3 scripts/verify_handover.py snapshot --context kind-yadgar --out after.json
python3 scripts/verify_handover.py diff before.json after.json
```

If a `-db` pod does not become Ready, its rollout stops behind
`maxUnavailable: 0` and the old pods keep serving. Read its log before you
revert.

**Rollback — a revert, then two deletes by hand.** If `yadgar`'s operation is
still Running or retrying, end it first:

```bash
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.status.operationState.phase} {.status.operationState.syncResult.revision}{"\n"}'
```

NEEDS-MAX: `argocd app terminate-op yadgar` against kind-yadgar's Argo CD. A
revert does not interrupt an operation already running: v3.1.8's Application
CRD has no `syncPolicy.retry.refresh`. Measured 2026-10-08: the live CRD's
`syncPolicy.retry` properties are `["backoff","limit"]`, and `argocd-server`
runs `quay.io/argoproj/argocd:v3.1.8`.

Then revert the merge. `yadgar` syncs 0.3.38: the images and the `-db` env go back, and the client-side apply
removes the edge `Prune=false` annotations. The hooks re-run with the 0.1.26
scripts. No migration ran, so the downgrade is schema-safe. `yadgar` has no
`automated.prune`, so the two Certificates stay, and `yadgar` reads OutOfSync
with them as `requiresPruning`. cert-manager here sets no owner reference, so
their Secrets stay too. Nothing mounts any of the four. NEEDS-MAX:

```bash
kubectl --context kind-yadgar -n yadgar delete certificate nats-tls valkey-tls
kubectl --context kind-yadgar -n yadgar delete secret nats-tls valkey-tls
```

## The four callers present their client leaves (ledger 770)

**What the merge does.** It adds four values to `applications/yadgar.yaml`:
`gateway.clientCertificate.secret: gateway-client-tls`, and
`clientCertSecret` under `iam.iamDb.tls`, `task.taskDb.tls` and
`project.projectDb.tls` (`iam-client-tls`, `task-client-tls`,
`project-client-tls`). `platform` already issues these four Secrets. Until
this merge, no pod mounts them. The pin stays at 0.13.13.

Merging is the deploy: `yadgar` syncs on its own (`selfHeal`, no `prune`).
Measured 2026-10-08 at parent 0.13.13 with helm 3.18.4 and 4.3.0 (same object
diff), and with a server-side dry run against `kind-yadgar`:

- 90 → 90 objects. 0 added. 0 removed.
- 4 changed: `Deployment/gateway`, `iam`, `task` and `project`. Each gains one
  Secret volume (`optional: true`, items `tls.crt` and `tls.key`), one
  read-only mount, and its `*_TLS_CLIENT_CERT_FILE` / `*_TLS_CLIENT_KEY_FILE`
  env. `gateway` gets the pair for each of its three dials (`IAM_`, `TASK_`,
  `PROJECT_`), all naming one file pair.
- The server dry run names the same four Deployments. Every changed line is
  an addition. It admits all 77 tracked objects and all 13 hooks.

**What runs.** The four Deployments roll, each `maxSurge: 1 /
maxUnavailable: 0`. No other object changes. No server asks for a client
certificate at this pin (no `*_TLS_CLIENT_AUTH` renders), so every handshake
completes as before.

### Before this merge — read-only

`--context kind-yadgar` on every line. This host's default context is a
production cluster.

```bash
# 1. yadgar Synced/Healthy at 0.13.13. Any other state → STOP.
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.spec.source.targetRevision} {.status.sync.status}/{.status.health.status} {.status.operationState.phase}{"\n"}'

# 2. The four Certificates are Ready. Read 2026-10-08: all True.
kubectl --context kind-yadgar -n yadgar get certificate \
  gateway-client-tls iam-client-tls task-client-tls project-client-tls

# 3. Baseline of the gauge, through the API server's service proxy
#    (prometheus-server port 80). Read 2026-10-08: empty, no kind="client" series.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=count%20by%20(service%2Ckind)(yadgar_tls_certificate_not_after_seconds%7Bkind%3D%22client%22%7D)'
```

### After this merge — read-only

```bash
# 1. yadgar Synced/Healthy, and its last operation Succeeded.
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.status.sync.status}/{.status.health.status} {.status.operationState.phase}{"\n"}'

# 2. The four Deployments rolled (new pod start times) and are Ready.
kubectl --context kind-yadgar -n yadgar get pods -l 'app in (gateway,iam,task,project)' \
  -o custom-columns=NAME:.metadata.name,START:.status.startTime,READY:.status.containerStatuses[0].ready

# 3. Each mounts its leaf.
kubectl --context kind-yadgar -n yadgar get deploy gateway iam task project \
  -o custom-columns=NAME:.metadata.name,SECRETS:.spec.template.spec.volumes[*].secret.secretName

# 4. The gauge: expect 2 (one per pod) for each of gateway, iam, task and project.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=count%20by%20(service%2Ckind)(yadgar_tls_certificate_not_after_seconds%7Bkind%3D%22client%22%7D)'

# 5. No watched file is unreadable: expect 0 for all 7 services.
#    Read 2026-10-08 before the merge: 0 for all 7.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service)(yadgar_rotation_watched_files_unreadable)'

# 6. The gateway's project registry loaded: expect 1 per gateway pod.
#    Read 2026-10-08 before the merge: 1 on both pods.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=yadgar_gateway_project_registry_loaded'

# 7. Judge by Deployment readiness: READY == REPLICAS and UPDATED == REPLICAS on all four.
kubectl --context kind-yadgar -n yadgar get deploy gateway iam task project \
  -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas,UPDATED:.status.updatedReplicas
```

The operation phase reads Succeeded even if a pod crashloops. Judge by
Deployment readiness (`readyReplicas == replicas`, `updatedReplicas ==
replicas`), step 7 above.

The gauge proves each process loaded its leaf and watches it. It does not
prove the dial presents the leaf; a server that runs `clientAuth` proves that.

If a pod does not become Ready, its rollout stops behind `maxUnavailable: 0`
and the old pods keep serving. Read its log before you revert.

### Rollback — a revert

If `yadgar`'s operation is still Running or retrying, end it first:

```bash
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.status.operationState.phase} {.status.operationState.syncResult.revision}{"\n"}'
```

NEEDS-MAX: `argocd app terminate-op yadgar` against kind-yadgar's Argo CD. A
revert does not interrupt an operation already running: v3.1.8's Application
CRD has no `syncPolicy.retry.refresh` (see "The yadgar pin moves to parent
chart 0.13.13", Rollback).

Then revert the merge. The added volume, mount and env lines are in each
Deployment's last-applied configuration, so the client-side apply removes
them, and the four Deployments roll back. This merge creates no object, so
nothing is left to prune. The four Secrets stay, as they were before the
merge.

## The yadgar pin moves to parent chart 0.19.1, and the six servers stage their client CA (PB-2, ledger 925)

**What the merge does.** It moves `applications/yadgar.yaml`'s
`targetRevision` from 0.13.13 to 0.19.1 and `scripts/chart_pin.json`'s
`chart_tag` to `v0.19.1`. `platform_version` stays 0.1.36, so no operator
Application moves. It also adds three values to `tls` on each of the six gRPC
servers (`iam`, `iam-db`, `task`, `task-db`, `project`, `project-db`):

- `clientAuth: "off"`. Parent 0.19.1's server charts require the key, with no
  default (ADR-0854). The binaries refuse to boot without
  `LISTEN_TLS_CLIENT_AUTH`.
- `clientCaSecret: <server>-tls` and `clientCaSecretKey: ca.crt` (ADR-0883).
  This stages the client CA now, so the later flip to `optional` (B-U8) is a
  one-key change.

Parent 0.19.1 moves six module pins and nothing else: iam 0.9.2 → 0.10.0,
iam-db 0.9.0 → 0.10.0, task 0.6.1 → 0.7.0, task-db 0.8.1 → 0.9.0, project
0.2.1 → 0.3.0, project-db 0.5.1 → 0.6.0. Each tag is the B-U5 contract
merge (iam#95, iam-db#86, task#74, task-db#87, project#30, project-db#56).
gateway stays 0.10.2, config 0.2.0, platform 0.1.36.

Merging is the deploy: `yadgar` syncs on its own (`selfHeal`, no `prune`).
Measured 2026-10-09 with helm 3.18.4 and 4.3.0 (byte-identical renders), and
with a server-side dry run against `kind-yadgar`:

- 90 → 90 objects. 0 added. 0 removed.
- 6 changed: the six server Deployments. Each gets a new image, the env
  `LISTEN_TLS_CLIENT_AUTH=off` and `LISTEN_TLS_CLIENT_CA_FILE`, and a NEW
  `client-ca` Secret volume (its `<server>-tls`, item `ca.crt`) with a
  read-only mount. The volume renders no `optional` field, so it is
  `optional: false`. `iam-db` mounts it at `/var/run/secrets/iam-db-client-ca`;
  the other five mount it at `/var/run/config/client-ca`.
- `gateway`, the hook Jobs, the Certificates and every other object are
  byte-identical.
- The dry run names the same six Deployments. It admits all 77 tracked
  objects and all 13 hooks. The `yadgar` Application CR diff is
  `targetRevision` and the 18 added value lines only.

**What runs.** The six Deployments roll, each `maxSurge: 1 /
maxUnavailable: 0`. No migration runs: no `-db` repository changes its
schema between the live tag and the new tag, and store stays v0.4.0. Under
`off`, `yadgar_lifecycle::serve_tls` (v0.2.20) drops the CA path. It does not
read the file, does not build a client verifier, and does not watch the
file. So no server asks a caller for a certificate, and every handshake
completes as before.

Two things look like faults and are not:

- Each server logs one WARN at boot: "`LISTEN_TLS_CLIENT_CA_FILE` names a
  client CA but `LISTEN_TLS_CLIENT_AUTH` is `off`, so this listener verifies
  NO client certificate". That is the staged state this merge creates.
- The `kind="serving"` gauge and the unreadable counter do not move. The CA
  is not in the watch set under `off`.

**A wrong staged name fails loudly.** The volume is not optional. A missing
Secret or a missing `ca.crt` key stops the new pod at `FailedMount`, and the
rollout stops behind `maxUnavailable: 0` while the old pods keep serving.
The dry run cannot see a mount failure; "Before" step 2 is the live check.

### Before this merge — read-only

`--context kind-yadgar` on every line. This host's default context is a
production cluster.

```bash
# 1. yadgar Synced/Healthy at 0.13.13. Any other state → STOP.
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.spec.source.targetRevision} {.status.sync.status}/{.status.health.status} {.status.operationState.phase}{"\n"}'

# 2. Each <server>-tls Secret exists, carries ca.crt, and comes from the Issuer
#    that issues the four *-client-tls leaves. KEY NAMES ONLY, never values.
#    Read 2026-10-09: all six carry `ca.crt tls.crt tls.key`, same Issuer as
#    the client leaves.
kubectl --context kind-yadgar -n yadgar get secrets \
  iam-tls iam-db-tls task-tls task-db-tls project-tls project-db-tls iam-client-tls \
  -o go-template='{{range .items}}{{.metadata.name}} [{{range $k,$v := .data}}{{$k}} {{end}}] {{index .metadata.annotations "cert-manager.io/issuer-name"}}{{"\n"}}{{end}}'

# 3. The six Deployments: READY == REPLICAS == UPDATED. Read 2026-10-09: 2/2/2 on all six.
kubectl --context kind-yadgar -n yadgar get deploy iam iam-db task task-db project project-db \
  -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas,UPDATED:.status.updatedReplicas

# 4. The serving gauge. Read 2026-10-09: 2 for each of the six servers.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=count%20by%20(service%2Ckind)(yadgar_tls_certificate_not_after_seconds%7Bkind%3D%22serving%22%7D)'

# 5. No watched file is unreadable. Read 2026-10-09: 0 for all 7 services.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service)(yadgar_rotation_watched_files_unreadable)'

# 6. The gateway's project registry. Read 2026-10-09: 1 on both gateway pods.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=yadgar_gateway_project_registry_loaded'
```

### After this merge — read-only

```bash
# 1. yadgar Synced/Healthy at 0.19.1, and its last operation Succeeded.
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.spec.source.targetRevision} {.status.sync.status}/{.status.health.status} {.status.operationState.phase}{"\n"}'

# 2. Judge by Deployment readiness, not by the operation phase (it reads
#    Succeeded even if a pod crashloops). Expect 2/2/2 on all six.
kubectl --context kind-yadgar -n yadgar get deploy iam iam-db task task-db project project-db \
  -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas,UPDATED:.status.updatedReplicas

# 3. The new env and volume are live: expect `off` and `<server>-tls` on each.
kubectl --context kind-yadgar -n yadgar get deploy iam iam-db task task-db project project-db \
  -o custom-columns='NAME:.metadata.name,AUTH:.spec.template.spec.containers[0].env[?(@.name=="LISTEN_TLS_CLIENT_AUTH")].value,CA:.spec.template.spec.volumes[?(@.name=="client-ca")].secret.secretName'

# 4. The serving gauge is unchanged: 2 for each of the six servers.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=count%20by%20(service%2Ckind)(yadgar_tls_certificate_not_after_seconds%7Bkind%3D%22serving%22%7D)'

# 5. Unreadable: 0 for all 7. Registry: 1 on both gateway pods.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service)(yadgar_rotation_watched_files_unreadable)'
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=yadgar_gateway_project_registry_loaded'
```

If a pod does not become Ready, its rollout stops behind
`maxUnavailable: 0` and the old pods keep serving. Read its events
(`FailedMount` names a wrong staged Secret) and its log before you revert.

### Rollback — a revert

If `yadgar`'s operation is still Running or retrying, end it first:

```bash
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.status.operationState.phase} {.status.operationState.syncResult.revision}{"\n"}'
```

NEEDS-MAX: `argocd app terminate-op yadgar` against kind-yadgar's Argo CD. A
revert does not interrupt an operation already running: v3.1.8's Application
CRD has no `syncPolicy.retry.refresh` (see "The yadgar pin moves to parent
chart 0.13.13", Rollback).

Then revert the merge. `yadgar` syncs 0.13.13: the six images go back, and
the added env, volume and mount lines are in each Deployment's last-applied
configuration, so the client-side apply removes them. No migration ran, so
the downgrade is schema-safe. This merge creates no object, so nothing is
left to prune.

## task-db asks its caller for a client certificate: `clientAuth: "optional"` (B-U8a, ledger 925)

**What the merge does.** It changes one value in `applications/yadgar.yaml`:
`task-db.tls.clientAuth` from `"off"` to `"optional"`. The other five servers
stay at `"off"`. The pin stays at 0.19.1. PB-2 already staged the client CA
(`client-ca` volume from `task-db-tls`, item `ca.crt`, and
`LISTEN_TLS_CLIENT_CA_FILE`), so this is a one-key change.

Merging is the deploy: `yadgar` syncs on its own (`selfHeal`, no `prune`).
Measured 2026-10-09 with helm 3.18.4 and 4.3.0 (byte-identical renders), and
with a server-side dry run against `kind-yadgar`:

- 90 → 90 objects. 0 added. 0 removed.
- 1 changed: `Deployment/task-db`. One env value moves:
  `LISTEN_TLS_CLIENT_AUTH` `off` → `optional`. Nothing else in it moves.
- The dry run names only `Deployment/task-db` (the env value and
  `generation`). It admits all 77 tracked objects and all 13 hooks. The
  `yadgar` Application CR diff is the one value.

**What runs.** `task-db` rolls, `maxSurge: 1 / maxUnavailable: 0`, 2
replicas. No other Deployment rolls. Under `optional`,
`yadgar_lifecycle::serve_tls` (lifecycle v0.2.20, task-db v0.9.0):

- reads `LISTEN_TLS_CLIENT_CA_FILE` at boot, and refuses to boot if the file
  is unreadable, unparsable, or holds no certificate;
- builds a client verifier with that file as its only anchor
  (`client_ca_root`) and `client_auth_optional(true)`;
- asks every caller for a certificate. A caller that sends none is accepted.
  A caller that sends one has it verified: a leaf from another CA, or an
  expired leaf, is refused;
- adds the CA file to the rotation watch set.

The only caller is `task` (`task-db-ingress` admits `app: task` only). `task`
presents `task-client-tls` (ledger 770, live since argocd#68). `task-db-tls`
and `task-client-tls` both come from `Issuer/yadgar-internal-ca`, so the CA
in `task-db-tls`'s `ca.crt` issued `task`'s leaf. `task-client-tls` carries
the `client auth` usage.

**`optional` is not a control.** Anyone can send no certificate. This step
is the first live use of `task`'s leaf: if the leaf is wrong or expired,
`task` → `task-db` fails now. Only `required` (B-U9.1) proves that the leaf
is on the wire.

**Rotation.** The CA file now joins the watch set (`File::read`). Readiness
does not change: the readiness probe is `tcpSocket` on `grpc`, which no
client-certificate mode touches. A leaf renewal rewrites `ca.crt` with the
same bytes, so it does not cause a second restart. Only a new CA changes the
file. The process then drains and exits 0, as for a leaf rotation.

**Things that change in the log, and are not faults.** The boot WARN
"`LISTEN_TLS_CLIENT_CA_FILE` names a client CA but `LISTEN_TLS_CLIENT_AUTH`
is `off` …" goes away. The `task-db listening` line reads
`"client_auth":"optional"` and `"watching":5` (4 before: the CA is the
fifth file).

### Before this merge — read-only

`--context kind-yadgar` on every line. This host's default context is a
production cluster.

```bash
# 1. yadgar Synced/Healthy at 0.19.1. Any other state → STOP.
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.spec.source.targetRevision} {.status.sync.status}/{.status.health.status} {.status.operationState.phase}{"\n"}'

# 2. task-db 2/2/2 at `off`, with the CA staged. Read 2026-10-09: 2 2 2 off task-db-tls.
kubectl --context kind-yadgar -n yadgar get deploy task-db task \
  -o custom-columns='NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas,UPDATED:.status.updatedReplicas,AUTH:.spec.template.spec.containers[0].env[?(@.name=="LISTEN_TLS_CLIENT_AUTH")].value,CA:.spec.template.spec.volumes[?(@.name=="client-ca")].secret.secretName'

# 3. The server leaf and the caller's leaf come from one Issuer. NAMES ONLY.
#    Read 2026-10-09: both `yadgar-internal-ca` (Issuer), both Ready.
kubectl --context kind-yadgar -n yadgar get certificate task-db-tls task-client-tls \
  -o custom-columns='NAME:.metadata.name,ISSUER:.spec.issuerRef.name,KIND:.spec.issuerRef.kind,READY:.status.conditions[?(@.type=="Ready")].status,NOTAFTER:.status.notAfter'
kubectl --context kind-yadgar -n yadgar get secret task-db-tls task-client-tls \
  -o go-template='{{range .items}}{{.metadata.name}} {{index .metadata.annotations "cert-manager.io/issuer-name"}}{{"\n"}}{{end}}'

# 4. The boot line. Read 2026-10-09: "client_auth":"off", "watching":4, and the
#    "names a client CA but ... is `off`" WARN.
kubectl --context kind-yadgar -n yadgar logs deploy/task-db | grep -E 'task-db listening|names a client CA'

# 5. Unreadable: 0 for all 7. Read 2026-10-09: 0 for all 7.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service)(yadgar_rotation_watched_files_unreadable)'

# 6. The task path's call counter. Read 2026-10-09: NO series in the window
#    (Prometheus keeps about 26 h here; the last task call was 2026-10-08 17:59Z).
#    task-db itself exports no `yadgar_calls_total` series, even for calls that
#    task counts as OK. That is observed, not explained.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service%2Ctool%2Coutcome)(yadgar_calls_total%7Bservice%3D~%22task%7Ctask-db%22%7D)'
```

### Live refusal probe — NEEDS-MAX, operator-run

A port-forward and a local `openssl` are a cluster action (`port-forward`
opens a tunnel into the pod). A throwaway pod in namespace `yadgar` is a
cluster mutation. Neither is run by an agent. Never use a pod that carries
`app: task`: the Service selects that label, so the pod takes real traffic.

```bash
# Terminal 1: a tunnel to ONE task-db pod. Leave it running.
kubectl --context kind-yadgar -n yadgar port-forward \
  "$(kubectl --context kind-yadgar -n yadgar get pod -l app=task-db -o name | head -1)" 15051:50051

# Terminal 2, BEFORE the merge (mode `off`): the tunnel works, and the server
# asks for no certificate. Expect CONNECTED, `ALPN protocol: h2`, and NO line
# "Acceptable client certificate CA names". If the handshake fails here, the
# tunnel is the problem, not the mode: STOP.
openssl s_client -connect 127.0.0.1:15051 -servername task-db.yadgar.svc -alpn h2 </dev/null 2>&1 \
  | grep -E 'CONNECTED|ALPN|Acceptable client certificate CA names|No client certificate CA names|alert'

# A leaf from a CA made on the spot (the "wrong anchor"). Local files only.
cd "$(mktemp -d)"
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 1 \
  -subj '/CN=b-u8a-probe-foreign-ca' -keyout ca.key -out ca.crt
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
  -subj '/CN=b-u8a-probe-leaf' -keyout leaf.key -out leaf.csr
printf 'keyUsage=critical,digitalSignature\nextendedKeyUsage=clientAuth\n' > leaf.ext
openssl x509 -req -in leaf.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 1 \
  -extfile leaf.ext -out leaf.crt
```

After the merge, when `task-db` is 2/2/2 at `optional` (restart the tunnel:
the old pod is gone):

```bash
# P1. No certificate → ACCEPTED. Expect "Acceptable client certificate CA names"
#     (the server now asks), a cipher, and NO alert.
openssl s_client -connect 127.0.0.1:15051 -servername task-db.yadgar.svc -alpn h2 -tls1_2 </dev/null 2>&1 \
  | grep -E 'CONNECTED|Acceptable client certificate CA names|Cipher is|alert'

# P2. A leaf from the wrong anchor → REFUSED. TLS 1.2 refuses inside the
#     handshake: expect an alert ("unknown ca" or "bad certificate") and
#     "Cipher is (NONE)".
openssl s_client -connect 127.0.0.1:15051 -servername task-db.yadgar.svc -alpn h2 -tls1_2 \
  -cert leaf.crt -key leaf.key </dev/null 2>&1 | grep -E 'Cipher is|alert'

# P2, TLS 1.3. The client finishes its side first, and the refusal arrives
# as an alert on the first read. Expect an alert line before the timeout.
# No alert at all means the leaf was ACCEPTED: STOP and revert.
(sleep 3) | timeout 8 openssl s_client -connect 127.0.0.1:15051 -servername task-db.yadgar.svc \
  -alpn h2 -tls1_3 -cert leaf.crt -key leaf.key -ign_eof 2>&1 | grep -E 'alert|Cipher is'
```

If `-tls1_2` fails at once with "protocol version" and no other alert, the
listener accepts TLS 1.3 only. Then use the TLS 1.3 forms for P1 as well.
Stop the tunnel and delete the temp directory afterwards.

### After this merge — read-only

```bash
# 1. yadgar Synced/Healthy at 0.19.1, and its last operation Succeeded.
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.spec.source.targetRevision} {.status.sync.status}/{.status.health.status} {.status.operationState.phase}{"\n"}'

# 2. Judge by Deployment readiness, not by the operation phase. Expect
#    task-db 2/2/2 with AUTH `optional` and new pod start times; task 2/2/2 at `off`.
kubectl --context kind-yadgar -n yadgar get deploy task-db task \
  -o custom-columns='NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas,UPDATED:.status.updatedReplicas,AUTH:.spec.template.spec.containers[0].env[?(@.name=="LISTEN_TLS_CLIENT_AUTH")].value'
kubectl --context kind-yadgar -n yadgar get pods -l app=task-db \
  -o custom-columns=NAME:.metadata.name,START:.status.startTime,READY:.status.containerStatuses[0].ready

# 3. The CA file is READ and WATCHED. Expect, on each new pod,
#    "client_auth":"optional" and "watching":5, and NO "names a client CA" WARN.
for p in $(kubectl --context kind-yadgar -n yadgar get pod -l app=task-db -o name); do
  kubectl --context kind-yadgar -n yadgar logs "$p" | grep -E 'task-db listening|names a client CA'
done

# 4. Unreadable: expect 0 for task-db (and for all 7).
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service)(yadgar_rotation_watched_files_unreadable)'

# 5. The serving gauge: still 2 for task-db.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=count%20by%20(service%2Ckind)(yadgar_tls_certificate_not_after_seconds%7Bkind%3D%22serving%22%7D)'
```

**The task path — what can and cannot be checked.**

- The estate smoke rows C-01 (iam path) and C-10 (task path) are the card's
  functional acceptance. They CANNOT run today: the estate verdict gate is red
  until the settled-state gate produces a `settled-verdict` artifact (ledger
  675, stage 3). Do not read a red estate run as this change.
- No passive signal exists. Nothing calls `task` on kind-yadgar between smoke
  runs, so no request crosses task → task-db after the roll on its own. The
  roll closes `task`'s open HTTP/2 connections to the old pods. The FIRST
  request after the roll is the first live check of `task-client-tls`.
- `yadgar_dial_upstream_never_resolved{app="task",upstream="task-db"}` reads 0.
  That proves name resolution only, not a handshake.
- **Substitute live signal (NEEDS-MAX, operator-run).** Send one task request
  through the gateway with your own client, for example a `find_tasks` from the
  yadgar client. Then read the counter. Expect a NEW `outcome="OK"` sample for
  `service="task"` (FindTasks), and no `UNAVAILABLE` or `INTERNAL`. A refused
  `task` leaf is a transport failure: it shows as a non-OK outcome on `task`'s
  call (`UNAVAILABLE` is the likely code; not measured).

```bash
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service%2Ctool%2Coutcome)(yadgar_calls_total%7Bservice%3D%22task%22%7D)'
```

If a `task-db` pod does not become Ready, the rollout stops behind
`maxUnavailable: 0` and the old pods keep serving. Read its log first: a CA
file that cannot be read or parsed refuses the boot and names the path.

### Rollback — a revert

If `yadgar`'s operation is still Running or retrying, end it first:

```bash
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.status.operationState.phase} {.status.operationState.syncResult.revision}{"\n"}'
```

NEEDS-MAX: `argocd app terminate-op yadgar` against kind-yadgar's Argo CD. A
revert does not interrupt an operation already running (see "The yadgar pin
moves to parent chart 0.13.13", Rollback).

Then revert the WHOLE merge commit: the value, the K3 table, the two gate
files and this section move together. A values-only revert reddens K3 and
`test_every_server_states_its_client_auth_and_stages_its_own_ca`. The revert
also removes the two ledger 1392 assertions (the `client-ca` items and the
non-vacuous issuer check); re-land them on their own if they are wanted.
`task-db` rolls back to `off`: it stops asking for a certificate, and drops
the CA from its watch set. Nothing is created, so nothing is left to prune.

Break-glass, if git cannot merge in time (NEEDS-MAX): suspend auto-sync on
`yadgar`, then
`kubectl --context kind-yadgar -n yadgar set env deployment/task-db LISTEN_TLS_CLIENT_AUTH=off`.
`off` is the only valid emergency value. Deleting the variable is a boot
refusal (ADR-0854). Revert in git afterwards.

## iam-db and project-db ask their callers for a client certificate: `clientAuth: "optional"` (B-U8b, ledger 925)

**What the merge does.** It changes two values in `applications/yadgar.yaml`:
`iam-db.tls.clientAuth` and `project-db.tls.clientAuth`, each from `"off"` to
`"optional"`. `task-db` stays at `"optional"` (B-U8a). `iam`, `task` and
`project` stay at `"off"`. The pin stays at 0.19.1. PB-2 already staged both
client CAs (`client-ca` volume from `<server>-tls`, item `ca.crt`, and
`LISTEN_TLS_CLIENT_CA_FILE`), so this is a two-key change.

Merging is the deploy: `yadgar` syncs on its own (`selfHeal`, no `prune`).
Measured 2026-10-09 at base `0b67914` with helm 3.18.4 and 4.3.0
(byte-identical renders), and with a server-side dry run against
`kind-yadgar`:

- 90 → 90 objects. 0 added. 0 removed.
- 2 changed: `Deployment/iam-db` and `Deployment/project-db`. In each, one env
  value moves: `LISTEN_TLS_CLIENT_AUTH` `off` → `optional`. Nothing else in
  them moves.
- The dry run names only those two Deployments (the env value and
  `generation`). It admits all 77 tracked objects and all 13 hooks. The
  `yadgar` Application CR diff is the two values.

**What runs.** `iam-db` and `project-db` roll in the same sync, each
`maxSurge: 1 / maxUnavailable: 0`, 2 replicas. They are two independent hops:
judge each by its own readiness. No other Deployment rolls. Under `optional`,
`yadgar_lifecycle::serve_tls` (lifecycle v0.2.20; iam-db v0.10.0, project-db
v0.6.0):

- reads `LISTEN_TLS_CLIENT_CA_FILE` at boot, and refuses to boot if the file
  is unreadable, unparsable, or holds no certificate;
- builds a client verifier with that file as its only anchor
  (`client_ca_root`) and `client_auth_optional(true)`;
- asks every caller for a certificate. A caller that sends none is accepted.
  A caller that sends one has it verified: a leaf from another CA, or an
  expired leaf, is refused;
- adds the CA file to the rotation watch set.

**The callers.** Each server has one caller.

| Server       | NetworkPolicy admits (port 50051) | Caller presents      | Caller env                                      |
| ------------ | --------------------------------- | -------------------- | ----------------------------------------------- |
| `iam-db`     | `app: iam` only                   | `iam-client-tls`     | `IAM_DB_TLS_CLIENT_CERT_FILE` / `_KEY_FILE`     |
| `project-db` | `app: project` only               | `project-client-tls` | `PROJECT_DB_TLS_CLIENT_CERT_FILE` / `_KEY_FILE` |

`iam` v0.10.0 and `project` v0.3.0 hand that identity to
`yadgar_dial::connect_tls` through `UpstreamTls::options()`, and register it
as `Presented::Client` in their watch sets. Both leaves and both server leaves
come from `Issuer/yadgar-internal-ca`, so the CA in each server's `ca.crt`
issued its caller's leaf. Both client leaves carry the `client auth` usage.
The NetworkPolicy is a render fact. Live corroboration (the control is the
NetworkPolicy, enforced per ledger 511): on both servers, every
`yadgar_calls_total` series is an RPC that the named caller makes (`GetKeyIdentity`, `SetKeyIdentity` on
`iam-db`; `ListProjects` on `project-db`).

**`optional` is not a control.** Anyone can send no certificate. This step
is the first live use of `iam`'s and `project`'s leaves: if a leaf is wrong
or expired, that hop fails now. Only `required` (B-U9.2) proves that the
leaf is on the wire.

**Rotation.** The CA file now joins each watch set (`File::read`). Readiness
does not change: both readiness probes are `tcpSocket` on `grpc`, which no
client-certificate mode touches. A leaf renewal rewrites `ca.crt` with the
same bytes, so it does not cause a second restart. Only a new CA changes the
file. The process then drains and exits 0, as for a leaf rotation.

**Things that change in the log, and are not faults.** The boot WARN
"`LISTEN_TLS_CLIENT_CA_FILE` names a client CA but `LISTEN_TLS_CLIENT_AUTH`
is `off` …" goes away on both servers. The `iam-db listening` and
`project-db listening` lines read `"watching":5` (4 before: serving
certificate, key, database password and rotation configuration; the CA is the
fifth file). This is a prediction from source, not yet observed.

**THESE TWO BOOT LINES CARRY NO `client_auth` FIELD.** Unlike `task-db`
v0.9.0, the `listening` line in iam-db v0.10.0 and project-db v0.6.0 logs
only `tls` and `watching`. The mode is read from the Deployment env, and the
log evidence is the WARN gone plus `"watching":5`.

### Before this merge — read-only

`--context kind-yadgar` on every line. This host's default context is a
production cluster.

```bash
# 1. yadgar Synced/Healthy at 0.19.1. Any other state → STOP.
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.spec.source.targetRevision} {.status.sync.status}/{.status.health.status} {.status.operationState.phase}{"\n"}'

# 2. iam-db and project-db 2/2/2 at `off`, with the CA staged.
#    Read 2026-10-09: iam-db 2 2 2 off iam-db-tls; project-db 2 2 2 off project-db-tls.
kubectl --context kind-yadgar -n yadgar get deploy iam-db project-db iam project \
  -o custom-columns='NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas,UPDATED:.status.updatedReplicas,AUTH:.spec.template.spec.containers[0].env[?(@.name=="LISTEN_TLS_CLIENT_AUTH")].value,CA:.spec.template.spec.volumes[?(@.name=="client-ca")].secret.secretName'

# 3. The server leaves and the callers' leaves come from one Issuer. NAMES ONLY.
#    Read 2026-10-09: all four `yadgar-internal-ca` (Issuer), all Ready.
kubectl --context kind-yadgar -n yadgar get certificate iam-db-tls project-db-tls iam-client-tls project-client-tls \
  -o custom-columns='NAME:.metadata.name,ISSUER:.spec.issuerRef.name,KIND:.spec.issuerRef.kind,READY:.status.conditions[?(@.type=="Ready")].status,NOTAFTER:.status.notAfter,USAGES:.spec.usages'
kubectl --context kind-yadgar -n yadgar get secret iam-db-tls project-db-tls iam-client-tls project-client-tls \
  -o go-template='{{range .items}}{{.metadata.name}} {{index .metadata.annotations "cert-manager.io/issuer-name"}}{{"\n"}}{{end}}'

# 4. The boot lines. Read 2026-10-09: "watching":4 and the
#    "names a client CA but ... is `off`" WARN, on all four pods.
for d in iam-db project-db; do
  for p in $(kubectl --context kind-yadgar -n yadgar get pod -l app=$d -o name); do
    kubectl --context kind-yadgar -n yadgar logs "$p" | grep -E "$d listening|names a client CA"
  done
done

# 5. Unreadable: 0 for all 7. Read 2026-10-09: 0 for all 7.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service)(yadgar_rotation_watched_files_unreadable)'

# 6. The passive call series, per pod. Read 2026-10-09, about 2.7/min for each
#    iam-db tool and about 2/min for project-db ListProjects, all on the old pods.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service%2Cpod%2Ctool%2Coutcome)(yadgar_calls_total%7Bservice%3D~%22iam-db%7Cproject-db%7Cproject%22%7D)'
```

### Live refusal probe — NEEDS-MAX, operator-run

A port-forward and a local `openssl` are a cluster action (`port-forward`
opens a tunnel into the pod). A throwaway pod in namespace `yadgar` is a
cluster mutation. Neither is run by an agent. Never use a pod that carries
`app: iam` or `app: project`: the Services select those labels, so the pod
takes real traffic.

```bash
# Terminal 1: a tunnel to ONE pod of each server. Leave both running.
kubectl --context kind-yadgar -n yadgar port-forward \
  "$(kubectl --context kind-yadgar -n yadgar get pod -l app=iam-db -o name | head -1)" 15052:50051
# Terminal 2:
kubectl --context kind-yadgar -n yadgar port-forward \
  "$(kubectl --context kind-yadgar -n yadgar get pod -l app=project-db -o name | head -1)" 15053:50051

# Terminal 3, BEFORE the merge (mode `off`): each tunnel works, and the server
# asks for no certificate. Expect CONNECTED, `ALPN protocol: h2`, and NO line
# "Acceptable client certificate CA names". If a handshake fails here, the
# tunnel is the problem, not the mode: STOP.
openssl s_client -connect 127.0.0.1:15052 -servername iam-db.yadgar.svc -alpn h2 </dev/null 2>&1 \
  | grep -E 'CONNECTED|ALPN|Acceptable client certificate CA names|No client certificate CA names|alert'
openssl s_client -connect 127.0.0.1:15053 -servername project-db.yadgar.svc -alpn h2 </dev/null 2>&1 \
  | grep -E 'CONNECTED|ALPN|Acceptable client certificate CA names|No client certificate CA names|alert'

# A leaf from a CA made on the spot (the "wrong anchor"). Local files only.
cd "$(mktemp -d)"
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 1 \
  -subj '/CN=b-u8b-probe-foreign-ca' -keyout ca.key -out ca.crt
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
  -subj '/CN=b-u8b-probe-leaf' -keyout leaf.key -out leaf.csr
printf 'keyUsage=critical,digitalSignature\nextendedKeyUsage=clientAuth\n' > leaf.ext
openssl x509 -req -in leaf.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 1 \
  -extfile leaf.ext -out leaf.crt
```

After the merge, when both servers are 2/2/2 at `optional` (restart both
tunnels: the old pods are gone). Run each probe against BOTH servers:
`15052` / `iam-db.yadgar.svc` and `15053` / `project-db.yadgar.svc`.

```bash
for t in 15052:iam-db 15053:project-db; do
  port=${t%%:*}; name=${t#*:}.yadgar.svc
  echo "== $name"
  # P1. No certificate → ACCEPTED. Expect "Acceptable client certificate CA
  #     names" (the server now asks), a cipher, and NO alert.
  openssl s_client -connect 127.0.0.1:$port -servername $name -alpn h2 -tls1_2 </dev/null 2>&1 \
    | grep -E 'CONNECTED|Acceptable client certificate CA names|Cipher is|alert'
  # P2. A leaf from the wrong anchor → REFUSED. TLS 1.2 refuses inside the
  #     handshake: expect an alert ("unknown ca" or "bad certificate") and
  #     "Cipher is (NONE)".
  openssl s_client -connect 127.0.0.1:$port -servername $name -alpn h2 -tls1_2 \
    -cert leaf.crt -key leaf.key </dev/null 2>&1 | grep -E 'Cipher is|alert'
  # P2, TLS 1.3. The client finishes its side first, and the refusal arrives
  # as an alert on the first read. Expect an alert line before the timeout.
  # No alert at all means the leaf was ACCEPTED: STOP and revert.
  (sleep 3) | timeout 8 openssl s_client -connect 127.0.0.1:$port -servername $name \
    -alpn h2 -tls1_3 -cert leaf.crt -key leaf.key -ign_eof 2>&1 | grep -E 'alert|Cipher is'
done
```

If `-tls1_2` fails at once with "protocol version" and no other alert, the
listener accepts TLS 1.3 only. Then use the TLS 1.3 forms for P1 as well.
Stop both tunnels and delete the temp directory afterwards.

### After this merge — read-only

```bash
# 1. yadgar Synced/Healthy at 0.19.1, and its last operation Succeeded.
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.spec.source.targetRevision} {.status.sync.status}/{.status.health.status} {.status.operationState.phase}{"\n"}'

# 2. Judge by Deployment readiness, not by the operation phase. Expect
#    iam-db and project-db each 2/2/2 with AUTH `optional` and new pod start
#    times; iam and project 2/2/2 at `off`, NOT rolled.
kubectl --context kind-yadgar -n yadgar get deploy iam-db project-db iam project \
  -o custom-columns='NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas,UPDATED:.status.updatedReplicas,AUTH:.spec.template.spec.containers[0].env[?(@.name=="LISTEN_TLS_CLIENT_AUTH")].value'
kubectl --context kind-yadgar -n yadgar get pods -l 'app in (iam-db,project-db)' \
  -o custom-columns=NAME:.metadata.name,START:.status.startTime,READY:.status.containerStatuses[0].ready

# 3. The CA file is READ and WATCHED. Expect, on each of the four new pods,
#    "watching":5 and NO "names a client CA" WARN. (No `client_auth` field:
#    these two binaries do not log it.)
for d in iam-db project-db; do
  for p in $(kubectl --context kind-yadgar -n yadgar get pod -l app=$d -o name); do
    kubectl --context kind-yadgar -n yadgar logs "$p" | grep -E "$d listening|names a client CA"
  done
done

# 4. Unreadable: expect 0 for iam-db and project-db (and for all 7).
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service)(yadgar_rotation_watched_files_unreadable)'

# 5. The serving gauge: still 2 for iam-db and 2 for project-db.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=count%20by%20(service%2Ckind)(yadgar_tls_certificate_not_after_seconds%7Bkind%3D%22serving%22%7D)'
```

**The two hops — the passive signal for each.** Unlike `task` → `task-db`,
both hops carry traffic on their own, so the new pods are checked within a
few minutes of becoming Ready, with no operator request; judge by two reads
at least 5 minutes apart. A failed
handshake never reaches the server's handler, so an `OK` call counted by a
NEW server pod is a call that crossed the hop after the roll.

- **`iam` → `iam-db`.** Signal: `yadgar_calls_total{service="iam-db",tool="GetKeyIdentity",outcome="OK"}`
  under the NEW `iam-db` pod names, growing. Its source is a standing defect,
  not normal traffic: `iam`'s key-identity task retries for ever, because
  `iam-db` answers `SetKeyIdentity` with `FAILED_PRECONDITION`, reason
  "the store holds rows and no marker" (read 2026-10-09; `iam` logs an ERROR
  "the key identity is UNVERIFIED …" every 300 s). Each retry is a
  `GetKeyIdentity` (OK), then a `SetKeyIdentity` (FAILED_PRECONDITION),
  about 2.7/min for each. Both are
  application answers, so both prove the handshake. If the marker defect is
  fixed first, this signal stops, and the hop has no passive traffic. The
  failure shape: the `reason` in `iam`'s ERROR line changes from that
  FAILED_PRECONDITION text to a transport reason, and the new `iam-db` pods
  count no calls.
- **`project` → `project-db`.** Signal: `yadgar_calls_total{service="project-db",tool="ListProjects",outcome="OK"}`
  under the NEW `project-db` pod names, growing. Its source is the gateway's
  project-registry poll (`YADGAR_PROJECT_REGISTRY_POLL_SECONDS` 60, on each
  gateway pod): gateway calls `project`'s `ListProjects`, and `project` calls
  `project-db`'s `ListProjects` once for each one (read 2026-10-09: about
  2/min on each, 1:1). The failure shape: `project`'s `ListProjects` shows a
  non-OK outcome (`UNAVAILABLE` is the likely code; not measured), and the
  gateway logs its registry-load failure.
- **`yadgar_gateway_project_registry_loaded` does NOT prove this hop after the
  roll (ledger 1395).** It is a gateway → `project` signal, and the poll does
  cross `project` → `project-db`. But the gauge means "EVER loaded" (gateway
  v0.10.2 `src/project/registry.rs`): a later failed reload leaves it at 1.
  The gateway does not roll here, so the gauge stays 1 whatever the new
  `project-db` pods do. Read the `project-db` counter above instead.
- **ledger 1393 is task-db only.** `iam-db` and `project-db` both export
  `yadgar_calls_total` (and `yadgar_call_duration_seconds`,
  `yadgar_bytes_returned_total`); `task-db` exports none of them.
- The estate smoke rows C-01 (iam path) and C-10 (task path) are the card's
  functional acceptance. They CANNOT run today: the estate verdict gate is red
  until the settled-state gate produces a `settled-verdict` artifact (ledger
  675, stage 3). Do not read a red estate run as this change.

```bash
# Expect NEW pod names on both servers, outcome OK, growing between two reads a
# few minutes apart; no new non-OK outcome on `project`.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service%2Cpod%2Ctool%2Coutcome)(yadgar_calls_total%7Bservice%3D~%22iam-db%7Cproject-db%7Cproject%22%7D)'
for p in $(kubectl --context kind-yadgar -n yadgar get pod -l app=iam -o name); do
  kubectl --context kind-yadgar -n yadgar logs --since=15m "$p" | grep -E 'key identity' | tail -2
done
```

If a pod of either server does not become Ready, its rollout stops behind
`maxUnavailable: 0` and its old pods keep serving. The other server's rollout
is independent. Read the log first: a CA file that cannot be read or parsed
refuses the boot and names the path.

### Rollback — a revert

If `yadgar`'s operation is still Running or retrying, end it first:

```bash
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.status.operationState.phase} {.status.operationState.syncResult.revision}{"\n"}'
```

NEEDS-MAX: `argocd app terminate-op yadgar` against kind-yadgar's Argo CD. A
revert does not interrupt an operation already running (see "The yadgar pin
moves to parent chart 0.13.13", Rollback).

Then revert the WHOLE merge commit: the two values, the K3 table, the gate
test and this section move together. A values-only revert reddens K3 and
`test_every_server_states_its_client_auth_and_stages_its_own_ca`. The revert
returns `EXPECTED_CLIENT_AUTH` to `task-db` alone at `optional`; it does not
touch B-U8a. Both servers roll back to `off`: they stop asking for a
certificate and drop the CA from their watch sets. Nothing is created, so
nothing is left to prune. One hop alone cannot be reverted with `git revert`;
for that, write a new PR that sets one value back to `"off"`, with its own K3
line and map entry.

Break-glass, if git cannot merge in time (NEEDS-MAX): suspend auto-sync on
`yadgar`, then, for the failing server or both:

```bash
kubectl --context kind-yadgar -n yadgar set env deployment/iam-db LISTEN_TLS_CLIENT_AUTH=off
kubectl --context kind-yadgar -n yadgar set env deployment/project-db LISTEN_TLS_CLIENT_AUTH=off
```

`off` is the only valid emergency value. Deleting the variable is a boot
refusal (ADR-0854). Revert in git afterwards.

## project asks its caller for a client certificate: `clientAuth: "optional"` (B-U8c, ledger 925)

**What the merge does.** It changes one value in `applications/yadgar.yaml`:
`project.tls.clientAuth`, from `"off"` to `"optional"`. `task-db` (B-U8a),
`iam-db` and `project-db` (B-U8b) stay at `"optional"`. `iam` and `task` stay
at `"off"`. The pin stays at 0.19.1. PB-2 already staged the client CA
(`client-ca` volume from `project-tls`, item `ca.crt`, and
`LISTEN_TLS_CLIENT_CA_FILE`), so this is a one-key change.

Merging is the deploy: `yadgar` syncs on its own (`selfHeal`, no `prune`).
Measured 2026-10-09 at base `da44a10` with helm 3.18.4 and 4.3.0
(byte-identical renders), and with a server-side dry run against
`kind-yadgar`:

- 90 → 90 objects. 0 added. 0 removed.
- 1 changed: `Deployment/project`. One env value moves:
  `LISTEN_TLS_CLIENT_AUTH` `off` → `optional`. Nothing else in it moves.
- The dry run names only `Deployment/project` (the env value and
  `generation`). It admits all 77 tracked objects and all 13 hooks. The
  `yadgar` Application CR diff is the one value.

**What runs.** `project` rolls, `maxSurge: 1 / maxUnavailable: 0`, 2
replicas. No other Deployment rolls; the gateway does NOT roll. Under
`optional`, `yadgar_lifecycle::serve_tls` (lifecycle v0.2.20; project v0.3.0):

- reads `LISTEN_TLS_CLIENT_CA_FILE` at boot, and refuses to boot if the file
  is unreadable, unparsable, or holds no certificate;
- builds a client verifier with that file as its only anchor
  (`client_ca_root`) and `client_auth_optional(true)`;
- asks every caller for a certificate. A caller that sends none is accepted.
  A caller that sends one has it verified: a leaf from another CA, or an
  expired leaf, is refused;
- adds the CA file to the rotation watch set.

**The caller.** `project` has one caller on its gRPC port, 50052.

| Server    | NetworkPolicy admits (port 50052) | Caller presents      | Caller env                                   |
| --------- | --------------------------------- | -------------------- | -------------------------------------------- |
| `project` | `app: gateway` only               | `gateway-client-tls` | `PROJECT_TLS_CLIENT_CERT_FILE` / `_KEY_FILE` |

`gateway` v0.10.2 reads the `PROJECT` prefix through `UpstreamTls::from_env`
and dials `project` with `yadgar_dial::connect_tls(.., &tls.options())`;
`options()` adds the identity, and dial v0.2.14 hands it to tonic as
`Identity::from_pem`. The gateway mounts ONE client leaf,
`gateway-client-tls`, for all three upstreams (task, iam, project). It is
NOT `gateway-tls`: that is the edge's serving leaf, from
`ClusterIssuer/yadgar-dev-ca`, and it is never presented to `project`.

`gateway-client-tls` and `project-tls` both come from
`Issuer/yadgar-internal-ca`, so the CA in `project-tls`'s `ca.crt` is the CA
that issued the gateway's leaf. Read 2026-10-09: the CA Certificate is at
revision 1, `notBefore` 2026-09-05T12:40:48Z, before both leaves
(`gateway-client-tls` 2026-09-05T12:40:52Z, `project-tls`
2026-09-07T13:01:34Z), so no CA rotation sits between the two issuances. The
leaf carries the `client auth` usage.

No other workload dials `project`: `task` v0.7.0 and `iam` v0.10.0 have no
`project` upstream, no hook Job references it, and the `estate-front` and
`post-merge-verifier` egress policies allow no yadgar pod on 50052. The
NetworkPolicy is the control (enforced per ledger 511). Live corroboration:
every `yadgar_calls_total{service="project"}` series is `ListProjects`, the
one RPC the gateway's registry poll makes, at about 1/min on each pod (two
gateway pods, a 60 s poll each). The readiness probe (`tcpSocket` on `grpc`)
and the Prometheus scrape (9090, plain HTTP) do not complete a TLS handshake
on 50052, so the mode does not touch them.

**`optional` is not a control.** Anyone can send no certificate. This step
is the first live use of `gateway-client-tls` against a verifying server: if
the leaf is wrong or expired, this hop fails now. An `OK` call proves only
that the handshake completed: either the leaf verified, or the gateway sent
none. That the gateway sends its leaf is a source fact, not an observation.
Only `required` (B-U9.3) proves that the leaf is on the wire.

**Rotation.** The CA file now joins the watch set (`File::read`). A leaf
renewal rewrites `ca.crt` with the same bytes, so it does not cause a second
restart. Only a new CA changes the file. The process then drains and exits 0,
as for a leaf rotation.

**Things that change in the log, and are not faults.** The boot WARN
"`LISTEN_TLS_CLIENT_CA_FILE` names a client CA but `LISTEN_TLS_CLIENT_AUTH`
is `off` …" goes away. The `project listening` line reads `"watching":7` (6
before: serving certificate and key, the `project-db` CA, the client
certificate and key, and the rotation configuration; the client CA is the
seventh file). This is a prediction from source, not yet observed.

**THE BOOT LINE CARRIES NO `client_auth` FIELD.** project v0.3.0's
`project listening` line logs only `addr`, `tls`, `watching` and the rotation
and drain settings. The mode is read from the Deployment env, and the log
evidence is the WARN gone plus `"watching":7`.

### Before this merge — read-only

`--context kind-yadgar` on every line. This host's default context is a
production cluster.

```bash
# 1. yadgar Synced/Healthy at 0.19.1. Any other state → STOP.
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.spec.source.targetRevision} {.status.sync.status}/{.status.health.status} {.status.operationState.phase}{"\n"}'

# 2. project 2/2/2 at `off`, with the CA staged; the gateway presents its
#    client leaf to project. Read 2026-10-09: project 2 2 2 off project-tls.
kubectl --context kind-yadgar -n yadgar get deploy project gateway \
  -o custom-columns='NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas,UPDATED:.status.updatedReplicas,AUTH:.spec.template.spec.containers[0].env[?(@.name=="LISTEN_TLS_CLIENT_AUTH")].value,CA:.spec.template.spec.volumes[?(@.name=="client-ca")].secret.secretName,LEAF:.spec.template.spec.volumes[?(@.name=="client-cert")].secret.secretName'
kubectl --context kind-yadgar -n yadgar get deploy gateway \
  -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}' \
  | grep -E '^PROJECT_|VALIDATION_MODE'

# 3. The server leaf and the caller's leaf come from one Issuer, and no CA
#    rotation sits between them. Certificate objects only: NAMES AND STATUS.
#    Read 2026-10-09: both `yadgar-internal-ca` (Issuer), Ready; CA revision 1,
#    notBefore before both leaves.
kubectl --context kind-yadgar -n yadgar get certificate yadgar-internal-ca gateway-client-tls project-tls \
  -o custom-columns='NAME:.metadata.name,ISSUER:.spec.issuerRef.name,KIND:.spec.issuerRef.kind,READY:.status.conditions[?(@.type=="Ready")].status,NOTBEFORE:.status.notBefore,REV:.status.revision,USAGES:.spec.usages'

# 4. The boot lines. Read 2026-10-09: "watching":6 and the
#    "names a client CA but ... is `off`" WARN, on both pods.
for p in $(kubectl --context kind-yadgar -n yadgar get pod -l app=project -o name); do
  kubectl --context kind-yadgar -n yadgar logs "$p" | grep -E "project listening|names a client CA"
done

# 5. Unreadable: 0 for all 7. Read 2026-10-09: 0 for all 7.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service)(yadgar_rotation_watched_files_unreadable)'

# 6. The passive call series, per pod. Read 2026-10-09: ListProjects OK only,
#    about 1/min on each project pod.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service%2Cpod%2Ctool%2Coutcome)(yadgar_calls_total%7Bservice%3D~%22project%7Cproject-db%22%7D)'

# 7. The gateway's registry refresh. Read 2026-10-09: one "is loaded" INFO per
#    gateway pod at its boot, and NO "could not be refreshed" WARN.
for p in $(kubectl --context kind-yadgar -n yadgar get pod -l app=gateway -o name); do
  kubectl --context kind-yadgar -n yadgar logs "$p" | grep -E 'project registry' | tail -3
done
```

### Live refusal probe — NEEDS-MAX, operator-run

A port-forward and a local `openssl` are a cluster action (`port-forward`
opens a tunnel into the pod). A throwaway pod in namespace `yadgar` is a
cluster mutation. Neither is run by an agent. Never use a pod that carries
`app: gateway`: the edge's Service selects that label, so the pod takes real
traffic.

```bash
# Terminal 1: a tunnel to ONE project pod. Leave it running.
kubectl --context kind-yadgar -n yadgar port-forward \
  "$(kubectl --context kind-yadgar -n yadgar get pod -l app=project -o name | head -1)" 15054:50052

# Terminal 2, BEFORE the merge (mode `off`): the tunnel works, and the server
# asks for no certificate. Expect CONNECTED, `ALPN protocol: h2`, and NO line
# "Acceptable client certificate CA names". If the handshake fails here, the
# tunnel is the problem, not the mode: STOP.
openssl s_client -connect 127.0.0.1:15054 -servername project.yadgar.svc -alpn h2 </dev/null 2>&1 \
  | grep -E 'CONNECTED|ALPN|Acceptable client certificate CA names|No client certificate CA names|alert'

# A leaf from a CA made on the spot (the "wrong anchor"). Local files only.
cd "$(mktemp -d)"
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 1 \
  -subj '/CN=b-u8c-probe-foreign-ca' -keyout ca.key -out ca.crt
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
  -subj '/CN=b-u8c-probe-leaf' -keyout leaf.key -out leaf.csr
printf 'keyUsage=critical,digitalSignature\nextendedKeyUsage=clientAuth\n' > leaf.ext
openssl x509 -req -in leaf.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 1 \
  -extfile leaf.ext -out leaf.crt
```

After the merge, when `project` is 2/2/2 at `optional` (restart the tunnel:
the old pods are gone):

```bash
# P1. No certificate → ACCEPTED. Expect "Acceptable client certificate CA
#     names" (the server now asks), a cipher, and NO alert.
openssl s_client -connect 127.0.0.1:15054 -servername project.yadgar.svc -alpn h2 -tls1_2 </dev/null 2>&1 \
  | grep -E 'CONNECTED|Acceptable client certificate CA names|Cipher is|alert'
# P2. A leaf from the wrong anchor → REFUSED. TLS 1.2 refuses inside the
#     handshake: expect an alert ("unknown ca" or "bad certificate") and
#     "Cipher is (NONE)".
openssl s_client -connect 127.0.0.1:15054 -servername project.yadgar.svc -alpn h2 -tls1_2 \
  -cert leaf.crt -key leaf.key </dev/null 2>&1 | grep -E 'Cipher is|alert'
# P2, TLS 1.3. The client finishes its side first, and the refusal arrives
# as an alert on the first read. Expect an alert line before the timeout.
# No alert at all means the leaf was ACCEPTED: STOP and revert.
(sleep 3) | timeout 8 openssl s_client -connect 127.0.0.1:15054 -servername project.yadgar.svc \
  -alpn h2 -tls1_3 -cert leaf.crt -key leaf.key -ign_eof 2>&1 | grep -E 'alert|Cipher is'
```

If `-tls1_2` fails at once with "protocol version" and no other alert, the
listener accepts TLS 1.3 only. Then use the TLS 1.3 form for P1 as well.
Stop the tunnel and delete the temp directory afterwards.

### After this merge — read-only

```bash
# 1. yadgar Synced/Healthy at 0.19.1, and its last operation Succeeded.
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.spec.source.targetRevision} {.status.sync.status}/{.status.health.status} {.status.operationState.phase}{"\n"}'

# 2. Judge by Deployment readiness, not by the operation phase. Expect
#    project 2/2/2 with AUTH `optional` and new pod start times; gateway
#    2/2/2, NOT rolled.
kubectl --context kind-yadgar -n yadgar get deploy project gateway \
  -o custom-columns='NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas,UPDATED:.status.updatedReplicas,AUTH:.spec.template.spec.containers[0].env[?(@.name=="LISTEN_TLS_CLIENT_AUTH")].value'
kubectl --context kind-yadgar -n yadgar get pods -l 'app in (project,gateway)' \
  -o custom-columns=NAME:.metadata.name,START:.status.startTime,READY:.status.containerStatuses[0].ready

# 3. The CA file is READ and WATCHED. Expect, on both new pods,
#    "watching":7 and NO "names a client CA" WARN. (No `client_auth` field:
#    this binary does not log it.)
for p in $(kubectl --context kind-yadgar -n yadgar get pod -l app=project -o name); do
  kubectl --context kind-yadgar -n yadgar logs "$p" | grep -E "project listening|names a client CA"
done

# 4. Unreadable: expect 0 for project (and for all 7).
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service)(yadgar_rotation_watched_files_unreadable)'

# 5. The serving gauge: still 2 for project.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=count%20by%20(service%2Ckind)(yadgar_tls_certificate_not_after_seconds%7Bkind%3D%22serving%22%7D)'
```

**The hop — the passive signal.** `gateway` → `project` carries traffic on
its own: each gateway pod polls the project registry every 60 s
(`YADGAR_PROJECT_REGISTRY_POLL_SECONDS`), and each poll is a `ListProjects`
call on `project`. The new pods are exercised within a few minutes (about 2
polls/min, balanced per request across both pods by dial's p2c); judge by
two reads at least 5 minutes apart. No operator request is needed. A failed handshake never reaches
the server's handler, so an `OK` call counted by a NEW `project` pod is a
call that crossed the hop after the roll.

- **Signal:** `yadgar_calls_total{service="project",tool="ListProjects",outcome="OK"}`
  under the NEW `project` pod names, growing (about 1/min on each pod).
  Under `optional` this proves the handshake completed, not that the leaf
  was presented (see above).
- **`yadgar_gateway_project_registry_loaded` does NOT prove this hop
  (ledger 1395).** It means "EVER loaded" (gateway v0.10.2
  `src/project/registry.rs`): a later failed reload leaves it at 1. The
  gateway does not roll here, so the gauge stays 1 whatever the new `project`
  pods do. The card's "registry loaded = 1" acceptance is replaced by the
  counter above.
- **The failure shape.** A refused leaf does NOT stop the rollout: the
  readiness probe is `tcpSocket`, so the new pods go Ready and the old pods
  are removed. Then the new `project` pods count no `ListProjects`, and each
  gateway pod logs, once a minute, the WARN "the project registry could not
  be refreshed; the previously loaded set stays in force" (0 such lines
  before the merge). `YADGAR_PROJECT_VALIDATION_MODE` is `counting` (live,
  2026-10-09), so a failed refresh is a degraded window, not a 503 per
  scoped call: the gateway keeps the set it already holds. Revert at once
  all the same.
- `project-db` also keeps counting `ListProjects` 1:1 with `project`; a stop
  there with `project` still OK is a `project` → `project-db` fault (B-U8b),
  not this change.
- The estate smoke rows C-01 (iam path) and C-10 (task path) are the card's
  functional acceptance. They CANNOT run today: the estate verdict gate is red
  until the settled-state gate produces a `settled-verdict` artifact (ledger
  675, stage 3). Do not read a red estate run as this change.

```bash
# Expect NEW project pod names, outcome OK, growing between two reads a few
# minutes apart; and no "could not be refreshed" WARN on either gateway pod.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service%2Cpod%2Ctool%2Coutcome)(yadgar_calls_total%7Bservice%3D~%22project%7Cproject-db%22%7D)'
for p in $(kubectl --context kind-yadgar -n yadgar get pod -l app=gateway -o name); do
  kubectl --context kind-yadgar -n yadgar logs --since=15m "$p" | grep -E 'project registry' | tail -2
done
```

If a `project` pod does not become Ready, the rollout stops behind
`maxUnavailable: 0` and the old pods keep serving. Read the log first: a CA
file that cannot be read or parsed refuses the boot and names the path.

### Rollback — a revert

If `yadgar`'s operation is still Running or retrying, end it first:

```bash
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.status.operationState.phase} {.status.operationState.syncResult.revision}{"\n"}'
```

NEEDS-MAX: `argocd app terminate-op yadgar` against kind-yadgar's Argo CD. A
revert does not interrupt an operation already running (see "The yadgar pin
moves to parent chart 0.13.13", Rollback).

Then revert the WHOLE merge commit: the value, the K3 table, the gate test
and this section move together. A values-only revert reddens K3 and
`test_every_server_states_its_client_auth_and_stages_its_own_ca`. The revert
returns `EXPECTED_CLIENT_AUTH` to the three `-db` servers at `optional`; it
does not touch B-U8a or B-U8b. `project` rolls back to `off`: it stops asking
for a certificate and drops the CA from its watch set. Nothing is created, so
nothing is left to prune.

Break-glass, if git cannot merge in time (NEEDS-MAX): suspend auto-sync on
`yadgar`, then
`kubectl --context kind-yadgar -n yadgar set env deployment/project LISTEN_TLS_CLIENT_AUTH=off`.
`off` is the only valid emergency value. Deleting the variable is a boot
refusal (ADR-0854). Revert in git afterwards.

## task asks its caller for a client certificate: `clientAuth: "optional"` (B-U8d, ledger 925)

**What the merge does.** It changes one value in `applications/yadgar.yaml`:
`task.tls.clientAuth`, from `"off"` to `"optional"`. `task-db` (B-U8a),
`iam-db` and `project-db` (B-U8b) and `project` (B-U8c) stay at
`"optional"`. `iam` stays at `"off"`. The pin stays at 0.19.1. PB-2 already
staged the client CA (`client-ca` volume from `task-tls`, item `ca.crt`, and
`LISTEN_TLS_CLIENT_CA_FILE`), so this is a one-key change.

Merging is the deploy: `yadgar` syncs on its own (`selfHeal`, no `prune`).
Measured 2026-10-09 at base `3cd2dfd` with helm 3.18.4 and 4.3.0
(byte-identical renders), and with a server-side dry run against
`kind-yadgar`:

- 90 → 90 objects. 0 added. 0 removed.
- 1 changed: `Deployment/task`. One env value moves:
  `LISTEN_TLS_CLIENT_AUTH` `off` → `optional`. Nothing else in it moves.
- The dry run names only `Deployment/task` (the env value and
  `generation`). It admits all 77 tracked objects and all 13 hooks. The
  `yadgar` Application CR diff is the one value.

**What runs.** `task` rolls, `maxSurge: 1 / maxUnavailable: 0`, 2 replicas.
No other Deployment rolls; the gateway does NOT roll. Under `optional`,
`yadgar_lifecycle::serve_tls` (lifecycle v0.2.20; task v0.7.0):

- reads `LISTEN_TLS_CLIENT_CA_FILE` at boot, and refuses to boot if the file
  is unreadable, unparsable, or holds no certificate;
- builds a client verifier with that file as its only anchor
  (`client_ca_root`) and `client_auth_optional(true)`;
- asks every caller for a certificate. A caller that sends none is accepted.
  A caller that sends one has it verified: a leaf from another CA, or an
  expired leaf, is refused;
- adds the CA file to the rotation watch set.

**The caller.** `task` has one caller on its gRPC port, 50052.

| Server | NetworkPolicy admits (port 50052) | Caller presents      | Caller env                                |
| ------ | --------------------------------- | -------------------- | ----------------------------------------- |
| `task` | `app: gateway` only               | `gateway-client-tls` | `TASK_TLS_CLIENT_CERT_FILE` / `_KEY_FILE` |

`gateway` v0.10.2 reads the `TASK` prefix through `UpstreamTls::from_env`
and dials `task` with `connect_task` → `yadgar_dial::connect_tls(..,
&tls.options())`; `options()` adds the identity, and dial v0.2.14 hands it to
tonic as `Identity::from_pem`. The rendered and live gateway env point
`TASK_TLS_CLIENT_CERT_FILE` / `_KEY_FILE` at the `client-cert` volume, which
is `gateway-client-tls`: the same leaf the gateway already presents to
`project` (B-U8c). It is NOT `gateway-tls`, the edge's serving leaf from
`ClusterIssuer/yadgar-dev-ca`, which is never presented to `task`.

The argocd gates do not pin this env (ledger 1396): they check only that the
gateway mounts `gateway-client-tls` (`CLIENT_LEAVES`), not that
`TASK_TLS_CLIENT_CERT_FILE` / `_KEY_FILE` point at it. That gate is deferred
to before B-U9. For this hop the values were read directly, in the render
and live on 2026-10-09: `/var/run/secrets/client-cert/client.crt` and
`/var/run/secrets/client-cert/client.key`, with the `client-cert` volume
from `gateway-client-tls`. Before-check 2 below reads them again.

`gateway-client-tls` and `task-tls` both come from
`Issuer/yadgar-internal-ca`, so the CA in `task-tls`'s `ca.crt` is the CA
that issued the gateway's leaf. Read 2026-10-09 (Certificate status only, no
Secret read): the CA Certificate is at revision 1, `notBefore`
2026-09-05T12:40:48Z, before both leaves (`gateway-client-tls` and
`task-tls`, both 2026-09-05T12:40:52Z, both revision 1), so no CA rotation
sits between the two issuances. The leaf carries the `client auth` usage.

No other workload dials `task`: `project` v0.3.0 and `iam` v0.10.0 have no
`task` upstream, no hook Job references it or carries `app: gateway`, and
the `estate-front` and `post-merge-verifier` egress policies allow no yadgar
pod on 50052. The NetworkPolicy is the control (enforced per ledger 511).
There is no live corroboration for this hop: no request reached `task` in
the Prometheus window (see the passive signal below). The readiness probe
(`tcpSocket` on `grpc`) and the Prometheus scrape (9090, plain HTTP) do not
complete a TLS handshake on 50052, so the mode does not touch them.

**`optional` is not a control.** Anyone can send no certificate. This step
is the first live use of `gateway-client-tls` against `task` with
verification on: if the leaf is wrong or expired, this hop fails now. An
`OK` call proves only that the handshake completed: either the leaf
verified, or the gateway sent none. That the gateway sends its leaf is a
source fact, not an observation. Only `required` (B-U9.4) proves that the
leaf is on the wire.

**The blast radius is larger than B-U8c's.** `task` carries all five task
tools (`create_task`, `read_task`, `find_tasks`, `edit_task`,
`transition_task`): `tools/call` reaches `task` through one channel
(`dispatch.rs`, `tools::call(state.task.clone(), ..)`). There is no
degraded mode like the project registry's `counting`: a refused leaf fails
every task tool call for every user, at once.

**Rotation.** The CA file now joins the watch set (`File::read`). A leaf
renewal rewrites `ca.crt` with the same bytes, so it does not cause a second
restart. Only a new CA changes the file. The process then drains and exits 0,
as for a leaf rotation.

**Things that change in the log, and are not faults.** The boot WARN
"`LISTEN_TLS_CLIENT_CA_FILE` names a client CA but `LISTEN_TLS_CLIENT_AUTH`
is `off` …" goes away. The `task listening` line reads `"watching":7` (6
before: serving certificate and key, the `task-db` CA, the client
certificate and key, and the rotation configuration; the client CA is the
seventh file). This is a prediction from source (task v0.7.0
`tests/assembly.rs::a_verifying_listener_watches_its_client_ca_and_off_does_not`),
not yet observed.

**THE BOOT LINE CARRIES NO `client_auth` FIELD.** task v0.7.0's
`task listening` line logs only `addr`, `tls`, `watching` and the rotation
and drain settings. The mode is read from the Deployment env, and the log
evidence is the WARN gone plus `"watching":7`.

### Before this merge — read-only

`--context kind-yadgar` on every line. This host's default context is a
production cluster.

```bash
# 1. yadgar Synced/Healthy at 0.19.1. Any other state → STOP.
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.spec.source.targetRevision} {.status.sync.status}/{.status.health.status} {.status.operationState.phase}{"\n"}'

# 2. task 2/2/2 at `off`, with the CA staged; the gateway presents its
#    client leaf to task. Read 2026-10-09: task 2 2 2 off task-tls.
kubectl --context kind-yadgar -n yadgar get deploy task gateway \
  -o custom-columns='NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas,UPDATED:.status.updatedReplicas,AUTH:.spec.template.spec.containers[0].env[?(@.name=="LISTEN_TLS_CLIENT_AUTH")].value,CA:.spec.template.spec.volumes[?(@.name=="client-ca")].secret.secretName,LEAF:.spec.template.spec.volumes[?(@.name=="client-cert")].secret.secretName'
kubectl --context kind-yadgar -n yadgar get deploy gateway \
  -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}' \
  | grep -E '^TASK_'

# 3. The CA and both leaves: one Issuer, and no CA rotation between them.
#    Certificate objects only: NAMES AND STATUS, no Secret read. Read
#    2026-10-09: CA revision 1, notBefore 2026-09-05T12:40:48Z; task-tls and
#    gateway-client-tls `yadgar-internal-ca` (Issuer), Ready, revision 1,
#    notBefore 2026-09-05T12:40:52Z. If the CA's revision is above 1, or its
#    notBefore is later than either leaf's: STOP.
kubectl --context kind-yadgar -n yadgar get certificate yadgar-internal-ca gateway-client-tls task-tls \
  -o custom-columns='NAME:.metadata.name,ISSUER:.spec.issuerRef.name,KIND:.spec.issuerRef.kind,READY:.status.conditions[?(@.type=="Ready")].status,NOTBEFORE:.status.notBefore,REV:.status.revision,USAGES:.spec.usages'

# 4. The boot lines. Read 2026-10-09: "watching":6 and the
#    "names a client CA but ... is `off`" WARN, on both pods.
for p in $(kubectl --context kind-yadgar -n yadgar get pod -l app=task -o name); do
  kubectl --context kind-yadgar -n yadgar logs "$p" | grep -E "task listening|names a client CA"
done

# 5. Unreadable: 0 for all 7. Read 2026-10-09: 0 for all 7.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service)(yadgar_rotation_watched_files_unreadable)'

# 6. The task call series. Read 2026-10-09: NO series on the current task
#    pods (started 2026-10-09T00:04Z), and zero increase for task in 7 days.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service%2Cpod%2Ctool%2Coutcome)(yadgar_calls_total%7Bservice%3D~%22task%7Cgateway%22%2Ctool!~%22server%2Fdiscover%22%7D)'
```

**7. Clear B-U8a's hop first — NEEDS-MAX, operator-run, before the merge.**
No request has crossed `task` → `task-db` since B-U8a synced (task-db rolled
2026-10-09T00:52Z; `task` has counted no call since). So B-U8a's acceptance
is still open. If B-U8d merges first, the first task request after it
exercises two unverified hops at once, and a failure does not say which one
broke. Send one task request through the gateway with your own client, for
example a `find_tasks` from the yadgar client, and read the step-6 query
again. Expect `service="task"`, `tool="FindTasks"`, `outcome="OK"` on a
current `task` pod. Anything else is a B-U8a fault: STOP, do not merge
B-U8d, and follow B-U8a's rollback.

### Live refusal probe — NEEDS-MAX, operator-run

A port-forward and a local `openssl` are a cluster action (`port-forward`
opens a tunnel into the pod). A throwaway pod in namespace `yadgar` is a
cluster mutation. Neither is run by an agent. Never use a pod that carries
`app: gateway`: the edge's Service selects that label, so the pod takes real
traffic.

```bash
# Terminal 1: a tunnel to ONE task pod. Leave it running.
kubectl --context kind-yadgar -n yadgar port-forward \
  "$(kubectl --context kind-yadgar -n yadgar get pod -l app=task -o name | head -1)" 15055:50052

# Terminal 2, BEFORE the merge (mode `off`): the tunnel works, and the server
# asks for no certificate. Expect CONNECTED, `ALPN protocol: h2`, and NO line
# "Acceptable client certificate CA names". If the handshake fails here, the
# tunnel is the problem, not the mode: STOP.
openssl s_client -connect 127.0.0.1:15055 -servername task.yadgar.svc -alpn h2 </dev/null 2>&1 \
  | grep -E 'CONNECTED|ALPN|Acceptable client certificate CA names|No client certificate CA names|alert'

# A leaf from a CA made on the spot (the "wrong anchor"). Local files only.
cd "$(mktemp -d)"
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 1 \
  -subj '/CN=b-u8d-probe-foreign-ca' -keyout ca.key -out ca.crt
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
  -subj '/CN=b-u8d-probe-leaf' -keyout leaf.key -out leaf.csr
printf 'keyUsage=critical,digitalSignature\nextendedKeyUsage=clientAuth\n' > leaf.ext
openssl x509 -req -in leaf.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 1 \
  -extfile leaf.ext -out leaf.crt
```

After the merge, when `task` is 2/2/2 at `optional` (restart the tunnel:
the old pods are gone):

```bash
# P1. No certificate → ACCEPTED. Expect "Acceptable client certificate CA
#     names" (the server now asks), a cipher, and NO alert.
openssl s_client -connect 127.0.0.1:15055 -servername task.yadgar.svc -alpn h2 -tls1_2 </dev/null 2>&1 \
  | grep -E 'CONNECTED|Acceptable client certificate CA names|Cipher is|alert'
# P2. A leaf from the wrong anchor → REFUSED. TLS 1.2 refuses inside the
#     handshake: expect an alert ("unknown ca" or "bad certificate") and
#     "Cipher is (NONE)".
openssl s_client -connect 127.0.0.1:15055 -servername task.yadgar.svc -alpn h2 -tls1_2 \
  -cert leaf.crt -key leaf.key </dev/null 2>&1 | grep -E 'Cipher is|alert'
# P2, TLS 1.3. The client finishes its side first, and the refusal arrives
# as an alert on the first read. Expect an alert line before the timeout.
# No alert at all means the leaf was ACCEPTED: STOP and revert.
(sleep 3) | timeout 8 openssl s_client -connect 127.0.0.1:15055 -servername task.yadgar.svc \
  -alpn h2 -tls1_3 -cert leaf.crt -key leaf.key -ign_eof 2>&1 | grep -E 'alert|Cipher is'
```

If `-tls1_2` fails at once with "protocol version" and no other alert, the
listener accepts TLS 1.3 only. Then use the TLS 1.3 form for P1 as well.
Stop the tunnel and delete the temp directory afterwards.

### After this merge — read-only

```bash
# 1. yadgar Synced/Healthy at 0.19.1, and its last operation Succeeded.
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.spec.source.targetRevision} {.status.sync.status}/{.status.health.status} {.status.operationState.phase}{"\n"}'

# 2. Judge by Deployment readiness, not by the operation phase. Expect
#    task 2/2/2 with AUTH `optional` and new pod start times; gateway
#    2/2/2, NOT rolled.
kubectl --context kind-yadgar -n yadgar get deploy task gateway \
  -o custom-columns='NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas,UPDATED:.status.updatedReplicas,AUTH:.spec.template.spec.containers[0].env[?(@.name=="LISTEN_TLS_CLIENT_AUTH")].value'
kubectl --context kind-yadgar -n yadgar get pods -l 'app in (task,gateway)' \
  -o custom-columns=NAME:.metadata.name,START:.status.startTime,READY:.status.containerStatuses[0].ready

# 3. The CA file is READ and WATCHED. Expect, on both new pods,
#    "watching":7 and NO "names a client CA" WARN. (No `client_auth` field:
#    this binary does not log it.)
for p in $(kubectl --context kind-yadgar -n yadgar get pod -l app=task -o name); do
  kubectl --context kind-yadgar -n yadgar logs "$p" | grep -E "task listening|names a client CA"
done

# 4. Unreadable: expect 0 for task (and for all 7).
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service)(yadgar_rotation_watched_files_unreadable)'

# 5. The serving gauge: still 2 for task.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=count%20by%20(service%2Ckind)(yadgar_tls_certificate_not_after_seconds%7Bkind%3D%22serving%22%7D)'
```

**The hop — no passive signal (ledger 1393).** Nothing calls `task` on its
own. In gateway v0.10.2 the only use of the `task` channel is `tools/call`
for the five task tools (`src/http/dispatch.rs`); `tools/list` answers from
a static catalogue and the registry poll calls `project`, not `task`. Read
2026-10-09: `yadgar_calls_total{service="task"}` shows zero increase over 7
days. So a broken hop stays invisible until a user calls a task tool, and
the readiness probe (`tcpSocket`) cannot see it either.

- **The after-check is one request, and it is mandatory (NEEDS-MAX,
  operator-run).** Send one task request through the gateway with your own
  client, for example a `find_tasks` from the yadgar client, as soon as
  `task` is 2/2/2. Then read the counters below.
- **Pass:** a NEW `yadgar_calls_total{service="task",tool="FindTasks",outcome="OK"}`
  sample under a NEW `task` pod name, and the gateway's own
  `yadgar_calls_total{service="gateway",tool="find_tasks",outcome="OK"}`
  growing by one. Under `optional` this proves the handshake completed, not
  that the leaf was presented (see above).
- **The failure shape.** A refused leaf does NOT stop the rollout: the
  readiness probe is `tcpSocket`, so the new pods go Ready and the old pods
  are removed. A handshake that `task` refuses never reaches `task`'s
  handler, so `task` counts NOTHING. The failure shows only on the gateway:
  `yadgar_calls_total{service="gateway",tool="find_tasks"}` with a non-OK
  `outcome` (the gRPC status name of the transport error; `UNAVAILABLE` is
  the likely one, not measured), and the client gets a tool error. Every
  task tool call for every user fails the same way: revert at once.
- The estate smoke rows C-01 (iam path) and C-10 (task path) are the card's
  functional acceptance. They CANNOT run today: the estate verdict gate is red
  until the settled-state gate produces a `settled-verdict` artifact (ledger
  675, stage 3). Do not read a red estate run as this change.

```bash
# Read once before the request and once after it. Expect a NEW task pod name
# with FindTasks OK, and gateway find_tasks OK one higher.
kubectl --context kind-yadgar get --raw \
  '/api/v1/namespaces/observability/services/prometheus-server:80/proxy/api/v1/query?query=sum%20by%20(service%2Cpod%2Ctool%2Coutcome)(yadgar_calls_total%7Bservice%3D~%22task%7Cgateway%22%2Ctool!~%22server%2Fdiscover%22%7D)'
```

If a `task` pod does not become Ready, the rollout stops behind
`maxUnavailable: 0` and the old pods keep serving. Read the log first: a CA
file that cannot be read or parsed refuses the boot and names the path.

### Rollback — a revert

If `yadgar`'s operation is still Running or retrying, end it first:

```bash
kubectl --context kind-yadgar -n argocd get application yadgar \
  -o jsonpath='{.status.operationState.phase} {.status.operationState.syncResult.revision}{"\n"}'
```

NEEDS-MAX: `argocd app terminate-op yadgar` against kind-yadgar's Argo CD. A
revert does not interrupt an operation already running (see "The yadgar pin
moves to parent chart 0.13.13", Rollback).

Then revert the WHOLE merge commit: the value, the K3 table, the gate test
and this section move together. A values-only revert reddens K3 and
`test_every_server_states_its_client_auth_and_stages_its_own_ca`. The revert
returns `EXPECTED_CLIENT_AUTH` to the three `-db` servers and `project` at
`optional`; it does not touch B-U8a, B-U8b or B-U8c. It also restores the
B-U8b wording fix below to its old text; re-land that on its own if wanted.
`task` rolls back to `off`: it stops asking for a certificate and drops the
CA from its watch set. Nothing is created, so nothing is left to prune.

Break-glass, if git cannot merge in time (NEEDS-MAX): suspend auto-sync on
`yadgar`, then
`kubectl --context kind-yadgar -n yadgar set env deployment/task LISTEN_TLS_CLIENT_AUTH=off`.
`off` is the only valid emergency value. Deleting the variable is a boot
refusal (ADR-0854). Revert in git afterwards.
