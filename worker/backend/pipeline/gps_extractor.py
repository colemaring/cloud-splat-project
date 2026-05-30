"""
GPS extraction and coordinate conversion utilities for .insv files
"""

import subprocess
import json
import xml.etree.ElementTree as ET
import numpy as np
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger(__name__)

# WGS84 ellipsoid parameters
WGS84_A = 6378137.0  # Semi-major axis (meters)
WGS84_F = 1 / 298.257223563  # Flattening
WGS84_E2 = 2 * WGS84_F - WGS84_F ** 2  # Eccentricity squared


def check_exiftool() -> bool:
    """
    Check if ExifTool is available in the system PATH

    Returns:
        True if ExifTool is available, False otherwise
    """
    try:
        result = subprocess.run(
            ["exiftool", "-ver"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            logger.info(f"ExifTool version: {result.stdout.strip()}")
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    logger.warning("ExifTool not found in PATH")
    return False


def extract_gps_track(insv_path: str, ffmpeg_exe: str) -> Tuple[List[Dict], float]:
    """
    Extract GPS track from .insv file using ExifTool

    Args:
        insv_path: Path to .insv file
        ffmpeg_exe: Path to ffmpeg executable (used for getting video fps)

    Returns:
        Tuple of (gps_points, fps) where gps_points is a list of dicts with keys:
            - lat: latitude in degrees
            - lon: longitude in degrees
            - alt: altitude in meters
            - time: timestamp string (optional)
        and fps is the video frame rate

    Raises:
        RuntimeError: If ExifTool fails or no GPS data found
    """
    if not check_exiftool():
        raise RuntimeError("ExifTool not available")

    # Get video fps using ffprobe
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=r_frame_rate",
                "-of", "json",
                insv_path
            ],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {result.stderr}")

        data = json.loads(result.stdout)
        fps_str = data["streams"][0]["r_frame_rate"]
        num, denom = map(int, fps_str.split("/"))
        fps = num / denom
        logger.info(f"Video FPS: {fps:.2f}")

    except Exception as e:
        logger.warning(f"Failed to get video fps: {e}, defaulting to 30")
        fps = 30.0

    # Extract GPS track using ExifTool with -ee flag for per-frame data
    logger.info(f"Extracting GPS track from {insv_path}")

    # Find gpx.fmt file (should be in same directory as this script)
    gpx_fmt_path = Path(__file__).parent / "gpx.fmt"
    if not gpx_fmt_path.exists():
        raise RuntimeError(f"GPX format file not found: {gpx_fmt_path}")

    try:
        # Extract per-frame GPS data using GPX format
        # The -p gpx.fmt extracts GPS from video stream, not just file metadata
        result = subprocess.run(
            ["exiftool", "-ee", "-api", "largefilesupport=1", "-p", str(gpx_fmt_path), insv_path],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            raise RuntimeError(f"ExifTool failed: {result.stderr}")

        gpx_data = result.stdout

        if not gpx_data or len(gpx_data.strip()) == 0:
            raise RuntimeError("No GPS data found in .insv file")

        # Parse GPX XML
        gps_points = parse_gpx(gpx_data)

        if not gps_points:
            raise RuntimeError("Failed to parse GPS data from .insv file")

        logger.info(f"Extracted {len(gps_points)} GPS points")
        return gps_points, fps

    except subprocess.TimeoutExpired:
        raise RuntimeError("ExifTool timeout - file may be corrupted")
    except Exception as e:
        raise RuntimeError(f"Failed to extract GPS data: {e}")


def parse_gpx(gpx_data: str) -> List[Dict]:
    """
    Parse GPX XML data into list of GPS points

    Args:
        gpx_data: GPX XML string

    Returns:
        List of dicts with lat, lon, alt, time
    """
    try:
        root = ET.fromstring(gpx_data)

        # Handle GPX namespace
        ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}

        # Find all track points
        trkpts = root.findall('.//gpx:trkpt', ns)
        if not trkpts:
            # Try without namespace
            trkpts = root.findall('.//trkpt')

        gps_points = []
        for trkpt in trkpts:
            lat = float(trkpt.get('lat'))
            lon = float(trkpt.get('lon'))

            # Get elevation (altitude)
            ele_elem = trkpt.find('gpx:ele', ns)
            if ele_elem is None:
                ele_elem = trkpt.find('ele')
            alt = float(ele_elem.text) if ele_elem is not None else 0.0

            # Get timestamp (optional)
            time_elem = trkpt.find('gpx:time', ns)
            if time_elem is None:
                time_elem = trkpt.find('time')
            time_str = time_elem.text if time_elem is not None else None

            gps_points.append({
                'lat': lat,
                'lon': lon,
                'alt': alt,
                'time': time_str
            })

        # Drop consecutive duplicates — .insv emits ~33 identical trkpts per
        # unique GPS chip reading, which would cause argmin ties during
        # timestamp-based matching.
        deduped: List[Dict] = []
        for pt in gps_points:
            if not deduped or (
                pt['lat'] != deduped[-1]['lat']
                or pt['lon'] != deduped[-1]['lon']
                or pt['alt'] != deduped[-1]['alt']
                or pt['time'] != deduped[-1]['time']
            ):
                deduped.append(pt)

        logger.info(f"Extracted {len(deduped)} unique GPS fixes (from {len(trkpts)} raw records)")
        return deduped

    except Exception as e:
        logger.error(f"Failed to parse GPX: {e}")
        return []


def geodetic_to_ecef(lat: float, lon: float, alt: float) -> np.ndarray:
    """
    Convert geodetic coordinates (lat, lon, alt) to ECEF (Earth-Centered, Earth-Fixed)

    Args:
        lat: Latitude in degrees
        lon: Longitude in degrees
        alt: Altitude in meters (above WGS84 ellipsoid)

    Returns:
        ECEF coordinates as [X, Y, Z] in meters
    """
    lat_rad = np.radians(lat)
    lon_rad = np.radians(lon)

    sin_lat = np.sin(lat_rad)
    cos_lat = np.cos(lat_rad)
    sin_lon = np.sin(lon_rad)
    cos_lon = np.cos(lon_rad)

    # Prime vertical radius of curvature
    N = WGS84_A / np.sqrt(1 - WGS84_E2 * sin_lat ** 2)

    X = (N + alt) * cos_lat * cos_lon
    Y = (N + alt) * cos_lat * sin_lon
    Z = (N * (1 - WGS84_E2) + alt) * sin_lat

    return np.array([X, Y, Z])


def ecef_to_enu(ecef: np.ndarray, ref_lat: float, ref_lon: float, ref_alt: float) -> np.ndarray:
    """
    Convert ECEF coordinates to local ENU (East-North-Up) coordinates

    Args:
        ecef: ECEF coordinates [X, Y, Z] in meters
        ref_lat: Reference latitude in degrees
        ref_lon: Reference longitude in degrees
        ref_alt: Reference altitude in meters

    Returns:
        ENU coordinates [East, North, Up] in meters
    """
    # Reference point in ECEF
    ref_ecef = geodetic_to_ecef(ref_lat, ref_lon, ref_alt)

    # Vector from reference to point
    delta = ecef - ref_ecef

    # Rotation matrix from ECEF to ENU
    lat_rad = np.radians(ref_lat)
    lon_rad = np.radians(ref_lon)

    sin_lat = np.sin(lat_rad)
    cos_lat = np.cos(lat_rad)
    sin_lon = np.sin(lon_rad)
    cos_lon = np.cos(lon_rad)

    # Rotation matrix
    R = np.array([
        [-sin_lon, cos_lon, 0],
        [-sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat],
        [cos_lat * cos_lon, cos_lat * sin_lon, sin_lat]
    ])

    enu = R @ delta
    return enu


def gps_to_local_coords(gps_points: List[Dict]) -> np.ndarray:
    """
    Convert GPS points to local Y-up coordinates for Three.js

    Uses the first GPS point as the reference origin.
    Coordinate system: X=East, Y=Up, Z=-North (Three.js convention)

    Args:
        gps_points: List of dicts with lat, lon, alt

    Returns:
        Nx3 numpy array of local coordinates where Y is up
    """
    if not gps_points:
        return np.array([])

    # Use first point as reference origin
    ref = gps_points[0]
    ref_lat = ref['lat']
    ref_lon = ref['lon']
    ref_alt = ref['alt']

    logger.info(f"Reference origin: lat={ref_lat:.6f}, lon={ref_lon:.6f}, alt={ref_alt:.2f}m")

    # Convert all points to local coordinates
    local_coords = []
    for point in gps_points:
        # Convert to ECEF
        ecef = geodetic_to_ecef(point['lat'], point['lon'], point['alt'])

        # Convert to ENU
        enu = ecef_to_enu(ecef, ref_lat, ref_lon, ref_alt)

        # Remap to Y-up for Three.js: (East, North, Up) -> (East, Up, -North)
        x = enu[0]  # East
        y = enu[2]  # Up
        z = -enu[1]  # -North

        local_coords.append([x, y, z])

    return np.array(local_coords)


def match_frames_to_gps_by_time(
    gps_points: List[Dict],
    frame_video_times: Dict[int, float],
    tolerance_s: float = 2.0,
) -> Dict[int, Dict]:
    """Match frame numbers to GPS points using wall-clock timestamps.

    Args:
        gps_points: GPS points from parse_gpx (must have 'time' fields).
        frame_video_times: Mapping of frame_num -> video time in seconds
            (typically frame_num * frame_interval).
        tolerance_s: Maximum allowed gap between video time and nearest
            GPS sample time. Frames outside this window are dropped.

    Returns:
        Dict mapping frame_num to the nearest matching GPS point dict.
    """
    timed = [(pt, pt.get('time')) for pt in gps_points if pt.get('time')]
    if not timed:
        logger.warning("GPS points have no timestamps; cannot do timestamp-based matching")
        return {}

    def _parse_iso(s: str) -> datetime:
        # Pad fractional seconds to 6 digits — Python 3.10 fromisoformat only
        # accepts 3 or 6 digits; ExifTool sometimes emits 2 (e.g. ".17Z").
        import re
        s = re.sub(r'(\.\d+)', lambda m: m.group(1).ljust(7, '0')[:7], s)
        return datetime.fromisoformat(s.replace('Z', '+00:00'))

    try:
        t0 = _parse_iso(timed[0][1])
        t_rel = np.array(
            [(_parse_iso(t) - t0).total_seconds() for _, t in timed],
            dtype=np.float64,
        )
    except Exception as e:
        logger.warning(f"Failed to parse GPS timestamps: {e}")
        return {}

    pts_ordered = [pt for pt, _ in timed]

    matched: Dict[int, Dict] = {}
    for frame_num, video_t in frame_video_times.items():
        diffs = np.abs(t_rel - video_t)
        idx = int(np.argmin(diffs))
        if diffs[idx] <= tolerance_s:
            matched[frame_num] = pts_ordered[idx]
        else:
            logger.debug(
                f"Frame {frame_num} (video t={video_t:.2f}s): nearest GPS sample "
                f"is {diffs[idx]:.2f}s away, exceeds tolerance ({tolerance_s}s) — skipped"
            )

    logger.info(
        f"Timestamp-based GPS match: {len(matched)}/{len(frame_video_times)} frames matched"
    )
    return matched


def interpolate_gps_gaps(
    matched: Dict[int, Dict],
    frame_video_times: Dict[int, float],
    max_gap_s: float = 10.0,
) -> Dict[int, Dict]:
    """Fill short GPS gaps by linear interpolation between matched neighbors.

    Hardware GPS loss creates windows with no fix at all. Gaps ≤ max_gap_s
    are bridged with linear lat/lon/alt interpolation. Longer gaps (e.g. 89 s
    signal loss) are left unmatched — interpolating across them would be
    misleading at city-scale distances.

    Args:
        matched: Output of match_frames_to_gps_by_time.
        frame_video_times: Full frame_num → video_t mapping (includes unmatched frames).
        max_gap_s: Maximum GPS gap (seconds) to bridge. Default 10 s.

    Returns:
        Augmented matched dict; interpolated entries have 'interpolated': True.
    """
    if len(matched) < 2:
        return matched

    import bisect

    result = dict(matched)

    matched_frames = sorted(matched.keys(), key=lambda f: frame_video_times[f])
    matched_video_t = [frame_video_times[f] for f in matched_frames]

    def _parse_iso(s: str) -> datetime:
        import re
        s = re.sub(r'(\.\d+)', lambda m: m.group(1).ljust(7, '0')[:7], s)
        return datetime.fromisoformat(s.replace('Z', '+00:00'))

    n_filled = 0
    for frame_num, video_t in frame_video_times.items():
        if frame_num in matched:
            continue

        idx = bisect.bisect_left(matched_video_t, video_t)

        if idx == 0 or idx == len(matched_frames):
            continue  # Outside the GPS coverage window — leave unmatched

        before_frame = matched_frames[idx - 1]
        after_frame = matched_frames[idx]
        before_pt = matched[before_frame]
        after_pt = matched[after_frame]

        before_time_str = before_pt.get('time')
        after_time_str = after_pt.get('time')
        if not before_time_str or not after_time_str:
            continue

        try:
            t_before = _parse_iso(before_time_str)
            t_after = _parse_iso(after_time_str)
            gap_s = (t_after - t_before).total_seconds()
        except Exception:
            continue

        if gap_s > max_gap_s:
            continue  # Gap too large — don't guess

        span_video = frame_video_times[after_frame] - frame_video_times[before_frame]
        if span_video <= 0:
            continue
        alpha = (video_t - frame_video_times[before_frame]) / span_video

        t_interp = t_before + (t_after - t_before) * alpha
        result[frame_num] = {
            'lat': before_pt['lat'] * (1 - alpha) + after_pt['lat'] * alpha,
            'lon': before_pt['lon'] * (1 - alpha) + after_pt['lon'] * alpha,
            'alt': before_pt['alt'] * (1 - alpha) + after_pt['alt'] * alpha,
            'time': t_interp.strftime('%Y-%m-%dT%H:%M:%S') + 'Z',
            'interpolated': True,
        }
        n_filled += 1

    if n_filled:
        logger.info(f"GPS gap interpolation: filled {n_filled} frames (gap threshold {max_gap_s}s)")
    return result


def match_frames_to_gps(
    colmap_frame_positions: Dict[int, np.ndarray],
    gps_points: List[Dict],
    frame_interval: float,
    fps: float
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Match COLMAP frame positions to GPS points

    Args:
        colmap_frame_positions: Dict mapping frame number to camera position (Nx3)
        gps_points: List of GPS points from extract_gps_track
        frame_interval: Frame extraction interval in seconds
        fps: Video frame rate

    Returns:
        Tuple of (colmap_positions, gps_positions) as Nx3 numpy arrays
        Only includes matched pairs
    """
    # Convert GPS to local coordinates
    gps_local = gps_to_local_coords(gps_points)

    if len(gps_local) == 0:
        logger.warning("No GPS points to match")
        return np.array([]), np.array([])

    colmap_matched = []
    gps_matched = []

    for frame_num, colmap_pos in colmap_frame_positions.items():
        # Calculate GPS index: gps_index = frame_num * frame_interval * fps
        gps_index = int(round(frame_num * frame_interval * fps))

        # Check if GPS index is valid
        if 0 <= gps_index < len(gps_local):
            colmap_matched.append(colmap_pos)
            gps_matched.append(gps_local[gps_index])
        else:
            logger.debug(f"Frame {frame_num} -> GPS index {gps_index} out of range (0-{len(gps_local)-1})")

    if not colmap_matched:
        logger.warning("No frames could be matched to GPS points")
        return np.array([]), np.array([])

    logger.info(f"Matched {len(colmap_matched)} camera positions to GPS points")

    return np.array(colmap_matched), np.array(gps_matched)
