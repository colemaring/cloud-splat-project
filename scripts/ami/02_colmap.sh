#!/usr/bin/env bash
# Build COLMAP 3.10 with CUDA (sm_75 for the T4). Deps installed in step 0.
set -euo pipefail

export PATH=/usr/local/cuda/bin:$PATH
cd /home/ubuntu
if [ ! -d colmap ]; then
  git clone --depth 1 --branch 3.10 https://github.com/colmap/colmap.git
fi
cmake -S colmap -B colmap/build -GNinja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=75
ninja -C colmap/build
sudo ninja -C colmap/build install
echo "=== colmap version ==="
colmap -h 2>&1 | head -3 || true
echo "=== COLMAP STEP DONE ==="
