# Building the golden GPU worker AMI

The per-job worker boots from a pre-baked AMI containing CUDA COLMAP, the
Insta360 Linux MediaSDK, a LichtFeld-Studio Linux build, FFmpeg, the Python
deps, and the worker code. You build this **once** by hand and reference its id
from Terraform (`golden_ami_id`).

> **A working AMI already exists:** `ami-0122307459cdd469c` (us-east-1, built
> 2026-06-01). It's referenced in `terraform/terraform.tfvars`. The steps below
> document how it was built (and how to rebuild). The authoritative, ordered
> recipe is the numbered scripts in [`scripts/ami/`](ami/) — they reflect what
> actually worked, which differs from the original monolithic `build_ami.sh`.

## What the real build looks like (lessons learned)

Base: **Ubuntu 22.04** GPU AMI (NOT 24.04). The Insta360 MediaSDK targets CUDA
11.7 / gcc-11 (= 22.04), and a 22.04 "Deep Learning Base" AMI ships CUDA 12.4 +
gcc-11, which both COLMAP and the rest build against.

- **Insta360 MediaSDK** is shipped as `libMediaSDK-dev-*.deb` + example *source*,
  not a ready binary. Install the `.deb` (`apt install ./libMediaSDK-dev*.deb`,
  puts `libMediaSDK.so` in `/usr/lib`, header in `/usr/include`), then **compile**
  `example/main.cc` into the CLI: `g++ main.cc -std=c++11 -lMediaSDK -lpthread`.
  Its CLI flags match the Windows `MediaSDKTest` (`-inputs -output -stitch_type
  aistitch -output_size -model_root_dir -enable_stitchfusion`). Models go in
  `/opt/mediasdk/models`. (`scripts/ami/01_mediasdk.sh`)
- **COLMAP 3.10** built with `-DCMAKE_CUDA_ARCHITECTURES=75` against CUDA 12.4.
  (`02_colmap.sh`)
- **Python venv**: torch `cu121` + the worker `requirements.txt` + YOLO26-s.
  (`03_python.sh`)
- **LichtFeld-Studio v0.5.1** is the heavy one — there is **no Linux binary**, and
  it needs **CUDA 12.8**, **gcc-14**, **CMake 4**, **vcpkg** (OpenUSD, OpenImageIO,
  ffmpeg, SDL3, assimp, rmlui…) and **LibTorch 2.7.0+cu128**. The vcpkg tree alone
  is ~2 h on 4 vCPUs. Two gotchas: init the `external/libvterm` git submodule, and
  the CMake check hard-requires driver ≥570 — we patch that one line and run CUDA
  12.8 on the 550 driver via CUDA-12 minor-version compatibility (+ `cuda-compat-12-8`
  as backup; verified with a real libtorch CUDA matmul). Installed behind a wrapper
  at `/opt/lichtfeld/bin/LichtFeld-Studio` that sets `LD_LIBRARY_PATH`.
  (`04`–`12_*.sh`)

To rebuild from scratch, run `scripts/ami/01..13_*.sh` in order on a fresh 22.04
GPU instance (after uploading the worker code + the Insta360 Linux MediaSDK zip).

## Steps

1. **Launch a base instance**
   - AMI: *Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)*
     (ships the NVIDIA driver + CUDA toolkit).
   - Type: `g4dn.xlarge` (the same GPU jobs run on — so COLMAP/LichtFeld build
     for the right `sm_75` arch and you can smoke-test).
   - Storage: ~200 GB gp3.
   - Give it a role with `s3:GetObject` if you'll pull the MediaSDK archive from S3.

2. **Get the code + the proprietary MediaSDK onto it**
   ```bash
   git clone https://github.com/colemaring/cloud-splat-project.git
   # copy your Insta360 Linux MediaSDK v3.1.1 archive up (scp or: aws s3 cp ...)
   ```

3. **Run the installer**
   ```bash
   sudo MEDIASDK_ARCHIVE=/home/ubuntu/LinuxMediaSDK-3.1.1.tar.gz \
        REPO_DIR=/home/ubuntu/cloud-splat-project \
        bash cloud-splat-project/scripts/build_ami.sh
   ```
   The COLMAP and LichtFeld source builds take a while. The script is sectioned
   and prints OK/`!!` per tool — resolve any `!!` before continuing.

4. **Smoke-test a job by hand** (catches Linux-port issues early — see the
   command the script prints). Confirm a `.ply` is produced.

5. **Snapshot to an AMI**
   ```bash
   aws ec2 create-image --instance-id <id> --name cloud-splat-worker-$(date +%Y%m%d)
   ```

6. **Wire it into Terraform**
   ```hcl
   # terraform/terraform.tfvars
   golden_ami_id = "ami-xxxxxxxxxxxxxxxxx"
   ```
   then `terraform apply`. New launch-template versions pick up the new AMI; the
   next job uses it.

## Notes / gotchas
- **Root device name**: the launch template uses `/dev/sda1` (the Ubuntu DLAMI
  root). If you base the AMI on something that uses `/dev/xvda`, update
  `terraform/modules/compute/main.tf`.
- **MediaSDK CLI**: the worker calls `MediaSDKTest -inputs FRONT BACK -output ...
  -stitch_type aistitch -output_size 5760x2880 -model_root_dir /opt/mediasdk/models
  -enable_stitchfusion`. If the Linux 3.1.1 CLI differs, adjust
  `worker/backend/pipeline/insv_to_equirect.py` and re-bake.
- **LichtFeld flags**: the worker passes `--train --headless --iter --tile-mode
  --max-cap --mask-mode=ignore --gut`. Verify your build supports them
  (`LichtFeld-Studio --help`).
- **High preset on a T4**: 2M gaussians at full res can exceed 16 GB VRAM. If
  jobs OOM, bump `worker_instance_type` (e.g. `g5.xlarge`) in tfvars — no code
  change needed.
