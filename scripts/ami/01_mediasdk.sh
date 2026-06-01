#!/usr/bin/env bash
# Build the MediaSDK CLI from the example source and install models.
# Assumes the libmediasdk-dev .deb is already installed (lib in /usr/lib,
# header in /usr/include) and the extracted package is under mediasdk_pkg/.
set -euo pipefail

SDK_DIR=$(ls -d /home/ubuntu/mediasdk_pkg/libMediaSDK-dev-*-amd64)
echo "SDK_DIR=$SDK_DIR"

sudo mkdir -p /opt/mediasdk/bin /opt/mediasdk/models
sudo cp -r "$SDK_DIR/models/." /opt/mediasdk/models/
echo "=== models installed ==="
ls /opt/mediasdk/models | head

echo "=== compiling MediaSDKTest from example/main.cc ==="
g++ "$SDK_DIR/example/main.cc" -std=c++11 -lMediaSDK -lpthread -o /tmp/MediaSDKTest
sudo mv /tmp/MediaSDKTest /opt/mediasdk/bin/MediaSDKTest
sudo chmod +x /opt/mediasdk/bin/MediaSDKTest

echo "=== ldd of built binary (missing?) ==="
ldd /opt/mediasdk/bin/MediaSDKTest | grep -i "not found" || echo "all deps resolved"

echo "=== run with no args to confirm the lib loads / env inits ==="
/opt/mediasdk/bin/MediaSDKTest 2>&1 | head -8 || echo "exit=$? (non-zero expected: no inputs given)"
echo "=== MEDIASDK STEP DONE ==="
