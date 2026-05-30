"""
Generate COLMAP rig configuration for 360° camera setup
"""

import json
import math
from pathlib import Path


def create_rig_config(output_path: str, num_frames: int):
    """
    Create COLMAP rig configuration JSON for 360° camera

    Each frame is a rig instance with 6 cameras (cubemap style)
    All 6 cameras share the same position (origin of rig)

    Args:
        output_path: Path to save rig_config.json
        num_frames: Number of frames (rig instances)

    Format follows: https://colmap.github.io/legacy/3.12/rigs.html
    """

    # Define the 6 cameras in the rig (cubemap: front, right, back, left, up, down)
    # COLMAP rig_configurator parses cam_from_rig_rotation with
    # Eigen::Quaterniond(w, x, y, z) (Hamilton convention), per doc/rigs.rst.
    # image_prefix matches the directory name for each camera
    cameras = [
        {
            "image_prefix": "front/",
            "ref_sensor": True  # front camera is reference (identity pose)
        },
        {
            "image_prefix": "right/",
            # 90° rotation around Y axis (right)
            "cam_from_rig_rotation": [0.7071068, 0.0, 0.7071068, 0.0],  # [w, x, y, z]
            "cam_from_rig_translation": [0.0, 0.0, 0.0]
        },
        {
            "image_prefix": "back/",
            # 180° rotation around Y axis (back)
            "cam_from_rig_rotation": [0.0, 0.0, 1.0, 0.0],
            "cam_from_rig_translation": [0.0, 0.0, 0.0]
        },
        {
            "image_prefix": "left/",
            # -90° rotation around Y axis (left)
            "cam_from_rig_rotation": [0.7071068, 0.0, -0.7071068, 0.0],
            "cam_from_rig_translation": [0.0, 0.0, 0.0]
        },
        {
            "image_prefix": "up/",
            # 90° rotation around X axis (up)
            "cam_from_rig_rotation": [0.7071068, 0.7071068, 0.0, 0.0],
            "cam_from_rig_translation": [0.0, 0.0, 0.0]
        },
        {
            "image_prefix": "down/",
            # -90° rotation around X axis (down)
            "cam_from_rig_rotation": [0.7071068, -0.7071068, 0.0, 0.0],
            "cam_from_rig_translation": [0.0, 0.0, 0.0]
        }
    ]

    # Build rig configuration as array of rig objects
    rig_config = [
        {
            "cameras": cameras
        }
    ]

    # Write config
    with open(output_path, 'w') as f:
        json.dump(rig_config, f, indent=2)

    return num_frames


def create_custom_rig_config(output_path: str, cameras: list[dict]):
    """
    Create COLMAP rig configuration for custom cameras.

    Each camera definition gets its own folder prefix.
    The first camera is the reference sensor.

    Args:
        output_path: Path to save rig_config.json
        cameras: List of camera dicts with {id, yaw, pitch, roll, h_fov, v_fov}
    """

    rig_cameras = []

    for i, cam in enumerate(cameras):
        cam_id = cam.get('id', f'cam_{i}')
        yaw = cam.get('yaw', 0)
        pitch = cam.get('pitch', 0)
        roll = cam.get('roll', 0)

        if i == 0:
            # First camera is reference sensor (identity pose)
            rig_cameras.append({
                "image_prefix": f"{cam_id}/",
                "ref_sensor": True
            })
        else:
            # Compute relative rotation from first camera to this camera
            # Using quaternion math
            quat = _ypr_to_quaternion(yaw, pitch, roll)
            ref_quat = _ypr_to_quaternion(
                cameras[0].get('yaw', 0),
                cameras[0].get('pitch', 0),
                cameras[0].get('roll', 0)
            )

            # Relative rotation: cam_from_rig = cam * inv(ref)
            rel_quat = _quat_multiply(quat, _quat_inverse(ref_quat))

            rig_cameras.append({
                "image_prefix": f"{cam_id}/",
                # COLMAP rig_configurator parses this with Eigen::Quaterniond(w, x, y, z)
                # (Hamilton convention), per doc/rigs.rst.
                "cam_from_rig_rotation": [rel_quat[0], rel_quat[1], rel_quat[2], rel_quat[3]],  # [w,x,y,z]
                "cam_from_rig_translation": [0.0, 0.0, 0.0]
            })

    rig_config = [{"cameras": rig_cameras}]

    with open(output_path, 'w') as f:
        json.dump(rig_config, f, indent=2)


def _ypr_to_quaternion(yaw: float, pitch: float, roll: float):
    """
    Convert yaw/pitch/roll (degrees) to quaternion [w, x, y, z].

    Follows FFmpeg v360 convention:
    - yaw: rotation around Y axis (left/right)
    - pitch: rotation around X axis (up/down)
    - roll: rotation around Z axis
    """
    # Convert to radians
    yaw_rad = math.radians(yaw)
    pitch_rad = math.radians(pitch)
    roll_rad = math.radians(roll)

    # Compute quaternion from Euler angles (YXZ order)
    cy = math.cos(yaw_rad * 0.5)
    sy = math.sin(yaw_rad * 0.5)
    cp = math.cos(pitch_rad * 0.5)
    sp = math.sin(pitch_rad * 0.5)
    cr = math.cos(roll_rad * 0.5)
    sr = math.sin(roll_rad * 0.5)

    w = cr * cp * cy - sr * sp * sy
    x = cr * sp * cy - sr * cp * sy
    y = cr * cp * sy + sr * sp * cy
    z = cr * sp * sy + sr * cp * cy

    return [w, x, y, z]


def _quat_inverse(q):
    """Compute inverse of quaternion [w, x, y, z]."""
    w, x, y, z = q
    norm_sq = w*w + x*x + y*y + z*z
    return [w/norm_sq, -x/norm_sq, -y/norm_sq, -z/norm_sq]


def _quat_multiply(q1, q2):
    """Multiply two quaternions [w, x, y, z]."""
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2

    w = w1*w2 - x1*x2 - y1*y2 - z1*z2
    x = w1*x2 + x1*w2 + y1*z2 - z1*y2
    y = w1*y2 - x1*z2 + y1*w2 + z1*x2
    z = w1*z2 + x1*y2 - y1*x2 + z1*w2

    return [w, x, y, z]
