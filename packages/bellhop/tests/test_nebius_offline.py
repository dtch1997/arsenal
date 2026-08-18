"""Offline unit tests for bellhop.nebius_box — fake SDK facade, no live cluster."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from bellhop import NebiusClusterConfig, PreflightError, ProvisionError, RunSpec
from bellhop.cluster import Cluster, run_cluster
from bellhop.nebius_box import _cloud_init, gc_nebius, nebius_cluster


class _AlwaysReady:
    async def __call__(self, pod):
        return True


async def _drain(ctx):
    """Enter and immediately exit an async cluster ctx (provision + teardown)."""
    async with ctx:
        pass


def _cfg(tmp_path, **kw):
    key = tmp_path / "id"
    key.write_text("x")
    (tmp_path / "id.pub").write_text("ssh-ed25519 AAAA test")
    kw.setdefault("fabric", "fabric-7")
    kw.setdefault("gpu", "H200")
    kw.setdefault("project_id", "project-e00test")
    kw.setdefault("subnet_id", "vpcsubnet-e00test")
    kw.setdefault("ssh_key", str(key))
    kw.setdefault("ready", _AlwaysReady())
    kw.setdefault("poll_interval", 0.0)
    return NebiusClusterConfig(**kw)


# ---- config resolution -------------------------------------------------------

def test_platform_preset_resolution(tmp_path):
    cfg = _cfg(tmp_path, gpu="H200", gpu_count=8)
    assert cfg.resolve_platform() == "gpu-h200-sxm"
    assert cfg.resolve_preset() == "8gpu-128vcpu-1600gb"
    assert _cfg(tmp_path, gpu="B200", gpu_count=1).resolve_preset() == "1gpu-20vcpu-224gb"


def test_verbatim_platform_and_preset_override(tmp_path):
    cfg = _cfg(tmp_path, gpu="gpu-h100-sxm", preset="8gpu-128vcpu-1600gb")
    assert cfg.resolve_platform() == "gpu-h100-sxm"
    assert cfg.resolve_preset() == "8gpu-128vcpu-1600gb"


def test_unknown_gpu_rejected(tmp_path):
    with pytest.raises(PreflightError, match="unknown gpu"):
        _cfg(tmp_path, gpu="TPU").resolve_platform()


def test_unavailable_preset_rejected(tmp_path):
    with pytest.raises(PreflightError, match="no 4-GPU preset"):
        _cfg(tmp_path, gpu_count=4).resolve_preset()


def test_fabric_required(tmp_path):
    with pytest.raises(PreflightError, match="fabric"):
        _cfg(tmp_path, fabric="")


def test_single_node_rejected(tmp_path):
    with pytest.raises(PreflightError, match="nodes >= 2"):
        _cfg(tmp_path, nodes=1)


def test_project_id_env_fallback(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, project_id=None)
    monkeypatch.delenv("NEBIUS_PROJECT_ID", raising=False)
    with pytest.raises(PreflightError, match="NEBIUS_PROJECT_ID"):
        cfg.resolve_project_id()
    monkeypatch.setenv("NEBIUS_PROJECT_ID", "project-fromenv")
    assert cfg.resolve_project_id() == "project-fromenv"


def test_cloud_init_contains_user_and_key():
    text = _cloud_init("bellhop", "ssh-ed25519 AAAA test")
    assert "#cloud-config" in text
    assert "name: bellhop" in text
    assert "- ssh-ed25519 AAAA test" in text


# ---- provision / teardown flow ----------------------------------------------

class _FakeApi:
    """In-memory _NebiusApi: instances go CREATING -> RUNNING on second poll."""

    def __init__(self, fail_instance_ranks=()):
        self.fail_instance_ranks = set(fail_instance_ranks)
        self.created = {"gpu_clusters": [], "disks": [], "instances": []}
        self.deleted = {"gpu_clusters": [], "disks": [], "instances": []}
        self.user_data = None
        self.instance_kw: dict[int, dict] = {}      # rank -> recorded create payload
        self._polls: dict[str, int] = {}
        self.listing: list[tuple[str, dict]] = []   # (kind, item) for gc tests

    async def first_subnet(self, project_id):
        return "vpcsubnet-first"

    async def create_gpu_cluster(self, project_id, name, fabric):
        cid = f"gpucluster-{len(self.created['gpu_clusters'])}"
        self.created["gpu_clusters"].append((cid, name, fabric))
        return cid

    async def create_boot_disk(self, project_id, name, *, size_gb, image_family):
        did = f"disk-{len(self.created['disks'])}"
        self.created["disks"].append((did, name, size_gb, image_family))
        return did

    async def create_instance(self, project_id, name, **kw):
        rank = int(name.rsplit("-", 1)[1])
        if rank in self.fail_instance_ranks:
            raise ProvisionError("Quota exceeded: compute.instances.gpus")
        self.user_data = kw["cloud_init_user_data"]
        # pin the create payload the lifecycle test asserts on
        self.instance_kw[rank] = {"preemptible": kw["preemptible"],
                                  "gpu_cluster_id": kw["gpu_cluster_id"]}
        iid = f"inst-{rank}"
        self.created["instances"].append((iid, name, kw["platform"], kw["preset"]))
        return iid

    async def get_instance(self, instance_id):
        n = self._polls[instance_id] = self._polls.get(instance_id, 0) + 1
        if n == 1:
            return {"state": "CREATING", "public_ip": None, "private_ip": None}
        rank = int(instance_id.rsplit("-", 1)[1])
        return {"state": "RUNNING", "public_ip": f"185.0.0.{10 + rank}",
                "private_ip": f"10.0.0.{10 + rank}"}

    async def delete_instance(self, iid):
        self.deleted["instances"].append(iid)

    async def delete_disk(self, did):
        self.deleted["disks"].append(did)

    async def delete_gpu_cluster(self, cid):
        self.deleted["gpu_clusters"].append(cid)

    async def list_named(self, project_id, kind):
        return [item for k, item in self.listing if k == kind]


def test_cluster_lifecycle(tmp_path):
    api = _FakeApi()
    cfg = _cfg(tmp_path, nodes=2)

    async def _run():
        async with nebius_cluster(cfg, _api=api) as clu:
            assert isinstance(clu, Cluster)
            assert clu.id == "gpucluster-0"
            assert [n.id for n in clu.nodes] == ["inst-0", "inst-1"]
            # rendezvous over private IPs, rank == creation index
            assert clu.node_ips == {0: "10.0.0.10", 1: "10.0.0.11"}
            assert clu.workdir == "/home/bellhop"
            env = clu.rank_env(1)
            assert env["MASTER_ADDR"] == "10.0.0.10"
            assert env["NODE_RANK"] == "1"
            assert env["NCCL_SOCKET_IFNAME"] == "eth0"
            # ssh goes to the public IP on plain port 22
            assert clu.nodes[1].host == "185.0.0.11"
            assert clu.nodes[1].mapped_port(22) == 22
            # cloud-init delivered the key for the configured user
            assert "name: bellhop" in api.user_data
            assert "ssh-ed25519 AAAA test" in api.user_data

    asyncio.run(_run())
    assert sorted(api.deleted["instances"]) == ["inst-0", "inst-1"]
    assert sorted(api.deleted["disks"]) == ["disk-0", "disk-1"]
    assert api.deleted["gpu_clusters"] == ["gpucluster-0"]
    # create payload: default tier is non-preemptible; every node joins the cluster
    assert api.instance_kw[0] == {"preemptible": False, "gpu_cluster_id": "gpucluster-0"}
    assert api.instance_kw[1] == {"preemptible": False, "gpu_cluster_id": "gpucluster-0"}
    # every resource name keeps config.name as prefix + one shared per-run suffix
    cl_name = api.created["gpu_clusters"][0][1]
    disk_names = [d[1] for d in api.created["disks"]]
    inst_names = [i[1] for i in api.created["instances"]]
    assert cl_name.startswith("bellhop-")
    suffix = cl_name[len("bellhop-"):]              # the uuid piece
    assert all(n.startswith(f"bellhop-{suffix}-") for n in disk_names + inst_names)


def test_preemptible_flag_flows_to_create(tmp_path):
    api = _FakeApi()

    async def _run():
        async with nebius_cluster(_cfg(tmp_path, nodes=2, preemptible=True), _api=api):
            pass

    asyncio.run(_run())
    assert api.instance_kw[0]["preemptible"] is True
    assert api.instance_kw[1]["preemptible"] is True


def test_per_run_suffix_differs_across_runs(tmp_path):
    """Two runs of the same config name never collide — gc can scope to one."""
    names = []
    for _ in range(2):
        api = _FakeApi()
        asyncio.run(_drain(nebius_cluster(_cfg(tmp_path, nodes=2), _api=api)))
        names.append(api.created["gpu_clusters"][0][1])
    assert names[0] != names[1]
    assert all(n.startswith("bellhop-") for n in names)


def test_partial_instance_failure_tears_down_survivors(tmp_path):
    api = _FakeApi(fail_instance_ranks={1})

    async def _run():
        async with nebius_cluster(_cfg(tmp_path, nodes=2), _api=api):
            pytest.fail("must not yield")

    with pytest.raises(ProvisionError, match="instance create failed"):
        asyncio.run(_run())
    # the rank-0 instance that DID come up is deleted, plus all disks + cluster
    assert api.deleted["instances"] == ["inst-0"]
    assert sorted(api.deleted["disks"]) == ["disk-0", "disk-1"]
    assert api.deleted["gpu_clusters"] == ["gpucluster-0"]


def test_watchdog_teardown_is_not_duplicated(tmp_path):
    """max_lifetime fires mid-run, then ctx-exit also runs — each id deleted once."""
    api = _FakeApi()
    cfg = _cfg(tmp_path, nodes=2, max_lifetime=timedelta(seconds=0))

    async def _run():
        async with nebius_cluster(cfg, _api=api):
            await asyncio.sleep(0.05)   # let the watchdog fire while yielded

    asyncio.run(_run())
    # single-shot teardown: no id appears twice despite two teardown paths
    assert sorted(api.deleted["instances"]) == ["inst-0", "inst-1"]
    assert sorted(api.deleted["disks"]) == ["disk-0", "disk-1"]
    assert api.deleted["gpu_clusters"] == ["gpucluster-0"]


def test_subnet_discovery_when_unset(tmp_path):
    api = _FakeApi()

    async def _run():
        async with nebius_cluster(_cfg(tmp_path, subnet_id=None), _api=api):
            pass

    asyncio.run(_run())
    assert api.created["instances"]  # provisioning went through the discovered subnet


def test_rank_env_without_ifname_omits_nccl_pin():
    class _N:
        def __init__(self):
            class _C:
                gpu_count = 8
            self.config = _C()
    clu = Cluster("cid", [_N(), _N()], {0: "10.0.0.1", 1: "10.0.0.2"},
                  nccl_socket_ifname=None)
    assert "NCCL_SOCKET_IFNAME" not in clu.rank_env(0)


def test_run_cluster_rejects_unknown_config(tmp_path):
    spec = RunSpec(slug="s", codebase=str(tmp_path), run="true", gcs_base=None)
    with pytest.raises(PreflightError, match="unknown cluster config"):
        asyncio.run(run_cluster(spec, object()))


# ---- gc ----------------------------------------------------------------------

def test_gc_reaps_by_prefix_and_age(tmp_path):
    api = _FakeApi()
    old = datetime.now(timezone.utc) - timedelta(hours=30)
    young = datetime.now(timezone.utc)
    api.listing = [
        ("instances", {"id": "inst-a", "name": "bellhop-0", "created_at": old}),
        ("instances", {"id": "inst-b", "name": "bellhop-1", "created_at": young}),
        ("instances", {"id": "inst-c", "name": "prod-db", "created_at": old}),
        # unknown creation time must never be treated as old
        ("instances", {"id": "inst-d", "name": "bellhop-2", "created_at": None}),
        ("disks", {"id": "disk-a", "name": "bellhop-0-boot", "created_at": old}),
        ("gpu_clusters", {"id": "gc-a", "name": "bellhop", "created_at": old}),
    ]
    reaped = asyncio.run(gc_nebius(timedelta(hours=24), project_id="project-x", _api=api))
    assert {r["id"] for r in reaped} == {"inst-a", "disk-a", "gc-a"}
    assert api.deleted["instances"] == ["inst-a"]      # young + foreign + unknown-age survive
    assert api.deleted["disks"] == ["disk-a"]
    assert api.deleted["gpu_clusters"] == ["gc-a"]


def test_gc_skips_unknown_created_at(tmp_path):
    """A resource whose created_at is None is never reaped (unknown age != old)."""
    api = _FakeApi()
    api.listing = [("instances", {"id": "i", "name": "bellhop-0", "created_at": None})]
    reaped = asyncio.run(gc_nebius(timedelta(hours=24), project_id="p", _api=api))
    assert reaped == []
    assert api.deleted["instances"] == []


def test_gc_dry_run_deletes_nothing():
    api = _FakeApi()
    old = datetime.now(timezone.utc) - timedelta(hours=30)
    api.listing = [("instances", {"id": "i", "name": "bellhop-0", "created_at": old})]
    reaped = asyncio.run(gc_nebius(timedelta(hours=24), project_id="p", _api=api, dry_run=True))
    assert [r["id"] for r in reaped] == ["i"]
    assert api.deleted["instances"] == []
