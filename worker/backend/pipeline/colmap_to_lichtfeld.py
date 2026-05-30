#!/usr/bin/env python3
"""
colmap_to_lichtfeld.py

Convert COLMAP sparse reconstruction (from cubemap face images) to
LichtFeld Studio transforms.json using the original equirectangular images.

COLMAP reconstruction is run on cubemap face perspectives for better SfM quality.
LichtFeld trains on the original equirectangular frames. This script recovers the
per-frame camera pose from the cubemap reference face and outputs EQUIRECTANGULAR
camera entries in transforms.json.

Coordinate system:
  COLMAP:    OpenCV (+X right, +Y down, +Z forward), cam_from_world
  LichtFeld: OpenGL (+X right, +Y up, +Z backward), camera-to-world
  LichtFeld applies 180° Y-rotation internally to cameras; we pre-compensate.
  Points (applied_transform) are NOT Y-rotated by LichtFeld — use identity.

Usage:
    python colmap_to_lichtfeld.py --colmap-dir ./sparse/0 --equirect-dir ./equirect --output ./lichtfeld_out
    python colmap_to_lichtfeld.py --colmap-dir ./sparse/0 --equirect-dir ./equirect --output ./out \\
        --mask-dir ./masks --downscale 2 --frame-offset 1

Dependencies:
    pip install numpy pillow
"""

import argparse
import json
import shutil
import struct
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np


# ── Quaternion / rotation ─────────────────────────────────────────────────────

def _quat_to_rot(qw: float, qx: float, qy: float, qz: float) -> np.ndarray:
    """COLMAP quaternion (w,x,y,z) → 3×3 rotation matrix."""
    return np.array([
        [1 - 2*(qy**2 + qz**2),  2*(qx*qy - qw*qz),  2*(qx*qz + qw*qy)],
        [2*(qx*qy + qw*qz),  1 - 2*(qx**2 + qz**2),  2*(qy*qz - qw*qx)],
        [2*(qx*qz - qw*qy),  2*(qy*qz + qw*qx),  1 - 2*(qx**2 + qy**2)]
    ])


# ── COLMAP text format parsing ────────────────────────────────────────────────

def parse_colmap_images(images_txt: Path) -> Dict[str, dict]:
    """
    Parse COLMAP images.txt → per-image pose data.

    Returns dict keyed by image name (e.g. "front/frame_000005.jpg") with:
      qw, qx, qy, qz, tx, ty, tz, cam_id (folder), frame_num (int or None)
    """
    images: Dict[str, dict] = {}

    with open(images_txt) as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith('#'):
            continue

        parts = line.split()
        if len(parts) < 10:
            i += 1  # skip points2D line
            continue

        try:
            qw, qx, qy, qz = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
            tx, ty, tz      = float(parts[5]), float(parts[6]), float(parts[7])
            image_name      = parts[9]
        except (ValueError, IndexError):
            i += 1
            continue

        cam_id = image_name.split('/')[0] if '/' in image_name else ''
        filename = image_name.split('/')[-1]
        frame_num: Optional[int] = None
        if 'frame_' in filename:
            try:
                frame_num = int(filename.split('frame_')[1].split('.')[0])
            except (ValueError, IndexError):
                pass

        images[image_name] = {
            'qw': qw, 'qx': qx, 'qy': qy, 'qz': qz,
            'tx': tx, 'ty': ty, 'tz': tz,
            'cam_id': cam_id,
            'frame_num': frame_num,
        }
        i += 1  # skip points2D line

    return images


def parse_colmap_points3d(points3d_txt: Path) -> Tuple[np.ndarray, np.ndarray]:
    """Parse COLMAP points3D.txt → (Nx3 float32 XYZ, Nx3 uint8 RGB)."""
    pts, cols = [], []
    with open(points3d_txt) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) < 7:
                continue
            try:
                pts.append([float(parts[1]), float(parts[2]), float(parts[3])])
                cols.append([int(parts[4]), int(parts[5]), int(parts[6])])
            except (ValueError, IndexError):
                continue
    if not pts:
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.uint8)
    return np.array(pts, np.float32), np.array(cols, np.uint8)


# ── Coordinate conversion ─────────────────────────────────────────────────────

def colmap_pose_to_lichtfeld_c2w(qw, qx, qy, qz, tx, ty, tz,
                                 world_center: Optional[np.ndarray] = None) -> np.ndarray:
    """
    Convert COLMAP cam_from_world (qw,qx,qy,qz,tx,ty,tz) to LichtFeld c2w 4×4.

    If world_center is given (rig-averaged camera center in COLMAP world coords),
    it replaces the center derived from (tx,ty,tz). Rotation still comes from the quaternion.

    Steps:
      1. Build c2w (world-from-camera) by inverting COLMAP's camera-from-world
      2. Flip camera-local Y and Z columns: OpenCV (+Y down, +Z fwd) → OpenGL (+Y up, +Z back)
      3. Pre-multiply by 180° Y-rotation to cancel LichtFeld's internal Y-rotation
         (LichtFeld applies y_rot_180 to cameras on load; we apply it here so it cancels)
      4. Point cloud applied_transform stays as identity — LichtFeld does NOT apply Y-rot to points
    """
    R_cw = _quat_to_rot(qw, qx, qy, qz)

    # Invert: camera-to-world
    R_wc = R_cw.T
    if world_center is not None:
        t_wc = np.asarray(world_center, dtype=float)
    else:
        t_cw = np.array([tx, ty, tz])
        t_wc = -R_cw.T @ t_cw  # camera center in world coords

    c2w = np.eye(4)
    c2w[:3, :3] = R_wc
    c2w[:3, 3]  = t_wc

    # OpenCV → OpenGL: flip Y and Z columns of the rotation part
    c2w[:, 1] *= -1
    c2w[:, 2] *= -1

    # Pre-compensate for LichtFeld's internal 180° Y-rotation on cameras
    y_rot_180 = np.diag([-1.0, 1.0, -1.0, 1.0])
    return y_rot_180 @ c2w


def get_applied_transform() -> np.ndarray:
    """
    3×4 applied_transform for the point cloud.
    LichtFeld does NOT apply Y-rotation to points, only to cameras.
    COLMAP world → LichtFeld effective world (after camera y_rot cancellation) = identity.
    """
    return np.eye(4)[:3, :]  # 3×4 identity


# ── Frame grouping ────────────────────────────────────────────────────────────

def group_frames(images: Dict[str, dict], ref_cam_id: str = 'front') -> Dict[int, dict]:
    """
    Group reconstructed images by COLMAP frame number.

    Returns dict: frame_num → {
        'ref': image_data of the reference camera face (or fallback to first),
        'position': average camera center across all faces (numpy 3-vec)
    }
    """
    by_frame: Dict[int, List[dict]] = {}
    for img in images.values():
        fn = img['frame_num']
        if fn is None:
            continue
        by_frame.setdefault(fn, []).append(img)

    result = {}
    for fn, imgs in by_frame.items():
        centers = []
        ref_img = None
        for img in imgs:
            R_cw = _quat_to_rot(img['qw'], img['qx'], img['qy'], img['qz'])
            t_cw = np.array([img['tx'], img['ty'], img['tz']])
            centers.append(-R_cw.T @ t_cw)
            if img['cam_id'] == ref_cam_id:
                ref_img = img
        if ref_img is None:
            ref_img = imgs[0]  # fall back to first reconstructed face
        result[fn] = {
            'ref': ref_img,
            'position': np.mean(centers, axis=0),
        }
    return result


# ── PLY writing ───────────────────────────────────────────────────────────────

def write_ply(output_path: Path, points: np.ndarray, colors: np.ndarray,
              applied_transform: np.ndarray) -> None:
    """Write binary PLY with applied_transform applied to positions."""
    if len(points) == 0:
        print("  Warning: no points in point cloud, skipping PLY.")
        return

    T = applied_transform[:3, :3]
    t = applied_transform[:3, 3]
    transformed = (T @ points.T).T + t
    transformed = transformed.astype(np.float32)

    with open(output_path, 'wb') as f:
        header = (
            f"ply\nformat binary_little_endian 1.0\n"
            f"element vertex {len(transformed)}\n"
            f"property float x\nproperty float y\nproperty float z\n"
            f"property uchar red\nproperty uchar green\nproperty uchar blue\n"
            f"end_header\n"
        )
        f.write(header.encode('ascii'))
        for i in range(len(transformed)):
            f.write(struct.pack('<fff', *transformed[i]))
            f.write(bytes(colors[i]))


# ── Image utilities ───────────────────────────────────────────────────────────

def get_image_size(path: Path) -> Tuple[int, int]:
    """Return (width, height) of an image without loading full data."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.size  # (width, height)
    except ImportError:
        # Fall back to reading JPEG header bytes
        with open(path, 'rb') as f:
            f.read(2)  # SOI marker
            while True:
                marker = f.read(2)
                if len(marker) < 2:
                    break
                length = struct.unpack('>H', f.read(2))[0]
                if marker[1] in (0xC0, 0xC2):
                    f.read(1)
                    h = struct.unpack('>H', f.read(2))[0]
                    w = struct.unpack('>H', f.read(2))[0]
                    return w, h
                f.read(length - 2)
        raise RuntimeError(f"Cannot determine image size: {path}. Install Pillow: pip install pillow")


def copy_or_downscale(src: Path, dst: Path, downscale: Optional[int]) -> None:
    """Copy image to dst, optionally downscaling."""
    if downscale and downscale > 1:
        try:
            from PIL import Image
            img = Image.open(src)
            new_size = (img.width // downscale, img.height // downscale)
            img = img.resize(new_size, Image.LANCZOS)
            img.save(dst, quality=95)
            return
        except ImportError:
            pass  # fall through to copy
    shutil.copy2(src, dst)


def copy_or_downscale_mask(src: Path, dst: Path, downscale: Optional[int]) -> None:
    """Copy mask to dst, optionally downscaling with NEAREST to preserve binary values."""
    if downscale and downscale > 1:
        try:
            from PIL import Image
            img = Image.open(src)
            new_size = (max(1, img.width // downscale), max(1, img.height // downscale))
            img = img.resize(new_size, Image.NEAREST)
            img.save(dst)
            return
        except ImportError:
            pass
    shutil.copy2(src, dst)


# ── Main conversion ───────────────────────────────────────────────────────────

def convert(
    colmap_dir: Path,
    equirect_dir: Path,
    output_dir: Path,
    mask_dir: Optional[Path] = None,
    downscale: Optional[int] = None,
    ref_cam_id: str = 'front',
    frame_offset: int = 1,
    verbose: bool = True,
    max_initial_points: Optional[int] = None,
) -> dict:
    """
    Convert COLMAP cubemap reconstruction to LichtFeld transforms.json.

    Args:
        colmap_dir:    COLMAP sparse reconstruction (cameras.txt, images.txt, points3D.txt)
        equirect_dir:  Original equirectangular frames (frame_NNNNNN.jpg)
        output_dir:    Output directory for LichtFeld dataset
        mask_dir:      Optional mask dir from mask_people_yolo.py
        downscale:     Optional image downscale factor
        ref_cam_id:    Cubemap face to use as equirect reference pose ('front' default)
        frame_offset:  COLMAP frame N corresponds to equirect frame N+frame_offset.
                       Default 1: insv_to_equirect produces 1-indexed frames (frame_000001..),
                       while cubemap extraction is 0-indexed (frame_000000..).
        verbose:       Print progress
        max_initial_points: If set, randomly subsample the COLMAP point cloud down
                       to this many points before writing pointcloud.ply. Use to
                       leave headroom under LichtFeld's --max-cap so densification
                       has free slots — LichtFeld v0.5.0 crashes with
                       cudaErrorIllegalAddress when it starts at 100% utilization.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    images_out = output_dir / "images"
    images_out.mkdir(parents=True, exist_ok=True)

    images_txt    = colmap_dir / "images.txt"
    points3d_txt  = colmap_dir / "points3D.txt"

    if not images_txt.exists():
        raise FileNotFoundError(f"images.txt not found in {colmap_dir}")

    if verbose:
        print(f"[1/4] Parsing COLMAP reconstruction: {colmap_dir}")

    colmap_images = parse_colmap_images(images_txt)
    if verbose:
        print(f"      {len(colmap_images)} reconstructed image poses")

    frame_data = group_frames(colmap_images, ref_cam_id=ref_cam_id)
    if verbose:
        print(f"      {len(frame_data)} unique frames reconstructed")

    # Find equirect frames
    equirect_frames = sorted(equirect_dir.glob("frame_*.jpg"))
    if not equirect_frames:
        equirect_frames = sorted(equirect_dir.glob("frame_*.png"))
    if not equirect_frames:
        raise FileNotFoundError(f"No equirectangular frames found in {equirect_dir}")

    if verbose:
        print(f"[2/4] Found {len(equirect_frames)} equirectangular frames in {equirect_dir}")

    # Determine dimensions from first frame
    sample = equirect_frames[0]
    equirect_w, equirect_h = get_image_size(sample)
    if downscale and downscale > 1:
        equirect_w //= downscale
        equirect_h //= downscale

    # EQUIRECTANGULAR intrinsics (nerfstudio/LichtFeld convention)
    fl_x = equirect_w / 2.0
    fl_y = float(equirect_h)   # full height maps to π radians
    cx   = equirect_w / 2.0
    cy   = equirect_h / 2.0

    if verbose:
        print(f"      Equirect size: {equirect_w}×{equirect_h}"
              + (f" (downscaled {downscale}×)" if downscale and downscale > 1 else ""))

    applied_transform = get_applied_transform()
    frames = []
    num_skipped = 0

    if verbose:
        print(f"[3/4] Building frame entries (frame_offset={frame_offset})...")

    for eq_frame in equirect_frames:
        # Parse equirect frame number (1-indexed from insv_to_equirect)
        try:
            eq_num = int(eq_frame.stem.split('frame_')[1])
        except (IndexError, ValueError):
            num_skipped += 1
            continue

        # Find corresponding COLMAP frame (0-indexed)
        colmap_num = eq_num - frame_offset
        if colmap_num not in frame_data:
            num_skipped += 1
            continue

        fdata = frame_data[colmap_num]
        ref   = fdata['ref']

        # Use rig-averaged camera center across all reconstructed faces for this
        # frame (fdata['position']) instead of the raw front-face translation.
        # Rotation still comes from the front face quaternion. Averaging the
        # position cancels per-face SfM noise that otherwise shows up as jitter
        # in the equirect poses.
        c2w = colmap_pose_to_lichtfeld_c2w(
            ref['qw'], ref['qx'], ref['qy'], ref['qz'],
            ref['tx'], ref['ty'], ref['tz'],
            world_center=fdata['position'],
        )

        dst_path = images_out / eq_frame.name
        copy_or_downscale(eq_frame, dst_path, downscale)

        frames.append({
            "file_path":        f"images/{eq_frame.name}",
            "transform_matrix": c2w.tolist(),
            "w":  equirect_w,
            "h":  equirect_h,
            "fl_x": fl_x,
            "fl_y": fl_y,
            "cx":   cx,
            "cy":   cy,
        })

    if verbose:
        print(f"      {len(frames)} frames included, {num_skipped} skipped")

    # Point cloud
    has_ply = False
    if points3d_txt.exists():
        if verbose:
            print(f"[4/4] Converting point cloud...")
        try:
            pts, cols = parse_colmap_points3d(points3d_txt)
            if max_initial_points is not None and len(pts) > max_initial_points:
                rng = np.random.default_rng(0)
                idx = rng.choice(len(pts), size=max_initial_points, replace=False)
                if verbose:
                    print(f"      Subsampling {len(pts)} → {max_initial_points} points "
                          f"(headroom for LichtFeld densification)")
                pts, cols = pts[idx], cols[idx]
            if len(pts) > 0:
                output_ply = output_dir / "pointcloud.ply"
                write_ply(output_ply, pts, cols, applied_transform)
                has_ply = True
                if verbose:
                    print(f"      {len(pts)} points → {output_ply}")
        except Exception as e:
            print(f"  Warning: point cloud conversion failed: {e}")
    else:
        if verbose:
            print("[4/4] No points3D.txt found — skipping point cloud")

    # Masks
    if mask_dir and mask_dir.is_dir():
        masks_out = output_dir / "masks"
        masks_out.mkdir(exist_ok=True)
        n = 0
        for mf in sorted(mask_dir.iterdir()):
            if mf.suffix.lower() in {'.png', '.jpg', '.jpeg'}:
                copy_or_downscale_mask(mf, masks_out / mf.name, downscale)
                n += 1
        if verbose and n:
            print(f"      Copied {n} masks → {masks_out}")

    # transforms.json
    transforms_data = {
        "camera_model":     "EQUIRECTANGULAR",
        "frames":           frames,
        "applied_transform": applied_transform.tolist(),
    }
    if has_ply:
        transforms_data["ply_file_path"] = "pointcloud.ply"

    out_json = output_dir / "transforms.json"
    with open(out_json, 'w') as f:
        json.dump(transforms_data, f, indent=4)

    if verbose:
        print(f"\nDone → {out_json}")
        print(f"  frames:      {len(frames)}")
        print(f"  skipped:     {num_skipped}")
        print(f"  point cloud: {'yes' if has_ply else 'no'}")

    return {"num_frames": len(frames), "num_skipped": num_skipped, "has_ply": has_ply}


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Convert COLMAP cubemap reconstruction to LichtFeld transforms.json (EQUIRECTANGULAR)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python colmap_to_lichtfeld.py --colmap-dir ./colmap/sparse/0 --equirect-dir ./equirect/frames --output ./lichtfeld_out
    python colmap_to_lichtfeld.py --colmap-dir ./colmap/sparse/0 --equirect-dir ./equirect/frames --output ./out --mask-dir ./equirect/frames/masks --downscale 2
        """,
    )
    p.add_argument("--colmap-dir",    type=Path, required=True,
                   help="COLMAP sparse reconstruction dir (cameras.txt, images.txt, points3D.txt)")
    p.add_argument("--equirect-dir",  type=Path, required=True,
                   help="Dir with equirectangular frames (frame_NNNNNN.jpg)")
    p.add_argument("--output",        type=Path, required=True,
                   help="Output dir for LichtFeld dataset")
    p.add_argument("--mask-dir",      type=Path, default=None,
                   help="Mask dir from mask_people_yolo.py (optional)")
    p.add_argument("--downscale",     type=int,  default=None,
                   help="Downscale images by this factor (e.g. 2 = half res)")
    p.add_argument("--ref-cam",       default="front",
                   help="Cubemap face to use as equirect reference pose (default: front)")
    p.add_argument("--frame-offset",  type=int, default=1,
                   help="COLMAP frame N → equirect frame N+offset (default: 1). "
                        "insv_to_equirect outputs 1-indexed frames; cubemap extraction is 0-indexed.")
    p.add_argument("--quiet",         action="store_true")

    args = p.parse_args()

    for path, name in [(args.colmap_dir, "--colmap-dir"), (args.equirect_dir, "--equirect-dir")]:
        if not path.exists():
            print(f"Error: {name} path not found: {path}")
            return 1

    try:
        convert(
            colmap_dir   = args.colmap_dir,
            equirect_dir = args.equirect_dir,
            output_dir   = args.output,
            mask_dir     = args.mask_dir,
            downscale    = args.downscale,
            ref_cam_id   = args.ref_cam,
            frame_offset = args.frame_offset,
            verbose      = not args.quiet,
        )
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
