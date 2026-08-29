# argocd — the GitOps control plane

Argo CD's own configuration. **Argo manages Argo:** the chart is applied once by
hand to bootstrap, and from then on every change here arrives the same way every
other change does.

The cluster and the infrastructure live in
[`yadgarhq/deploy`](https://github.com/yadgarhq/deploy). Decisions are in
[`yadgarhq/docs`](https://github.com/yadgarhq/docs) — D54 especially.

| Path | |
|---|---|
| `install/values.yaml` | Helm values for the `argo-cd` chart |
| `projects/root.yaml` | app-of-apps root, applied once during bootstrap |
| `applicationsets/modules.yaml` | discovers module repos across the organisation |

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
