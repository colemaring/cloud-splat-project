#!/usr/bin/env bash
# Smoke-test the freshly built LichtFeld binary under cuda-compat (driver 550).
BIN=/home/ubuntu/LichtFeld-v051/build/LichtFeld-Studio
export LD_LIBRARY_PATH=/home/ubuntu/LichtFeld-v051/external/release/libtorch/lib:/usr/local/cuda-12.8/compat:/usr/local/cuda-12.8/lib64:/home/ubuntu/LichtFeld-v051/build

echo "=== driver ==="
nvidia-smi --query-gpu=driver_version,name --format=csv,noheader
echo "=== ldd missing libs ==="
ldd "$BIN" | grep -i "not found" || echo "all libs linked"
echo "=== --help (validates load + arg parser) ==="
"$BIN" --help 2>&1 | head -30
echo "exit=$?"
