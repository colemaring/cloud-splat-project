#!/usr/bin/env bash
# Prove libtorch (cu128) actually runs CUDA kernels on the 550 driver — the
# core of LichtFeld training. Builds a tiny program against the same libtorch.
set -e
LT=/home/ubuntu/LichtFeld-v051/external/release/libtorch
D=/home/ubuntu/torchtest
mkdir -p "$D"; cd "$D"
cat > t.cpp <<'EOF'
#include <torch/torch.h>
#include <iostream>
int main(){
  std::cout << "cuda available: " << torch::cuda::is_available() << "\n";
  if(!torch::cuda::is_available()) return 2;
  auto a = torch::randn({1024,1024}, torch::device(torch::kCUDA));
  auto b = torch::randn({1024,1024}, torch::device(torch::kCUDA));
  auto c = torch::mm(a,b).sum().item<float>();
  std::cout << "OK cuda matmul ran, sum=" << c << "\n";
  return 0;
}
EOF
cat > CMakeLists.txt <<'EOF'
cmake_minimum_required(VERSION 3.18)
project(t LANGUAGES CXX)
find_package(Torch REQUIRED)
add_executable(t t.cpp)
target_link_libraries(t ${TORCH_LIBRARIES})
set_property(TARGET t PROPERTY CXX_STANDARD 17)
EOF
export PATH=/usr/local/cuda-12.8/bin:$PATH
cmake -B build -DTorch_DIR="$LT/share/cmake/Torch" -DCMAKE_PREFIX_PATH="$LT" >/dev/null 2>&1
cmake --build build -j4 >/dev/null 2>&1
echo "=== running libtorch cu128 CUDA test ==="
LD_LIBRARY_PATH=$LT/lib:/usr/local/cuda-12.8/compat:/usr/local/cuda-12.8/lib64 ./build/t
echo "exit=$?"
