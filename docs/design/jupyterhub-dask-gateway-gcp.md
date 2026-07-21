# JupyterHub + Dask Gateway + kbatch on GCP — deployment procedure and lightcone-cli integration

*Status: design proposal — researched 2026-07-12 against `main` (post-#157).*
*Revised 2026-07-12 — see §7 for the implemented design, which supersedes
§4.2's owned-cluster branch and reframes the container story of §4.5.*

## 1. Why this works almost out of the box

The execution layer on `main` is already shaped for this. `lc run` always invokes Snakemake with
`--executor dask` (our first-party plugin in `src/snakemake_executor_plugin_dask/`), and cluster
lifecycle lives in `src/lightcone/engine/dask_cluster.py:cluster_for_run()`, which has three
branches:

1. **`DASK_SCHEDULER_ADDRESS` set** → attach to an external scheduler verbatim, no cluster
   creation or teardown (`dask_cluster.py:101`).
2. **`SLURM_JOB_ID` set** → in-process scheduler + `srun`-launched workers.
3. **Neither** → sized `LocalCluster`.

Branch 1 is the Dask Gateway seam: a Gateway cluster hands us a scheduler address; exporting it
before `lc run` makes the whole pipeline execute on Kubernetes **with zero code changes**. The
executor plugin (`executor.py:104`) is a pure `Client(addr)` consumer of the same variable.

Three real constraints follow from how the executor works:

- **Shared filesystem.** The plugin declares `implies_no_shared_fs=False`; each task is a child
  `snakemake` invocation running the rule body on a worker (`executor.py:113-128`,
  `runner.py:52-107`). The project directory, `results/`, `.snakemake/` state, and the
  `LIGHTCONE_OUT_LOCK` flock file must be visible at the **same path** on the driver and every
  worker. On GKE that means an RWX PersistentVolume (Filestore) — *not* GCS FUSE, which does not
  support POSIX `flock`.
- **Worker resource contract.** Workers must advertise Dask abstract resources
  `cpus` / `memory` / `gpus` (`dask_cluster.py:33-35`) or per-rule `resources:` requests will
  never schedule (`executor.py:78-90`). Gateway workers don't set these by default; we configure
  them via `dask` config env vars in the worker pod spec (§4.3).
- **Container gap.** `wrap_recipe()` (`container.py:706-751`) shells out to
  `docker` / `podman` / `podman-hpc`, none of which exist inside a pod. Kubernetes *is* the
  container runtime: the worker pod image must *be* the project environment, and the project runs
  with `runtime: none`. (Longer term: image-per-rule via Gateway cluster options, §6.4.)

## 2. Current state of the upstream stack (verified July 2026)

| Component | Status | Version to use |
|---|---|---|
| Zero to JupyterHub (z2jh) | Actively maintained | Helm chart **4.4.0** (Jun 2026), ships JupyterHub 5.5.0; requires k8s ≥ 1.28, Helm ≥ 3.5 |
| Dask Gateway | Actively maintained | Helm chart **2026.3.x** from `https://helm.dask.org`; requires k8s ≥ 1.30 |
| DaskHub combined chart | **Deprecated** — do not use for new deployments | Install z2jh + dask-gateway as separate charts; DaskHub's `values.yaml` remains the reference for the wiring between them |
| kbatch | **Lightly maintained.** Last stable release 0.4.2 (Sep 2023); 0.5.0 alphas/betas through late 2024; quiescent since | `kbatch-proxy` chart from `https://kbatch-dev.github.io/helm-chart` (pin `0.5.0-alpha.1`) |

kbatch's quiescence is a real consideration. It is deliberately tiny (a thin authz proxy in front
of the k8s Jobs API, JupyterHub-authenticated), which limits bit-rot risk, and it's exactly the
right shape for our use case (submit a long-running `lc run` driver as a k8s Job that survives
notebook disconnects). But treat it as a component we may eventually vendor or replace
(alternatives: `jupyter-scheduler`, a 20-line FastAPI hub service of our own, or Argo Workflows
if we ever want DAG-level k8s orchestration — which we don't, Snakemake owns the DAG).

## 3. GCP deployment procedure

### 3.0 Prerequisites

```bash
gcloud services enable container.googleapis.com file.googleapis.com \
    artifactregistry.googleapis.com
gcloud config set project <PROJECT_ID>
# local tools: kubectl, helm >= 3.5
```

### 3.1 GKE cluster

Use a **Standard** (not Autopilot) cluster: we need node pools with taints, scale-to-zero, and
Spot VMs for workers; Autopilot's per-pod model fights the Dask worker pattern and the Filestore
mount topology.

```bash
ZONE=europe-west1-b   # or --region for HA control plane (99.95% SLA)
CLUSTER=lightcone-hub

# Core pool: hub, proxy, gateway api/traefik/controller, kbatch-proxy
gcloud container clusters create $CLUSTER \
  --zone $ZONE --cluster-version latest \
  --machine-type e2-standard-4 --num-nodes 1 \
  --enable-network-policy \
  --addons GcpFilestoreCsiDriver \
  --workload-pool=<PROJECT_ID>.svc.id.goog

# User pool: JupyterLab singleuser pods (the lc/Claude driver environment)
gcloud container node-pools create user-pool \
  --cluster $CLUSTER --zone $ZONE \
  --machine-type e2-standard-8 \
  --num-nodes 0 --enable-autoscaling --min-nodes 0 --max-nodes 10 \
  --node-labels hub.jupyter.org/node-purpose=user \
  --node-taints hub.jupyter.org_dedicated=user:NoSchedule

# Compute pool: Dask Gateway workers + kbatch jobs — Spot for ~60-90% discount
gcloud container node-pools create dask-pool \
  --cluster $CLUSTER --zone $ZONE \
  --machine-type c2-standard-16 --spot \
  --num-nodes 0 --enable-autoscaling --min-nodes 0 --max-nodes 50 \
  --node-labels lightcone.dev/node-purpose=compute \
  --node-taints lightcone.dev_dedicated=compute:NoSchedule

kubectl create clusterrolebinding cluster-admin-binding \
  --clusterrole=cluster-admin --user=<YOUR_GOOGLE_EMAIL>
```

Add a GPU pool later with `--accelerator type=nvidia-l4,count=1 --spot` when a project needs
`gpus_per_task`.

### 3.2 Shared filesystem (Filestore RWX)

This is the load-bearing piece for lightcone: driver and workers must share
`/home/jovyan/<project>` (or a dedicated `/shared`) with POSIX `flock` support.

```yaml
# filestore-pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: lightcone-shared
  namespace: hub
spec:
  accessModes: [ReadWriteMany]
  storageClassName: standard-rwx        # GKE Filestore CSI, Basic HDD
  resources:
    requests:
      storage: 1Ti                      # Filestore Basic minimum; ~$160-200/mo
```

Cost note: Filestore Basic has a 1 TiB floor. For an early/staging deployment, a single-pod NFS
server (`nfs-ganesha` or `nfs-server-provisioner` chart) backed by a cheap PD gets you RWX +
flock for ~$40/mo; swap to Filestore when it matters. Keep per-user home directories on z2jh's
default dynamic PD volumes; the RWX volume is for **project workspaces and results**.

### 3.3 Images (Artifact Registry)

```bash
gcloud artifacts repositories create lightcone --repository-format=docker --location=europe-west1
```

Two images:

1. **`lightcone-notebook`** — base `quay.io/jupyter/scipy-notebook` (or pangeo-notebook) plus
   `lightcone-cli`, `astra-tools`, `dask-gateway` (client), `kbatch`, git, and Claude Code. This
   is both the JupyterLab singleuser image and the kbatch job image.
2. **`lightcone-worker-<project>`** — per-project compute environment: `lightcone-cli`,
   `snakemake`, `dask distributed` (versions matching the notebook image — Dask is picky about
   client/scheduler/worker version skew), plus the project's science deps. Initially this can be
   built *from* the project's existing Containerfile with a `pip install lightcone-cli snakemake`
   layer appended — `lc build` already computes content-addressed tags
   (`compute_image_tag()`, `container.py:319`), so pushing the same tag to Artifact Registry is a
   natural extension (§6.4).

### 3.4 JupyterHub (z2jh chart 4.4.0)

```bash
helm repo add jupyterhub https://hub.jupyter.org/helm-chart/
helm repo update
```

```yaml
# jupyterhub-values.yaml
hub:
  config:
    # start with GitHub/Google OAuth; dummy auth only for a private staging cluster
    GitHubOAuthenticator:
      client_id: <...>
      client_secret: <...>
      oauth_callback_url: https://hub.<domain>/hub/oauth_callback
      allowed_organizations: [LightconeResearch]
    JupyterHub:
      authenticator_class: github
  services:
    dask-gateway:
      display: false
      apiToken: "<TOKEN_A>"          # openssl rand -hex 32; must match gateway values
    kbatch:
      display: false
      apiToken: "<TOKEN_B>"
      url: http://kbatch-proxy.kbatch.svc.cluster.local

proxy:
  https:
    enabled: true
    hosts: [hub.<domain>]
    letsencrypt:
      contactEmail: fr.eiffel@gmail.com

singleuser:
  image:
    name: europe-west1-docker.pkg.dev/<PROJECT_ID>/lightcone/lightcone-notebook
    tag: "<tag>"
  cpu: {limit: 4, guarantee: 1}
  memory: {limit: 16G, guarantee: 4G}
  defaultUrl: /lab
  storage:
    capacity: 20Gi                   # per-user home (PD)
    extraVolumes:
      - name: lightcone-shared
        persistentVolumeClaim:
          claimName: lightcone-shared
    extraVolumeMounts:
      - name: lightcone-shared
        mountPath: /shared           # same path in worker pods — see gateway values
  extraEnv:
    LIGHTCONE_SCRATCH: /shared/scratch
    # Dask Gateway client defaults, so Gateway() works with no args:
    DASK_GATEWAY__ADDRESS: https://hub.<domain>/services/dask-gateway/
    DASK_GATEWAY__PROXY_ADDRESS: gateway://traefik-dask-gateway-dask-gateway.dask-gateway:80
    DASK_GATEWAY__PUBLIC_ADDRESS: /services/dask-gateway/
    DASK_GATEWAY__AUTH__TYPE: jupyterhub
```

```bash
helm upgrade --cleanup-on-fail --install jupyterhub jupyterhub/jupyterhub \
  --namespace hub --create-namespace --version 4.4.0 \
  --values jupyterhub-values.yaml
```

Point DNS at the `proxy-public` LoadBalancer IP, wait for Let's Encrypt.

### 3.5 Dask Gateway (chart 2026.3.x)

The hub proxy routes `/services/dask-gateway/` to the gateway's traefik; the gateway
authenticates users against the hub with `TOKEN_A`.

```yaml
# dask-gateway-values.yaml
gateway:
  prefix: /services/dask-gateway     # served behind the hub proxy
  auth:
    type: jupyterhub
    jupyterhub:
      apiToken: "<TOKEN_A>"          # same as hub.services.dask-gateway.apiToken
      apiUrl: http://hub.hub.svc.cluster.local:8081/hub/api
  backend:
    image:
      name: europe-west1-docker.pkg.dev/<PROJECT_ID>/lightcone/lightcone-worker-default
      tag: "<tag>"
    scheduler:
      cores: {request: 1, limit: 2}
      memory: {request: 2G, limit: 4G}
    worker:
      extraPodConfig:
        tolerations:
          - key: lightcone.dev_dedicated
            operator: Equal
            value: compute
            effect: NoSchedule
        volumes:
          - name: lightcone-shared
            persistentVolumeClaim:
              claimName: lightcone-shared    # needs a PVC in this ns, or cross-ns PV binding
      extraContainerConfig:
        volumeMounts:
          - name: lightcone-shared
            mountPath: /shared               # MUST equal the singleuser mountPath
  # user-selectable knobs + the lightcone resource contract (see §4.3)
  extraConfig:
    clusteroptions: |
      from dask_gateway_server.options import Options, Integer, Float, String

      def options_handler(options):
          return {
              "worker_cores": options.worker_cores,
              "worker_memory": "%fG" % options.worker_memory,
              "image": options.image,
              "environment": {
                  # advertise lightcone's Dask resource contract on every worker
                  "DASK_DISTRIBUTED__WORKER__RESOURCES__CPUS": str(options.worker_cores),
                  "DASK_DISTRIBUTED__WORKER__RESOURCES__MEMORY": str(int(options.worker_memory * 1e9)),
                  "DASK_DISTRIBUTED__WORKER__RESOURCES__GPUS": "0",
              },
          }

      c.Backend.cluster_options = Options(
          Integer("worker_cores", default=4, min=1, max=16, label="Worker cores"),
          Float("worker_memory", default=16, min=4, max=64, label="Worker memory (GB)"),
          String("image",
                 default="europe-west1-docker.pkg.dev/<PROJECT_ID>/lightcone/lightcone-worker-default:<tag>",
                 label="Worker image"),
          handler=options_handler,
      )

traefik:
  service:
    type: ClusterIP                  # not exposed; everything flows through the hub proxy
```

```bash
helm upgrade --install dask-gateway dask-gateway \
  --repo=https://helm.dask.org \
  --namespace dask-gateway --create-namespace \
  --values dask-gateway-values.yaml
```

Namespace note: the simplest topology is to deploy dask-gateway **in the `hub` namespace** so
the shared PVC binds directly and the daskhub-style service discovery just works; the split shown
above requires either a second PVC bound to the same Filestore PV or a `ReferenceGrant`-style
cross-namespace PV. Same-namespace is the recommended starting point.

### 3.6 kbatch

```bash
helm repo add kbatch https://kbatch-dev.github.io/helm-chart
kubectl create namespace kbatch
```

```yaml
# kbatch-values.yaml
app:
  jupyterhub_api_token: "<TOKEN_B>"      # same as hub.services.kbatch.apiToken
  jupyterhub_api_url: http://hub.hub.svc.cluster.local:8081/hub/api
  extra_env:
    KBATCH_PREFIX: /services/kbatch
  # job template: run on the compute pool, mount the shared volume
  job_template:
    spec:
      template:
        spec:
          tolerations:
            - key: lightcone.dev_dedicated
              operator: Equal
              value: compute
              effect: NoSchedule
          containers:
            - volumeMounts:
                - name: lightcone-shared
                  mountPath: /shared
          volumes:
            - name: lightcone-shared
              persistentVolumeClaim:
                claimName: lightcone-shared
```

```bash
helm install kbatch kbatch/kbatch-proxy --version 0.5.0-alpha.1 \
  --namespace kbatch --values kbatch-values.yaml
```

The `hub.services.kbatch` entry from §3.4 makes the hub proxy route
`https://hub.<domain>/services/kbatch/` to it. (Verify the exact values-key spelling against the
chart's `values.yaml` at install time — the alpha chart has shifted key names between releases.)

### 3.7 Smoke test

From a JupyterLab terminal on the hub:

```bash
python -c "
from dask_gateway import Gateway
g = Gateway()                       # picks up DASK_GATEWAY__* env
c = g.new_cluster(worker_cores=2, worker_memory=4)
c.scale(2)
client = c.get_client()
print(client.scheduler_info())
print(client.run(lambda: __import__('os').path.exists('/shared')))
"
kbatch configure --kbatch-url https://hub.<domain>/services/kbatch --token $JUPYTERHUB_API_TOKEN
kbatch job submit --name smoke --image busybox --command 'ls /shared'
```

## 4. Integration with lightcone-cli

### 4.1 Phase 0 — zero code changes (works today)

From a notebook or terminal in the singleuser pod, with the project checked out under `/shared`:

```python
from dask_gateway import Gateway
gw = Gateway()
cluster = gw.new_cluster(worker_cores=8, worker_memory=32)
cluster.adapt(minimum=0, maximum=20)
print(cluster.scheduler_address)     # tls://...
```

```bash
export DASK_SCHEDULER_ADDRESS='tls://...'
cd /shared/my-analysis && lc run
```

`cluster_for_run` branch 1 attaches and never tears down; `resources:` per rule schedule
correctly because §3.5 injected the `cpus/memory/gpus` worker resources. Requirements: project
lives on `/shared`; project `lightcone.yaml` (or `LIGHTCONE_SCRATCH`) points scratch at
`/shared/scratch`; container `runtime: none` (worker image *is* the environment).

One wrinkle: Gateway schedulers speak TLS with per-cluster credentials, so a bare
`Client(addr)` in the executor may need the Gateway security context. If
`DASK_SCHEDULER_ADDRESS` alone proves insufficient, that's the first concrete argument for
Phase 1's native branch (which gets `cluster.get_client()` for free). Alternatively
`gw.new_cluster(...)` → `cluster.security` can be exported as cert/key paths — but at that point
the native branch is less code.

### 4.2 Phase 1 — a `gateway` branch in `cluster_for_run` + a site entry

Add a fourth branch to `dask_cluster.cluster_for_run()`, gated on config/env (not on
`site["backend"]`, which is inert today):

```
LIGHTCONE_DASK_GATEWAY=https://hub.<domain>/services/dask-gateway/   # or config.yaml key
```

```python
def _gateway_cluster(...):
    from dask_gateway import Gateway
    gw = Gateway()                              # env-configured, jupyterhub auth
    cluster = gw.new_cluster(**_options_from_config())
    cluster.adapt(minimum=0, maximum=cfg.max_workers)
    client = cluster.get_client()               # handles TLS security context
    try:
        yield cluster.scheduler_address
    finally:
        cluster.shutdown()
```

Plus a `SITE_DEFAULTS["lightcone-hub"]` entry (`site_registry.py:27`):

```python
"lightcone-hub": {
    "hostname_patterns": [],            # detected via JUPYTERHUB_SERVICE_PREFIX env instead
    "display_name": "Lightcone JupyterHub (GCP)",
    "backend": "dask-gateway",
    "container_runtime": "none",
    "scratch_root": "/shared/scratch",
}
```

Detection: `JUPYTERHUB_USER` + `DASK_GATEWAY__ADDRESS` in the environment is a reliable "we're
on a hub" signal — cleaner than hostname patterns for pods. This is also where the
`design_review.md` aspiration ("laptop, SLURM allocation, Kubernetes pod — only configuration is
a `--target` choice") gets its third leg: `lc run --target hub|slurm|local`, defaulting to
auto-detect.

The executor's `code_version`/manifest layer needs nothing: manifests are written host-side by
the worker process onto the shared FS, `lc status`/`lc verify` read manifests only, and the
flock serialization (`LIGHTCONE_OUT_LOCK`) works on Filestore.

### 4.3 Worker resource contract (the one silent failure mode)

If workers don't advertise `cpus`/`memory`/`gpus`, every submitted task hangs unscheduled —
no error. Belt and suspenders:

- Gateway side: the `cluster_options` handler in §3.5 sets
  `DASK_DISTRIBUTED__WORKER__RESOURCES__{CPUS,MEMORY,GPUS}` from the chosen worker shape.
- lightcone side (Phase 1): after `wait_for_workers`, assert the resources are present on at
  least one worker and fail fast with a pointed message if not. Cheap, saves an afternoon.

### 4.4 Phase 2 — kbatch as the headless driver channel

`lc run` is a long-running driver (Snakemake + in-process Dask client). Running it in a notebook
terminal dies with the singleuser pod (culling, browser close). kbatch turns it into a k8s Job:

```bash
kbatch job submit --name my-analysis \
  --image europe-west1-docker.pkg.dev/<PROJECT_ID>/lightcone/lightcone-notebook:<tag> \
  --command "bash -c 'cd /shared/my-analysis && lc run'"
```

Natural CLI sugar: **`lc submit [outputs...]`** — generates the kbatch job spec (image = notebook
image, workdir = project dir on `/shared`, env = gateway address), submits via kbatch's Python
API, and prints `kbatch job logs` / `lc status` follow-up commands. `kbatch cronjob submit`
gives scheduled re-runs (nightly `lc run && lc verify`) for free. This is also the substrate for
agentic loops: a `ralph`-style long-running Claude Code session can itself be a kbatch job that
shells out to `lc run`, with the human reviewing results in JupyterLab against the same shared
volume.

Given kbatch's maintenance state, keep the coupling thin: one module
(`src/lightcone/engine/kbatch.py` or similar) that builds the job spec, so swapping the
submission backend later is a one-file change.

### 4.5 Phase 3 — per-rule images (closing the container gap properly)

Today all rules in a Gateway run share one worker image, and recipes run unwrapped
(`runtime: none`). ASTRA's `astra.yaml` already carries per-output container specs, and
`compute_image_tag()` gives content-addressed tags. The clean endgame:

1. `lc build --push` builds each rule's image and pushes `lc-<name>-<hash>` to Artifact Registry.
2. The gateway branch groups rules by image and requests one Gateway cluster per image (Gateway
   cluster options already accept `image`), or — simpler and probably sufficient — `lc run`
   errors early when `astra.yaml` declares more than one distinct container and suggests a
   combined image.
3. Manifests keep recording the image tag either way, so provenance is intact.

Defer this until a real project needs heterogeneous environments; most current projects use a
single environment.

## 5. Cost & ops summary

- Idle floor: 1× `e2-standard-4` core node (~$100/mo) + Filestore 1 TiB (~$160-200/mo, or ~$40/mo
  with the NFS-pod stopgap) + LoadBalancer (~$20/mo). User and compute pools scale to zero.
- Spot VMs on the compute pool are safe: Snakemake retries failed jobs, manifests make partial
  progress durable, and `lc verify` catches anything torn.
- Upgrades: z2jh and dask-gateway both publish frequent chart releases; pin versions in a
  `helmfile`/terraform and bump deliberately. kbatch: pin and don't expect upstream movement.
- GPU: add an L4/A100 Spot pool + a `worker_gpus` cluster option mapping to
  `nvidia.com/gpu` limits and `DASK_DISTRIBUTED__WORKER__RESOURCES__GPUS`.

## 6. Open questions

1. **ANSWERED 2026-07-12 (staging cluster, lightcone-hub repo): Phase 1 is required.**
   Gateway's `cluster.scheduler_address` is not `tls://` but a custom scheme —
   `gateway://traefik-dask-gateway.hub:80/<cluster-name>` — that only the `dask_gateway`
   client library's comm layer can dial (traefik multiplexing + per-cluster TLS + SNI). A bare
   `distributed.Client(addr)`, which is what the executor creates, fails with
   `unknown address scheme 'gateway'` regardless of TLS env configuration. Phase 0
   (pure `DASK_SCHEDULER_ADDRESS` env) cannot work against Gateway; the `gateway` branch in
   `cluster_for_run` must create the cluster and hold the `GatewayCluster` object. The executor's
   `Client(addr)` will additionally need the Gateway security context — simplest is for the
   branch to pass the client through (or export the `gateway://` handling) rather than an address
   string alone. Everything else validated on staging: workers advertise the `cpus/memory/gpus`
   resource contract via the cluster-options env injection, `/shared` (NFS RWX) is visible on
   workers, and `flock` works on it.
2. **PVC namespace topology** — same-namespace deploy (hub + gateway + kbatch jobs in one ns) vs.
   the three-namespace layout with shared-PV plumbing. Recommend starting same-namespace.
3. **Home vs. shared workspace layout** — one `/shared/<user>/` convention vs. per-project
   subpaths; interacts with `lc init` scaffolding and the session-start hooks.
4. **kbatch alternative** — if the 0.5 alphas misbehave, a minimal in-house hub service
   (JupyterHub-authenticated POST → k8s Job) is ~150 lines and removes the dependency.
5. **On-hub container build path — RESOLVED 2026-07-21: BinderHub service (see §8).**
   The deployment enables 2i2c's *binderhub-service* in API-only mode with a push credential for
   the deployment registry, which answers the least-privilege sub-question: users never hold
   registry credentials — the build service holds the only writer key, and users reach it through
   JupyterHub auth. This is a variant of (c) that we don't have to build or operate ourselves.
   Original framing kept below for the record.
   §7.2 currently defers all image builds off-hub: `lc build` in the JupyterLab pod prints the
   `docker build && docker push` commands to run elsewhere, because there is no docker in-pod by
   design. End-to-end testing on the staging cluster confirmed this *works* but is a real friction
   point — every dependency change forces the user to leave the hub, build on a docker-equipped
   machine with registry access, push, and only then start a Gateway cluster on the new image. The
   open decision is which build story we commit to:
   - **(a) Keep builds off-hub, just document/smooth them** (status quo, §7.2). Cheapest; the
     friction stays.
   - **(b) In-cluster rootless build** (kaniko/buildkit) that builds the project image and pushes
     to Artifact Registry from within the pod. §7.2 deferred this "until someone actually cannot
     reach a docker machine" — LCR-176 is arguably that moment.
   - **(c) Hub-side build service** — a JupyterHub-authenticated POST → a Kubernetes build Job,
     the same shape as the kbatch-alternative in item 4 and the `lc submit` service.

   The blocking sub-question is the one already raised as PRD open-question #3: **can an arbitrary
   hub user be allowed to push to the deployment registry?** Options (b) and (c) both need an answer
   on registry credentials / least privilege before they can be built. Two constraints any choice
   must respect: the worker pod image *is* the recipe environment (so the build target is a real
   Gateway-worker image, `FROM lightcone-worker-default`, not a slim base), and — from LCR-175 —
   the driver (notebook) and worker images must stay on the **same lightcone-cli version**, because
   `snakemake_executor_plugin_dask` runs split across both and version skew breaks execution; the
   build path must keep them in lockstep the way we pin dask/distributed. *(The related but separate
   defect — `lc init` scaffolding a `FROM python:3.12-slim` Containerfile that can never run as a
   Gateway worker — is being handled as a code fix under LCR-176 and is not part of this open
   question.)*

## 7. Implemented design (2026-07-12) — attach-only + kubernetes as a first-class runtime

> **Partially superseded by §8 (2026-07-21):** §7.1 and §7.2 stand;
> §7.3's attach-only lifecycle and §7.4's build/lifecycle rows are
> replaced by the PRD create/cull lifecycle and the BinderHub build
> path.

What actually shipped diverges from §4.2/§4.5 in three deliberate ways, all
in the direction of less machinery:

### 7.1 `kubernetes` is a container runtime, not a workaround

§4.2's `container_runtime: "none"` was a lie with side effects: `load_runtime()`
never consulted the site entry, auto-detection found no OCI binary, and every
`lc run` on the hub fired the "no container runtime found / provenance will
not match" warning — for recipes that *are* containerized, by the worker pod.

The implemented model: `container_runtime: "kubernetes"` (site-declared,
treated as **explicit** by `load_runtime()` — a deployment fact, not a
detection guess). `wrap_recipe()` is a no-op for it, same as `none`, but the
semantics differ: the pod is the container. Site-declared *OCI* runtimes
(Perlmutter → podman-hpc) keep their detection-order-hint semantics.

### 7.2 Registry wiring by convention: `LIGHTCONE_REGISTRY`

The deployment names its registry via singleuser env (exactly like
`LIGHTCONE_SCRATCH` and `DASK_GATEWAY__*`). A `container: Containerfile` spec
resolves to `$LIGHTCONE_REGISTRY/lc-<project>:<hash>` — the same content hash
as the local `lc-<project>-<hash>` tag, so the environment is provably the
same artifact on every path. `code_version` keeps hashing the *local* tag on
all paths (`resolve_image_for_run` stays site-agnostic) so moving a project
laptop↔hub is not code drift; the registry ref (`resolve_worker_image`) is
used only to name the worker image and verify the attached cluster.

`lc build` on the hub builds nothing (no docker in-pod, by design): it probes
the registry (metadata-server token, best-effort, never gating) and prints the
exact `docker build -t <ref> … && docker push <ref>` commands to run from any
docker-equipped machine. In-cluster kaniko builds remain deliberately
deferred until someone actually cannot reach one.

The worker image must carry dask/distributed/lightcone-cli at hub-matching
pins; the recommended convention is `FROM <registry>/lightcone-worker-default:<tag>`
in the project Containerfile, which then serves every path (locally it wraps;
on the hub it runs as the pod).

### 7.3 Attach-only Gateway branch

`lc run` never creates Gateway clusters (§4.2's `new_cluster` + adapt +
shutdown path is gone). The user creates one from JupyterLab — sidebar or
notebook, where the options widget (image/cores/memory) and dashboard live —
and `lc run` discovers it: `list_clusters()` is user-scoped under JupyterHub
auth, so exactly one running cluster attaches with zero configuration; zero
raises with a copy-pasteable `new_cluster(image=…)` snippet (image pre-filled
from the project's resolved ref); several raises listing names, with
`LIGHTCONE_GATEWAY_CLUSTER` as the disambiguator. Scaling is never touched;
the cluster is left running on exit — the Gateway flavor of the
`DASK_SCHEDULER_ADDRESS` convention, and the one intentional asymmetry with
the owned local/SLURM clusters.

Two contract checks at attach time, both deployment-backed:

- **Resources**: live workers must advertise `cpus`/`memory`/`gpus`
  (injected by the cluster-options handler); zero live workers is fine
  (adaptive clusters scale on demand).
- **Image**: the options handler also injects `LIGHTCONE_WORKER_IMAGE` into
  scheduler+worker pods; lc reads it back via `run_on_scheduler` (a lambda,
  cloudpickled by value, so the scheduler needn't import lightcone) and
  warns on mismatch with the project's resolved image. Manifests record the
  worker pod's actual image as `worker_image` (additive field, like
  `slurm_job_id`), so provenance is ground truth even on a stale cluster.

### 7.4 Path similarity, as implemented

| | local | SLURM | hub |
|---|---|---|---|
| environment defined by | `Containerfile` | same | same |
| `lc build` | build into local store | build via podman-hpc | check ref exists in deployment registry |
| recipe isolation | `docker run` wrap | `podman-hpc run` wrap | worker pod **is** the image |
| cluster lifecycle | owned per-run | owned per-run | attached, user-owned |
| manifest truth | declared spec + code_version(tag) | same | same + `worker_image` ground truth |

Deployment-side counterpart (lightcone-hub repo): `LIGHTCONE_REGISTRY` in
singleuser extraEnv, `LIGHTCONE_WORKER_IMAGE` in the cluster-options
environment, worker-default as the default cluster image, and a
`just build-worker` recipe mirroring `build-notebook`.

## Sources

- z2jh docs & GKE setup: https://z2jh.jupyter.org/en/stable/ ·
  https://z2jh.jupyter.org/en/stable/kubernetes/google/step-zero-gcp.html
- z2jh chart releases: https://hub.jupyter.org/helm-chart/ (4.4.0 / JupyterHub 5.5.0, Jun 2026)
- Dask Gateway on k8s: https://gateway.dask.org/install-kube.html (chart 2026.3.x, k8s ≥ 1.30)
- DaskHub deprecation & wiring reference: https://github.com/dask/helm-chart (daskhub/values.yaml);
  2i2c infra guide https://infrastructure.2i2c.org/topic/infrastructure/hub-helm-charts/
- kbatch: https://kbatch.readthedocs.io/ · https://github.com/kbatch-dev/kbatch ·
  https://kbatch-dev.github.io/helm-chart/
- Worked example of gateway-behind-hub-proxy: https://www.zonca.dev/posts/2022-04-04-dask-gateway-jupyterhub

## 8. Implemented design (2026-07-21) — PRD lifecycle: create/cull + BinderHub builds

Implements the resolved decisions of the *Integration with Kubernetes and
JupyterHub* PRD (LCR-174) on top of §7's runtime model. Two changes.

### 8.1 Run-scoped Gateway clusters (replaces §7.3)

`lc run` on a hub now creates a Gateway cluster per run — with the project's
worker image as the `image` cluster option, adaptive `1..--jobs` — and shuts
it down when the run finishes. This is PRD decision #1, and it is what the
native-k8s model requires: a Gateway cluster's image is fixed at creation, so
"the image is up to date" is only achievable with a fresh cluster. Startup
waits for the first worker (`LIGHTCONE_GATEWAY_WORKER_TIMEOUT`, default
600 s) so an unpullable image is a loud error, not a silent zero-worker hang.

`LIGHTCONE_GATEWAY_CLUSTER=<name>` keeps the §7.3 behavior as an explicit
attach mode (long-lived cluster iteration): never rescaled, never shut down,
image drift warned about via the `LIGHTCONE_WORKER_IMAGE` check.

### 8.2 On-hub image builds through binderhub-service (resolves §6.5)

The deployment runs 2i2c's binderhub-service (API-only mode, repo2docker
build pods, pushing to the deployment registry). `lightcone.engine.binder`
drives it from the user pod:

- **Auth**: the ambient `JUPYTERHUB_API_TOKEN`; service URL defaults to
  `http://proxy-public/services/binder` (`LIGHTCONE_BINDER_URL` overrides).
- **Environment ref**: the last commit touching the env-defining files —
  `env_context_paths()` = Containerfile + dependency files + named COPY
  sources, *excluding* a whole-tree `COPY .` (code reaches workers via the
  shared home; the image is the environment). Code-only commits therefore
  reuse the previous image; env edits are auto-committed (scoped to those
  paths; `lc build --no-commit` refuses instead) and pushed, since build
  pods clone from the git remote.
- **repo2docker bridge**: a committed root `Dockerfile → Containerfile`
  symlink (repo2docker does not read `Containerfile`).
- **Build**: `GET /build/<provider>/<spec>/<sha>?build_only=true` streamed
  as SSE until `ready`/`failed`; the terminal event carries `imageName`.
  BinderHub consults its registry first, so an already-built ref is one
  round-trip — `lc run` calls this ensure step on every kubernetes-runtime
  run and then creates the cluster with the returned image.

Trade-offs accepted: the project must have a (public, until the deployment
configures a provider token) GitHub remote; the image tag is the env sha
rather than the content-addressed `lc-<project>-<hash>` scheme (§7.2's
registry probing remains as the off-hub fallback path); repo2docker builds
at the env sha, so a whole-tree `COPY .` bakes code as of that commit — a
non-issue on the gateway path where recipes run from the shared home via
`--directory`.

### 8.3 Path similarity, as implemented (updates §7.4)

| | local | SLURM | hub |
|---|---|---|---|
| environment defined by | `Containerfile` | same | same |
| `lc build` | build into local store | build via podman-hpc | BinderHub service → deployment registry |
| recipe isolation | `docker run` wrap | `podman-hpc run` wrap | worker pod **is** the image |
| cluster lifecycle | owned per-run | owned per-run | owned per-run (attach opt-in) |
| manifest truth | declared spec + code_version(tag) | same | same + `worker_image` ground truth |
