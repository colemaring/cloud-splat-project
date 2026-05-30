# Cloud Splat

Upload an Insta360 360° capture, pick a quality, and get a viewable Gaussian
splat in the browser. Each job gets its **own** ephemeral GPU instance (no
shared queue); the instance cleans up its input and self-terminates when done.

```
Browser ──presigned multipart──▶ S3 (uploads)
   │  POST /jobs
   ▼
API Gateway ▶ Lambda(create_job) ──RunInstances──▶ g4dn.xlarge (Linux, golden AMI)
   │                                                   │ MediaSDK stitch → frames → YOLO mask
DynamoDB (job status) ◀──stage updates────────────────┤ → cubemap → COLMAP → LichtFeld train
   ▲  GET /jobs/{id} (poll)                            │ → scene.ply ▶ S3 (outputs)
   │                                                   └ delete input, shutdown (terminate)
Browser ◀── CloudFront ── S3 (site + /outputs/*.ply)
```

- **Frontend** (`frontend/`) — vanilla JS + Vite + three + `@sparkjsdev/spark`.
  Pages: upload (`/`), status (`/status/{id}`), viewer (`/viewer/{id}`,
  first-person WASD), gallery (`/gallery`). Static, served by CloudFront.
- **API** (`lambda/`) — Python on API Gateway HTTP API: presigned multipart
  upload, job create/get/list, plus a scheduled `reaper` cost-safety backstop.
- **Worker** (`worker/`) — `run_job.py` wraps the pipeline
  (`backend/pipeline/`, ported to Linux), maps the quality preset to CLI args,
  streams `##STAGE:` markers into DynamoDB, uploads the `.ply`, self-terminates.
- **Infra** (`terraform/`) — S3, CloudFront (with COOP/COEP for the splat
  renderer), DynamoDB, API Gateway, Lambda, the GPU launch template, and the
  reaper schedule.

## Quality presets
| Preset | downscale | frame interval | iters | max gaussians |
|--------|-----------|----------------|-------|---------------|
| Draft  | 4 | 0.5s | 5,000 | 300k |
| Low    | 2 | 0.5s | 10,000 | 500k |
| Medium | 1 | 0.3s | 15,000 | 750k |
| High   | 1 | 0.25s | 30,000 | 2M |

## Deploy

1. **Build the golden GPU AMI** — see [`scripts/README_AMI.md`](scripts/README_AMI.md).
   Put the AMI id in `terraform/terraform.tfvars`:
   ```hcl
   golden_ami_id = "ami-xxxxxxxxxxxxxxxxx"
   # region = "us-east-1"            # optional
   # worker_instance_type = "g4dn.xlarge"
   ```

2. **Provision infrastructure**
   ```bash
   cd terraform
   terraform init
   terraform validate
   terraform apply
   ```
   Note the outputs: `api_endpoint`, `site_url`, `site_bucket`.

3. **Build + deploy the frontend** (inject the API endpoint at build time)
   ```bash
   cd frontend
   npm install
   VITE_API_BASE="$(terraform -chdir=../terraform output -raw api_endpoint)" npm run build
   aws s3 sync dist/ "s3://$(terraform -chdir=../terraform output -raw site_bucket)/" --delete
   # (optional) invalidate CloudFront so the new build shows immediately
   ```

4. Open the `site_url`. Upload the `_00_` and `_10_` `.insv` files (skip `_11_`),
   pick a quality, and watch it build.

## Notes
- **No auth** — anyone with the URL can launch a paid GPU job. Three guardrails
  cap cost: an in-worker wall-clock watchdog, a user-data shutdown timer, and the
  scheduled `reaper` Lambda. Add a gate before exposing this widely.
- **The Linux port is the risky part** — Insta360 MediaSDK Linux CLI, the
  LichtFeld Linux build, and COLMAP's CUDA arch (`sm_75`). Smoke-test on the AMI
  before relying on it (see the AMI README).
- The trained `.ply` is served same-origin under `/outputs/{id}/scene.ply`, so
  the cross-origin-isolated viewer loads it without CORS headaches.
