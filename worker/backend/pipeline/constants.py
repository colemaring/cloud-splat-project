"""
Constants for video processing pipeline
"""

import json
from pathlib import Path


def _load_paths() -> dict:
    config_path = Path(__file__).parents[2] / "config.json"
    try:
        with open(config_path) as f:
            return json.load(f).get("paths", {})
    except Exception:
        return {}


# Insta360 MediaSDK Paths — set via paths.mediasdk_root in config.json.
# Cross-platform: on the Linux worker AMI the MediaSDK ships an unpacked tree
# with bin/ and models/ subdirs just like the Windows build, so deriving the
# subdirs with pathlib (not a hard-coded backslash) is all that's needed.
# paths.mediasdk_bin / paths.mediasdk_models may override the derived dirs.
_PATHS = _load_paths()
MEDIASDK_ROOT = _PATHS.get("mediasdk_root", "")
MEDIASDK_BIN_DIR = _PATHS.get("mediasdk_bin") or (str(Path(MEDIASDK_ROOT) / "bin") if MEDIASDK_ROOT else "")
MEDIASDK_MODELS_DIR = _PATHS.get("mediasdk_models") or (str(Path(MEDIASDK_ROOT) / "models") if MEDIASDK_ROOT else "")

# Name of the MediaSDK CLI binary (no extension on Linux, "MediaSDKTest.exe" on Windows).
MEDIASDK_EXE_NAME = _PATHS.get("mediasdk_exe_name", "MediaSDKTest")

# MediaSDK Stitching Defaults
DEFAULT_STITCH_TYPE = "aistitch"
ENABLE_STITCH_FUSION = True
ENABLE_CUDA = True

# Legacy FFmpeg v360 Filter Parameters (kept as fallback)
FISHEYE_H_FOV = 190
FISHEYE_V_FOV = 190
FISHEYE_YAW = -90

# Default Video Resolutions
DEFAULT_VIDEO_WIDTH = 3840
DEFAULT_VIDEO_HEIGHT = 1920
DEFAULT_EQUIRECT_WIDTH = 5760
DEFAULT_EQUIRECT_HEIGHT = 2880

# Aspect Ratio Detection Thresholds
DUAL_FISHEYE_ASPECT_RATIO = 2.0
SINGLE_FISHEYE_ASPECT_RATIO = 1.0
ASPECT_RATIO_TOLERANCE = 0.1

# Custom Camera Extraction Defaults
DEFAULT_CAMERA_IMAGE_SIZE = 2048

# Preview Generation Settings
PREVIEW_IMAGE_WIDTH = 512
PREVIEW_JPEG_QUALITY = 3  # FFmpeg quality scale (2-5, lower is better)
PREVIEW_GENERATION_TIMEOUT = 10  # seconds

# Processing Pipeline Progress Percentages
PROGRESS_START = 0
PROGRESS_COMBINING_FILES = 5
PROGRESS_INSV_CONVERT = 10
PROGRESS_CUSTOM_EXTRACTION = 20
PROGRESS_COLMAP_START = 40
PROGRESS_COLMAP_END = 90
PROGRESS_SPLAT_TRAINING_START = 90
PROGRESS_SPLAT_TRAINING_END = 98
PROGRESS_EXPORT_PLY = 98
PROGRESS_LEVELING = 99
PROGRESS_COMPLETE = 100

# Training Defaults
DEFAULT_TRAINING_STEPS = 30000

# Logging
PROGRESS_LOG_INTERVAL = 10  # Log progress every N frames
