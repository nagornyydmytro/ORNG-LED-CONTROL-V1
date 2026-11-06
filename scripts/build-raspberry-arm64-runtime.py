#!/usr/bin/env python3
"""Build a relocatable Linux aarch64 Python ≥3.12 runtime + arm64 wheelhouse via Docker.

Uses Docker Desktop ``--platform linux/arm64`` (QEMU). Output is written outside
the git worktree by default (TEMP). Does not publish releases or touch hardware.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"

DOCKERFILE = r'''
FROM --platform=linux/arm64 debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl xz-utils build-essential \
    libssl-dev zlib1g-dev libffi-dev libbz2-dev libreadline-dev \
    libsqlite3-dev libncursesw5-dev liblzma-dev tk-dev \
    linux-libc-dev \
    libportaudio2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

# python-build-standalone install_only (relocatable, Bookworm-compatible glibc).
ARG PBS_URL
ARG PBS_SHA256
WORKDIR /tmp
RUN curl -fsSL -o pbs.tar.gz "$PBS_URL" \
    && echo "$PBS_SHA256  pbs.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/orng-runtime \
    && tar -xzf pbs.tar.gz -C /opt/orng-runtime --strip-components=1 \
    && rm pbs.tar.gz \
    && /opt/orng-runtime/bin/python3 --version \
    && /opt/orng-runtime/bin/python3 -c "import platform; assert platform.machine()=='aarch64'"

# Ensure `python` exists next to python3.
RUN if [ ! -e /opt/orng-runtime/bin/python ]; then \
      ln -s python3 /opt/orng-runtime/bin/python; \
    fi

WORKDIR /src
COPY backend/pyproject.toml /src/backend/pyproject.toml
COPY backend/README.md /src/backend/README.md
COPY backend/orng_led /src/backend/orng_led

# Install application + cloud/linux deps into the embedded runtime (no PyPI later).
RUN /opt/orng-runtime/bin/python -m pip install --upgrade pip setuptools wheel \
    && /opt/orng-runtime/bin/python -m pip install \
        "/src/backend[cloud,linux]" \
    && /opt/orng-runtime/bin/python -c "import fastapi,uvicorn,pydantic,pydantic_core,yaml,httpx,websockets,cryptography,evdev,numpy,sounddevice,orng_led"

# Recovery wheelhouse (aarch64 binaries / sdists built here).
RUN mkdir -p /out/wheelhouse \
    && /opt/orng-runtime/bin/python -m pip download \
        -d /out/wheelhouse \
        "/src/backend[cloud,linux]" \
    && test -n "$(find /out/wheelhouse -name '*.whl' -o -name '*.tar.gz' | head -1)"

# Strip caches / tests to control size.
RUN find /opt/orng-runtime -type d -name '__pycache__' -prune -exec rm -rf {} + \
    && find /opt/orng-runtime -type d -name 'tests' -prune -exec rm -rf {} + \
    && find /opt/orng-runtime -type d -name 'test' -prune -exec rm -rf {} + \
    && rm -rf /opt/orng-runtime/share/man /opt/orng-runtime/share/doc || true

RUN mkdir -p /out/runtime \
    && cp -a /opt/orng-runtime/. /out/runtime/ \
    && /out/runtime/bin/python -c "import sys,platform; print(sys.version); print(platform.machine())" \
    && printf '%s\n' \
        "python=$(/out/runtime/bin/python -c 'import sys; print(\"%d.%d.%d\"%sys.version_info[:3])')" \
        "machine=$(/out/runtime/bin/python -c 'import platform; print(platform.machine())')" \
        "wheels=$(find /out/wheelhouse -name '*.whl' | wc -l)" \
        > /out/RUNTIME_META.txt
'''


# Pin a known aarch64 install_only build (update with checksum when bumping).
# Source: python-build-standalone / astral-sh releases.
DEFAULT_PBS_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    "20241016/cpython-3.12.7%2B20241016-aarch64-unknown-linux-gnu-install_only.tar.gz"
)
DEFAULT_PBS_SHA256 = (
    "bba3c6be6153f715f2941da34f3a6a69c2d0035c9c5396bc5bb68c6d2bd1065a"
)


def _default_out() -> Path:
    base = Path(tempfile.gettempdir()) / "orng-led-arm64"
    return base


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--pbs-url", default=os.environ.get("ORNG_PBS_URL", DEFAULT_PBS_URL))
    parser.add_argument(
        "--pbs-sha256",
        default=os.environ.get("ORNG_PBS_SHA256", DEFAULT_PBS_SHA256),
    )
    parser.add_argument("--keep-context", action="store_true")
    args = parser.parse_args()
    out = (args.out or _default_out()).resolve()
    out.mkdir(parents=True, exist_ok=True)

    # Verify docker arm64 works before a long build.
    probe = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/arm64",
            "debian:bookworm-slim",
            "uname",
            "-m",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0 or "aarch64" not in (probe.stdout or ""):
        print("Docker linux/arm64 probe failed:", probe.stderr or probe.stdout, file=sys.stderr)
        return 2

    ctx = Path(tempfile.mkdtemp(prefix="orng-arm64-ctx-"))
    try:
        (ctx / "Dockerfile").write_text(DOCKERFILE.strip() + "\n", encoding="utf-8")
        # Minimal build context: only backend package sources.
        shutil.copytree(
            BACKEND / "orng_led",
            ctx / "backend" / "orng_led",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
        shutil.copy2(BACKEND / "pyproject.toml", ctx / "backend" / "pyproject.toml")
        # hatchling needs a package; README optional
        readme = BACKEND / "README.md"
        if readme.is_file():
            shutil.copy2(readme, ctx / "backend" / "README.md")
        else:
            (ctx / "backend" / "README.md").write_text("orng-led-control\n", encoding="utf-8")

        image = "orng-led-arm64-runtime:bookworm"
        print("Building", image, "…", flush=True)
        subprocess.check_call(
            [
                "docker",
                "buildx",
                "build",
                "--platform",
                "linux/arm64",
                "--load",
                "--build-arg",
                f"PBS_URL={args.pbs_url}",
                "--build-arg",
                f"PBS_SHA256={args.pbs_sha256}",
                "-t",
                image,
                str(ctx),
            ]
        )

        # Extract /out from a throwaway container.
        name = "orng-arm64-extract"
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        subprocess.check_call(
            ["docker", "create", "--name", name, "--platform", "linux/arm64", image, "true"]
        )
        runtime_dest = out / "runtime"
        wheel_dest = out / "wheelhouse"
        if runtime_dest.exists():
            shutil.rmtree(runtime_dest)
        if wheel_dest.exists():
            shutil.rmtree(wheel_dest)
        subprocess.check_call(["docker", "cp", f"{name}:/out/runtime", str(runtime_dest)])
        subprocess.check_call(["docker", "cp", f"{name}:/out/wheelhouse", str(wheel_dest)])
        meta_host = out / "RUNTIME_META.txt"
        subprocess.check_call(["docker", "cp", f"{name}:/out/RUNTIME_META.txt", str(meta_host)])
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)

        py = runtime_dest / "bin" / "python"
        if not py.is_file():
            # Some PBS layouts only ship python3.
            py3 = runtime_dest / "bin" / "python3"
            if py3.is_file():
                if os.name == "nt":
                    # Symlinks from Linux extract may be broken on Windows; leave python3.
                    pass
                else:
                    py.symlink_to("python3")
            else:
                print("runtime/bin/python missing after extract", file=sys.stderr)
                return 3

        wheels = list(wheel_dest.glob("*.whl")) + list(wheel_dest.glob("*.tar.gz"))
        meta = {
            "pbs_url": args.pbs_url,
            "pbs_sha256": args.pbs_sha256,
            "runtime_root": str(runtime_dest),
            "wheelhouse": str(wheel_dest),
            "wheel_count": len(wheels),
            "meta_txt": meta_host.read_text(encoding="utf-8") if meta_host.is_file() else "",
        }
        (out / "build-meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(meta, indent=2))
        print("ORNG_RASPBERRY_RUNTIME_ROOT=" + str(runtime_dest))
        return 0
    finally:
        if not args.keep_context:
            shutil.rmtree(ctx, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
