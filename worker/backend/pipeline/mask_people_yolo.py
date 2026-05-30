"""
Mask people in equirectangular images using YOLO segmentation.

This script generates masks of people detected in images. The masks can be used with
Agisoft Metashape or Lichtfeld Studio to exclude moving people from processing.

Usage:
    python mask_people_yolo.py <input_directory> [--output OUTPUT_DIR] [--model {n,s,m,l,x}] [--conf 0.25] [--device cpu] [--bottom-mask 0.10]

Example:
    python mask_people_yolo.py ./frames --output ./frames/masks --model s --conf 0.10
    python mask_people_yolo.py ./frames --bottom-mask 0.10

Output:
    - Separate mask files in output directory
    - White (255) = keep pixel
    - Black (0) = mask out (person)
    - PNG format with same filename as source image

Note: This script does NOT modify your original images. Masks are saved as separate files.
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

try:
    import cv2
    import numpy as np
    from PIL import Image
except ImportError as e:
    print(f"Missing required dependency: {e}")
    print("Install with: pip install opencv-python numpy Pillow")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

YOLO_MODEL_CACHE = {}


def check_dependencies() -> bool:
    """Check if required dependencies are installed."""
    try:
        import torch
        from ultralytics import YOLO

        if torch.cuda.is_available():
            logger.info(f"CUDA detected: {torch.cuda.get_device_name(0)}")
        else:
            logger.info("CUDA not available, using CPU")

        return True
    except ImportError as e:
        logger.error(f"Missing dependency: {e}")
        logger.error("Install with: pip install ultralytics torch")
        return False


def get_yolo_model(size: str = "n", device: str = "auto") -> "YOLO":
    """Load and cache YOLO segmentation model.

    Args:
        size: Model size - n(nano), s(small), m(medium), l(large), x(xlarge)
        device: Device to run inference on - 'cpu', 'cuda', 'cuda:0', or 'auto'

    Returns:
        YOLO model instance
    """
    import torch

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(f"Auto-detected device: {device}")

    model_name = f"yolo26{size}-seg.pt"

    if model_name not in YOLO_MODEL_CACHE:
        logger.info(f"Loading YOLO26-{size.upper()} segmentation model...")
        from ultralytics import YOLO

        model = YOLO(model_name)
        YOLO_MODEL_CACHE[model_name] = model
        logger.info(f"Model loaded: {model_name}")

    return YOLO_MODEL_CACHE[model_name]


def collect_images(input_dir: Path) -> list[Path]:
    """Collect all supported image files from input directory recursively.

    Args:
        input_dir: Directory to search for images

    Returns:
        List of image file paths sorted by name
    """
    # Use a set keyed on resolved path to dedupe — on case-insensitive
    # filesystems (Windows, default macOS) `*.jpg` and `*.JPG` resolve to
    # the same files and the two rglob calls would otherwise return each
    # frame twice.
    image_set: set[Path] = set()
    for ext in SUPPORTED_EXTENSIONS:
        image_set.update(p.resolve() for p in input_dir.rglob(f"*{ext}"))
        image_set.update(p.resolve() for p in input_dir.rglob(f"*{ext.upper()}"))

    return sorted(image_set)


def process_image(
    image_path: Path,
    model: "YOLO",
    conf_threshold: float,
    device: str,
    bottom_mask: float = 0.0,
) -> Optional[np.ndarray]:
    """Detect people in image and create binary mask.

    Args:
        image_path: Path to input image
        model: YOLO segmentation model
        conf_threshold: Confidence threshold for detection
        device: Device to run inference on

    Returns:
        Binary mask as numpy array (uint8) where:
        - 255 (white) = keep pixel
        - 0 (black) = mask out (person)
        Returns None if no people detected or on error.
    """
    import torch

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    try:
        img = cv2.imread(str(image_path))
        if img is None:
            logger.warning(f"Could not read image: {image_path}")
            return None

        h, w = img.shape[:2]

        results = model(
            img,
            conf=conf_threshold,
            classes=[0],
            device=device,
            verbose=False,
        )

        combined_mask = np.zeros((h, w), dtype=np.uint8)

        if len(results) > 0 and results[0].masks is not None:
            masks_data = results[0].masks.data
            for mask in masks_data:
                mask_np = mask.cpu().numpy()
                if mask_np.shape != (h, w):
                    mask_np = cv2.resize(mask_np, (w, h), interpolation=cv2.INTER_LINEAR)
                combined_mask = np.maximum(combined_mask, (mask_np > 0.5).astype(np.uint8) * 255)

        combined_mask = 255 - combined_mask

        if bottom_mask > 0:
            bottom_rows = int(h * bottom_mask)
            combined_mask[h - bottom_rows:, :] = 0

        return combined_mask

    except Exception as e:
        logger.warning(f"Error processing {image_path.name}: {e}")
        return None


def save_mask(mask: np.ndarray, output_path: Path) -> bool:
    """Save mask image as PNG.

    Args:
        mask: Binary mask array (uint8)
        output_path: Path to save mask

    Returns:
        True if saved successfully, False otherwise
    """
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        mask_img = Image.fromarray(mask, mode="L")
        mask_img.save(output_path, "PNG")
        return True
    except Exception as e:
        logger.warning(f"Could not save mask {output_path}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Mask people in images using YOLO segmentation for Metashape",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python mask_people_yolo.py ./frames
    python mask_people_yolo.py ./frames --output ./masks --model s --conf 0.10
    python mask_people_yolo.py ./frames --device cpu
    python mask_people_yolo.py ./frames --bottom-mask 0.10

Note: This script does NOT modify original images. Masks are saved as separate PNG files.
        """,
    )

    parser.add_argument(
        "input_directory",
        type=Path,
        help="Directory containing images to process",
    )

    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output directory for masks (default: <input_directory>/masks/)",
    )

    parser.add_argument(
        "--model",
        "-m",
        choices=["n", "s", "m", "l", "x"],
        default="n",
        help="YOLO model size: n=nano (fastest), s=small, m=medium, l=large, x=xlarge (most accurate). Default: n",
    )

    parser.add_argument(
        "--conf",
        "-c",
        type=float,
        default=0.25,
        help="Confidence threshold for person detection (0.0-1.0). Default: 0.25",
    )

    parser.add_argument(
        "--device",
        "-d",
        default="auto",
        help="Device to use: 'cpu', 'cuda', 'cuda:0', or 'auto'. Default: auto",
    )

    parser.add_argument(
        "--bottom-mask",
        "-bm",
        type=float,
        default=0.0,
        help="Mask out the bottom X portion of each frame (0.0-1.0). Example: 0.10 masks bottom 10%% of image. Default: 0.0",
    )

    args = parser.parse_args()

    if not args.input_directory.exists():
        logger.error(f"Input directory does not exist: {args.input_directory}")
        sys.exit(1)

    if not check_dependencies():
        sys.exit(1)

    if args.output is None:
        output_dir = args.input_directory / "masks"
    else:
        output_dir = args.output

    logger.info(f"Input directory: {args.input_directory}")
    logger.info(f"Output directory: {output_dir}")
    logger.info(f"Model: yolo26{args.model}-seg.pt")
    logger.info(f"Confidence threshold: {args.conf}")
    logger.info(f"Device: {args.device}")
    if args.bottom_mask > 0:
        logger.info(f"Bottom mask: {args.bottom_mask:.2%} of image height")

    image_files = collect_images(args.input_directory)

    if not image_files:
        logger.error(f"No supported images found in {args.input_directory}")
        logger.error(f"Supported formats: {', '.join(SUPPORTED_EXTENSIONS)}")
        sys.exit(1)

    logger.info(f"Found {len(image_files)} images to process")

    model = get_yolo_model(args.model, args.device)

    processed = 0
    masks_created = 0
    errors = 0

    for i, image_path in enumerate(image_files, 1):
        try:
            mask = process_image(image_path, model, args.conf, args.device, args.bottom_mask)

            rel_path = image_path.relative_to(args.input_directory)
            mask_path = output_dir / rel_path.with_suffix(".png")

            if mask is not None:
                if save_mask(mask, mask_path):
                    masks_created += 1
                    logger.info(f"[{i}/{len(image_files)}] {image_path.name} -> {mask_path.name}")
                else:
                    errors += 1
            else:
                errors += 1

            processed += 1

        except Exception as e:
            logger.warning(f"[{i}/{len(image_files)}] Error with {image_path.name}: {e}")
            errors += 1

    logger.info("=" * 50)
    logger.info(f"Processing complete")
    logger.info(f"Images processed: {processed}")
    logger.info(f"Masks created: {masks_created}")
    logger.info(f"Errors: {errors}")
    logger.info(f"Output directory: {output_dir}")

    if masks_created > 0:
        logger.info("")
        logger.info("Mask files created in:")
        logger.info(f"  {output_dir}")
        logger.info("")
        logger.info("For Metashape:")
        logger.info("1. Select all images in the Workspace pane")
        logger.info("2. Right-click and select 'Import Masks'")
        logger.info(f"3. Navigate to: {output_dir}")
        logger.info("4. Select 'From files' and choose the mask images")
        logger.info("")
        logger.info("For Lichtfeld Studio:")
        logger.info("1. Load your images normally")
        logger.info("2. Enable mask mode and set mask directory to:")
        logger.info(f"   {output_dir}")


if __name__ == "__main__":
    main()
