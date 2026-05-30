"""
Gaussian Splat leveling using GPS data and Kabsch-Umeyama alignment
"""

import numpy as np
import json
import logging
from pathlib import Path
from typing import Dict, Tuple, Optional
from plyfile import PlyData, PlyElement

from .gps_extractor import extract_gps_track, match_frames_to_gps

logger = logging.getLogger(__name__)


def parse_colmap_images(images_txt_path: str) -> Dict[int, np.ndarray]:
    """
    Parse COLMAP images.txt to extract camera positions grouped by frame number

    Args:
        images_txt_path: Path to COLMAP images.txt file

    Returns:
        Dict mapping frame number to average camera position [x, y, z]
    """
    frame_positions = {}

    with open(images_txt_path, 'r') as f:
        lines = f.readlines()

    # Skip header comments
    line_idx = 0
    while line_idx < len(lines) and lines[line_idx].startswith('#'):
        line_idx += 1

    # Parse image entries (2 lines per image)
    while line_idx < len(lines):
        line = lines[line_idx].strip()
        if not line:
            line_idx += 1
            continue

        # Parse image line: IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME
        parts = line.split()
        if len(parts) < 10:
            line_idx += 1
            continue

        qw, qx, qy, qz = map(float, parts[1:5])
        tx, ty, tz = map(float, parts[5:8])
        image_name = parts[9]

        # Extract frame number from filename
        # Expected format: cam_front/frame_000005.jpg or frame_000005.jpg
        try:
            if '/' in image_name:
                # Per-folder format: cam_front/frame_000005.jpg
                filename = image_name.split('/')[-1]
            else:
                filename = image_name

            # Extract frame number: frame_000005.jpg -> 5
            if 'frame_' in filename:
                frame_str = filename.split('frame_')[1].split('.')[0]
                frame_num = int(frame_str)
            else:
                # Skip if no frame number
                line_idx += 2
                continue
        except (ValueError, IndexError):
            logger.debug(f"Could not parse frame number from {image_name}")
            line_idx += 2
            continue

        # Convert quaternion rotation and translation to camera center
        # Camera center C = -R^T * t
        # R from quaternion (w, x, y, z)
        R = quaternion_to_rotation_matrix(qw, qx, qy, qz)
        t = np.array([tx, ty, tz])
        camera_center = -R.T @ t

        # Group by frame number (average if multiple cameras per frame)
        if frame_num not in frame_positions:
            frame_positions[frame_num] = []
        frame_positions[frame_num].append(camera_center)

        # Skip the points line
        line_idx += 2

    # Average positions for each frame
    frame_avg_positions = {}
    for frame_num, positions in frame_positions.items():
        frame_avg_positions[frame_num] = np.mean(positions, axis=0)

    logger.info(f"Parsed {len(frame_avg_positions)} frames from COLMAP images.txt")
    return frame_avg_positions


def quaternion_to_rotation_matrix(w: float, x: float, y: float, z: float) -> np.ndarray:
    """
    Convert quaternion (w, x, y, z) to 3x3 rotation matrix

    Args:
        w, x, y, z: Quaternion components

    Returns:
        3x3 rotation matrix
    """
    R = np.array([
        [1 - 2*(y**2 + z**2), 2*(x*y - w*z), 2*(x*z + w*y)],
        [2*(x*y + w*z), 1 - 2*(x**2 + z**2), 2*(y*z - w*x)],
        [2*(x*z - w*y), 2*(y*z + w*x), 1 - 2*(x**2 + y**2)]
    ])
    return R


def rotation_matrix_to_quaternion(R: np.ndarray) -> np.ndarray:
    """
    Convert 3x3 rotation matrix to quaternion (w, x, y, z)

    Args:
        R: 3x3 rotation matrix

    Returns:
        Quaternion as [w, x, y, z]
    """
    trace = np.trace(R)

    if trace > 0:
        s = 0.5 / np.sqrt(trace + 1.0)
        w = 0.25 / s
        x = (R[2, 1] - R[1, 2]) * s
        y = (R[0, 2] - R[2, 0]) * s
        z = (R[1, 0] - R[0, 1]) * s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = 2.0 * np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = 2.0 * np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = 2.0 * np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s

    q = np.array([w, x, y, z])
    return q / np.linalg.norm(q)  # Normalize


def quaternion_multiply(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    """
    Hamilton product of two quaternions (w, x, y, z)

    Args:
        q1: First quaternion [w, x, y, z]
        q2: Second quaternion [w, x, y, z]

    Returns:
        Product quaternion [w, x, y, z]
    """
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2

    w = w1*w2 - x1*x2 - y1*y2 - z1*z2
    x = w1*x2 + x1*w2 + y1*z2 - z1*y2
    y = w1*y2 - x1*z2 + y1*w2 + z1*x2
    z = w1*z2 + x1*y2 - y1*x2 + z1*w2

    return np.array([w, x, y, z])


def kabsch_umeyama(P: np.ndarray, Q: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
    """
    Compute optimal similarity transformation from P to Q using Kabsch-Umeyama algorithm

    Args:
        P: Source points (Nx3) - COLMAP camera positions
        Q: Target points (Nx3) - GPS-derived ENU coordinates

    Returns:
        R: 3x3 rotation matrix
        t: 3x1 translation vector
        s: scale factor (scalar)
    """
    assert P.shape == Q.shape, "Point sets must have same shape"
    assert P.shape[1] == 3, "Points must be 3D"

    n = P.shape[0]

    # Center the point sets
    P_mean = P.mean(axis=0)
    Q_mean = Q.mean(axis=0)
    P_centered = P - P_mean
    Q_centered = Q - Q_mean

    # Compute scale
    P_scale = np.sqrt((P_centered ** 2).sum() / n)
    Q_scale = np.sqrt((Q_centered ** 2).sum() / n)
    s = Q_scale / P_scale

    # Normalize by scale
    P_normalized = P_centered / P_scale
    Q_normalized = Q_centered / Q_scale

    # Compute rotation using SVD
    H = P_normalized.T @ Q_normalized
    U, _, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T

    # Handle reflection case (ensure proper rotation)
    if np.linalg.det(R) < 0:
        logger.warning("Reflection detected in Kabsch-Umeyama, correcting...")
        Vt[-1, :] *= -1
        R = Vt.T @ U.T

    # Compute translation
    t = Q_mean - s * R @ P_mean

    return R, t, s


def compute_alignment_error(source_transformed: np.ndarray, target: np.ndarray) -> float:
    """
    Compute RMSE after alignment

    Args:
        source_transformed: Transformed source points
        target: Target points

    Returns:
        RMSE value
    """
    squared_dists = np.sum((source_transformed - target) ** 2, axis=1)
    rmse = np.sqrt(squared_dists.mean())
    return rmse


def level_splat_ply(input_ply: str, output_ply: str, R: np.ndarray) -> None:
    """
    Apply rotation to Gaussian splat PLY file

    Rotates positions and quaternions. Does NOT scale (rotation-only leveling).

    Args:
        input_ply: Path to input splat PLY
        output_ply: Path to output leveled PLY
        R: 3x3 rotation matrix
    """
    logger.info(f"Leveling splat PLY: {input_ply}")

    # Read PLY
    plydata = PlyData.read(input_ply)
    vertex = plydata['vertex']

    # Extract positions
    x = vertex['x']
    y = vertex['y']
    z = vertex['z']
    positions = np.vstack([x, y, z]).T

    # Rotate positions
    rotated_positions = (R @ positions.T).T

    # Extract quaternions (rot_0=w, rot_1=x, rot_2=y, rot_3=z)
    q_w = vertex['rot_0']
    q_x = vertex['rot_1']
    q_y = vertex['rot_2']
    q_z = vertex['rot_3']

    # Convert R to quaternion
    q_R = rotation_matrix_to_quaternion(R)

    # Rotate each Gaussian's quaternion: q_new = q_R * q_old
    rotated_quats = []
    for i in range(len(q_w)):
        q_old = np.array([q_w[i], q_x[i], q_y[i], q_z[i]])
        q_new = quaternion_multiply(q_R, q_old)
        q_new = q_new / np.linalg.norm(q_new)  # Normalize
        rotated_quats.append(q_new)

    rotated_quats = np.array(rotated_quats)

    # Create new vertex data with rotated positions and quaternions
    new_vertex_data = []
    for i in range(len(vertex)):
        row = list(vertex[i])
        # Update positions
        row[0] = rotated_positions[i, 0]  # x
        row[1] = rotated_positions[i, 1]  # y
        row[2] = rotated_positions[i, 2]  # z

        # Update quaternions (find indices for rot_0, rot_1, rot_2, rot_3)
        dtype_names = vertex.dtype().names
        rot_0_idx = dtype_names.index('rot_0')
        row[rot_0_idx] = rotated_quats[i, 0]  # w
        row[rot_0_idx + 1] = rotated_quats[i, 1]  # x
        row[rot_0_idx + 2] = rotated_quats[i, 2]  # y
        row[rot_0_idx + 3] = rotated_quats[i, 3]  # z

        new_vertex_data.append(tuple(row))

    # Create new PLY element
    new_vertex = PlyElement.describe(
        np.array(new_vertex_data, dtype=vertex.dtype()),
        'vertex'
    )

    # Write output PLY
    PlyData([new_vertex], text=plydata.text).write(output_ply)
    logger.info(f"Saved leveled splat to: {output_ply}")


def level_pointcloud_ply(input_ply: str, output_ply: str, R: np.ndarray) -> None:
    """
    Apply rotation to point cloud PLY file

    Args:
        input_ply: Path to input point cloud PLY
        output_ply: Path to output leveled PLY
        R: 3x3 rotation matrix
    """
    logger.info(f"Leveling point cloud PLY: {input_ply}")

    # Read PLY
    plydata = PlyData.read(input_ply)
    vertex = plydata['vertex']

    # Extract positions
    x = vertex['x']
    y = vertex['y']
    z = vertex['z']
    positions = np.vstack([x, y, z]).T

    # Rotate positions
    rotated_positions = (R @ positions.T).T

    # Create new vertex data
    new_vertex_data = []
    for i in range(len(vertex)):
        row = list(vertex[i])
        row[0] = rotated_positions[i, 0]  # x
        row[1] = rotated_positions[i, 1]  # y
        row[2] = rotated_positions[i, 2]  # z
        new_vertex_data.append(tuple(row))

    # Create new PLY element
    new_vertex = PlyElement.describe(
        np.array(new_vertex_data, dtype=vertex.dtype()),
        'vertex'
    )

    # Write output PLY
    PlyData([new_vertex], text=plydata.text).write(output_ply)
    logger.info(f"Saved leveled point cloud to: {output_ply}")


def compute_and_apply_leveling(
    insv_path: str,
    colmap_reconstruction_dir: str,
    splat_ply: str,
    pointcloud_ply: str,
    frame_interval: float,
    ffmpeg_exe: str
) -> Tuple[bool, Optional[float]]:
    """
    Main orchestrator for GPS-based scene leveling

    Args:
        insv_path: Path to .insv file with GPS data
        colmap_reconstruction_dir: Path to COLMAP reconstruction directory (contains images.txt)
        splat_ply: Path to Gaussian splat PLY file
        pointcloud_ply: Path to point cloud PLY file
        frame_interval: Frame extraction interval in seconds
        ffmpeg_exe: Path to ffmpeg executable

    Returns:
        Tuple of (success: bool, rmse: Optional[float])
        rmse is the alignment error in meters, or None if leveling failed
    """
    try:
        logger.info("=" * 80)
        logger.info("GPS-BASED SCENE LEVELING")
        logger.info("=" * 80)

        # Step 1: Extract GPS track
        logger.info("Step 1/4: Extracting GPS track from .insv file")
        gps_points, fps = extract_gps_track(insv_path, ffmpeg_exe)

        if len(gps_points) == 0:
            logger.warning("No GPS points found - skipping leveling")
            return False, None

        # Check if camera is stationary (all GPS points are identical)
        gps_lats = [p['lat'] for p in gps_points]
        gps_lons = [p['lon'] for p in gps_points]
        if np.std(gps_lats) < 1e-6 and np.std(gps_lons) < 1e-6:
            logger.warning("Camera appears stationary (no GPS variation) - skipping leveling")
            return False, None

        # Step 2: Parse COLMAP camera positions
        logger.info("Step 2/4: Parsing COLMAP camera positions")
        images_txt = Path(colmap_reconstruction_dir) / "images.txt"
        if not images_txt.exists():
            logger.warning(f"COLMAP images.txt not found at {images_txt} - skipping leveling")
            return False, None

        colmap_frame_positions = parse_colmap_images(str(images_txt))

        if len(colmap_frame_positions) == 0:
            logger.warning("No camera positions found in COLMAP reconstruction - skipping leveling")
            return False, None

        # Step 3: Match frames to GPS and compute Kabsch-Umeyama
        logger.info("Step 3/4: Matching cameras to GPS and computing alignment")
        colmap_positions, gps_positions = match_frames_to_gps(
            colmap_frame_positions,
            gps_points,
            frame_interval,
            fps
        )

        if len(colmap_positions) < 3:
            logger.warning(f"Only {len(colmap_positions)} matched camera-GPS pairs - need at least 3 for alignment")
            return False, None

        # Compute Kabsch-Umeyama transformation
        R, t, s = kabsch_umeyama(colmap_positions, gps_positions)

        logger.info(f"Transformation computed:")
        logger.info(f"  Scale: {s:.6f}")
        logger.info(f"  Rotation matrix:")
        for row in R:
            logger.info(f"    [{row[0]:8.5f}, {row[1]:8.5f}, {row[2]:8.5f}]")
        logger.info(f"  Translation: [{t[0]:8.3f}, {t[1]:8.3f}, {t[2]:8.3f}]")

        # Validate alignment
        colmap_transformed = s * (colmap_positions @ R.T) + t
        rmse = compute_alignment_error(colmap_transformed, gps_positions)
        logger.info(f"Alignment RMSE: {rmse:.6f} meters")

        if rmse > 100:
            logger.warning(f"RMSE is very high ({rmse:.2f}m) - alignment may be inaccurate, but applying anyway")

        # Save transformation matrix for debugging
        transformation_data = {
            "rotation": R.tolist(),
            "translation": t.tolist(),
            "scale": float(s),
            "rmse": float(rmse),
            "num_matched_cameras": len(colmap_positions)
        }

        leveling_json = Path(splat_ply).parent / "leveling_transform.json"
        with open(leveling_json, 'w') as f:
            json.dump(transformation_data, f, indent=2)
        logger.info(f"Saved leveling transform to: {leveling_json}")

        # Step 4: Apply rotation to PLY files (rotation-only, no scale/translation)
        logger.info("Step 4/4: Applying rotation to PLY files")

        # Apply to splat
        if Path(splat_ply).exists():
            splat_leveled = str(Path(splat_ply).parent / f"splat_{Path(splat_ply).stem.split('_')[-1]}_leveled.ply")
            level_splat_ply(splat_ply, splat_leveled, R)
            # Replace original with leveled version
            Path(splat_leveled).replace(splat_ply)
            logger.info(f"Replaced {splat_ply} with leveled version")
        else:
            logger.info(f"Splat PLY not found at {splat_ply} - skipping")

        # Apply to point cloud
        if Path(pointcloud_ply).exists():
            pc_leveled = str(Path(pointcloud_ply).parent / f"pointcloud_{Path(pointcloud_ply).stem.split('_')[-1]}_leveled.ply")
            level_pointcloud_ply(pointcloud_ply, pc_leveled, R)
            # Replace original with leveled version
            Path(pc_leveled).replace(pointcloud_ply)
            logger.info(f"Replaced {pointcloud_ply} with leveled version")
        else:
            logger.info(f"Point cloud PLY not found at {pointcloud_ply} - skipping")

        logger.info("=" * 80)
        logger.info("LEVELING COMPLETE!")
        logger.info("=" * 80)

        return True, rmse

    except Exception as e:
        logger.warning(f"Leveling failed: {e}")
        return False, None
