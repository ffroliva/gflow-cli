"""Session-scoped Docker container fixtures for storage integration tests.

Requires Docker daemon and the ``containers`` dependency group::

    uv run --group containers pytest -m containers tests/integration/

Tests are skipped automatically when ``testcontainers`` or the cloud-storage
extras are not installed.
"""

from __future__ import annotations

import os
import socket
import time
import urllib.request
from collections.abc import Generator
from typing import Any

import pytest

from tests.integration.constants import (
    FAKE_GCS_IMAGE,
    GCS_BUCKET,
    MINIO_ACCESS_KEY,
    MINIO_BUCKET,
    MINIO_IMAGE,
    MINIO_SECRET_KEY,
    STORAGE_PREFIX,
)


def _wait_http_200(url: str, timeout: float = 30.0) -> None:
    """Poll *url* until it returns HTTP 200 or *timeout* elapses."""
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
                if resp.status == 200:
                    return
        except Exception as exc:
            last_exc = exc
            time.sleep(0.5)
    raise TimeoutError(f"Service did not become healthy at {url!r}: {last_exc}")


def _free_tcp_port() -> int:
    """Reserve and release a free localhost port for Docker host binding."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ---------------------------------------------------------------------------
# S3 / MinIO
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def minio_storage_uri() -> Generator[str, None, None]:
    """Start MinIO in Docker, configure AWS env vars, yield s3:// URI.

    The fixture is skipped when ``testcontainers`` or ``s3fs`` are not
    installed, or when Docker is unavailable.
    """
    # Use importorskip to both guard against missing deps and get the module.
    # Accessing DockerContainer via the module avoids a `from X import Y`
    # inside the function body, which ruff's isort (I001) would flag.
    tc_container_mod: Any = pytest.importorskip("testcontainers.core.container")
    s3fs_mod: Any = pytest.importorskip("s3fs")
    pytest.importorskip("upath")

    docker_container_cls = tc_container_mod.DockerContainer

    container = docker_container_cls(MINIO_IMAGE)
    container.with_command("server /data")
    container.with_env("MINIO_ROOT_USER", MINIO_ACCESS_KEY)
    container.with_env("MINIO_ROOT_PASSWORD", MINIO_SECRET_KEY)
    container.with_exposed_ports(9000)

    with container:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(9000))
        endpoint_url = f"http://{host}:{port}"
        _wait_http_200(f"{endpoint_url}/minio/health/live")

        # Create the test bucket via s3fs (avoids an extra minio-py dependency).
        fs = s3fs_mod.S3FileSystem(
            key=MINIO_ACCESS_KEY,
            secret=MINIO_SECRET_KEY,
            endpoint_url=endpoint_url,
            client_kwargs={"region_name": "us-east-1"},
        )
        fs.mkdir(MINIO_BUCKET)
        s3fs_mod.S3FileSystem.clear_instance_cache()

        env_vars = {
            "AWS_ACCESS_KEY_ID": MINIO_ACCESS_KEY,
            "AWS_SECRET_ACCESS_KEY": MINIO_SECRET_KEY,
            "AWS_ENDPOINT_URL": endpoint_url,
            "AWS_DEFAULT_REGION": "us-east-1",
        }
        saved = {k: os.environ.get(k) for k in env_vars}
        os.environ.update(env_vars)
        s3fs_mod.S3FileSystem.clear_instance_cache()

        try:
            yield f"s3://{MINIO_BUCKET}/{STORAGE_PREFIX}"
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            s3fs_mod.S3FileSystem.clear_instance_cache()


# ---------------------------------------------------------------------------
# GCS / fake-gcs-server
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def fake_gcs_storage_uri() -> Generator[str, None, None]:
    """Start fake-gcs-server in Docker, configure STORAGE_EMULATOR_HOST, yield gs:// URI.

    The fixture is skipped when ``testcontainers`` or ``gcsfs`` are not
    installed, or when Docker is unavailable.
    """
    tc_container_mod: Any = pytest.importorskip("testcontainers.core.container")
    gcsfs_mod: Any = pytest.importorskip("gcsfs")
    pytest.importorskip("upath")

    docker_container_cls = tc_container_mod.DockerContainer

    host_port = _free_tcp_port()
    external_url = f"http://127.0.0.1:{host_port}"
    container = docker_container_cls(FAKE_GCS_IMAGE)
    container.with_command(f"-scheme http -port 4443 -backend memory -external-url {external_url}")
    container.with_bind_ports(4443, host_port)

    with container:
        emulator_host = external_url
        _wait_http_200(f"{emulator_host}/storage/v1/b")

        # Create the test bucket via gcsfs.
        fs = gcsfs_mod.GCSFileSystem(
            endpoint_url=emulator_host,
            token="anon",
        )
        fs.mkdir(GCS_BUCKET)

        saved_emulator = os.environ.get("STORAGE_EMULATOR_HOST")
        os.environ["STORAGE_EMULATOR_HOST"] = emulator_host

        try:
            yield f"gs://{GCS_BUCKET}/{STORAGE_PREFIX}"
        finally:
            if saved_emulator is None:
                os.environ.pop("STORAGE_EMULATOR_HOST", None)
            else:
                os.environ["STORAGE_EMULATOR_HOST"] = saved_emulator
