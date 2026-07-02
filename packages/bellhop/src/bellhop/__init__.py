"""bellhop: check your code into an ephemeral box (RunPod pod or Modal sandbox), run it, bring results back, check out."""

from .backend import ExecBox, ExecResult, open_box
from .errors import (
    BellhopError,
    GcsUploadError,
    PodNotReadyError,
    PreflightError,
    ProvisionError,
    RemoteJobError,
    ResultsMissingError,
    RunpodError,
)
from .graphql import RunpodGraphQL
from .modal_box import ModalConfig, Sandbox, sandbox
from .pod import GPU_ALIASES, IMAGE_PRESETS, Pod, PodConfig, pod
from .probes import HttpProbe, LogMarkerProbe, ReadyProbe, SshProbe, TcpProbe
from .rest import RunpodRest
from .run import RunResult, RunSpec, run, run_many

__all__ = [
    # backend-agnostic surface
    "run", "run_many", "RunSpec", "RunResult",
    "open_box", "ExecBox", "ExecResult",
    # RunPod backend
    "pod", "Pod", "PodConfig", "IMAGE_PRESETS", "GPU_ALIASES",
    "RunpodRest", "RunpodGraphQL",
    "ReadyProbe", "SshProbe", "TcpProbe", "HttpProbe", "LogMarkerProbe",
    # Modal backend
    "sandbox", "Sandbox", "ModalConfig",
    # errors
    "BellhopError", "RunpodError", "PreflightError", "ProvisionError", "PodNotReadyError",
    "RemoteJobError", "ResultsMissingError", "GcsUploadError",
]
