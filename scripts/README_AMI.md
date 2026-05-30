# Building the golden GPU worker AMI

The per-job worker boots from a pre-baked AMI containing CUDA COLMAP, the
Insta360 Linux MediaSDK, a LichtFeld-Studio Linux build, FFmpeg, the Python
deps, and the worker code. You build this **once** by hand and reference its id
from Terraform (`golden_ami_id`).

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
