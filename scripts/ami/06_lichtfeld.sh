#!/usr/bin/env bash
# Build LichtFeld-Studio v0.5.1 from source on Ubuntu 22.04.
# Mirrors build_lichtfeld.ps1: vcpkg toolchain + LibTorch 2.7.0+cu128 + CUDA 12.8.
# Long build (vcpkg compiles OpenUSD/OpenImageIO/etc.). Idempotent-ish: re-runs
# resume from vcpkg cache and skip already-done steps.
set -euo pipefail

LFS=/home/ubuntu/LichtFeld-v051
JOBS=$(nproc)
ARCH=$(uname -m)

echo "############ [0] swap (prevent OOM during linking) ############"
if [ ! -f /swapfile ]; then
  sudo fallocate -l 64G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
fi
free -h

echo "############ [1] system deps for vcpkg ports ############"
sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  build-essential curl git ca-certificates gnupg2 lsb-release locales \
  python3 python3-pip python3-venv python3-dev python3-jinja2 \
  wget unzip pkg-config zip nasm autoconf autoconf-archive automake libtool \
  bison flex gperf \
  libxinerama-dev libxcursor-dev xorg-dev libglu1-mesa-dev libxi-dev libxrandr-dev \
  ninja-build software-properties-common

echo "############ [2] gcc-14 ############"
if ! command -v g++-14 >/dev/null 2>&1; then
  sudo add-apt-repository -y ppa:ubuntu-toolchain-r/test
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq gcc-14 g++-14 gfortran-14
fi
sudo update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-14 60 --slave /usr/bin/g++ g++ /usr/bin/g++-14
sudo update-alternatives --set gcc /usr/bin/gcc-14
gcc --version | head -1

echo "############ [3] CMake 4.0.3 ############"
if ! cmake --version 2>/dev/null | head -1 | grep -qE '4\.'; then
  wget -q "https://github.com/Kitware/CMake/releases/download/v4.0.3/cmake-4.0.3-linux-${ARCH}.sh"
  chmod +x "cmake-4.0.3-linux-${ARCH}.sh"
  sudo ./"cmake-4.0.3-linux-${ARCH}.sh" --skip-license --prefix=/usr/local
  rm -f "cmake-4.0.3-linux-${ARCH}.sh"
fi
hash -r; cmake --version | head -1

echo "############ [4] CUDA 12.8 toolkit + forward-compat ############"
if [ ! -d /usr/local/cuda-12.8 ]; then
  wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
  sudo dpkg -i cuda-keyring_1.1-1_all.deb
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cuda-toolkit-12-8 cuda-compat-12-8
  rm -f cuda-keyring_1.1-1_all.deb
fi
export PATH=/usr/local/cuda-12.8/bin:$PATH
export CUDACXX=/usr/local/cuda-12.8/bin/nvcc
nvcc --version | tail -2

echo "############ [5] vcpkg ############"
if [ ! -x /home/ubuntu/vcpkg/vcpkg ]; then
  rm -rf /home/ubuntu/vcpkg
  git clone https://github.com/microsoft/vcpkg.git /home/ubuntu/vcpkg
  /home/ubuntu/vcpkg/bootstrap-vcpkg.sh -disableMetrics
fi
export VCPKG_ROOT=/home/ubuntu/vcpkg
export PATH=$VCPKG_ROOT:$PATH

echo "############ [6] LibTorch 2.7.0+cu128 (linux cxx11-abi) ############"
mkdir -p "$LFS/external/release"
if [ ! -d "$LFS/external/release/libtorch" ]; then
  wget -q -O /tmp/libtorch.zip "https://download.pytorch.org/libtorch/cu128/libtorch-cxx11-abi-shared-with-deps-2.7.0%2Bcu128.zip"
  unzip -q /tmp/libtorch.zip -d "$LFS/external/release"
  rm -f /tmp/libtorch.zip
fi
ls "$LFS/external/release/libtorch/share/cmake/Torch" >/dev/null && echo "libtorch OK"

echo "############ [7] configure ############"
cd "$LFS"
cmake -B build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_TOOLCHAIN_FILE="$VCPKG_ROOT/scripts/buildsystems/vcpkg.cmake" \
  -DTorch_DIR="$LFS/external/release/libtorch/share/cmake/Torch" \
  -DCMAKE_PREFIX_PATH="$LFS/external/release/libtorch" \
  -DCMAKE_CUDA_ARCHITECTURES=75

echo "############ [8] build (this is the long part) ############"
cmake --build build -j"$JOBS"

echo "############ [9] locate binary ############"
find "$LFS/build" -maxdepth 3 -type f -name 'LichtFeld-Studio' -exec ls -lh {} \;
echo "############ LICHTFELD BUILD DONE ############"
