"""Hardware calibration.

Throwaway spike harness for gm-database-schema-gkt.1, adapted from
``investigations/calibration/calibrate.py`` on the owner's earlier
``db-alternatives`` branch.

Standard library only, deliberately. It is copied to a bare Ubuntu instance and
run with the system ``python3``; there is no virtualenv there and installing one
to measure a machine would be measuring the install.

What it is for. The same workload is measured on a 2 vCPU Docker VM on a laptop
and on an 8 vCPU IBM Cloud instance, and a reader has to be able to tell which
differences are the engine and which are the machine. This writes down what the
machine can do so the spike document can say that rather than assume it.

What it is NOT for. The spike does not rescale a measured latency by a
calibration factor and report the result as if it had been measured. The
db-alternatives harness does that, in two places, with two different and
mutually inconsistent conventions. Every latency in this spike's document is a
latency something actually took on a machine named beside it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from typing import Any


CALIBRATION_VERSION = 1


def _cpu_single_thread(duration_sec: float = 5.0) -> dict[str, Any]:
    block = os.urandom(4096)
    ops = 0
    deadline = time.perf_counter() + duration_sec
    while time.perf_counter() < deadline:
        hashlib.sha256(block).digest()
        ops += 1
    elapsed = duration_sec
    return {"test": "cpu_single_thread_sha256", "ops": ops, "duration_sec": elapsed, "ops_per_sec": round(ops / elapsed, 1)}


def _cpu_multi_thread(duration_sec: float = 5.0) -> dict[str, Any]:
    cores = os.cpu_count() or 1
    block = os.urandom(4096)

    def worker() -> int:
        ops = 0
        deadline = time.perf_counter() + duration_sec
        while time.perf_counter() < deadline:
            hashlib.sha256(block).digest()
            ops += 1
        return ops

    with ThreadPoolExecutor(max_workers=cores) as pool:
        ops = sum(f.result() for f in [pool.submit(worker) for _ in range(cores)])
    return {
        "test": "cpu_multi_thread_sha256",
        "cores": cores,
        "ops": ops,
        "duration_sec": duration_sec,
        "ops_per_sec": round(ops / duration_sec, 1),
    }


def _memory_bandwidth() -> dict[str, Any]:
    size = 64 * 1024 * 1024
    buf = bytearray(size)
    started = time.perf_counter()
    for _ in range(5):
        for offset in range(0, size, 4096):
            buf[offset] = 1
    write_sec = time.perf_counter() - started
    started = time.perf_counter()
    total = 0
    for _ in range(5):
        for offset in range(0, size, 4096):
            total += buf[offset]
    read_sec = time.perf_counter() - started
    mb = (size / (1024 * 1024)) * 5
    return {
        "test": "memory_bandwidth_64mb",
        "write_mb_per_sec": round(mb / write_sec, 1),
        "read_mb_per_sec": round(mb / read_sec, 1),
    }


def _disk_sequential_write(size_mb: int = 256) -> dict[str, Any]:
    chunk = os.urandom(1024 * 1024)
    target = Path("calibration-scratch.bin")
    started = time.perf_counter()
    with target.open("wb") as handle:
        for _ in range(size_mb):
            handle.write(chunk)
        handle.flush()
        os.fsync(handle.fileno())
    elapsed = time.perf_counter() - started
    return {
        "test": "disk_sequential_write",
        "size_mb": size_mb,
        "duration_sec": round(elapsed, 3),
        "mb_per_sec": round(size_mb / elapsed, 1),
    }


def _disk_sequential_read(size_mb: int = 256) -> dict[str, Any]:
    target = Path("calibration-scratch.bin")
    with suppress(Exception):
        Path("/proc/sys/vm/drop_caches").write_text("3")
    started = time.perf_counter()
    with target.open("rb") as handle:
        while handle.read(1024 * 1024):
            pass
    elapsed = time.perf_counter() - started
    return {
        "test": "disk_sequential_read",
        "size_mb": size_mb,
        "duration_sec": round(elapsed, 3),
        "mb_per_sec": round(size_mb / elapsed, 1),
    }


def _disk_random_iops(duration_sec: float = 5.0) -> dict[str, Any]:
    target = Path("calibration-scratch.bin")
    size = target.stat().st_size
    ops = 0
    deadline = time.perf_counter() + duration_sec
    with target.open("rb") as handle:
        while time.perf_counter() < deadline:
            handle.seek((ops * 104_729 * 4096) % max(size - 4096, 1))
            handle.read(4096)
            ops += 1
    return {"test": "disk_random_read_4k", "duration_sec": duration_sec, "iops": round(ops / duration_sec, 1)}


def _python_sort_throughput() -> dict[str, Any]:
    import random  # noqa: PLC0415

    data = [random.random() for _ in range(1_000_000)]  # noqa: S311
    iterations = 5
    started = time.perf_counter()
    for _ in range(iterations):
        sorted(data)
    elapsed = time.perf_counter() - started
    return {
        "test": "python_sort_1m_floats",
        "elements": 1_000_000,
        "iterations": iterations,
        "avg_sec": round(elapsed / iterations, 4),
        "sorts_per_sec": round(iterations / elapsed, 3),
    }


def _ram_gb() -> float:
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / (1024 * 1024), 2)
    with suppress(Exception):
        out = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True)
        return round(int(out.stdout.strip()) / (1024**3), 2)
    return 0.0


def _system_info() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python_version": platform.python_version(),
        "ram_gb": _ram_gb(),
    }


def run_calibration() -> dict[str, Any]:
    benchmarks = {
        "cpu_single_thread": _cpu_single_thread(),
        "cpu_multi_thread": _cpu_multi_thread(),
        "memory_bandwidth": _memory_bandwidth(),
        "disk_sequential_write": _disk_sequential_write(),
        "disk_sequential_read": _disk_sequential_read(),
        "disk_random_read": _disk_random_iops(),
        "python_sort": _python_sort_throughput(),
    }
    Path("calibration-scratch.bin").unlink(missing_ok=True)
    return {
        "calibration_version": CALIBRATION_VERSION,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "system": _system_info(),
        "benchmarks": benchmarks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure what this machine can do.")
    parser.add_argument("--output", "-o", type=Path, default=Path("calibration.json"))
    parser.add_argument("--label", default="", help="a name for this machine, carried into the report")
    args = parser.parse_args(argv)
    payload = run_calibration()
    if args.label:
        payload["label"] = args.label
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2))
    sys.stderr.write(f"wrote {args.output}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
