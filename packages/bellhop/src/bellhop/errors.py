"""Exception hierarchy.

Mirrors the exit-code ladder of the original ``run.sh`` driver so callers can
branch on failure mode instead of parsing exit codes:

    10 preflight, 20 provision, 30 ssh-never-ready,
    40 remote-job-failed, 50 results-missing, 60 gcs-upload-failed.
"""

from __future__ import annotations


class RunpodError(Exception):
    """Base for everything this library raises."""

    exit_code = 1


class PreflightError(RunpodError):
    """Bad config or missing local prerequisite (key, gcloud, codebase)."""

    exit_code = 10


class ProvisionError(RunpodError):
    """Pod create failed (e.g. out of stock, bad image/gpu id)."""

    exit_code = 20


class PodNotReadyError(RunpodError):
    """Pod never became functional within the timeout."""

    exit_code = 30


class RemoteJobError(RunpodError):
    """The remote command(s) exited non-zero."""

    exit_code = 40

    def __init__(self, message: str, *, remote_exit: int, log_tail: str = ""):
        super().__init__(message)
        self.remote_exit = remote_exit
        self.log_tail = log_tail


class ResultsMissingError(RunpodError):
    """The job produced no results directory to pull back."""

    exit_code = 50


class GcsUploadError(RunpodError):
    """Uploading the pulled artifacts to GCS failed."""

    exit_code = 60
