#!/usr/bin/env bash
# Install LichtFeld behind a stable wrapper that sets the runtime library path,
# matching config.json's lichtfeld_exe = /opt/lichtfeld/bin/LichtFeld-Studio.
set -e
sudo mkdir -p /opt/lichtfeld/bin
sudo tee /opt/lichtfeld/bin/LichtFeld-Studio >/dev/null <<'EOF'
#!/bin/bash
# libtorch (cu128) + cuda-compat + cuda 12.8 runtime + the binary's own dir
export LD_LIBRARY_PATH=/home/ubuntu/LichtFeld-v051/external/release/libtorch/lib:/usr/local/cuda-12.8/compat:/usr/local/cuda-12.8/lib64:/home/ubuntu/LichtFeld-v051/build${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
exec /home/ubuntu/LichtFeld-v051/build/LichtFeld-Studio "$@"
EOF
sudo chmod +x /opt/lichtfeld/bin/LichtFeld-Studio

echo "=== disk ==="
df -h / | tail -1
echo "=== bounded headless run (expect CUDA init, then complain about bogus data dir) ==="
timeout 90 /opt/lichtfeld/bin/LichtFeld-Studio --headless -d /tmp/nodata_xyz -o /tmp/lfsout --iter 1 2>&1 | head -30 || true
echo "=== INSTALL DONE ==="
