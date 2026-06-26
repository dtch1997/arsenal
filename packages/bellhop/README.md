# bellhop

**Check your code into an ephemeral [RunPod](https://runpod.io) pod, run it,
bring the results back, and check out** — an async Python library for
disposable GPU pods.

Like a hotel bellhop: it books a room (provisions a pod), waits until it's
actually ready, carries your luggage up (uploads your code), and when you leave
it brings your bags back down (pulls results) and checks out (tears the pod
down) — so you never leave a pod (or a bill) running by accident.

```python
import asyncio
from bellhop import pod, PodConfig

async def main():
    async with pod(PodConfig(compute="gpu", gpu_id="NVIDIA GeForce RTX 4090")) as p:
        await p.push("./mycode", "/workspace/job")
        r = await p.exec("cd /workspace/job && python train.py")
        print(r.stdout)
        await p.pull("/workspace/job/out", "./results")
    # pod is gone here — even if the body raised

asyncio.run(main())
```

It talks to the RunPod **REST API** (`rest.runpod.io/v1`) directly over `httpx`,
falling back to the **GraphQL API** only to set native safety timers. No
`runpodctl`, no vendored SDK.

## Install

```bash
pip install bellhop          # or: pip install git+https://github.com/dtch1997/bellhop
```

Set `RUNPOD_API_KEY` in your environment. Connection uses your SSH keypair
(`~/.ssh/id_ed25519` by default): bellhop injects the public key as the pod's
`PUBLIC_KEY` env so `root@pod` is reachable. (GCS upload, if you enable it,
needs `gcloud` on your `PATH`.)

## "Return when functional" — the hard part

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

## Two ways to use it

### Composable pod — multi-step / interactive

Keep one pod alive and run many steps against it:

```python
async with pod(PodConfig(compute="gpu", gpu_id="NVIDIA GeForce RTX 4090")) as p:
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
    PodConfig(compute="gpu", gpu_id="NVIDIA GeForce RTX 4090"),
))
print(res.remote_exit, res.local_results)
```

`run()` provisions → waits-functional → uploads the codebase (local dir *or* git
URL) → runs `setup` then `run` (tee'd to `results/run.log`) → pulls the results
dir back → optionally uploads to GCS → tears down → returns a `RunResult`.

CLI equivalent:

```bash
bellhop run --slug demo --codebase ./mycode --run "python go.py" \
            --compute gpu --gpu-id "NVIDIA GeForce RTX 4090"
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

## Optional: persist results to GCS

Off by default. Pass `gcs_base` (or `--gcs-base`) to upload the pulled results
to Google Cloud Storage from your machine (credentials never touch the pod):

```python
RunSpec(slug="demo", codebase="./code", run="python go.py",
        gcs_base="gs://your-bucket/experiments")
# res.gcs_uri and res.retrieve_cmd are populated
```

## Typed errors

`RunpodError` subclasses let you branch on failure mode:
`PreflightError` (bad config / missing key), `ProvisionError` (create failed),
`PodNotReadyError` (never became functional), `RemoteJobError` (carries
`.remote_exit` + `.log_tail`), `ResultsMissingError`, `GcsUploadError`.

## Notes

- Code/result transfer is **tar-over-ssh** — only needs `tar` + `ssh` in the
  image (no rsync).
- Env vars passed to `exec(env=...)` are exported *inside* the remote script and
  fed over stdin, so a fresh sshd session picks them up and secret values never
  appear in the pod's process list.
- On out-of-stock, a `COMMUNITY` request retries on `SECURE` automatically
  (toggle with `cloud_fallback=False`).

## Development

```bash
pip install -e ".[dev]"
pytest                              # offline unit tests (no pod, no cost)
RUNPOD_LIVE=1 pytest tests/integration_live.py -s   # billed end-to-end test
```

## License

MIT
