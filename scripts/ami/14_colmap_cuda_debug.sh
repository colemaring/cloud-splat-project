#!/usr/bin/env bash
# Diagnose COLMAP's CUDA init failure and test the cuda-compat fix.
echo "=== /usr/local/cuda symlink ==="; ls -l /usr/local/cuda
echo "=== colmap links which cudart/cuda ==="; ldd /usr/local/bin/colmap | grep -iE 'cudart|libcuda' || echo "no cudart in ldd"
echo "=== nvidia-smi CUDA version ==="; nvidia-smi | grep -i "CUDA Version"

mkdir -p /tmp/coltest/imgs
cp /home/ubuntu/test/out/cubemap/front/frame_000000.jpg /tmp/coltest/imgs/ 2>/dev/null || \
  cp "$(find /home/ubuntu/test/out/cubemap -name '*.jpg' | head -1)" /tmp/coltest/imgs/ 2>/dev/null
echo "=== test image: $(ls /tmp/coltest/imgs) ==="

echo "=== COLMAP feature_extractor WITHOUT compat ==="
rm -f /tmp/coltest/db1.db
colmap feature_extractor --database_path /tmp/coltest/db1.db --image_path /tmp/coltest/imgs 2>&1 | tail -5

echo "=== COLMAP feature_extractor WITH cuda-compat on LD_LIBRARY_PATH ==="
rm -f /tmp/coltest/db2.db
LD_LIBRARY_PATH=/usr/local/cuda-12.8/compat colmap feature_extractor --database_path /tmp/coltest/db2.db --image_path /tmp/coltest/imgs 2>&1 | tail -5
echo "=== DONE ==="
