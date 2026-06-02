#!/usr/bin/env python3
"""GPU worker entrypoint — runs one Gaussian-splat job, then self-terminates.

Launched by EC2 user-data on the per-job g4dn.xlarge instance. Reads the job
row from DynamoDB, downloads the two .insv files from S3, runs the (Linux)
pipeline, streams ``##STAGE:`` markers into DynamoDB so the web UI shows live
progress, uploads the trained ``scene.ply`` to S3, deletes the input .insv, and
shuts the instance down (the launch template terminates on shutdown).

Required env:
  JOB_ID, JOBS_TABLE, UPLOADS_BUCKET, OUTPUTS_BUCKET
Optional env:
  AWS_REGION (boto3 default chain otherwise)
  SCRATCH_DIR        (default /scratch)
  MAX_RUNTIME_SECS   (hard wall-clock cap; default 21600 = 6h)
  SELF_TERMINATE     ("1" to `shutdown -h now` on exit; set by user-data)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import boto3

WORKER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKER_DIR))

from stages import JobStatus  # noqa: E402

# preset -> pipeline CLI args. Mirrors the table in the project plan.
PRESETS = {
    "Draft":  dict(downscale=4, frame_interval=0.5,  lfs_iters=5000,  lfs_max_cap=300000),
    "Low":    dict(downscale=2, frame_interval=0.5,  lfs_iters=10000, lfs_max_cap=500000),
    "Medium": dict(downscale=1, frame_interval=0.3,  lfs_iters=15000, lfs_max_cap=750000),
    "High":   dict(downscale=1, frame_interval=0.25, lfs_iters=30000, lfs_max_cap=2000000),
}

STAGE_RE = re.compile(r"##STAGE:([a-z_]+)")

# Substrings that mean the GPU ran out of memory — surfaced as a clear failure
# message instead of a cryptic stack trace (the most likely High-preset failure
# on the T4's 16 GB).
_OOM_MARKERS = (
    "out of memory",
    "CUDA out of memory",
    "cudaErrorMemoryAllocation",
    "OOM in memory_arena.cu",
)


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"missing required env var: {name}")
    return val  # type: ignore[return-value]


def _arm_watchdog(max_secs: int, self_terminate: bool) -> None:
    """Force shutdown after ``max_secs`` no matter what the pipeline is doing."""
    def _kill():
        time.sleep(max_secs)
        sys.stderr.write(f"[watchdog] exceeded {max_secs}s wall clock — forcing shutdown\n")
        sys.stderr.flush()
        if self_terminate:
            subprocess.run(["sudo", "shutdown", "-h", "now"], check=False)
        else:
            os._exit(2)
    threading.Thread(target=_kill, daemon=True).start()


def _download_inputs(s3, bucket: str, front_key: str, back_key: str, dest: Path) -> tuple[Path, Path]:
    dest.mkdir(parents=True, exist_ok=True)
    front = dest / Path(front_key).name
    back = dest / Path(back_key).name
    print(f"[worker] downloading s3://{bucket}/{front_key}")
    s3.download_file(bucket, front_key, str(front))
    print(f"[worker] downloading s3://{bucket}/{back_key}")
    s3.download_file(bucket, back_key, str(back))
    return front, back


def _build_pipeline_cmd(front: Path, back: Path, out_dir: Path, preset: str, masking: bool) -> list[str]:
    cfg = PRESETS[preset]
    cmd = [
        sys.executable, "-u", "-m", "backend.pipeline.pipeline_lichtfeld",
        str(front), str(back),
        "-o", str(out_dir),
        "-f", str(cfg["frame_interval"]),
        "--downscale", str(cfg["downscale"]),
        "--lfs-iters", str(cfg["lfs_iters"]),
        "--lfs-max-cap", str(cfg["lfs_max_cap"]),
    ]
    if not masking:
        cmd.append("--skip-masking")
    return cmd


def _pipeline_env() -> dict:
    """Environment for the pipeline subprocess.

    Prepends the CUDA forward-compat dir to LD_LIBRARY_PATH so COLMAP (and any
    other CUDA tool that inherits this env) uses the cuda-compat libcuda. The
    AMI's CUDA 12.8 toolkit links libcudart that the 550-series driver rejects
    natively ("unsupported display driver / cuda driver combination"); the
    compat libcuda bridges it on the T4. MediaSDK and the LichtFeld wrapper set
    their own LD_LIBRARY_PATH on top of this.
    """
    env = os.environ.copy()
    compat = "/usr/local/cuda-12.8/compat"
    existing = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = compat + (os.pathsep + existing if existing else "")
    return env


def _run_pipeline(cmd: list[str], job: JobStatus, log_path: Path) -> None:
    """Run the pipeline, mapping ##STAGE markers to DynamoDB updates and writing
    the full stdout/stderr to ``log_path`` (uploaded to S3 for debugging).

    Raises RuntimeError on nonzero exit (with an OOM-specific message when the
    log shows a memory failure).
    """
    print(f"[worker] running: {' '.join(cmd)}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        cmd, cwd=str(WORKER_DIR), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1, env=_pipeline_env(),
    )
    assert proc.stdout is not None
    saw_oom = False
    tail: list[str] = []
    with open(log_path, "w", encoding="utf-8") as lf:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            lf.write(line)
            tail.append(line)
            if len(tail) > 50:
                tail.pop(0)
            m = STAGE_RE.search(line)
            if m:
                job.set_stage(m.group(1))
            if not saw_oom and any(mk in line for mk in _OOM_MARKERS):
                saw_oom = True
    rc = proc.wait()
    if rc != 0:
        if saw_oom:
            raise RuntimeError(
                "GPU ran out of memory during training. Try a lower quality "
                "preset (the High preset can exceed the T4's 16 GB)."
            )
        raise RuntimeError(f"pipeline exited with code {rc}. Last log lines:\n" + "".join(tail))


def _find_output_ply(out_dir: Path) -> Path:
    """The single-section pipeline writes the trained splat to <out>/splat/."""
    candidates = sorted(
        (out_dir / "splat").rglob("*.ply"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not candidates:
        # fall back to anywhere under the output dir
        candidates = sorted(out_dir.rglob("*.ply"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError(f"no .ply produced under {out_dir}")
    return candidates[0]


def main() -> int:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    job_id = _env("JOB_ID", required=True)
    table = _env("JOBS_TABLE", required=True)
    uploads_bucket = _env("UPLOADS_BUCKET", required=True)
    outputs_bucket = _env("OUTPUTS_BUCKET", required=True)
    scratch = Path(_env("SCRATCH_DIR", "/scratch"))
    max_secs = int(_env("MAX_RUNTIME_SECS", "21600"))
    self_terminate = _env("SELF_TERMINATE", "0") == "1"

    _arm_watchdog(max_secs, self_terminate)

    s3 = boto3.client("s3", region_name=region)
    job = JobStatus(table, job_id, region=region)

    out_dir = scratch / job_id / "out"
    log_path = scratch / job_id / "pipeline.log"
    front_key = back_key = None
    succeeded = False
    try:
        job.set_stage("booting", status="RUNNING")
        item = job.get()
        preset = item.get("preset", "Draft")
        masking = bool(item.get("masking", False))
        front_key = item["frontKey"]
        back_key = item["backKey"]
        if preset not in PRESETS:
            raise RuntimeError(f"unknown preset {preset!r}")

        job.set_stage("downloading")
        in_dir = scratch / job_id / "in"
        front, back = _download_inputs(s3, uploads_bucket, front_key, back_key, in_dir)

        cmd = _build_pipeline_cmd(front, back, out_dir, preset, masking)
        _run_pipeline(cmd, job, log_path)

        ply = _find_output_ply(out_dir)
        job.set_stage("uploading_result")
        out_key = f"outputs/{job_id}/scene.ply"
        print(f"[worker] uploading {ply} -> s3://{outputs_bucket}/{out_key}")
        s3.upload_file(
            str(ply), outputs_bucket, out_key,
            ExtraArgs={"ContentType": "application/octet-stream"},
        )
        # Record the output key, mark done.
        job._table.update_item(  # noqa: SLF001 - tiny helper, intentional
            Key={"jobId": job_id},
            UpdateExpression="SET outputKey = :k",
            ExpressionAttributeValues={":k": out_key},
        )
        job.set_stage("done", status="SUCCEEDED")
        succeeded = True
        print("[worker] job complete")
        return 0

    except Exception as e:  # noqa: BLE001 - top-level worker guard
        traceback.print_exc()
        try:
            job.set_stage("failed", status="FAILED", error=str(e))
        except Exception:
            traceback.print_exc()
        return 1

    finally:
        # Always persist logs to S3 so failures are debuggable after the
        # instance self-terminates (outputs/logs/{jobId}/).
        for src, name in ((log_path, "pipeline.log"),
                          (out_dir / "colmap" / "colmap.log", "colmap.log"),
                          (out_dir / "lichtfeld_train.log", "lichtfeld_train.log")):
            try:
                if src.exists():
                    s3.upload_file(str(src), outputs_bucket, f"logs/{job_id}/{name}")
            except Exception:
                traceback.print_exc()

        # Delete the input .insv only on success. On failure keep them so the
        # job can be reproduced/debugged (the uploads bucket lifecycle expires
        # them after 2 days regardless).
        if succeeded:
            for key in (front_key, back_key):
                if not key:
                    continue
                try:
                    s3.delete_object(Bucket=uploads_bucket, Key=key)
                    print(f"[worker] deleted input s3://{uploads_bucket}/{key}")
                except Exception:
                    traceback.print_exc()
        if self_terminate:
            print("[worker] shutting down (instance terminates on shutdown)")
            sys.stdout.flush()
            subprocess.run(["sudo", "shutdown", "-h", "now"], check=False)


if __name__ == "__main__":
    sys.exit(main())
