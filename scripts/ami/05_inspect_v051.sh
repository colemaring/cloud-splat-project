#!/usr/bin/env bash
# Clone the v0.5.1 tag (the version validated locally) and print build reqs.
set -euo pipefail
cd /home/ubuntu
rm -rf LichtFeld-v051
git clone --depth 1 --branch v0.5.1 https://github.com/MrNeRF/LichtFeld-Studio.git LichtFeld-v051 2>&1 | tail -2
cd LichtFeld-v051
echo "=== top-level ==="; ls
echo "=== cmake_minimum_required ==="; grep -n cmake_minimum_required CMakeLists.txt || true
echo "=== cuda / std / dep system ==="
grep -niE "cudatoolkit|find_package|fetchcontent|cpm|vcpkg|cxx_standard|cuda_standard|cuda_arch" CMakeLists.txt | head -50 || true
echo "=== uses vcpkg? ==="; ls vcpkg.json 2>/dev/null && echo USES_VCPKG || echo no_vcpkg
echo "=== submodules ==="; cat .gitmodules 2>/dev/null || echo none
echo "=== CLI flags present in source ==="
grep -rniE "headless|gut|tile-mode|tile_mode|mask-mode|mask_mode|max-cap|max_cap" src 2>/dev/null | head -20 || true
echo "=== build docs ==="; ls docs 2>/dev/null; grep -rniE "cmake|cuda|ubuntu|apt-get|build" README.md 2>/dev/null | head -30 || true
echo "=== INSPECT v051 DONE ==="
