# deploy.ps1 — build the frontend against the live API and publish it.
#
# Run AFTER `terraform apply` has succeeded (it reads Terraform outputs).
#   pwsh scripts/deploy.ps1            # build + sync + invalidate
#   pwsh scripts/deploy.ps1 -SkipInstall   # skip `npm install`
#
param([switch]$SkipInstall)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

Write-Host "==> Reading Terraform outputs"
Push-Location "$root/terraform"
try {
  $api    = (terraform output -raw api_endpoint).Trim()
  $bucket = (terraform output -raw site_bucket).Trim()
  $distId = (terraform output -raw cloudfront_distribution_id).Trim()
} finally { Pop-Location }
Write-Host "    api_endpoint = $api"
Write-Host "    site_bucket  = $bucket"

Push-Location "$root/frontend"
try {
  if (-not $SkipInstall) {
    Write-Host "==> npm install"
    npm install --no-audit --no-fund
  }
  Write-Host "==> Building frontend (VITE_API_BASE=$api)"
  $env:VITE_API_BASE = $api
  npm run build

  Write-Host "==> Syncing dist/ to s3://$bucket"
  aws s3 sync dist/ "s3://$bucket/" --delete

  Write-Host "==> Invalidating CloudFront ($distId)"
  aws cloudfront create-invalidation --distribution-id $distId --paths "/*" | Out-Null
} finally { Pop-Location }

Write-Host "==> Done. Open: https://$((terraform -chdir="$root/terraform" output -raw cdn_domain).Trim())"
