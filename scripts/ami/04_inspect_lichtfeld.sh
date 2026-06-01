#!/usr/bin/env bash
# Shallow-clone LichtFeld-Studio (no submodules) and print its build requirements.
set -euo pipefail
cd /home/ubuntu
if [ ! -d LichtFeld-Studio ]; then
  git clone --depth 1 https://github.com/MrNeRF/LichtFeld-Studio.git
fi
cd LichtFeld-Studio
echo "=== top-level files ==="
ls
echo "=== cmake_minimum_required ==="
grep -n cmake_minimum_required CMakeLists.txt || true
echo "=== CUDA / arch / standard hints in CMakeLists ==="
grep -niE "cuda_arch|cxx_standard|cuda_standard|find_package|FetchContent|CPM|libtorch|Torch" CMakeLists.txt | head -40 || true
echo "=== submodules ==="
cat .gitmodules 2>/dev/null || echo "no .gitmodules"
echo "=== README build section ==="
grep -niE "cmake|build|cuda|ubuntu|apt|gcc|prereq|depend" README.md | head -50 || true
echo "=== INSPECT DONE ==="
