"""The Nebius backend — multi-node GPU clusters on Nebius AI Cloud.

The Nebius analogue of :mod:`bellhop.cluster`: provision N VMs joined to one
InfiniBand fabric, drive them as a :class:`~bellhop.cluster.Cluster`, tear
everything down on exit. Differences from the RunPod Instant Clusters path,
which shape this module:

- **VMs, not containers.** A node is (boot disk from an image family) +
  (instance on a GPU platform/preset). There is no image pull and no
  ``PUBLIC_KEY`` convention; ssh access is injected via cloud-init.
- **No bidding.** Nebius prices are posted, not auctioned — create either
  succeeds or fails (quota / capacity). ``preemptible=True`` selects the
  cheaper interruptible tier.
- **Ranks are ours.** Nebius injects no ``NODE_RANK``; we create instances
  rank-by-rank (``<name>-0``, ``<name>-1``, …) so rank == creation index and
  the rendezvous env is derived exactly as on RunPod.
- **The fabric is the cluster.** A ``GpuCluster`` resource pins all member
  instances to one physical InfiniBand fabric (e.g. ``fabric-7`` for H200 in
  eu-north1); NCCL then uses IB verbs for collectives. The fabric name is
  region-specific and required — see docs.nebius.com/compute/clusters/gpu.
- **Teardown is client-owned** (same as RunPod clusters): the context manager
  always deletes instances, then boot disks, then the GpuCluster; a watchdog
  enforces ``max_lifetime``; ``gc_nebius`` reaps leaks by name prefix + age.

``nebius`` (the official gRPC SDK) is an optional dependency
(``pip install 'bellhop-py[nebius]'``), imported lazily. Auth follows the SDK:
``NEBIUS_IAM_TOKEN`` env var, or pass a configured ``SDK`` object. The parent
project comes from ``project_id=`` or the ``NEBIUS_PROJECT_ID`` env var.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from .cluster import Cluster, DEFAULT_RDZV_PORT
from .errors import PodNotReadyError, PreflightError, ProvisionError
from .pod import Pod, PodConfig
from .probes import ReadyProbe, SshProbe

# Platform/preset vocabulary (docs.nebius.com/compute/virtual-machines/types).
# Keyed by the same canonical short names as pod.GPU_ALIASES so `gpu="H200"`
# means the same card on either backend.
NEBIUS_PLATFORMS: dict[str, str] = {
    "H100": "gpu-h100-sxm",
    "H200": "gpu-h200-sxm",
    "B200": "gpu-b200-sxm",
    "B300": "gpu-b300-sxm",
}
NEBIUS_PRESETS: dict[str, dict[int, str]] = {
    "gpu-h100-sxm": {1: "1gpu-16vcpu-200gb", 8: "8gpu-128vcpu-1600gb"},
    "gpu-h200-sxm": {1: "1gpu-16vcpu-200gb", 8: "8gpu-128vcpu-1600gb"},
    "gpu-b200-sxm": {1: "1gpu-20vcpu-224gb", 8: "8gpu-160vcpu-1792gb"},
    "gpu-b300-sxm": {1: "1gpu-24vcpu-346gb", 8: "8gpu-192vcpu-2768gb"},
}

# GPU image families ship NVIDIA drivers + CUDA userland preinstalled.
DEFAULT_IMAGE_FAMILY = "ubuntu22.04-cuda12"


def _import_nebius():
    try:
        import nebius  # noqa: F401
        return nebius
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise PreflightError(
            "the Nebius backend needs the 'nebius' SDK: "
            "pip install 'bellhop-py[nebius]'"
        ) from e


def _cloud_init(user: str, pubkey: str) -> str:
    return (
        "#cloud-config\n"
        "users:\n"
        f"  - name: {user}\n"
        "    sudo: ALL=(ALL) NOPASSWD:ALL\n"
        "    shell: /bin/bash\n"
        "    ssh_authorized_keys:\n"
        f"      - {pubkey}\n"
    )


@dataclass
class NebiusClusterConfig:
    """Shape of a Nebius GPU cluster; the Nebius sibling of ClusterConfig."""

    fabric: str                           # InfiniBand fabric id, e.g. "fabric-7" (region-specific)
    gpu: str = "H200"                     # canonical short name or a verbatim platform id
    nodes: int = 2
    gpu_count: int = 8                    # picks the preset (1 or 8 per platform)
    platform: str | None = None           # verbatim override, wins over gpu=
    preset: str | None = None             # verbatim override, wins over gpu_count=
    project_id: str | None = None         # default: NEBIUS_PROJECT_ID env
    subnet_id: str | None = None          # default: first subnet in the project
    image_family: str = DEFAULT_IMAGE_FAMILY
    boot_disk_gb: int = 500
    preemptible: bool = False
    # auth / connection (per-node, same fields as PodConfig)
    ssh_key: str | None = None
    ssh_user: str = "bellhop"             # created via cloud-init on every node
    ready: ReadyProbe = field(default_factory=lambda: SshProbe("true"))
    provision_timeout: timedelta = timedelta(seconds=900)
    ready_timeout: timedelta = timedelta(seconds=900)
    poll_interval: float = 10.0
    # client-side hard cap — Nebius has no server-side TTL either
    max_lifetime: timedelta = timedelta(hours=24)
    rendezvous_port: int = DEFAULT_RDZV_PORT
    # bootstrap NIC for torch.distributed / NCCL out-of-band traffic; the
    # collectives themselves go over the IB fabric. None = let NCCL pick.
    nccl_socket_ifname: str | None = "eth0"
    name: str = "bellhop"                 # resource-name prefix (also what gc reaps)

    def __post_init__(self):
        if self.nodes < 2:
            raise PreflightError("a cluster needs nodes >= 2 (use PodConfig for one box)")
        if not self.fabric:
            raise PreflightError(
                "fabric= is required: the InfiniBand fabric id pins all nodes to one "
                "physical fabric (region-specific, e.g. 'fabric-7' for H200 in eu-north1)"
            )

    def resolve_project_id(self) -> str:
        proj = self.project_id or os.environ.get("NEBIUS_PROJECT_ID")
        if not proj:
            raise PreflightError(
                "no Nebius project: set project_id= or the NEBIUS_PROJECT_ID env var")
        return proj

    def resolve_platform(self) -> str:
        if self.platform:
            return self.platform
        hit = NEBIUS_PLATFORMS.get(self.gpu.upper())
        if hit:
            return hit
        if self.gpu.startswith("gpu-"):
            return self.gpu  # verbatim platform id
        raise PreflightError(
            f"unknown gpu {self.gpu!r} for Nebius; known: {sorted(NEBIUS_PLATFORMS)} "
            "(a verbatim platform id like 'gpu-h200-sxm' also works)")

    def resolve_preset(self) -> str:
        if self.preset:
            return self.preset
        platform = self.resolve_platform()
        presets = NEBIUS_PRESETS.get(platform)
        if not presets:
            raise PreflightError(
                f"no preset table for platform {platform!r}; pass preset= explicitly")
        hit = presets.get(self.gpu_count)
        if not hit:
            raise PreflightError(
                f"platform {platform!r} has no {self.gpu_count}-GPU preset "
                f"(available: {sorted(presets)}); pass preset= to override")
        return hit

    def _node_pod_config(self) -> PodConfig:
        """Per-node PodConfig carrying the ssh/probe/timeout settings.

        ``gpu`` is left unset on purpose: this config never provisions a
        RunPod pod — it only feeds the inherited ssh channel (key/user/probe
        timeouts) and ``gpu_count`` for the rank env.
        """
        return PodConfig(
            gpu_count=self.gpu_count,
            ssh_key=self.ssh_key, ssh_user=self.ssh_user, ready=self.ready,
            provision_timeout=self.provision_timeout,
            ready_timeout=self.ready_timeout, poll_interval=self.poll_interval,
            name=self.name,
        )


class _NebiusApi:
    """Thin, awaitable facade over the SDK: proto types stay behind this line.

    Every method takes/returns plain Python so NebiusNode and the context
    manager are testable with a fake. Construction is lazy so importing this
    module never requires the ``nebius`` package.
    """

    def __init__(self, sdk=None):
        _import_nebius()
        if sdk is None:
            from nebius.sdk import SDK
            sdk = SDK(user_agent_prefix="bellhop")
        self._sdk = sdk
        from nebius.api.nebius.compute.v1 import (
            DiskServiceClient, GpuClusterServiceClient, InstanceServiceClient)
        from nebius.api.nebius.vpc.v1 import SubnetServiceClient
        self._instances = InstanceServiceClient(sdk)
        self._disks = DiskServiceClient(sdk)
        self._clusters = GpuClusterServiceClient(sdk)
        self._subnets = SubnetServiceClient(sdk)

    async def first_subnet(self, project_id: str) -> str:
        from nebius.api.nebius.vpc.v1 import ListSubnetsRequest
        resp = await self._subnets.list(ListSubnetsRequest(parent_id=project_id))
        items = list(resp.items)
        if not items:
            raise ProvisionError(f"project {project_id} has no VPC subnet")
        return items[0].metadata.id

    async def create_gpu_cluster(self, project_id: str, name: str, fabric: str) -> str:
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.compute.v1 import CreateGpuClusterRequest, GpuClusterSpec
        op = await self._clusters.create(CreateGpuClusterRequest(
            metadata=ResourceMetadata(parent_id=project_id, name=name),
            spec=GpuClusterSpec(infiniband_fabric=fabric)))
        await op.wait()
        return op.resource_id

    async def create_boot_disk(self, project_id: str, name: str, *,
                               size_gb: int, image_family: str) -> str:
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.compute.v1 import (
            CreateDiskRequest, DiskSpec, SourceImageFamily)
        op = await self._disks.create(CreateDiskRequest(
            metadata=ResourceMetadata(parent_id=project_id, name=name),
            spec=DiskSpec(
                size_gibibytes=size_gb,
                type=DiskSpec.DiskType.NETWORK_SSD,
                source_image_family=SourceImageFamily(image_family=image_family))))
        await op.wait()
        return op.resource_id

    async def create_instance(self, project_id: str, name: str, *, platform: str,
                              preset: str, gpu_cluster_id: str, boot_disk_id: str,
                              subnet_id: str, cloud_init_user_data: str,
                              preemptible: bool) -> str:
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.compute.v1 import (
            AttachedDiskSpec, CreateInstanceRequest, ExistingDisk, IPAddress,
            InstanceGpuClusterSpec, InstanceSpec, NetworkInterfaceSpec,
            PreemptibleSpec, PublicIPAddress, ResourcesSpec)
        # InstanceSpec.preemptible is a PreemptibleSpec message, not a bool:
        # request the cheaper interruptible tier (STOP on preemption) only when
        # asked, and leave the field at its default otherwise.
        preempt = (PreemptibleSpec(on_preemption=PreemptibleSpec.PreemptionPolicy.STOP)
                   if preemptible else None)
        op = await self._instances.create(CreateInstanceRequest(
            metadata=ResourceMetadata(parent_id=project_id, name=name),
            spec=InstanceSpec(
                resources=ResourcesSpec(platform=platform, preset=preset),
                gpu_cluster=InstanceGpuClusterSpec(id=gpu_cluster_id),
                boot_disk=AttachedDiskSpec(
                    attach_mode=AttachedDiskSpec.AttachMode.READ_WRITE,
                    existing_disk=ExistingDisk(id=boot_disk_id)),
                network_interfaces=[NetworkInterfaceSpec(
                    subnet_id=subnet_id, name="eth0",
                    ip_address=IPAddress(),
                    public_ip_address=PublicIPAddress())],
                cloud_init_user_data=cloud_init_user_data,
                preemptible=preempt)))
        # settle the create op so the resource exists before we poll
        # get_instance for RUNNING + addressable (the long, minutes-scale wait)
        await op.wait()
        return op.resource_id

    async def get_instance(self, instance_id: str) -> dict:
        """→ {"state": str, "public_ip": str|None, "private_ip": str|None}."""
        from nebius.api.nebius.compute.v1 import GetInstanceRequest, InstanceStatus
        inst = await self._instances.get(GetInstanceRequest(id=instance_id))
        state = InstanceStatus.InstanceState(inst.status.state).name
        pub = priv = None
        for ni in inst.status.network_interfaces:
            if ni.public_ip_address and ni.public_ip_address.address:
                pub = ni.public_ip_address.address.split("/")[0]
            if ni.ip_address and ni.ip_address.address:
                priv = ni.ip_address.address.split("/")[0]
        return {"state": state, "public_ip": pub, "private_ip": priv}

    async def delete_instance(self, instance_id: str) -> None:
        from nebius.api.nebius.compute.v1 import DeleteInstanceRequest
        op = await self._instances.delete(DeleteInstanceRequest(id=instance_id))
        await op.wait()

    async def delete_disk(self, disk_id: str) -> None:
        from nebius.api.nebius.compute.v1 import DeleteDiskRequest
        op = await self._disks.delete(DeleteDiskRequest(id=disk_id))
        await op.wait()

    async def delete_gpu_cluster(self, cluster_id: str) -> None:
        from nebius.api.nebius.compute.v1 import DeleteGpuClusterRequest
        op = await self._clusters.delete(DeleteGpuClusterRequest(id=cluster_id))
        await op.wait()

    async def list_named(self, project_id: str, kind: str) -> list[dict]:
        """List instances|disks|gpu_clusters → [{"id", "name", "created_at"}]."""
        from nebius.api.nebius.compute.v1 import (
            ListDisksRequest, ListGpuClustersRequest, ListInstancesRequest)
        svc, req = {
            "instances": (self._instances, ListInstancesRequest),
            "disks": (self._disks, ListDisksRequest),
            "gpu_clusters": (self._clusters, ListGpuClustersRequest),
        }[kind]
        resp = await svc.list(req(parent_id=project_id))
        return [{"id": it.metadata.id, "name": it.metadata.name,
                 "created_at": it.metadata.created_at} for it in resp.items]


class NebiusNode(Pod):
    """One VM of a Nebius cluster, driven over plain ssh (port 22, public IP).

    Inherits the whole ssh channel (exec / push / pull / probes) from
    :class:`~bellhop.pod.Pod` and replaces the RunPod-specific lifecycle:
    state comes from the Nebius SDK, the ssh port is always 22, and teardown
    deletes the instance then its boot disk.
    """

    def __init__(self, api: _NebiusApi, instance_id: str, config: PodConfig,
                 boot_disk_id: str):
        super().__init__(rest=None, pod_id=instance_id, config=config)  # type: ignore[arg-type]
        self._api = api
        self.boot_disk_id = boot_disk_id

    # ---- lifecycle overrides (everything ssh is inherited) -----------------
    async def refresh(self) -> dict:
        info = await self._api.get_instance(self.id)
        self._meta = {"publicIp": info["public_ip"], "privateIp": info["private_ip"],
                      "desiredStatus": info["state"]}
        return self._meta

    @property
    def private_ip(self) -> str | None:
        return self._meta.get("privateIp")

    def mapped_port(self, container_port: int = 22) -> int | None:
        return container_port  # VMs listen directly; no port mapping layer

    def proxy_url(self, container_port: int) -> str:
        raise PreflightError("Nebius has no RunPod-style HTTP proxy; connect to the public IP")

    async def _wait_provision(self) -> None:
        deadline = time.monotonic() + self.config.provision_timeout.total_seconds()
        while True:
            await self.refresh()
            if self.status in ("ERROR", "DELETING"):
                raise ProvisionError(f"instance {self.id} entered terminal state {self.status}")
            if self.status == "RUNNING" and self.host and self.private_ip:
                return
            if time.monotonic() >= deadline:
                raise PodNotReadyError(
                    f"instance {self.id} not RUNNING+addressable within "
                    f"{self.config.provision_timeout.total_seconds():.0f}s (state={self.status})")
            await asyncio.sleep(self.config.poll_interval)

    async def teardown(self) -> None:
        await self._api.delete_instance(self.id)
        await self._api.delete_disk(self.boot_disk_id)


async def _create_tracked(coros, into: list[str], what: str) -> None:
    """Gather resource creations, recording every success into ``into`` BEFORE
    raising on any failure — so a partial fan-out never leaks paid resources
    (the ids in ``into`` are what teardown deletes)."""
    results = await asyncio.gather(*coros, return_exceptions=True)
    into.extend(r for r in results if isinstance(r, str))
    errs = [r for r in results if isinstance(r, BaseException)]
    if errs:
        raise ProvisionError(
            f"{what} failed on {len(errs)}/{len(results)} nodes: {errs[0]}") from errs[0]


async def _teardown_all(api: _NebiusApi, instance_ids: list[str],
                        disk_ids: list[str], cluster_id: str | None) -> None:
    """Best-effort full teardown; safe on partially-created fleets.

    A failed delete leaves paid GPUs running, so every failure is logged to
    stderr (kind + id) rather than swallowed — but order is preserved: all
    instances (concurrently), then disks, then the cluster.
    """
    results = await asyncio.gather(
        *(api.delete_instance(i) for i in instance_ids), return_exceptions=True)
    for iid, r in zip(instance_ids, results, strict=True):  # settle before disks detach
        if isinstance(r, BaseException):
            print(f"bellhop: nebius instance {iid} delete failed: {r!r}",
                  file=sys.stderr, flush=True)
    for d in disk_ids:
        try:
            await api.delete_disk(d)
        except Exception as e:
            print(f"bellhop: nebius disk {d} delete failed: {e!r}",
                  file=sys.stderr, flush=True)
    if cluster_id:
        try:
            await api.delete_gpu_cluster(cluster_id)
        except Exception as e:
            print(f"bellhop: nebius gpu-cluster {cluster_id} delete failed: {e!r}",
                  file=sys.stderr, flush=True)


async def _lifetime_watchdog(cluster_id: str, lifetime: timedelta, teardown) -> None:
    """Sleep out ``max_lifetime`` then run the shared single-shot ``teardown``."""
    await asyncio.sleep(lifetime.total_seconds())
    print(f"bellhop: nebius cluster {cluster_id} hit max_lifetime {lifetime} — tearing down",
          file=sys.stderr, flush=True)
    with contextlib.suppress(Exception):
        await teardown()


@contextlib.asynccontextmanager
async def nebius_cluster(config: NebiusClusterConfig, *, sdk=None, _api=None):
    """Provision a Nebius GPU cluster, yield a :class:`Cluster`, always tear down.

    ``sdk`` is an optional pre-configured ``nebius.sdk.SDK`` (else env auth);
    ``_api`` injects a fake API facade in tests.
    """
    node_cfg = config._node_pod_config()
    pubkey = node_cfg.pubkey_text()       # preflight ssh key before spending money
    project = config.resolve_project_id()
    platform, preset = config.resolve_platform(), config.resolve_preset()

    api = _api or _NebiusApi(sdk)
    subnet = config.subnet_id or await api.first_subnet(project)
    # One per-run suffix keeps config.name as the (gc-matchable) prefix while
    # making every resource name unique, so concurrent runs sharing a name
    # never see or reap each other's fleet.
    prefix = f"{config.name}-{uuid4().hex[:8]}"
    cluster_id: str | None = None
    disk_ids: list[str] = []
    instance_ids: list[str] = []

    torn_down = False
    async def _teardown_once() -> None:
        # single-shot: the watchdog may fire mid-run and the ctx exit also
        # tears down — running _teardown_all twice would issue concurrent
        # deletes on the same ids, which Nebius rejects.
        nonlocal torn_down
        if torn_down:
            return
        torn_down = True
        await _teardown_all(api, instance_ids, disk_ids, cluster_id)

    try:
        cluster_id = await api.create_gpu_cluster(project, prefix, config.fabric)
        await _create_tracked(
            (api.create_boot_disk(project, f"{prefix}-{r}-boot",
                                  size_gb=config.boot_disk_gb,
                                  image_family=config.image_family)
             for r in range(config.nodes)),
            disk_ids, "boot-disk create")
        user_data = _cloud_init(config.ssh_user, pubkey)
        await _create_tracked(
            (api.create_instance(project, f"{prefix}-{r}", platform=platform,
                                 preset=preset, gpu_cluster_id=cluster_id,
                                 boot_disk_id=disk_ids[r], subnet_id=subnet,
                                 cloud_init_user_data=user_data,
                                 preemptible=config.preemptible)
             for r in range(config.nodes)),
            instance_ids, "instance create")
        nodes = [NebiusNode(api, iid, node_cfg, did)
                 for iid, did in zip(instance_ids, disk_ids, strict=True)]
        await asyncio.gather(*(n._wait_provision() for n in nodes))
        await asyncio.gather(*(n._wait_ready() for n in nodes))
        ips = {rank: n.private_ip for rank, n in enumerate(nodes)}
        clu = Cluster(cluster_id, nodes, ips, config.rendezvous_port,
                      nccl_socket_ifname=config.nccl_socket_ifname,
                      workdir=f"/home/{config.ssh_user}")
        watchdog = asyncio.create_task(
            _lifetime_watchdog(cluster_id, config.max_lifetime, _teardown_once))
        try:
            yield clu
        finally:
            # drain the watchdog before tearing down, so its teardown (if any)
            # can't race the ctx-exit teardown on the same resource ids
            watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog
    finally:
        await _teardown_once()


async def gc_nebius(older_than: timedelta, *, project_id: str | None = None,
                    name_prefix: str = "bellhop", sdk=None, _api=None,
                    dry_run: bool = False) -> list[dict]:
    """Reap bellhop-named Nebius resources older than ``older_than``.

    Matches by name prefix (instances/disks/GPU clusters created by this
    module are all named ``<config.name>...``), the same contract as
    ``gc_clusters`` on RunPod: the missing server-side TTL, done client-side.
    """
    from datetime import datetime, timezone

    project = project_id or os.environ.get("NEBIUS_PROJECT_ID")
    if not project:
        raise PreflightError("no Nebius project: pass project_id= or set NEBIUS_PROJECT_ID")
    api = _api or _NebiusApi(sdk)
    now = datetime.now(timezone.utc)
    reaped: list[dict] = []
    # instances first (disks can't go while attached), clusters last
    for kind, deleter in (("instances", api.delete_instance),
                          ("disks", api.delete_disk),
                          ("gpu_clusters", api.delete_gpu_cluster)):
        for item in await api.list_named(project, kind):
            if not (item["name"] or "").startswith(name_prefix):
                continue
            created = item["created_at"]
            if created is None:
                continue  # unknown age is not old age — never reap
            if (now - created) < older_than:
                continue
            if not dry_run:
                with contextlib.suppress(Exception):
                    await deleter(item["id"])
            reaped.append({**item, "kind": kind})
    return reaped
