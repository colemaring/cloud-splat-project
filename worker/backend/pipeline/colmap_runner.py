"""
COLMAP Pipeline Runner
Adapted from 3d-project3 for 360-to-splat processing
"""

import os
import subprocess
import logging
import math
import time
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class ColmapRunner:
    """Runs COLMAP reconstruction pipeline"""

    def __init__(self, colmap_exe: str):
        self.colmap_exe = colmap_exe

    def run(self, images_dir: str, output_dir: str, log_file: str, progress_callback=None, rig_config_path: str = None, camera_h_fov: float = None, camera_image_size: int = None, mask_path: str = None):
        """
        Run COLMAP reconstruction pipeline

        Args:
            images_dir: Directory containing perspective images
            output_dir: Directory to save COLMAP output
            log_file: Path to log file
            progress_callback: Optional callback(percent, message) for progress updates
            rig_config_path: Optional path to rig configuration JSON for 360° camera
            camera_h_fov: Horizontal FOV in degrees (used to compute known focal length)
            camera_image_size: Image width in pixels (used to compute known focal length)
            mask_path: Optional mask directory (COLMAP convention: mirrors images_dir,
                       with mask files named '<image_name>.png'). When provided, COLMAP
                       ignores black pixels during feature extraction.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Setup paths
        database_path = output_dir / "database.db"
        sparse_dir = output_dir / "sparse"
        sparse_dir.mkdir(exist_ok=True)

        use_rig = rig_config_path is not None

        # Compute known focal length from FOV if provided
        # For PINHOLE: focal_length = image_size / (2 * tan(h_fov / 2))
        known_camera_params = None
        if camera_h_fov is not None and camera_image_size is not None:
            fl = camera_image_size / (2.0 * math.tan(math.radians(camera_h_fov) / 2.0))
            cx = cy = camera_image_size / 2.0
            known_camera_params = f"{fl:.4f},{fl:.4f},{cx:.4f},{cy:.4f}"
            logger.info(f"Using fixed camera intrinsics: focal_length={fl:.1f}, cx=cy={cx:.1f} (from {camera_h_fov}° FOV, {camera_image_size}px)")

        logger.info(f"Starting COLMAP reconstruction pipeline (rig mode: {use_rig})")

        try:
            # Step 1: Feature extraction
            if progress_callback:
                progress_callback(0, "Extracting features...")

            feature_extractor_args = [
                "--database_path", str(database_path),
                "--image_path", images_dir,
                "--ImageReader.camera_model", "PINHOLE",  # No distortion for cubemap faces
            ]

            if known_camera_params:
                feature_extractor_args.extend([
                    "--ImageReader.camera_params", known_camera_params
                ])

            if mask_path:
                feature_extractor_args.extend([
                    "--ImageReader.mask_path", mask_path
                ])
                logger.info(f"Using feature masks from: {mask_path}")

            # For rig mode: one camera per folder (each folder = one camera in the rig)
            # All images in a folder share the same camera parameters
            if use_rig:
                feature_extractor_args.extend([
                    "--ImageReader.single_camera_per_folder", "1"
                ])
            else:
                feature_extractor_args.extend([
                    "--ImageReader.single_camera", "1"
                ])

            self._run_colmap_command(
                "feature_extractor",
                feature_extractor_args,
                log_file
            )

            # Step 1.5: Configure rig (if using rig mode)
            if use_rig:
                if progress_callback:
                    progress_callback(20, "Configuring camera rig...")

                logger.info("Configuring camera rig from JSON")
                self._run_colmap_command(
                    "rig_configurator",
                    [
                        "--database_path", str(database_path),
                        "--rig_config_path", rig_config_path
                    ],
                    log_file
                )

            # Step 2: Feature matching
            # For cubemap faces: use exhaustive matcher for better results
            # Sequential matcher works well when images are named frame_NNNNNN_face.jpg
            if progress_callback:
                progress_callback(30, "Matching features...")

            # Determine if this looks like multi-camera setup (cubemap or custom cameras)
            # If images_dir contains files like "frame_000000_front.jpg" or "frame_000000_cam_1.jpg",
            # or subdirectories, use exhaustive matcher
            import os
            sample_files = os.listdir(images_dir)[:10]

            # Check for subdirectories (per-folder mode)
            has_subdirs = any(os.path.isdir(os.path.join(images_dir, f)) for f in sample_files)

            # Check for cubemap face naming
            has_cubemap = any('_front' in f or '_back' in f or '_left' in f or '_right' in f
                             for f in sample_files)

            # Check for custom camera naming
            has_custom_cam = any('_cam_' in f or '_camera_' in f for f in sample_files)

            use_exhaustive = has_cubemap or has_custom_cam

            # Rig setups with per-camera subdirectories should use sequential
            # matching, not exhaustive. With a large angular gap between camera
            # groups (e.g. front-facing cameras only), exhaustive matching wastes
            # effort on cross-group pairs that share no visual overlap, producing
            # hundreds of Cholesky failures and a degenerate reconstruction.
            # Sequential matching connects frames through temporal neighbors,
            # while the rig constraint handles cross-camera geometry.
            if use_rig and has_subdirs:
                use_exhaustive = False

            if use_exhaustive:
                logger.info("Detected multi-camera setup - using exhaustive matcher")
                self._run_colmap_command(
                    "exhaustive_matcher",
                    [
                        "--database_path", str(database_path)
                    ],
                    log_file
                )
            else:
                logger.info("Using sequential matcher")
                self._run_colmap_command(
                    "sequential_matcher",
                    [
                        "--database_path", str(database_path),
                        "--SequentialMatching.overlap", "20"
                    ],
                    log_file
                )

            # Step 3: Sparse reconstruction (incremental mapper).
            # global_mapper silently glued disconnected SfM components into a
            # single model when the matching graph fragmented across sharp
            # turns, producing visible teleports in the trained splat.
            # Incremental mapper either extends one model or starts a new
            # sparse/N — both paths are caught by _validate_reconstruction.
            if progress_callback:
                progress_callback(60, "Building sparse reconstruction...")

            mapper_args = [
                "--database_path", str(database_path),
                "--image_path", images_dir,
                "--output_path", str(sparse_dir),
                # COLMAP defaults fire global BA whenever the registered
                # frame / 3D-point counts grow by 10%, which on a
                # ~400-frame capture produces ~10 multi-minute global BA
                # passes (observed: 8–20 min silent gaps in the mapper
                # log). Bumping both ratios to 1.5 cuts that to ~6 passes
                # without weakening per-image refinement (local BA still
                # runs after every registration with the default 25-iter
                # budget). Safe because the rig + known-intrinsics
                # constraints below keep each BA problem well-posed.
                # Note: the flag is `ba_global_frames_ratio` (not
                # `_images_ratio`) in COLMAP 3.10+ / cuda builds — the
                # incremental mapper counts rig frames, not images.
                "--Mapper.ba_global_frames_ratio", "1.5",
                "--Mapper.ba_global_points_ratio", "1.5",
            ]

            if known_camera_params:
                mapper_args.extend([
                    "--Mapper.ba_refine_focal_length", "0",
                    "--Mapper.ba_refine_principal_point", "0",
                    "--Mapper.ba_refine_extra_params", "0"
                ])

            if use_rig:
                # The cubemap rig is mathematically exact (orthogonal 90°/
                # 180°/-90° rotations at zero translation). Refining it adds
                # 18 free BA parameters that can drive the solver into a
                # singular Hessian on captures with sharp turns — observed
                # crash on 370045fb57db where retriangulation BA failed
                # with "Matrix not positive definite" then "Failed to fix
                # Gauge ... 0 fixed points" then a CHECK that aborts the
                # process. Locking the rig keeps BA strictly easier.
                mapper_args.extend([
                    "--Mapper.ba_refine_sensor_from_rig", "0"
                ])

            self._run_colmap_command(
                "mapper",
                mapper_args,
                log_file
            )

            # Step 4: Validate, convert to text, and audit trail continuity.
            # Conversion happens inside the validator so the audit can read
            # images.txt directly. A discontinuity in the camera trail
            # (max-step / median-step above threshold) raises here, before
            # the pipeline wastes minutes on LichtFeld training.
            if progress_callback:
                progress_callback(85, "Validating reconstruction...")

            reconstruction_dir = self._validate_reconstruction(sparse_dir, log_file)

            logger.info(f"COLMAP reconstruction complete: {reconstruction_dir}")

            if progress_callback:
                progress_callback(100, "COLMAP complete!")

        except Exception as e:
            logger.error(f"COLMAP failed: {str(e)}")
            raise Exception(f"COLMAP reconstruction failed: {str(e)}")

    def _run_colmap_command(self, command: str, args: list, log_file: str):
        """Run a COLMAP command, tee its output to the log file and to our
        own stdout (with a ``[colmap]`` prefix) so the train tab's SSE log
        sees progress lines like ``Processed file [N/TOTAL]`` in real time."""
        cmd = [self.colmap_exe, command] + args

        logger.info(f"Running COLMAP command: {command}")

        with open(log_file, 'a', encoding='utf-8') as log:
            log.write(f"\n=== COLMAP {command} ===\n")
            log.write(f"Command: {' '.join(cmd)}\n\n")
            log.flush()

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
            )
            assert proc.stdout is not None
            _n = 0
            for raw in proc.stdout:
                log.write(raw)
                log.flush()
                if _n % 10 == 0:
                    print(f"[colmap] {raw.rstrip()}", flush=True)
                _n += 1
            rc = proc.wait()

        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)

    def _validate_reconstruction(self, sparse_dir: Path, log_file: str) -> Path:
        """
        Pick the most-registered sparse model, convert it to TXT, and
        audit trail continuity. Incremental mapper routinely emits tiny
        stub models alongside the main reconstruction (a few unregistrable
        pairs pulled into their own component); we ignore those rather
        than risking a `model_merger` corruption.
        """
        subdirs = [d for d in os.listdir(sparse_dir)
                   if os.path.isdir(sparse_dir / d) and d.isdigit()]

        if not subdirs:
            raise Exception(
                "No reconstruction was created by COLMAP. "
                "Images may not have sufficient features or overlap."
            )

        # Convert every candidate so we can compare image counts.
        for d in subdirs:
            self._run_colmap_command(
                "model_converter",
                [
                    "--input_path",  str(sparse_dir / d),
                    "--output_path", str(sparse_dir / d),
                    "--output_type", "TXT",
                ],
                log_file,
            )

        from .colmap_to_lichtfeld import parse_colmap_images
        counts = []
        for d in subdirs:
            poses = parse_colmap_images(sparse_dir / d / "images.txt")
            counts.append((d, len(poses)))
        counts.sort(key=lambda x: -x[1])

        chosen, chosen_n = counts[0]
        if len(counts) > 1:
            others = ", ".join(f"sparse/{d}({n})" for d, n in counts[1:])
            logger.info(
                f"Multiple reconstructions found; using sparse/{chosen} "
                f"({chosen_n} images), ignoring stub(s): {others}"
            )
        else:
            logger.info(f"Single reconstruction found: sparse/{chosen} ({chosen_n} images)")

        reconstruction_dir = sparse_dir / chosen
        self._audit_trail_continuity(reconstruction_dir)
        return reconstruction_dir

    def _audit_trail_continuity(
        self,
        reconstruction_dir: Path,
        *,
        jump_ratio_threshold: float = 10.0,
    ) -> None:
        """Hard-fail when the camera trail has discontinuities consistent
        with silent SfM islands. Healthy captures sit at max/median step
        ratios of 2–3×; a global-mapper-glued multi-component reconstruction
        produces ratios of 100×+ (capture 96340f3fc1c8 measured 266×)."""
        import math
        import statistics

        # Lazy import: colmap_to_lichtfeld imports numpy, which we don't
        # want to pull in at module import time.
        from .colmap_to_lichtfeld import parse_colmap_images

        images_txt = reconstruction_dir / "images.txt"
        if not images_txt.is_file():
            logger.warning(
                f"images.txt missing at {images_txt}; skipping trail audit."
            )
            return

        poses = parse_colmap_images(images_txt)

        # One center per frame. Prefer the rig's reference sensor (front);
        # fall back to whichever face was registered.
        by_frame: dict[int, dict[str, list[float]]] = {}
        for _name, info in poses.items():
            fr = info.get("frame_num")
            if fr is None:
                continue
            qw, qx, qy, qz = info["qw"], info["qx"], info["qy"], info["qz"]
            tx, ty, tz = info["tx"], info["ty"], info["tz"]
            # COLMAP cam_from_world R, then C = -R^T t.
            r00 = 1 - 2 * (qy * qy + qz * qz)
            r01 = 2 * (qx * qy - qz * qw)
            r02 = 2 * (qx * qz + qy * qw)
            r10 = 2 * (qx * qy + qz * qw)
            r11 = 1 - 2 * (qx * qx + qz * qz)
            r12 = 2 * (qy * qz - qx * qw)
            r20 = 2 * (qx * qz - qy * qw)
            r21 = 2 * (qy * qz + qx * qw)
            r22 = 1 - 2 * (qx * qx + qy * qy)
            cx = -(r00 * tx + r10 * ty + r20 * tz)
            cy = -(r01 * tx + r11 * ty + r21 * tz)
            cz = -(r02 * tx + r12 * ty + r22 * tz)
            by_frame.setdefault(fr, {})[info.get("cam_id") or ""] = [cx, cy, cz]

        cameras: list[tuple[int, list[float]]] = []
        for fr in sorted(by_frame):
            cams = by_frame[fr]
            cameras.append((fr, cams.get("front") or next(iter(cams.values()))))

        if len(cameras) < 2:
            logger.info("Trail audit: <2 registered frames; skipping.")
            return

        steps: list[tuple[int, int, float]] = []
        for (fa, ca), (fb, cb) in zip(cameras, cameras[1:]):
            d = math.sqrt(sum((a - b) ** 2 for a, b in zip(ca, cb)))
            steps.append((fa, fb, d))

        step_vals = [d for _, _, d in steps]
        median = statistics.median(step_vals) or 1e-9
        worst_d = max(step_vals)
        ratio = worst_d / median

        if ratio <= jump_ratio_threshold:
            logger.info(
                f"Trail continuity OK: {len(cameras)} frames, "
                f"median step {median:.4f}, max {worst_d:.4f} "
                f"({ratio:.1f}x median)."
            )
            return

        worst = sorted(steps, key=lambda x: -x[2])[:5]
        details = ", ".join(
            f"frame {fa}->{fb}: {d:.3f} ({d/median:.1f}x)"
            for fa, fb, d in worst
        )
        raise Exception(
            f"COLMAP trail discontinuity: max/median step ratio = "
            f"{ratio:.1f}x (threshold {jump_ratio_threshold:.1f}x, "
            f"{len(cameras)} frames). The mapper likely produced silent "
            f"SfM islands stitched into one model — the matching graph "
            f"fragmented (typically across a sharp turn). Worst jumps: "
            f"{details}"
        )

    def _merge_reconstructions(self, sparse_dir: Path, subdirs: list, log_file: str) -> Path:
        """
        Merge multiple COLMAP reconstructions into one

        Args:
            sparse_dir: Path to sparse directory
            subdirs: List of reconstruction subdirectory names
            log_file: Path to log file

        Returns:
            Path to merged reconstruction directory
        """
        merged_dir = sparse_dir / "merged"
        merged_dir.mkdir(exist_ok=True)

        logger.info(f"Merging {len(subdirs)} reconstructions...")

        try:
            # Merge first two models
            self._run_colmap_command(
                "model_merger",
                [
                    "--input_path1", str(sparse_dir / subdirs[0]),
                    "--input_path2", str(sparse_dir / subdirs[1]),
                    "--output_path", str(merged_dir)
                ],
                log_file
            )

            # Merge remaining models iteratively
            for i in range(2, len(subdirs)):
                temp_merged = sparse_dir / f'merged_temp_{i}'
                temp_merged.mkdir(exist_ok=True)

                self._run_colmap_command(
                    "model_merger",
                    [
                        "--input_path1", str(merged_dir),
                        "--input_path2", str(sparse_dir / subdirs[i]),
                        "--output_path", str(temp_merged)
                    ],
                    log_file
                )

                # Replace merged_dir with temp
                shutil.rmtree(merged_dir)
                shutil.move(temp_merged, merged_dir)

            # Bundle adjustment to refine merged model
            logger.info("Running bundle adjustment on merged model...")
            self._run_colmap_command(
                "bundle_adjuster",
                [
                    "--input_path", str(merged_dir),
                    "--output_path", str(merged_dir)
                ],
                log_file
            )

            logger.info(f"Successfully merged reconstructions")
            return merged_dir

        except Exception as e:
            logger.error(f"Failed to merge reconstructions: {str(e)}")
            raise Exception(f"Reconstruction merge failed: {str(e)}")
