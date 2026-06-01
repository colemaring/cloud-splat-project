#!/usr/bin/env bash
# Install the worker code + Python venv (torch cu121) + pre-cache YOLO weights.
set -euo pipefail

sudo mkdir -p /opt/worker
sudo cp -r /home/ubuntu/cloud-splat-project/worker/. /opt/worker/
sudo rm -rf /opt/worker/__pycache__ /opt/worker/backend/pipeline/__pycache__
sudo chown -R ubuntu:ubuntu /opt/worker

python3 -m venv /opt/worker/venv
/opt/worker/venv/bin/pip install --upgrade pip wheel
echo "=== installing torch (cu121) ==="
/opt/worker/venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
echo "=== installing worker requirements ==="
/opt/worker/venv/bin/pip install -r /opt/worker/requirements.txt

echo "=== verify torch sees the GPU ==="
/opt/worker/venv/bin/python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"

echo "=== pre-cache YOLO26-s weights ==="
export YOLO_CONFIG_DIR=/opt/worker/.ultralytics
/opt/worker/venv/bin/python -c "from ultralytics import YOLO; YOLO('yolo26s-seg.pt'); print('yolo cached')" || echo "WARN: yolo26s-seg.pt pre-cache failed (will download at runtime)"
echo "=== PYTHON STEP DONE ==="
