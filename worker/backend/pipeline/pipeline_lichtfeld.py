#!/usr/bin/env python3
"""
pipeline_lichtfeld.py

End-to-end pipeline: Insta360 .insv → LichtFeld Studio dataset
Replaces the manual Metashape step with free COLMAP SfM.

Steps:
  1. Insta360 MediaSDK → equirectangular frames  (insv_to_equirect.py)
  2. YOLO → person masks                          (mask_people_yolo.py)
  3. FFmpeg v360 → cubemap face images per folder
  4. COLMAP SfM on cubemap faces (rig-constrained)
  5. colmap_to_lichtfeld.py → transforms.json + equirect images + point cloud

Usage:
    python pipeline_lichtfeld.py VID_001_00.insv VID_001_10.insv -o my_dataset
    python pipeline_lichtfeld.py VID_001_00.insv VID_001_10.insv -o out --skip-top-bottom -f 0.5
    python pipeline_lichtfeld.py VID_001_00.insv VID_001_10.insv -o out --skip-masking --downscale 2
    python pipeline_lichtfeld.py VID_001_00.insv VID_001_10.insv -o out --sections 3

Output structure (single section):
    <output>/
      equirect/frames/      equirectangular frames
      equirect/frames/masks/  YOLO masks (if not --skip-masking)
      cubemap/              per-camera subdirs (front/, right/, back/, left/, ...)
      colmap/               COLMAP database + sparse reconstruction
      lichtfeld/            transforms.json + images/ + masks/ + pointcloud.ply
                            ← drag this folder into LichtFeld Studio

Output structure (--sections N):
    <output>/
      section_01/
        equirect/frames/
        cubemap/
        colmap/
        lichtfeld/
      section_02/
        ...
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Vendored layout: this file lives at <project_root>/backend/pipeline/
PIPELINE_DIR = Path(__file__).parent
PROJECT_ROOT = PIPELINE_DIR.parents[1]

from .custom_camera_extractor import CustomCameraExtractor
from .rig_config import create_custom_rig_config
from .colmap_runner import ColmapRunner
from .colmap_to_lichtfeld import convert as colmap_to_lichtfeld
from .insv_to_equirect import sdk_stitch_to_mp4


# ── Camera definitions ────────────────────────────────────────────────────────

# Standard 6-face cubemap. yaw=0=front, yaw=90=right (user-facing convention).
# CustomCameraExtractor negates yaw/pitch internally for FFmpeg.
# create_custom_rig_config uses these values to compute rig quaternions.
ALL_CAMERAS = [
    {"id": "front", "name": "Front", "yaw":   0, "pitch":   0, "roll": 0, "h_fov": 90, "v_fov": 90},
    {"id": "right", "name": "Right", "yaw":  90, "pitch":   0, "roll": 0, "h_fov": 90, "v_fov": 90},
    {"id": "back",  "name": "Back",  "yaw": 180, "pitch":   0, "roll": 0, "h_fov": 90, "v_fov": 90},
    {"id": "left",  "name": "Left",  "yaw": 270, "pitch":   0, "roll": 0, "h_fov": 90, "v_fov": 90},
    {"id": "up",    "name": "Up",    "yaw":   0, "pitch":  90, "roll": 0, "h_fov": 90, "v_fov": 90},
    {"id": "down",  "name": "Down",  "yaw":   0, "pitch": -90, "roll": 0, "h_fov": 90, "v_fov": 90},
]


def load_config(config_path: Path) -> dict:
    """Load config.json for tool paths."""
    with open(config_path) as f:
        return json.load(f)


def find_colmap_reconstruction(sparse_dir: Path) -> Path:
    """Find the COLMAP reconstruction dir containing cameras.txt.

    Prefer 'merged' (result of model_merger). Otherwise scan numeric
    sub-models and pick the one with the most registered images — the
    incremental mapper routinely emits a degenerate 2-image stub as
    sparse/0 alongside the real reconstruction, and picking by lowest
    number silently produces a 3-frame dataset with no point cloud.
    """
    merged = sparse_dir / 'merged'
    if merged.is_dir() and (merged / "cameras.txt").exists():
        return merged

    from .colmap_to_lichtfeld import parse_colmap_images
    candidates = []
    for name in sorted(os.listdir(sparse_dir)) if sparse_dir.is_dir() else []:
        sub = sparse_dir / name
        if not (name.isdigit() and sub.is_dir() and (sub / "images.txt").exists()):
            continue
        candidates.append((name, len(parse_colmap_images(sub / "images.txt"))))

    if not candidates:
        raise RuntimeError(
            f"No COLMAP reconstruction found in {sparse_dir}. "
            "Check the COLMAP log at <output>/colmap/colmap.log"
        )

    candidates.sort(key=lambda x: -x[1])
    chosen, chosen_n = candidates[0]
    if len(candidates) > 1:
        others = ", ".join(f"sparse/{d}({n})" for d, n in candidates[1:])
        print(f"Multiple reconstructions found; using sparse/{chosen} "
              f"({chosen_n} images), ignoring stub(s): {others}")
    return sparse_dir / chosen


def step(num: int, total: int, msg: str) -> None:
    print(f"\n{'='*60}")
    print(f"  Step {num}/{total}: {msg}")
    print(f"{'='*60}")


def emit_stage(name: str) -> None:
    """Emit a machine-readable stage marker on stdout.

    The cloud worker (worker/run_job.py) parses lines that start with
    ``##STAGE:`` and writes the stage to DynamoDB so the web UI can show
    live pipeline progress. Printing to stdout keeps the pipeline usable
    standalone (the markers are just harmless log lines locally).
    """
    print(f"##STAGE:{name}", flush=True)


def get_video_duration(ffmpeg_exe: str, mp4_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    ffmpeg_path = Path(ffmpeg_exe)
    ffprobe_exe = str(ffmpeg_path.with_name("ffprobe" + ffmpeg_path.suffix))
    if not Path(ffprobe_exe).exists():
        ffprobe_exe = "ffprobe"
    result = subprocess.run(
        [ffprobe_exe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", mp4_path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def extract_frames_range(
    ffmpeg_exe: str,
    mp4_path: str,
    output_dir: str,
    start_sec: float,
    end_sec: float,
    frame_interval: float,
) -> list:
    """Extract frames from [start_sec, end_sec] of an MP4 at the given interval."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    fps = 1.0 / frame_interval
    cmd = [
        ffmpeg_exe,
        "-ss", str(start_sec),
        "-to", str(end_sec),
        "-i", mp4_path,
        "-r", str(fps),
        "-q:v", "2",
        "-y",
        str(output_dir / "frame_%06d.jpg"),
    ]
    print(f"[FFMPEG] Extracting frames [{start_sec:.1f}s – {end_sec:.1f}s] at {fps:.3f} fps...")
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frames = sorted(output_dir.glob("frame_*.jpg"))
    print(f"[FFMPEG] Extracted {len(frames)} frames")
    return frames


# Substrings that mark the documented LichtFeld --gut CUDA crash family.
# When any of these appears in LichtFeld's stdout AND the binary exits
# nonzero, we treat the failure as the upstream intermittent bug and
# retry from scratch. See docs/TECH_DEBT.md and
# https://github.com/MrNeRF/LichtFeld-Studio/issues/1091.
_CUDA_CRASH_MARKERS = (
    "cudaErrorIllegalAddress",
    "CUDA memcpy failed in clone()",
    "tensor_strided_ops.cu:328",
    "OOM in memory_arena.cu",
)


def run_lichtfeld_training(
    lichtfeld_exe: str,
    dataset_dir: Path,
    output_dir: Path,
    iters: int,
    tile_mode: int,
    max_cap: int,
    gut: bool,
    alpha_as_mask: bool,
    extra_args: list,
    log_file: Path,
    max_retries: int = 2,
    retry_on_cuda_crash: bool = True,
) -> Path:
    """Run LichtFeld-Studio on the prepared dataset. Returns path to trained .ply.

    Retries up to ``max_retries`` times when LichtFeld exits nonzero AND its
    log contains the documented ``--gut`` CUDA crash signature (upstream
    issue #1091). Each retry wipes ``output_dir`` and re-invokes the binary
    with the same args. Set ``retry_on_cuda_crash=False`` for a single attempt.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        lichtfeld_exe,
        "-d", str(dataset_dir),
        "-o", str(output_dir),
        "--train",
        "--headless",
        f"--iter={iters}",
        f"--tile-mode={tile_mode}",
        f"--max-cap={max_cap}",
        # Make LichtFeld honour the YOLO masks emitted by the dataset
        # converter: masked-out pixels (people, etc.) are excluded from
        # the photometric loss instead of being treated as real geometry.
        "--mask-mode=ignore",
    ]
    if gut:
        cmd.append("--gut")
    if not alpha_as_mask:
        cmd.append("--no-alpha-as-mask")
    cmd.extend(extra_args)
    print(f"[LICHTFELD] Running: {' '.join(cmd)}")
    print(f"[LICHTFELD] Log file: {log_file}")

    total_attempts = (max_retries + 1) if retry_on_cuda_crash else 1
    last_rc = 0
    for attempt in range(1, total_attempts + 1):
        if attempt > 1:
            # Clean slate so LichtFeld doesn't pick up a partial
            # checkpoint or stale state from the previous attempt.
            shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

        banner = (
            f"=== Attempt {attempt}/{total_attempts} "
            f"({datetime.now().isoformat(timespec='seconds')}) ==="
        )
        if attempt > 1:
            print(f"[LICHTFELD] {banner}")

        log_mode = "w" if attempt == 1 else "a"
        saw_cuda_crash = False
        with open(log_file, log_mode, encoding="utf-8") as lf:
            if attempt > 1:
                lf.write(f"\n{banner}\n")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                sys.stdout.write(line)
                lf.write(line)
                if not saw_cuda_crash and any(m in line for m in _CUDA_CRASH_MARKERS):
                    saw_cuda_crash = True
            rc = proc.wait()
        last_rc = rc

        if rc == 0:
            plys = sorted(output_dir.rglob("*.ply"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not plys:
                raise RuntimeError(f"LichtFeld training finished but no .ply found in {output_dir}")
            return plys[0]

        # Nonzero exit. Retry only if the crash matches the known
        # intermittent signature; unrelated failures fail-fast.
        if not (retry_on_cuda_crash and saw_cuda_crash):
            raise RuntimeError(f"LichtFeld-Studio exited with code {rc}. See {log_file}")

        if attempt < total_attempts:
            remaining = total_attempts - attempt
            print(
                f"[LICHTFELD] CUDA crash signature detected on attempt "
                f"{attempt}/{total_attempts}; retrying ({remaining} left). "
                f"See upstream issue #1091."
            )

    print(f"[LICHTFELD] All {total_attempts} attempts hit the --gut CUDA crash; giving up.")
    raise RuntimeError(
        f"LichtFeld-Studio exited with code {last_rc} after {total_attempts} "
        f"attempts (every attempt hit the documented --gut CUDA crash signature). "
        f"See {log_file}"
    )


def process_section(
    section_dir: Path,
    equirect_frames: Path,
    args: argparse.Namespace,
    cameras: list,
    ffmpeg_exe: str,
    colmap_exe: str,
    section_label: str,
    total_steps: int,
) -> dict:
    """Run masking → cubemap → COLMAP → lichtfeld for one section."""
    mask_dir = equirect_frames / "masks"
    cubemap_dir = section_dir / "cubemap"
    cubemap_masks_dir = section_dir / "cubemap_masks"
    colmap_out_dir = section_dir / "colmap"
    lichtfeld_dir = section_dir / "lichtfeld"
    log_file = str(colmap_out_dir / "colmap.log")
    current_step = 2

    # YOLO masking
    if not args.skip_masking:
        step(current_step, total_steps, f"[{section_label}] Masking people with YOLO")
        mask_cmd = [
            sys.executable, str(PIPELINE_DIR / "mask_people_yolo.py"),
            str(equirect_frames),
            "--model", args.yolo_model,
            "--conf", str(args.yolo_conf),
        ]
        if args.bottom_mask > 0:
            mask_cmd += ["-bm", str(args.bottom_mask)]
        print(f"Running: {' '.join(mask_cmd)}")
        subprocess.run(mask_cmd, check=True)
        current_step += 1

    # Cubemap extraction
    step(current_step, total_steps, f"[{section_label}] Extracting cubemap faces")
    current_step += 1
    extractor = CustomCameraExtractor(ffmpeg_exe)
    extractor.extract_custom_cameras_from_all_frames(
        equirect_dir=str(equirect_frames),
        output_dir=str(cubemap_dir),
        cameras=cameras,
        image_size=2048,
        per_folder=True,
    )

    # Per-face mask extraction for COLMAP (skip if masking was disabled)
    colmap_mask_arg = None
    if not args.skip_masking and mask_dir.is_dir():
        extractor.extract_mask_faces_from_all_frames(
            equirect_dir=str(equirect_frames),
            mask_dir=str(mask_dir),
            output_dir=str(cubemap_masks_dir),
            cameras=cameras,
            image_size=2048,
        )
        colmap_mask_arg = str(cubemap_masks_dir)

    # COLMAP
    step(current_step, total_steps, f"[{section_label}] Running COLMAP sparse reconstruction")
    current_step += 1
    colmap_out_dir.mkdir(parents=True, exist_ok=True)
    rig_config_path = str(colmap_out_dir / "rig_config.json")
    create_custom_rig_config(rig_config_path, cameras)
    print(f"Rig config written: {rig_config_path}")
    runner = ColmapRunner(colmap_exe)
    runner.run(
        images_dir=str(cubemap_dir),
        output_dir=str(colmap_out_dir),
        log_file=log_file,
        rig_config_path=rig_config_path,
        camera_h_fov=90.0,
        camera_image_size=2048,
        mask_path=colmap_mask_arg,
    )
    print(f"COLMAP log: {log_file}")
    reconstruction_dir = find_colmap_reconstruction(colmap_out_dir / "sparse")
    print(f"Reconstruction: {reconstruction_dir}")

    # LichtFeld conversion. Cap initial points at 95% of LichtFeld's --max-cap
    # so densification has free slots in the pre-allocated buffer; LichtFeld
    # v0.5.0 crashes with cudaErrorIllegalAddress when it starts at 100%
    # utilization (clone-into-full-buffer in tensor_strided_ops.cu:328).
    step(current_step, total_steps, f"[{section_label}] Converting to LichtFeld format")
    mask_dir_arg = mask_dir if (not args.skip_masking and mask_dir.is_dir()) else None
    return colmap_to_lichtfeld(
        colmap_dir=reconstruction_dir,
        equirect_dir=equirect_frames,
        output_dir=lichtfeld_dir,
        mask_dir=mask_dir_arg,
        downscale=args.downscale,
        ref_cam_id="front",
        frame_offset=1,
        verbose=True,
        max_initial_points=int(args.lfs_max_cap * 0.95),
    )


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline(args: argparse.Namespace) -> None:
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(PROJECT_ROOT / "config.json")
    paths = config["paths"]
    ffmpeg_exe = args.ffmpeg or paths.get("ffmpeg_exe", "ffmpeg")
    colmap_exe = paths["colmap_exe"]

    lichtfeld_exe = None
    if not args.skip_train:
        lichtfeld_exe = args.lichtfeld_exe or paths.get("lichtfeld_exe")
        if not lichtfeld_exe or not Path(lichtfeld_exe).exists():
            raise RuntimeError(
                f"LichtFeld exe not found: {lichtfeld_exe}. "
                "Set paths.lichtfeld_exe in config.json or pass --lichtfeld-exe, "
                "or pass --skip-train to skip training."
            )
    extra_lfs_args = args.lfs_extra_args.split() if args.lfs_extra_args else []

    cameras = [c for c in ALL_CAMERAS
               if not (args.skip_top    and c["id"] == "up")
               and not (args.skip_bottom and c["id"] == "down")]

    print(f"Using {len(cameras)} cubemap faces: {[c['id'] for c in cameras]}")

    # ── Single-section (original behavior) ───────────────────────────────────
    if args.sections == 1:
        equirect_dir     = output_dir / "equirect"
        equirect_frames  = equirect_dir / "frames"
        mask_dir         = equirect_frames / "masks"
        cubemap_dir      = output_dir / "cubemap"
        cubemap_masks_dir = output_dir / "cubemap_masks"
        colmap_out_dir   = output_dir / "colmap"
        lichtfeld_dir    = output_dir / "lichtfeld"
        log_file         = str(colmap_out_dir / "colmap.log")
        total_steps      = (5 if not args.skip_masking else 4) + (0 if args.skip_train else 1)

        step(1, total_steps, "Converting .insv → equirectangular frames")
        insv_cmd = [
            sys.executable, str(PIPELINE_DIR / "insv_to_equirect.py"),
            args.front_insv, args.back_insv,
            "-o", str(equirect_dir),
            "-f", str(args.frame_interval),
            "--stitch-type", args.stitch_type,
            "--ffmpeg", ffmpeg_exe,
        ]
        if args.output_width:
            insv_cmd += ["--output-width", str(args.output_width)]
        if args.output_height:
            insv_cmd += ["--output-height", str(args.output_height)]
        print(f"Running: {' '.join(insv_cmd)}")
        subprocess.run(insv_cmd, check=True)

        frames = sorted(equirect_frames.glob("frame_*.jpg"))
        if not frames:
            raise RuntimeError(f"No frames found in {equirect_frames} after insv_to_equirect")
        print(f"Extracted {len(frames)} equirectangular frames")

        current_step = 2
        if not args.skip_masking:
            emit_stage("masking")
            step(current_step, total_steps, "Masking people with YOLO")
            mask_cmd = [
                sys.executable, str(PIPELINE_DIR / "mask_people_yolo.py"),
                str(equirect_frames),
                "--model", args.yolo_model,
                "--conf", str(args.yolo_conf),
            ]
            if args.bottom_mask > 0:
                mask_cmd += ["-bm", str(args.bottom_mask)]
            print(f"Running: {' '.join(mask_cmd)}")
            subprocess.run(mask_cmd, check=True)
            current_step += 1

        emit_stage("extracting_cubemaps")
        step(current_step, total_steps, "Extracting cubemap face images")
        current_step += 1
        extractor = CustomCameraExtractor(ffmpeg_exe)
        extractor.extract_custom_cameras_from_all_frames(
            equirect_dir=str(equirect_frames),
            output_dir=str(cubemap_dir),
            cameras=cameras,
            image_size=2048,
            per_folder=True,
        )

        # Per-face mask extraction for COLMAP (skip if masking was disabled)
        colmap_mask_arg = None
        if not args.skip_masking and mask_dir.is_dir():
            extractor.extract_mask_faces_from_all_frames(
                equirect_dir=str(equirect_frames),
                mask_dir=str(mask_dir),
                output_dir=str(cubemap_masks_dir),
                cameras=cameras,
                image_size=2048,
            )
            colmap_mask_arg = str(cubemap_masks_dir)

        emit_stage("running_sfm")
        step(current_step, total_steps, "Running COLMAP sparse reconstruction")
        current_step += 1
        colmap_out_dir.mkdir(parents=True, exist_ok=True)
        rig_config_path = str(colmap_out_dir / "rig_config.json")
        create_custom_rig_config(rig_config_path, cameras)
        print(f"Rig config written: {rig_config_path}")
        runner = ColmapRunner(colmap_exe)
        runner.run(
            images_dir=str(cubemap_dir),
            output_dir=str(colmap_out_dir),
            log_file=log_file,
            rig_config_path=rig_config_path,
            camera_h_fov=90.0,
            camera_image_size=2048,
            mask_path=colmap_mask_arg,
        )
        print(f"COLMAP log: {log_file}")
        reconstruction_dir = find_colmap_reconstruction(colmap_out_dir / "sparse")
        print(f"Reconstruction: {reconstruction_dir}")

        emit_stage("converting")
        step(current_step, total_steps, "Converting to LichtFeld format")
        mask_dir_arg = mask_dir if (not args.skip_masking and mask_dir.is_dir()) else None
        result = colmap_to_lichtfeld(
            colmap_dir=reconstruction_dir,
            equirect_dir=equirect_frames,
            output_dir=lichtfeld_dir,
            mask_dir=mask_dir_arg,
            downscale=args.downscale,
            ref_cam_id="front",
            frame_offset=1,
            verbose=True,
            max_initial_points=int(args.lfs_max_cap * 0.95),
        )

        splat_ply = None
        if not args.skip_train:
            current_step += 1
            emit_stage("training")
            step(current_step, total_steps, "Training with LichtFeld-Studio")
            splat_dir = output_dir / "splat"
            splat_ply = run_lichtfeld_training(
                lichtfeld_exe=lichtfeld_exe,
                dataset_dir=lichtfeld_dir,
                output_dir=splat_dir,
                iters=args.lfs_iters,
                tile_mode=args.lfs_tile_mode,
                max_cap=args.lfs_max_cap,
                gut=not args.lfs_no_gut,
                alpha_as_mask=args.lfs_alpha_as_mask,
                extra_args=extra_lfs_args,
                log_file=output_dir / "lichtfeld_train.log",
                retry_on_cuda_crash=not args.lfs_no_retry,
            )

        print(f"\n{'='*60}")
        print(f"  PIPELINE COMPLETE")
        print(f"{'='*60}")
        print(f"  LichtFeld dataset: {lichtfeld_dir}")
        print(f"  Frames:            {result['num_frames']}")
        print(f"  Skipped:           {result['num_skipped']}")
        print(f"  Point cloud:       {'yes' if result['has_ply'] else 'no'}")
        if splat_ply is not None:
            print(f"  Trained splat:     {splat_ply}")
        else:
            print(f"\n  Drag '{lichtfeld_dir}' into LichtFeld Studio to train.")
        print()

    # ── Multi-section pipeline ────────────────────────────────────────────────
    else:
        num_sections = args.sections
        total_steps  = (5 if not args.skip_masking else 4) + (0 if args.skip_train else 1)

        # Step 1: Stitch the full video once; reuse across all sections
        stitched_mp4 = output_dir / "stitched.mp4"
        step(1, total_steps,
             f"Stitching .insv → equirectangular MP4 (shared for all {num_sections} sections)")
        sdk_kwargs = {"stitch_type": args.stitch_type}
        if args.output_width:
            sdk_kwargs["output_width"] = args.output_width
        if args.output_height:
            sdk_kwargs["output_height"] = args.output_height
        sdk_stitch_to_mp4(args.front_insv, args.back_insv, str(stitched_mp4), **sdk_kwargs)

        duration = get_video_duration(ffmpeg_exe, str(stitched_mp4))
        section_duration = duration / num_sections
        print(f"\nVideo duration: {duration:.1f}s  →  {num_sections} sections of ~{section_duration:.1f}s each")

        results = []
        for i in range(num_sections):
            section_label   = f"section_{i+1:02d}"
            section_dir     = output_dir / section_label
            start_sec       = i * section_duration
            end_sec         = duration if i == num_sections - 1 else (i + 1) * section_duration
            equirect_frames = section_dir / "equirect" / "frames"

            print(f"\n{'#'*60}")
            print(f"  SECTION {i+1}/{num_sections}: {start_sec:.1f}s – {end_sec:.1f}s  →  {section_label}/")
            print(f"{'#'*60}")

            step(1, total_steps, f"[{section_label}] Extracting equirectangular frames")
            frames = extract_frames_range(
                ffmpeg_exe, str(stitched_mp4), str(equirect_frames),
                start_sec, end_sec, args.frame_interval,
            )
            if not frames:
                print(f"  WARNING: No frames extracted for {section_label}, skipping.")
                results.append({"section": section_label, "status": "skipped (no frames)"})
                continue

            try:
                result = process_section(
                    section_dir=section_dir,
                    equirect_frames=equirect_frames,
                    args=args,
                    cameras=cameras,
                    ffmpeg_exe=ffmpeg_exe,
                    colmap_exe=colmap_exe,
                    section_label=section_label,
                    total_steps=total_steps,
                )
                splat_ply = None
                if not args.skip_train:
                    step(total_steps, total_steps, f"[{section_label}] Training with LichtFeld-Studio")
                    splat_ply = run_lichtfeld_training(
                        lichtfeld_exe=lichtfeld_exe,
                        dataset_dir=section_dir / "lichtfeld",
                        output_dir=section_dir / "splat",
                        iters=args.lfs_iters,
                        tile_mode=args.lfs_tile_mode,
                        max_cap=args.lfs_max_cap,
                        gut=not args.lfs_no_gut,
                        alpha_as_mask=args.lfs_alpha_as_mask,
                        extra_args=extra_lfs_args,
                        log_file=section_dir / "lichtfeld_train.log",
                        retry_on_cuda_crash=not args.lfs_no_retry,
                    )
                results.append({"section": section_label, "status": "ok", "result": result, "splat_ply": splat_ply})
            except Exception as e:
                print(f"\n  ERROR in {section_label}: {e}")
                results.append({"section": section_label, "status": f"failed: {e}"})

        print(f"\nCleaning up intermediate MP4...")
        stitched_mp4.unlink(missing_ok=True)

        print(f"\n{'='*60}")
        print(f"  PIPELINE COMPLETE — {num_sections} sections")
        print(f"{'='*60}")
        for r in results:
            if r["status"] == "ok":
                res = r["result"]
                lichtfeld_dir = output_dir / r["section"] / "lichtfeld"
                line = (f"  {r['section']}/lichtfeld  "
                        f"frames={res['num_frames']}  skipped={res['num_skipped']}  "
                        f"ply={'yes' if res['has_ply'] else 'no'}")
                if r.get("splat_ply") is not None:
                    line += f"  splat={r['splat_ply']}"
                print(line)
            else:
                print(f"  {r['section']}  [{r['status']}]")
        print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="End-to-end .insv → LichtFeld Studio dataset pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python pipeline_lichtfeld.py VID_001_00.insv VID_001_10.insv -o my_dataset
    python pipeline_lichtfeld.py VID_001_00.insv VID_001_10.insv -o out --skip-top-bottom -f 0.5
    python pipeline_lichtfeld.py VID_001_00.insv VID_001_10.insv -o out --skip-masking --downscale 2
    python pipeline_lichtfeld.py VID_001_00.insv VID_001_10.insv -o out --sections 3
        """,
    )

    # Required
    p.add_argument("front_insv", help="Front camera .insv file (_00_)")
    p.add_argument("back_insv",  help="Back camera .insv file (_10_)")
    p.add_argument("-o", "--output", required=True, help="Output directory")

    # Equirect extraction
    p.add_argument("-f", "--frame-interval", type=float, default=1.0,
                   help="Seconds between frames (default: 1.0)")
    p.add_argument("--stitch-type", default="aistitch",
                   choices=["aistitch", "optflow", "template", "dynamicstitch"],
                   help="SDK stitch algorithm (default: aistitch)")
    p.add_argument("--output-width",  type=int, default=None,
                   help="Equirectangular output width (default from SDK)")
    p.add_argument("--output-height", type=int, default=None,
                   help="Equirectangular output height (default from SDK)")
    p.add_argument("--ffmpeg", default=None,
                   help="Path to ffmpeg (overrides config.json)")

    # Masking
    p.add_argument("--skip-masking", action="store_true",
                   help="Skip YOLO person masking step")
    p.add_argument("--yolo-model", default="s", choices=["n", "s", "m", "l", "x"],
                   help="YOLO model size (default: s)")
    p.add_argument("--yolo-conf", type=float, default=0.10,
                   help="YOLO confidence threshold (default: 0.10)")
    p.add_argument("--bottom-mask", type=float, default=0.10,
                   help="Fraction of bottom to mask (default: 0.10, set 0 to disable)")

    # Cubemap face selection
    p.add_argument("--skip-top",        action="store_true",
                   help="Skip the top (up) cubemap face")
    p.add_argument("--skip-bottom",     action="store_true",
                   help="Skip the bottom (down) cubemap face")
    p.add_argument("--skip-top-bottom", action="store_true",
                   help="Skip both top and bottom cubemap faces (4-face: front/right/back/left)")

    # LichtFeld output
    p.add_argument("--downscale", type=int, default=None,
                   help="Downscale training images by this factor (e.g. 2 = half res)")

    # Sections
    p.add_argument("--sections", type=int, default=1,
                   help="Split video into N equal time sections, each processed independently (default: 1)")

    # LichtFeld training
    p.add_argument("--skip-train", action="store_true",
                   help="Skip LichtFeld-Studio training step (stop after dataset conversion)")
    p.add_argument("--lichtfeld-exe", default=None,
                   help="Path to LichtFeld-Studio.exe (overrides config.json)")
    p.add_argument("--lfs-iters", type=int, default=30000,
                   help="LichtFeld training iterations (default: 30000)")
    p.add_argument("--lfs-tile-mode", type=int, default=2, choices=[1, 2, 4],
                   help="LichtFeld --tile-mode (default: 2)")
    p.add_argument("--lfs-max-cap", type=int, default=500000,
                   help="LichtFeld max Gaussians / --max-cap (default: 500000)")
    p.add_argument("--lfs-no-gut", action="store_true",
                   help="Disable --gut mode (on by default)")
    p.add_argument("--lfs-no-retry", action="store_true",
                   help="Disable auto-retry on the documented --gut CUDA crash "
                        "(upstream issue #1091). Retry is on by default, up to "
                        "2 retries (3 attempts total).")
    p.add_argument("--lfs-alpha-as-mask", action="store_true",
                   help="Enable alpha-as-mask (off by default — equivalent to --no-alpha-as-mask)")
    p.add_argument("--lfs-extra-args", default="",
                   help='Extra args passed verbatim to LichtFeld-Studio, e.g. --lfs-extra-args="--strategy=mcmc"')

    args = p.parse_args()

    # --skip-top-bottom is shorthand
    if args.skip_top_bottom:
        args.skip_top    = True
        args.skip_bottom = True

    if args.sections < 1:
        print("Error: --sections must be >= 1")
        sys.exit(1)

    for path in [args.front_insv, args.back_insv]:
        if not Path(path).exists():
            print(f"Error: file not found: {path}")
            sys.exit(1)

    config_path = PROJECT_ROOT / "config.json"
    if not config_path.exists():
        print(f"Error: config.json not found at {config_path}")
        sys.exit(1)

    try:
        run_pipeline(args)
    except subprocess.CalledProcessError as e:
        print(f"\nError: subprocess failed (exit code {e.returncode})")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
