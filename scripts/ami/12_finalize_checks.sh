#!/usr/bin/env bash
# Validate the exact LichtFeld arg set our pipeline sends, then reclaim disk.
echo "=== exact pipeline flags (expect training start, then 'path not found') ==="
timeout 60 /opt/lichtfeld/bin/LichtFeld-Studio \
  -d /tmp/nodata_xyz -o /tmp/lfsout2 \
  --train --headless --iter=1 --tile-mode=2 --max-cap=300000 \
  --mask-mode=ignore --gut --no-alpha-as-mask 2>&1 | head -25 || true

echo
echo "=== reclaim ~14G of vcpkg build caches (runtime libs live in build/vcpkg_installed) ==="
rm -rf /home/ubuntu/vcpkg/buildtrees /home/ubuntu/vcpkg/downloads /home/ubuntu/vcpkg/packages
df -h / | tail -1
echo "=== FINALIZE CHECKS DONE ==="
