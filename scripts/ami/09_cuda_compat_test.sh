#!/usr/bin/env bash
# Prove CUDA 12.8 runtime initializes on the 550 driver via cuda-compat-12-8.
set -uo pipefail
cat > /tmp/cc.cu <<'EOF'
#include <cstdio>
#include <cuda_runtime.h>
int main(){
  int n=0; cudaError_t e=cudaGetDeviceCount(&n);
  if(e){ printf("ERROR: %s\n", cudaGetErrorString(e)); return 1; }
  cudaDeviceProp p; cudaGetDeviceProperties(&p,0);
  printf("OK devices=%d name=%s cc=%d.%d\n", n, p.name, p.major, p.minor);
  return 0;
}
EOF
/usr/local/cuda-12.8/bin/nvcc /tmp/cc.cu -o /tmp/cc
echo "=== WITHOUT compat (expect insufficient-driver error) ==="
LD_LIBRARY_PATH=/usr/local/cuda-12.8/lib64 /tmp/cc
echo "=== WITH compat (expect OK + Tesla T4) ==="
LD_LIBRARY_PATH=/usr/local/cuda-12.8/compat:/usr/local/cuda-12.8/lib64 /tmp/cc
