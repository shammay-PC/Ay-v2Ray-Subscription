#!/bin/bash
# Ay-v2Ray-Subscription v4.0.0 Installer

PROJECT_DIR="/root/ay-custom-sub"
REPO_URL="https://github.com/shammay-PC/Ay-v2Ray-Subscription.git"

install_project() {
  echo -e "${CYAN}=== Ay-Custom-Sub v4.0.0 Installation ===${NC}"

  # Check X-UI
  if ! command -v x-ui &>/dev/null; then
    echo -e "${YELLOW}هشدار: X-UI نصب نیست. آیا ادامه می‌دهید؟ (y/n)${NC}"
    read -r cont
    [[ "$cont" != "y" ]] && return 1
  fi

  # Domain
  read -rp "دامنه (example.com): " DOMAIN
  [[ -z "$DOMAIN" ]] && DOMAIN="sub.example.com"

  # Ports
  read -rp "پورت وب (پیش‌فرض 4321): " WEB_PORT
  WEB_PORT=${WEB_PORT:-4321}
  read -rp "پورت اصلی X-UI (پیش‌فرض 8868): " MAIN_PORT
  MAIN_PORT=${MAIN_PORT:-8868}
  read -rp "پورت‌های جانبی جدا با کاما (پیش‌فرض 6854,8688): " EXTRA_PORTS
  EXTRA_PORTS=${EXTRA_PORTS:-6854,8688}

  # Credentials
  read -rp "نام کاربری (پیش‌فرض AyAdmin): " PANEL_USER
  PANEL_USER=${PANEL_USER:-AyAdmin}
  read -rsp "رمز عبور (پیش‌فرض AyPass): " PANEL_PASS
  echo
  PANEL_PASS=${PANEL_PASS:-AyPass}

  # Cert paths
  read -rp "مسیر گواهی SSL (پیش‌فرض /root/cert/${DOMAIN}/fullchain.pem): " CERT_PATH
  CERT_PATH=${CERT_PATH:-/root/cert/${DOMAIN}/fullchain.pem}
  read -rp "مسیر کلید SSL (پیش‌فرض /root/cert/${DOMAIN}/privkey.pem): " KEY_PATH
  KEY_PATH=${KEY_PATH:-/root/cert/${DOMAIN}/privkey.pem}

  # Clone/Update
  rm -rf "$PROJECT_DIR"
  git clone "$REPO_URL" "$PROJECT_DIR"

  # Install requirements
  apt update && apt install -y python3 python3-pip sqlite3
  pip3 install -r "$PROJECT_DIR/requirements.txt"

  # Write config.json
  cat > "$PROJECT_DIR/config.json" <<EOF
{
  "username": "$PANEL_USER",
  "password": "$PANEL_PASS",
  "web_port": $WEB_PORT,
  "domain": "$DOMAIN",
  "main_port": $MAIN_PORT,
  "sub_path": "sub",
  "extra_ports": [$(echo $EXTRA_PORTS | tr ',' ',')],
  "cert_path": "$CERT_PATH",
  "key_path": "$KEY_PATH",
  "side_collection_urls": [],
  "expired_message": "⛔ اشتراک شما منقضی شده ⛔",
  "no_sub_message": "⛔ شما اشتراکی ندارید ⛔",
  "dummy_config_template": "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:1?type=ws#{MESSAGE}"
}
EOF

  # Install systemd units
  for unit in ay-custom-sub ay-custom-web ay-sync-clients; do
    cp "$PROJECT_DIR/systemd/${unit}.service" /etc/systemd/system/ 2>/dev/null || true
  done
  cp "$PROJECT_DIR/systemd/ay-sync-clients.timer" /etc/systemd/system/ 2>/dev/null || true

  systemctl daemon-reload
  systemctl enable --now ay-custom-sub ay-custom-web

  # Shortcut
  ln -sf "$PROJECT_DIR/setup.sh" /usr/local/bin/ay-sub
  chmod +x /usr/local/bin/ay-sub

  echo -e "${GREEN}نصب کامل شد!${NC}"
  echo "Web Panel: https://${DOMAIN}:${WEB_PORT}"
  echo "Username: ${PANEL_USER}"
}