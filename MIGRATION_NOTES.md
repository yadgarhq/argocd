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
