# EC2 deployment (staging + main)

This directory contains a full two-environment deployment setup for one EC2 host:

- staging environment from branch `build/sentinel-gj`
- main environment from branch `main`
- GitHub Actions based CI and CD
- host-level nginx reverse proxy routing each domain to the right environment

## Files

- `deploy/docker-compose.ec2.yml`: compose overrides for EC2
- `deploy/env/staging.env.example`: staging runtime env template
- `deploy/env/main.env.example`: main runtime env template
- `deploy/nginx/sentinel-edge.conf`: nginx config for both environments
- `deploy/scripts/bootstrap_ec2.sh`: one-time host setup
- `deploy/scripts/update_and_deploy.sh`: idempotent pull/build/restart script
- `.github/workflows/ci.yml`: lint + checks
- `.github/workflows/deploy-staging.yml`: auto deploy staging
- `.github/workflows/deploy-main.yml`: auto deploy main

## One-time EC2 setup

Run this on a fresh Ubuntu EC2 instance:

```bash
git clone https://github.com/<your-org-or-user>/SENTINEL-GJ.git
cd SENTINEL-GJ
chmod +x deploy/scripts/bootstrap_ec2.sh deploy/scripts/update_and_deploy.sh
./deploy/scripts/bootstrap_ec2.sh https://github.com/<your-org-or-user>/SENTINEL-GJ.git
```

Then edit:

- `/etc/sentinel-gj/staging.env`
- `/etc/sentinel-gj/main.env`
- `/etc/nginx/sites-available/sentinel-gj.conf` (set real domains)

Reload nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## Manual deployment

```bash
# staging
bash /opt/sentinel-gj/staging/deploy/scripts/update_and_deploy.sh staging build/sentinel-gj

# main
bash /opt/sentinel-gj/main/deploy/scripts/update_and_deploy.sh main main
```

## GitHub secrets required

For staging workflow:

- `STAGING_EC2_HOST`
- `STAGING_EC2_USER`
- `STAGING_EC2_SSH_KEY`

For main workflow:

- `MAIN_EC2_HOST`
- `MAIN_EC2_USER`
- `MAIN_EC2_SSH_KEY`

Optional hardening:

- put staging and main on separate EC2 instances
- terminate TLS with certbot or ALB
- restrict SSH with source IP allowlist and short-lived keys
