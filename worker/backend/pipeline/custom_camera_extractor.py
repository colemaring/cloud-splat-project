"""
Custom Camera Extractor
Extracts custom perspective views from equirectangular frames
"""

import subprocess
import logging
from pathlib import Path
from typing import Dict, List
from .constants import (
    DEFAULT_CAMERA_IMAGE_SIZE,
)

logger = logging.getLogger(__name__)


class CustomCameraExtractor:
    """
    Extracts custom perspective camera views from equirectangular 360° images.

    Uses a single batched ffmpeg invocation per pass (one for images, one
    for masks) that streams the entire equirect sequence through a
    `filter_complex` graph: the input is split N ways and each branch
    applies its own v360 transform. This replaces an earlier per-frame
    subprocess loop that spawned one ffmpeg process per (frame × camera)
    — process startup alone dominated wall-clock on large captures.
    """

    def __init__(self, ffmpeg_exe: str):
        self.ffmpeg_exe = ffmpeg_exe

    def extract_custom_cameras_from_all_frames(
        self,
        equirect_dir: str,
        output_dir: str,
        cameras: List[dict],
        image_size: int = DEFAULT_CAMERA_IMAGE_SIZE,
        per_folder: bool = False
    ) -> Dict[str, List[str]]:
        """
        Extract custom camera views from all equirectangular frames.

        Args:
            equirect_dir: Directory containing equirectangular frames
            output_dir: Directory to save extracted images
            cameras: List of camera dicts with {id, name, yaw, pitch, roll, h_fov, v_fov}
            image_size: Output image width (height computed from FOV ratio)
            per_folder: If True, organize into subdirectories per camera (for different FOVs)

        Returns:
            Dictionary mapping camera_id -> list of extracted image paths

        Raises:
            FileNotFoundError: If no equirectangular frames found
            RuntimeError: If FFmpeg fails to extract camera views
        """
        equirect_dir = Path(equirect_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        frames = sorted(equirect_dir.glob("frame_*.jpg"))
        if not frames:
            raise FileNotFoundError(f"No equirectangular frames found in {equirect_dir}")

        start_num = self._verify_contiguous(frames, equirect_dir)

        logger.info(
            f"Extracting {len(cameras)} custom cameras from {len(frames)} frames "
            f"(batched ffmpeg, single invocation)"
        )
        if per_folder:
            logger.info("Using per-camera subdirectories for different FOVs")

        if per_folder:
            for cam in cameras:
                (output_dir / cam['id']).mkdir(parents=True, exist_ok=True)

        filter_graph = self._build_filter_complex(cameras, image_size, mask_mode=False)

        cmd = [
            self.ffmpeg_exe,
            '-loglevel', 'error',
            '-framerate', '1',
            '-start_number', str(start_num),
            '-i', str(equirect_dir / "frame_%06d.jpg"),
            '-filter_complex', filter_graph,
        ]

        for k, cam in enumerate(cameras):
            cam_id = cam['id']
            if per_folder:
                out_pattern = str(output_dir / cam_id / "frame_%06d.jpg")
            else:
                out_pattern = str(output_dir / f"frame_%06d_{cam_id}.jpg")
            cmd += [
                '-map', f'[o{k}]',
                '-q:v', '2',
                '-start_number', '0',
                '-y',
                out_pattern,
            ]

        self._run_ffmpeg(cmd, "cubemap face extraction")

        # Build the return dict by globbing each output area. Match the
        # legacy contract: list of absolute paths per camera, keyed by id.
        extracted_files: Dict[str, List[str]] = {cam['id']: [] for cam in cameras}
        for cam in cameras:
            cam_id = cam['id']
            if per_folder:
                files = sorted((output_dir / cam_id).glob("frame_*.jpg"))
            else:
                files = sorted(output_dir.glob(f"frame_*_{cam_id}.jpg"))
            extracted_files[cam_id] = [str(p) for p in files]

        total_extracted = sum(len(v) for v in extracted_files.values())
        logger.info(f"Extracted {total_extracted} total custom camera images")
        for cam in cameras:
            logger.info(f"  {cam['name']} ({cam['id']}): {len(extracted_files[cam['id']])} images")

        return extracted_files

    def extract_mask_faces_from_all_frames(
        self,
        equirect_dir: str,
        mask_dir: str,
        output_dir: str,
        cameras: List[dict],
        image_size: int = DEFAULT_CAMERA_IMAGE_SIZE,
    ) -> int:
        """
        Extract cubemap face masks from equirect YOLO masks.

        Mirrors extract_custom_cameras_from_all_frames so that face frame indices
        match 1:1 with the face image indices. Uses nearest-neighbor interpolation
        (interp=near) to preserve binary mask values. Output filenames follow
        COLMAP's mask_path convention: '<image_name>.png', where image_name is
        the face image filename (e.g. 'frame_000000.jpg' → 'frame_000000.jpg.png').

        Args:
            equirect_dir: Directory with equirect frames — used to derive the
                          frame index sequence (must match what
                          extract_custom_cameras_from_all_frames saw).
            mask_dir:     Directory with YOLO equirect masks (<stem>.png).
            output_dir:   Root directory for cubemap face masks. Mirrors the
                          image cubemap layout: <output>/<cam_id>/<image>.png.
            cameras:      Same camera dict list used for image extraction.
            image_size:   Output face width (must match image extraction).

        Returns:
            Number of mask files written.
        """
        equirect_dir = Path(equirect_dir)
        mask_dir = Path(mask_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        frames = sorted(equirect_dir.glob("frame_*.jpg"))
        if not frames:
            raise FileNotFoundError(f"No equirectangular frames found in {equirect_dir}")

        start_num = self._verify_contiguous(frames, equirect_dir)

        # Verify every frame has a matching mask. If any are missing, fail
        # loudly with a clear message — silently skipping (the legacy
        # behavior) hid YOLO failures and produced cubemap masks
        # misaligned with the image stream.
        missing = [f.stem for f in frames if not (mask_dir / f"{f.stem}.png").exists()]
        if missing:
            raise FileNotFoundError(
                f"{len(missing)} equirect frames have no matching mask in "
                f"{mask_dir} (e.g. {missing[:3]}). Re-run YOLO masking before "
                f"extracting per-face masks."
            )

        logger.info(
            f"Extracting per-face masks for {len(cameras)} cameras from "
            f"{len(frames)} frames (batched ffmpeg, nearest-neighbor)"
        )

        for cam in cameras:
            (output_dir / cam['id']).mkdir(parents=True, exist_ok=True)

        filter_graph = self._build_filter_complex(cameras, image_size, mask_mode=True)

        cmd = [
            self.ffmpeg_exe,
            '-loglevel', 'error',
            '-framerate', '1',
            '-start_number', str(start_num),
            '-i', str(mask_dir / "frame_%06d.png"),
            '-filter_complex', filter_graph,
        ]

        for k, cam in enumerate(cameras):
            # COLMAP expects '<image_name>.png'. Face image is 'frame_NNNNNN.jpg',
            # so the mask is 'frame_NNNNNN.jpg.png' — ffmpeg's image2 muxer
            # parses %06d in-place so the suffix stays literal.
            out_pattern = str(output_dir / cam['id'] / "frame_%06d.jpg.png")
            cmd += [
                '-map', f'[o{k}]',
                '-start_number', '0',
                '-y',
                out_pattern,
            ]

        self._run_ffmpeg(cmd, "cubemap mask extraction")

        count = 0
        for cam in cameras:
            count += len(list((output_dir / cam['id']).glob("frame_*.jpg.png")))

        logger.info(f"Extracted {count} per-face mask images into {output_dir}")
        return count

    def _build_filter_complex(
        self,
        cameras: List[dict],
        image_size: int,
        *,
        mask_mode: bool,
    ) -> str:
        """Build a single filter_complex graph that splits the input N
        ways and applies one v360 branch per camera. Mask mode adds
        `interp=near` to preserve binary values."""
        n = len(cameras)
        if n == 0:
            raise ValueError("cameras list is empty")

        split_labels = "".join(f"[i{k}]" for k in range(n))
        parts = [f"[0:v]split={n}{split_labels}"]

        for k, cam in enumerate(cameras):
            h_fov = cam.get('h_fov', 90)
            v_fov = cam.get('v_fov', 90)
            yaw = cam['yaw']
            pitch = cam['pitch']
            roll = cam.get('roll', 0)

            out_w = image_size
            out_h = int(image_size * v_fov / h_fov)

            # FFmpeg v360 filter uses opposite sign for yaw and pitch when extracting flat from equirect.
            # Negate both to match the Three.js visualization coordinate system.
            # Normalize to [-180, 180] / [-90, 90] required by FFmpeg.
            ffmpeg_yaw = ((-yaw + 180) % 360) - 180
            ffmpeg_pitch = ((-pitch + 90) % 180) - 90

            v360 = (
                f"[i{k}]v360=e:flat"
                f":yaw={ffmpeg_yaw}"
                f":pitch={ffmpeg_pitch}"
                f":roll={roll}"
                f":h_fov={h_fov}"
                f":v_fov={v_fov}"
                f":w={out_w}:h={out_h}"
            )
            if mask_mode:
                v360 += ":interp=near"
            v360 += f"[o{k}]"
            parts.append(v360)

        return "; ".join(parts)

    @staticmethod
    def _verify_contiguous(frames: List[Path], equirect_dir: Path) -> int:
        """Confirm `frames` is a contiguous numbered sequence and return
        the starting frame number. Batched extraction via ffmpeg's image2
        demuxer requires a gap-free sequence."""
        try:
            first = int(frames[0].stem.split('_')[-1])
        except ValueError as e:
            raise RuntimeError(
                f"Could not parse frame number from {frames[0].name}"
            ) from e

        for i, f in enumerate(frames):
            try:
                actual = int(f.stem.split('_')[-1])
            except ValueError as e:
                raise RuntimeError(
                    f"Could not parse frame number from {f.name}"
                ) from e
            expected = first + i
            if actual != expected:
                raise RuntimeError(
                    f"Equirect frames in {equirect_dir} are non-contiguous: "
                    f"expected frame_{expected:06d} but found {f.name}. "
                    f"Batched ffmpeg extraction needs a gap-free sequence."
                )
        return first

    def _run_ffmpeg(self, cmd: List[str], step_name: str) -> None:
        """Run ffmpeg with stderr captured for diagnostics on failure."""
        try:
            subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg failed during {step_name}: {e.stdout}")
            raise RuntimeError(
                f"FFmpeg failed during {step_name}: {e.stdout}"
            ) from e
