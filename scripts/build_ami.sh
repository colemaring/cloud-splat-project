#!/usr/bin/env bash
#
# build_ami.sh — provision the golden GPU worker image (run ONCE, then snapshot).
#
# Run this on a freshly launched g4dn.xlarge using the "Deep Learning Base OSS
# Nvidia Driver GPU AMI (Ubuntu 22.04)" (ships NVIDIA driver + CUDA toolkit).
# After it finishes and you've smoke-tested a job, create an AMI from the
# instance and put its id in terraform/terraform.tfvars as golden_ami_id.
#
# Prerequisites you must supply (proprietary / large — not downloadable here):
#   - Insta360 Linux MediaSDK v3.1.1 archive, made available locally or in S3.
#     Set MEDIASDK_ARCHIVE to its path (a .tar.gz/.zip) OR pre-unpack it to
#     /opt/mediasdk yourself (must end up with /opt/mediasdk/bin/MediaSDKTest
#     and /opt/mediasdk/models/).
#
# Usage:
#   sudo MEDIASDK_ARCHIVE=/home/ubuntu/LinuxMediaSDK-3.1.1.tar.gz \
#        REPO_DIR=/home/ubuntu/cloud-splat-project \
#        bash scripts/build_ami.sh
#
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
CUDA_ARCH="${CUDA_ARCH:-75}"          # T4 (g4dn) = sm_75
TORCH_CUDA="${TORCH_CUDA:-cu121}"
WORKER_DIR=/opt/worker
MEDIASDK_DIR=/opt/mediasdk
LICHTFELD_DIR=/opt/lichtfeld

echo "==> [1/7] System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  git cmake build-essential ninja-build pkg-config \
  ffmpeg libimage-exiftool-perl python3 python3-venv python3-pip \
  libgl1 libglib2.0-0 unzip wget ca-certificates

echo "==> [2/7] COLMAP with CUDA (sm_${CUDA_ARCH})"
# Ubuntu's apt colmap is often CPU-only; build from source so GPU SIFT works.
if ! command -v colmap >/dev/null 2>&1; then
  apt-get install -y --no-install-recommends \
    libboost-program-options-dev libboost-graph-dev libboost-system-dev \
    libeigen3-dev libflann-dev libfreeimage-dev libmetis-dev libgoogle-glog-dev \
    libgtest-dev libsqlite3-dev libglew-dev qtbase5-dev libqt5opengl5-dev \
    libcgal-dev libceres-dev
  tmp=$(mktemp -d)
  git clone --depth 1 --branch 3.10 https://github.com/colmap/colmap.git "$tmp/colmap"
  cmake -S "$tmp/colmap" -B "$tmp/colmap/build" -GNinja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCH}"
  ninja -C "$tmp/colmap/build"
  ninja -C "$tmp/colmap/build" install
  rm -rf "$tmp"
fi
colmap -h >/dev/null && echo "    colmap OK"

echo "==> [3/7] Insta360 Linux MediaSDK -> ${MEDIASDK_DIR}"
if [[ ! -x "${MEDIASDK_DIR}/bin/MediaSDKTest" ]]; then
  if [[ -n "${MEDIASDK_ARCHIVE:-}" && -f "${MEDIASDK_ARCHIVE}" ]]; then
    mkdir -p "${MEDIASDK_DIR}"
    case "${MEDIASDK_ARCHIVE}" in
      *.zip)            unzip -o "${MEDIASDK_ARCHIVE}" -d "${MEDIASDK_DIR}" ;;
      *.tar.gz|*.tgz)   tar -xzf "${MEDIASDK_ARCHIVE}" -C "${MEDIASDK_DIR}" --strip-components=1 ;;
      *) echo "Unknown MEDIASDK_ARCHIVE format: ${MEDIASDK_ARCHIVE}" >&2; exit 1 ;;
    esac
  fi
fi
if [[ ! -x "${MEDIASDK_DIR}/bin/MediaSDKTest" ]]; then
  echo "    !! MediaSDK not found at ${MEDIASDK_DIR}/bin/MediaSDKTest."
  echo "       Unpack the Insta360 Linux MediaSDK v3.1.1 there (bin/ + models/) and re-run."
  echo "       (Continuing so the rest of the image still builds.)"
else
  echo "    MediaSDK OK"
fi

echo "==> [4/7] LichtFeld-Studio (Linux, CUDA)"
if [[ ! -x "${LICHTFELD_DIR}/bin/LichtFeld-Studio" ]]; then
  tmp=$(mktemp -d)
  git clone --recursive --depth 1 https://github.com/MrNeRF/LichtFeld-Studio.git "$tmp/lfs"
  cmake -S "$tmp/lfs" -B "$tmp/lfs/build" -GNinja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCH}"
  ninja -C "$tmp/lfs/build"
  mkdir -p "${LICHTFELD_DIR}/bin"
  # Binary name/location can vary by release; copy whatever was produced.
  found=$(find "$tmp/lfs/build" -maxdepth 3 -type f -name 'LichtFeld-Studio' | head -1 || true)
  [[ -n "$found" ]] && cp "$found" "${LICHTFELD_DIR}/bin/LichtFeld-Studio"
  rm -rf "$tmp"
fi
[[ -x "${LICHTFELD_DIR}/bin/LichtFeld-Studio" ]] && echo "    LichtFeld OK" \
  || echo "    !! LichtFeld-Studio binary not found — check the build output."

echo "==> [5/7] Worker code -> ${WORKER_DIR}"
mkdir -p "${WORKER_DIR}"
cp -r "${REPO_DIR}/worker/." "${WORKER_DIR}/"

echo "==> [6/7] Python venv + deps"
python3 -m venv "${WORKER_DIR}/venv"
"${WORKER_DIR}/venv/bin/pip" install --upgrade pip
"${WORKER_DIR}/venv/bin/pip" install \
  torch torchvision --index-url "https://download.pytorch.org/whl/${TORCH_CUDA}"
"${WORKER_DIR}/venv/bin/pip" install -r "${WORKER_DIR}/requirements.txt"

echo "==> [7/7] Pre-cache YOLO weights"
export YOLO_CONFIG_DIR="${WORKER_DIR}/.ultralytics"
"${WORKER_DIR}/venv/bin/python" - <<'PY' || echo "    (YOLO pre-cache skipped)"
from ultralytics import YOLO
YOLO("yolov8s-seg.pt")  # downloads + caches the weights into the image
print("    YOLO weights cached")
PY

cat <<EOF

==================================================================
Golden AMI provisioning complete.

Quick smoke test (recommended before snapshotting):
  cd ${WORKER_DIR}
  # place a front/back .insv pair somewhere, then:
  ./venv/bin/python -m backend.pipeline.pipeline_lichtfeld FRONT.insv BACK.insv \\
      -o /tmp/test -f 0.5 --downscale 4 --lfs-iters 1000 --lfs-max-cap 300000

Then, from your workstation:
  aws ec2 create-image --instance-id <this-instance-id> --name cloud-splat-worker-\$(date +%Y%m%d)
  # put the returned AMI id in terraform/terraform.tfvars as golden_ami_id
==================================================================
EOF
