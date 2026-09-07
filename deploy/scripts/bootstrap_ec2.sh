#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <repo-url>"
  echo "Example: $0 https://github.com/makvana-vaibhav/SIH-ANPR-Vehicle-Tracking.git"
  exit 1
fi

REPO_URL="$1"
APP_ROOT="/opt/nagarnetra"

echo "Installing system packages"
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release git nginx

if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker Engine"
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

sudo usermod -aG docker "$USER" || true

sudo mkdir -p "$APP_ROOT" /etc/nagarnetra
sudo chown -R "$USER":"$USER" "$APP_ROOT"

for env_name in staging main; do
  target_dir="$APP_ROOT/$env_name"
  if [[ ! -d "$target_dir/.git" ]]; then
    branch="main"
    if [[ "$env_name" == "staging" ]]; then
      branch="build/nagarnetra"
    fi
    git clone --branch "$branch" "$REPO_URL" "$target_dir"
  fi

  if [[ ! -f "/etc/nagarnetra/${env_name}.env" ]]; then
    sudo cp "$target_dir/deploy/env/${env_name}.env.example" "/etc/nagarnetra/${env_name}.env"
    sudo chown root:root "/etc/nagarnetra/${env_name}.env"
    sudo chmod 640 "/etc/nagarnetra/${env_name}.env"
    echo "Created /etc/nagarnetra/${env_name}.env (edit secrets before deploy)"
  fi
done

sudo cp "$APP_ROOT/main/deploy/nginx/nagarnetra-edge.conf" /etc/nginx/sites-available/nagarnetra.conf
sudo ln -sf /etc/nginx/sites-available/nagarnetra.conf /etc/nginx/sites-enabled/nagarnetra.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx

echo "Bootstrap complete"
echo "Next steps:"
echo "1) Edit /etc/nagarnetra/staging.env and /etc/nagarnetra/main.env"
echo "2) Edit /etc/nginx/sites-available/nagarnetra.conf domains"
echo "3) sudo systemctl reload nginx"
echo "4) Run deploy/scripts/update_and_deploy.sh staging"
