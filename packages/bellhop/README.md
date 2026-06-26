# runpod-runner

**Async Python library to provision a [RunPod](https://runpod.io) pod, run a
codebase on it, persist the artifacts, and tear the pod down** — a library
replacement for the `runpod-runner` bash driver.

Two layers:

| Layer | What you get |
|-------|--------------|
| `pod(...)` | a composable **Pod resource** — provision, wait *until functional*, then `exec` / `push` / `pull` against it as many times as you like. The bash driver can't do this. |
| `run(...)` | the **one-shot pipeline**: create → wait-functional → upload codebase → setup+run → pull results → upload to GCS → teardown → structured `RunResult`. |

It talks to the RunPod **REST API** directly (`rest.runpod.io/v1`) over `httpx`
— no `runpodctl`, no `runpod` SDK. GCS upload runs on *your* box, so cloud
credentials never touch the pod.

## Install

```bash
pip install -e .            # needs RUNPOD_API_KEY in the environment
```

Connection uses your SSH keypair (`~/.ssh/id_ed25519` by default): the public
key is injected as the pod's `PUBLIC_KEY` env so `root@pod` is reachable.

## The crux: "return when functional"

`desiredStatus == RUNNING` is necessary but **not sufficient** — sshd / your
server lags the RUNNING state by 30–60s. So after the pod is routable
(`RUNNING` + public IP + mapped port), the runner runs a **readiness probe**
until it passes. "Functional" is caller-specific, so it's pluggable:

```python
from runpod_runner import SshProbe, TcpProbe, HttpProbe, LogMarkerProbe

ready = SshProbe("true")                       # ssh job pods (default)
ready = HttpProbe(8000, "/health")             # a served endpoint
ready = LogMarkerProbe("=== server up")        # headless pods
```

## One-shot (the run.sh replacement)

```python
import asyncio
from runpod_runner import run, RunSpec, PodConfig

res = asyncio.run(run(
    RunSpec(slug="demo", codebase="./mycode", run="python go.py"),
    PodConfig(compute="gpu", gpu_id="NVIDIA GeForce RTX 4090"),
))
print(res.remote_exit, res.gcs_uri, res.retrieve_cmd)
```

CLI with the same flags as the old driver:

```bash
runpod-runner run --slug demo --codebase ./mycode --run "python go.py" \
                  --compute gpu --gpu-id "NVIDIA GeForce RTX 4090"
```

## Composable pod (multi-step / interactive)

```python
async with pod(PodConfig(compute="gpu", gpu_id="NVIDIA GeForce RTX 4090")) as p:
    await p.push("./code", "/workspace/job")
    r = await p.exec("cd /workspace/job && python train.py", env={"HF_TOKEN": tok})
    await p.exec("python eval.py")                  # same pod, no re-provision
    await p.pull("/workspace/job/results", "./out")
    print(p.proxy_url(8000))                         # https://<id>-8000.proxy.runpod.net
# pod torn down on exit (pass keep=True to leave it up)
```

## Fan-out a sweep

```python
from dataclasses import replace
from runpod_runner import run_many

specs = [replace(base, slug=f"lr{lr}", run=f"python train.py --lr {lr}")
         for lr in (1e-4, 3e-4, 1e-3)]
results = await run_many(specs, gpu_cfg, max_concurrency=4)
```

## Typed errors

`RunpodError` subclasses map 1:1 to the old exit-code ladder:
`PreflightError` (10), `ProvisionError` (20), `PodNotReadyError` (30),
`RemoteJobError` (40, carries `.remote_exit` + `.log_tail`),
`ResultsMissingError` (50), `GcsUploadError` (60).

## What's preserved from the bash driver

- env vars exported *inside* the remote script over stdin (sshd sessions don't
  inherit container PID-1 env; secrets stay off `ps`),
- tar-over-ssh transfer (needs only `tar`+`ssh` in the image),
- GCS upload from the local box,
- COMMUNITY→SECURE out-of-stock fallback,
- always-teardown via `finally`.

## Known gap vs the bash driver

The bash `--terminate-after` set a **server-side** self-destruct (survives the
client dying). RunPod REST v1 has no equivalent field, so teardown here is
client-side (`finally`) only. A crashed *host* process can still leak a pod.
Backstop options on the roadmap: a `dockerStartCmd` deadline that self-deletes,
or an external reaper. Until then, don't `kill -9` the driver.
