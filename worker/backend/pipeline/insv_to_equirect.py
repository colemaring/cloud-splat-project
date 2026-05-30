#!/usr/bin/env python3
"""
Convert dual .insv files (front/back cameras) into equirectangular frames
using the Insta360 MediaSDK for high-quality stitching.

Two-step pipeline:
  1. MediaSDK stitches .insv -> equirectangular MP4 (AI-calibrated seam)
  2. FFMPEG extracts frames at the desired interval from the stitched MP4

Usage:
  python insv_to_equirect.py VID_001_00.insv VID_001_10.insv -o my_frames -f 0.5
"""

import os
import platform
import subprocess
import argparse
import shutil
from pathlib import Path

# Imported two ways — as a sibling subprocess script
# (`python insv_to_equirect.py ...`, where script dir is on sys.path[0]) AND
# as `backend.pipeline.insv_to_equirect` from pipeline_lichtfeld.py. Try the
# package-relative path first; fall back to sibling lookup.
try:
    from .constants import (  # type: ignore[import-not-found]
        MEDIASDK_BIN_DIR,
        MEDIASDK_MODELS_DIR,
        MEDIASDK_EXE_NAME,
        DEFAULT_STITCH_TYPE,
        ENABLE_STITCH_FUSION,
        ENABLE_CUDA,
        DEFAULT_EQUIRECT_WIDTH,
        DEFAULT_EQUIRECT_HEIGHT,
    )
except ImportError:
    from constants import (  # type: ignore[import-not-found,no-redef]
        MEDIASDK_BIN_DIR,
        MEDIASDK_MODELS_DIR,
        MEDIASDK_EXE_NAME,
        DEFAULT_STITCH_TYPE,
        ENABLE_STITCH_FUSION,
        ENABLE_CUDA,
        DEFAULT_EQUIRECT_WIDTH,
        DEFAULT_EQUIRECT_HEIGHT,
    )


def sdk_stitch_to_mp4(
    front_path: str,
    back_path: str,
    output_mp4: str,
    stitch_type: str = DEFAULT_STITCH_TYPE,
    output_width: int = DEFAULT_EQUIRECT_WIDTH,
    output_height: int = DEFAULT_EQUIRECT_HEIGHT,
    enable_stitchfusion: bool = ENABLE_STITCH_FUSION,
    enable_cuda: bool = ENABLE_CUDA,
    model_root_dir: str = MEDIASDK_MODELS_DIR,
) -> str:
    # Windows ships "MediaSDKTest.exe"; the Linux MediaSDK ships "MediaSDKTest".
    # MEDIASDK_EXE_NAME comes from config (default "MediaSDKTest"); on Windows we
    # append ".exe" if it isn't already there.
    exe_name = MEDIASDK_EXE_NAME
    if platform.system() == "Windows" and not exe_name.lower().endswith(".exe"):
        exe_name += ".exe"
    sdk_exe = str(Path(MEDIASDK_BIN_DIR) / exe_name)
    if not Path(sdk_exe).exists():
        raise FileNotFoundError(f"MediaSDK binary not found at {sdk_exe}")

    cmd = [
        sdk_exe,
        "-inputs", front_path, back_path,
        "-output", output_mp4,
        "-stitch_type", stitch_type,
        "-output_size", f"{output_width}x{output_height}",
        "-model_root_dir", model_root_dir,
    ]
    if enable_stitchfusion:
        cmd.append("-enable_stitchfusion")
    if not enable_cuda:
        cmd.append("-disable_cuda")

    print(f"[SDK] Stitching with {stitch_type} at {output_width}x{output_height}...")
    print(f"[SDK] Command: {' '.join(cmd)}")

    # The Linux MediaSDK loads libMediaSDK.so from the bin/ dir at runtime;
    # make sure the dynamic linker can find it without a system-wide install.
    env = os.environ.copy()
    if platform.system() != "Windows":
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = MEDIASDK_BIN_DIR + (os.pathsep + existing if existing else "")

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)

    if not Path(output_mp4).exists():
        raise RuntimeError(f"SDK stitching failed - output not created: {output_mp4}\nstderr: {result.stderr}\nstdout: {result.stdout}")

    print(f"[SDK] Stitched MP4 saved to: {output_mp4}")
    return output_mp4


def extract_frames_from_mp4(
    ffmpeg_exe: str,
    input_mp4: str,
    output_dir: str,
    frame_interval: float = 1.0,
):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_pattern = str(output_dir / "frame_%06d.jpg")
    fps = 1.0 / frame_interval

    cmd = [
        ffmpeg_exe,
        "-i", input_mp4,
        "-r", str(fps),
        "-q:v", "2",
        "-y",
        output_pattern,
    ]

    print(f"[FFMPEG] Extracting frames at {fps} fps...")
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    frames = sorted(output_dir.glob("frame_*.jpg"))
    print(f"[FFMPEG] Extracted {len(frames)} equirectangular frames to: {output_dir}")
    return frames


def main():
    parser = argparse.ArgumentParser(description="Convert Insta360 .insv files to equirectangular frames using MediaSDK")
    parser.add_argument("front_camera", help="Path to front camera .insv file (_00_)")
    parser.add_argument("back_camera", help="Path to back camera .insv file (_10_)")
    parser.add_argument("-o", "--output", default="equirect_frames", help="Output directory")
    parser.add_argument("-f", "--frame-interval", type=float, default=1.0, help="Seconds between frames (default: 1.0)")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="Path to ffmpeg executable")
    parser.add_argument("--stitch-type", default=DEFAULT_STITCH_TYPE, choices=["template", "optflow", "dynamicstitch", "aistitch"], help="SDK stitch algorithm")
    parser.add_argument("--output-width", type=int, default=DEFAULT_EQUIRECT_WIDTH, help="Output equirectangular width")
    parser.add_argument("--output-height", type=int, default=DEFAULT_EQUIRECT_HEIGHT, help="Output equirectangular height")
    parser.add_argument("--no-stitchfusion", action="store_true", help="Disable stitch fusion (chromatic calibration)")
    parser.add_argument("--no-cuda", action="store_true", help="Disable CUDA GPU acceleration")
    parser.add_argument("--keep-mp4", action="store_true", help="Keep the intermediate stitched MP4 file")

    args = parser.parse_args()

    front_path = Path(args.front_camera)
    back_path = Path(args.back_camera)

    if not front_path.exists():
        raise FileNotFoundError(f"Front camera file not found: {front_path}")
    if not back_path.exists():
        raise FileNotFoundError(f"Back camera file not found: {back_path}")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    stitched_mp4 = str(output_dir / "stitched.mp4")

    # Stage marker for the cloud worker (see pipeline_lichtfeld.emit_stage).
    print("##STAGE:stitching", flush=True)
    sdk_stitch_to_mp4(
        str(front_path),
        str(back_path),
        stitched_mp4,
        stitch_type=args.stitch_type,
        output_width=args.output_width,
        output_height=args.output_height,
        enable_stitchfusion=not args.no_stitchfusion,
        enable_cuda=not args.no_cuda,
    )

    print("##STAGE:extracting_frames", flush=True)
    frames = extract_frames_from_mp4(
        args.ffmpeg,
        stitched_mp4,
        str(output_dir / "frames"),
        args.frame_interval,
    )

    if not args.keep_mp4:
        print(f"[Cleanup] Removing intermediate MP4: {stitched_mp4}")
        Path(stitched_mp4).unlink(missing_ok=True)
    else:
        print(f"[Keep] Stitched MP4 retained at: {stitched_mp4}")

    print(f"\nDone! {len(frames)} frames saved to: {output_dir / 'frames'}")


if __name__ == "__main__":
    main()
