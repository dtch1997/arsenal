# bellhop

**Check your code into an ephemeral box, run it, bring the results back, and
check out** — an async Python library for disposable compute. Two backends:
a [RunPod](https://runpod.io) pod or a [Modal](https://modal.com) sandbox.

Like a hotel bellhop: it books a room (provisions the box), waits until it's
actually ready, carries your luggage up (uploads your code), and when you leave
it brings your bags back down (pulls results) and checks out (tears the box
down) — so you never leave a box (or a bill) running by accident.

```python
import asyncio
from bellhop import pod, PodConfig

async def main():
    async with pod(PodConfig(gpu="RTX4090")) as p:
        await p.push("./mycode", "/workspace/job")
        r = await p.exec("cd /workspace/job && python train.py")
        print(r.stdout)
        await p.pull("/workspace/job/out", "./results")
    # pod is gone here — even if the body raised

asyncio.run(main())
```

The same code runs on Modal by swapping the config — `sandbox(ModalConfig(...))`
instead of `pod(PodConfig(...))` (see [Two backends](#two-backends) below).

The RunPod backend talks to the RunPod **REST API** (`rest.runpod.io/v1`)
directly over `httpx`, falling back to the **GraphQL API** only to set native
safety timers. No `runpodctl`, no vendored SDK. The Modal backend drives a
Modal **Sandbox** via the `modal` SDK.

## Install

```bash
pip install bellhop-py           # RunPod backend (or: pip install git+https://github.com/dtch1997/bellhop)
pip install 'bellhop-py[modal]'  # add the Modal backend
```

(The PyPI distribution is `bellhop-py` — the bare `bellhop` name is an
unrelated package — but the import name and CLI are plain `bellhop`.)

For the **RunPod** backend, set `RUNPOD_API_KEY`. Connection uses your SSH
keypair (`~/.ssh/id_ed25519` by default): bellhop injects the public key as the
pod's `PUBLIC_KEY` env so `root@pod` is reachable. For the **Modal** backend,
configure Modal auth (`modal token new`, or `MODAL_TOKEN_ID` /
`MODAL_TOKEN_SECRET`). (GCS upload, if you enable it, needs `gcloud` on your
`PATH` either way.)

## Two backends

Both backends implement the same `ExecBox` contract — `exec` / `push` / `pull`
/ `exists_remote` / `teardown` — so the high-level `run()` / `run_many()`
pipeline (below) is provider-agnostic: hand it a `PodConfig` for RunPod or a
`ModalConfig` for Modal and everything else is identical.

```python
from bellhop import sandbox, ModalConfig

async with sandbox(ModalConfig(gpu="A10G")) as b:   # CPU box: omit gpu
    await b.push("./mycode", "/workspace/job")
    r = await b.exec("cd /workspace/job && python train.py")
    print(r.stdout)
    await b.pull("/workspace/job/out", "./results")
# sandbox terminated on exit (pass keep=True to leave it up)
```

The whole common surface is spelled the same on both configs:

- **`gpu=`** — a canonical short name (`"A100"`, `"H100"`, `"L4"`, …); `None`
  means a CPU box. On RunPod the name expands through `GPU_ALIASES` to the
  *list* of matching gpuTypeIds (e.g. `"A100"` → PCIe *and* SXM), which the
  REST API accepts wholesale — better stock availability than naming one SKU.
  A full RunPod id (`"NVIDIA GeForce RTX 4090"`) still passes verbatim.
- **`max_lifetime=`** — the hard server-side kill switch, `timedelta` on both
  (maps to `terminate_after` on RunPod, `timeout` on Modal).
- **`image=` / `image_preset=`** — the `pytorch-cuda` preset is pinned to the
  same torch 2.4.0 + CUDA 12.4 environment on both backends.

What genuinely differs stays backend-specific:

| | RunPod (`PodConfig`, `pod()`) | Modal (`ModalConfig`, `sandbox()`) |
|---|---|---|
| Readiness | SSH/probe wait (below) | none — `create()` returns an execable box |
| Extra TTL | `stop_after` (wall-clock compute halt) | `idle_timeout` (kill after inactivity) |
| Image extras | — | `pip=` / `apt=`, `modal.Image`, `secrets=`, `volumes=` |
| Placement | `cloud=` SECURE/COMMUNITY (+fallback) | `region=`, `cpu=`, `memory=` |
| Auth | `RUNPOD_API_KEY` + SSH keypair | Modal token (`modal token new`) |

(`stop_after` and `idle_timeout` are deliberately *not* unified — one is a
wall-clock timer, the other an inactivity timer; pretending they're the same
concept would be a trap. `gpu_id=` remains as a legacy spelling of a verbatim
RunPod id.)

## "Return when functional" — the hard part (RunPod only)

`desiredStatus == RUNNING` is necessary but **not sufficient**: sshd / your
server typically lags the RUNNING state by 30–60s. So once a pod is routable
(RUNNING + public IP + mapped port), bellhop runs a **readiness probe** until it
passes before handing the pod to you. "Functional" is caller-specific, so it's
pluggable:

```python
from bellhop import SshProbe, TcpProbe, HttpProbe, LogMarkerProbe

PodConfig(..., ready=SshProbe("true"))            # ssh job pods (default)
PodConfig(..., ready=HttpProbe(8000, "/health"))  # a served endpoint
PodConfig(..., ready=LogMarkerProbe("server up")) # headless pods
```

(Modal sandboxes are execable as soon as `create()` returns, so there's no
probe step on that backend.)

## Two ways to use it

### Composable pod — multi-step / interactive

Keep one pod alive and run many steps against it:

```python
async with pod(PodConfig(gpu="RTX4090")) as p:
    await p.push("./code", "/workspace/job")
    await p.exec("cd /workspace/job && python train.py", env={"HF_TOKEN": tok})
    await p.exec("python eval.py")                  # same pod, no re-provision
    await p.pull("/workspace/job/results", "./out")
    print(p.proxy_url(8000))                         # https://<id>-8000.proxy.runpod.net
# torn down on exit (pass keep=True to leave it up)
```

### One-shot — provision, run, collect, done

```python
import asyncio
from bellhop import run, RunSpec, PodConfig

res = asyncio.run(run(
    RunSpec(slug="demo", codebase="./mycode", run="python go.py"),
    PodConfig(gpu="A100"),      # ModalConfig(gpu="A100") runs the same pipeline on Modal
))
print(res.remote_exit, res.local_results)
```

`run()` provisions → waits-functional → uploads the codebase (local dir *or* git
URL) → runs `setup` then `run` (tee'd to `results/run.log`) → pulls the results
dir back → optionally uploads to GCS → tears down → returns a `RunResult`. Pass
a `ModalConfig` instead of a `PodConfig` to run the exact same pipeline on a
Modal sandbox.

CLI equivalent — the same `--gpu` flag works on both backends (omit it for a
CPU box):

```bash
bellhop run --slug demo --codebase ./mycode --run "python go.py" --gpu A100
bellhop run --backend modal --slug demo --codebase ./mycode --run "python go.py" --gpu A100
```

### Fan out a sweep

```python
from dataclasses import replace
from bellhop import run_many

base = RunSpec(slug="sweep", codebase="./code", run="python train.py")
specs = [replace(base, slug=f"lr{lr}", run=f"python train.py --lr {lr}")
         for lr in (1e-4, 3e-4, 1e-3)]
results = await run_many(specs, gpu_cfg, max_concurrency=4)
```

## Cleanup: two layers

| When | Handled by |
|------|------------|
| Normal exit, exception, Ctrl-C | the `async with` block's `finally` — **always** tears the pod down (unless `keep=True`) |
| The host process itself dies (kill -9, crash, reboot) | native RunPod safety timers (below) |

The context manager is the primary guarantee and covers essentially everything.
The timers are a backstop for the one case `finally` can't reach.

### Native safety timers

Every GPU pod is created with RunPod's own server-side timers, set atomically at
creation so they hold even if your process dies the instant after:

```python
from datetime import timedelta
PodConfig(
    stop_after=timedelta(hours=24),       # halt compute billing; disk persists, restartable
    terminate_after=timedelta(hours=72),  # delete the pod; all billing stops
)
# set either to None to disable
```

These use the GraphQL `podFindAndDeployOnDemand` mutation (REST has no TTL
field), so setting a timer routes pod creation through GraphQL automatically.

> **Granularity caveat.** RunPod enforces these on a coarse schedule, *not*
> minute-precise — a short timer may fire well after its deadline. Treat them as
> an hours-scale backstop, not a precise kill switch. The `async with` cleanup
> is what you should rely on for prompt teardown. Native TTL currently applies
> to GPU pods only (the on-demand path); CPU pods rely on `finally` alone.

On the **Modal** backend the equivalents are first-class `create` kwargs:
`ModalConfig(timeout=timedelta(hours=24))` is the hard max lifetime and
`idle_timeout=timedelta(minutes=30)` terminates the sandbox after inactivity —
no GraphQL detour, and they apply to CPU and GPU sandboxes alike.

The backend-agnostic spelling of the hard kill is
**`max_lifetime=timedelta(...)`** — set it on either config (or
`--max-lifetime-hours` on the CLI) and it maps to `terminate_after` on RunPod
and `timeout` on Modal, taking precedence over those fields.

## Optional: persist results to GCS

Off by default. Pass `gcs_base` (or `--gcs-base`) to upload the pulled results
to Google Cloud Storage from your machine (credentials never touch the pod):

```python
RunSpec(slug="demo", codebase="./code", run="python go.py",
        gcs_base="gs://your-bucket/experiments")
# res.gcs_uri and res.retrieve_cmd are populated
```

## Typed errors

`BellhopError` subclasses let you branch on failure mode:
`PreflightError` (bad config / missing key / `modal` not installed),
`ProvisionError` (pod or sandbox create failed), `PodNotReadyError` (never became
functional), `RemoteJobError` (carries `.remote_exit` + `.log_tail`),
`ResultsMissingError`, `GcsUploadError`. (`RunpodError` is a back-compat alias
for `BellhopError`.)

## Notes

- Code/result transfer is **tar-over-ssh** on RunPod and **tar-over-exec** on
  Modal — only needs `tar` in the image (no rsync; on RunPod also `ssh`).
- Env vars passed to `exec(env=...)` never appear in the box's process list:
  RunPod exports them inside a script fed over stdin; Modal passes them over its
  API, not argv.
- On out-of-stock, a RunPod `COMMUNITY` request retries on `SECURE` automatically
  (toggle with `cloud_fallback=False`).
- The Modal default image is `debian_slim` with `git` + `tar`; add packages with
  `ModalConfig(pip=[...], apt=[...])`, or supply your own `modal.Image` /
  registry ref (assumed to already have `tar`).

## Development

```bash
pip install -e ".[dev]"
pytest                              # offline unit tests (no pod/sandbox, no cost)
RUNPOD_LIVE=1 pytest tests/integration_live.py -s     # billed RunPod end-to-end test
MODAL_LIVE=1  pytest tests/integration_modal.py -s    # billed Modal end-to-end test
```

## License

MIT
