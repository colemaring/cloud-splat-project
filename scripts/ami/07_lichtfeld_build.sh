#!/usr/bin/env bash
# Re-configure + build LichtFeld after the vcpkg deps are in place.
# Neutralizes the driver>=570 hard gate; we run CUDA 12.8 on the 550 driver via
# the cuda-compat-12-8 forward-compat libs (supported on the T4 datacenter GPU).
set -euo pipefail
LFS=/home/ubuntu/LichtFeld-v051
export PATH=/usr/local/cuda-12.8/bin:/home/ubuntu/vcpkg:$PATH
export CUDACXX=/usr/local/cuda-12.8/bin/nvcc
export VCPKG_ROOT=/home/ubuntu/vcpkg
cd "$LFS"

sed -i 's/VERSION_LESS "570"/VERSION_LESS "0"/' CMakeLists.txt
echo "=== driver-gate line after patch ==="
grep -n 'VERSION_LESS' CMakeLists.txt | head

rm -rf build
cmake -B build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_TOOLCHAIN_FILE="$VCPKG_ROOT/scripts/buildsystems/vcpkg.cmake" \
  -DTorch_DIR="$LFS/external/release/libtorch/share/cmake/Torch" \
  -DCMAKE_PREFIX_PATH="$LFS/external/release/libtorch" \
  -DCMAKE_CUDA_ARCHITECTURES=75

cmake --build build -j"$(nproc)"

echo "=== built binary ==="
find "$LFS/build" -maxdepth 3 -type f -name 'LichtFeld-Studio' -exec ls -lh {} \;
echo "############ LICHTFELD BUILD DONE ############"
