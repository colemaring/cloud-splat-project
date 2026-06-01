#!/usr/bin/env bash
# Final AMI readiness: absolute tool paths in the worker config + stage YOLO weight.
set -e
COLMAP=$(command -v colmap || echo /usr/local/bin/colmap)
sudo sed -i "s#\"colmap_exe\":[^,]*,#\"colmap_exe\": \"$COLMAP\",#" /opt/worker/config.json

# stage the already-downloaded YOLO weight so jobs don't re-download it
for d in /home/ubuntu /home/ubuntu/ami; do
  [ -f "$d/yolo26s-seg.pt" ] && sudo cp "$d/yolo26s-seg.pt" /opt/worker/ && break
done || true

echo "=== /opt/worker/config.json ==="
cat /opt/worker/config.json
echo "=== tool sanity ==="
command -v ffmpeg; command -v colmap; command -v exiftool
ls -la /opt/mediasdk/bin/MediaSDKTest
ls -la /opt/lichtfeld/bin/LichtFeld-Studio
ls -la /opt/worker/venv/bin/python
ls -la /opt/worker/yolo26s-seg.pt 2>/dev/null || echo "yolo weight will download at runtime"
echo "=== AMI PREP DONE ==="
