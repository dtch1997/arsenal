"""``run()`` — the one-shot pipeline: a library replacement for run.sh.

create -> wait-functional -> upload codebase -> setup+run (tee'd) -> pull
results -> upload to GCS -> teardown -> structured RunResult.
"""

from __future__ import annotations

import asyncio
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from .errors import GcsUploadError, PreflightError, RemoteJobError, ResultsMissingError
from .pod import PodConfig, pod

DEFAULT_GCS_BASE = "gs://alignment-team-general-storage/daniel/jarvis/experiments"


@dataclass
class RunSpec:
    slug: str
    codebase: str                       # local dir OR git URL (auto-detected)
    run: str                            # the job (required)
    setup: str | None = None            # deps, run before `run`
    results_subdir: str = "results"     # path on the pod to pull back
    local_out: str | None = None        # default ./experiments/<slug>
    gcs_base: str | None = DEFAULT_GCS_BASE   # set None to skip GCS upload
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class RunResult:
    slug: str
    pod_id: str
    remote_exit: int
    local_results: str
    gcs_uri: str | None
    retrieve_cmd: str | None
    log_tail: str


def _is_git(codebase: str) -> bool:
    return codebase.startswith(("http://", "https://", "git@"))


async def run(spec: RunSpec, pod_config: PodConfig, *, keep_pod: bool = False,
              api_key: str | None = None) -> RunResult:
    if not (spec.slug and spec.codebase and spec.run):
        raise PreflightError("slug, codebase and run are all required")
    if not _is_git(spec.codebase) and not Path(spec.codebase).is_dir():
        raise PreflightError(f"codebase dir not found: {spec.codebase}")

    local_out = spec.local_out or os.path.join(os.getcwd(), "experiments", spec.slug)
    Path(local_out).mkdir(parents=True, exist_ok=True)
    run_dir = f"/workspace/{spec.slug}"
    results_remote = f"{run_dir}/{spec.results_subdir}"
    pod_config.name = f"runpod-runner-{spec.slug}"

    async with pod(pod_config, keep=keep_pod, api_key=api_key) as p:
        # --- upload codebase ---
        if _is_git(spec.codebase):
            r = await p.exec(f"git clone --depth 1 {shlex.quote(spec.codebase)} {shlex.quote(run_dir)}")
            if r.exit_code != 0:
                raise RemoteJobError("git clone failed", remote_exit=r.exit_code, log_tail=r.stderr[-2000:])
        else:
            await p.exec(f"mkdir -p {shlex.quote(run_dir)}")
            await p.push(spec.codebase, run_dir)

        # --- run (setup then job), tee'd to a log that travels back ---
        setup_block = f"echo '--- setup ---'\n{spec.setup}\n" if spec.setup else ""
        job = (
            f"cd {shlex.quote(run_dir)}\n"
            f"mkdir -p {shlex.quote(spec.results_subdir)}\n"
            f"{{\n{setup_block}echo '--- run ---'\n{spec.run}\n}} 2>&1 | tee {shlex.quote(spec.results_subdir + '/run.log')}\n"
            f"exit ${{PIPESTATUS[0]}}\n"
        )
        job_res = await p.exec(job, env=spec.env)
        remote_exit = job_res.exit_code

        # --- pull results ---
        if await p.exists_remote(results_remote):
            await p.pull(results_remote, local_out)
        elif remote_exit == 0:
            raise ResultsMissingError(f"job succeeded but no results dir at {results_remote}")

        # --- upload to GCS (from this box; creds never touch the pod) ---
        gcs_uri = retrieve_cmd = None
        if spec.gcs_base:
            gcs_uri = spec.gcs_base.rstrip("/") + f"/{spec.slug}/"
            await _gcs_upload(local_out, gcs_uri)
            retrieve_cmd = f"gcloud storage cp -r {gcs_uri} ./"

        log_tail = _tail(os.path.join(local_out, spec.results_subdir, "run.log"))
        result = RunResult(
            slug=spec.slug, pod_id=p.id, remote_exit=remote_exit,
            local_results=local_out, gcs_uri=gcs_uri, retrieve_cmd=retrieve_cmd, log_tail=log_tail,
        )

    if remote_exit != 0:
        raise RemoteJobError(f"remote job exited {remote_exit}", remote_exit=remote_exit, log_tail=result.log_tail)
    return result


async def run_many(specs: list[RunSpec], pod_config: PodConfig, *,
                   max_concurrency: int = 4, **kw) -> list[RunResult | BaseException]:
    """Fan a sweep out across pods. Returns results/exceptions positionally."""
    sem = asyncio.Semaphore(max_concurrency)

    async def _one(s: RunSpec):
        async with sem:
            return await run(s, pod_config, **kw)

    return await asyncio.gather(*(_one(s) for s in specs), return_exceptions=True)


async def _gcs_upload(local_dir: str, gcs_uri: str) -> None:
    entries = list(Path(local_dir).iterdir())
    if not entries:
        return
    proc = await asyncio.create_subprocess_exec(
        "gcloud", "storage", "cp", "-r", *[str(e) for e in entries], gcs_uri,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise GcsUploadError(f"gcloud upload failed: {err.decode('utf-8','replace')[:500]}")


def _tail(path: str, n: int = 20) -> str:
    try:
        return "\n".join(Path(path).read_text("utf-8", "replace").splitlines()[-n:])
    except Exception:
        return ""
