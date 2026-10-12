#!/bin/bash
# Ay-Custom-Sub v4.0.0 installer
set -e

GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'

PROJECT_DIR="/root/ay-custom-sub"
PANEL_INFO_FILE="$PROJECT_DIR/.panel_info"
REPO_URL="https://github.com/shammay-PC/Ay-v2Ray-Subscription.git"

print_success() { echo -e "${GREEN}$1${NC}"; }
print_error()   { echo -e "${RED}$1${NC}"; }
print_info()    { echo -e "${CYAN}$1${NC}"; }

require_root() {
  if [ "$EUID" -ne 0 ]; then print_error "Please run as root."; exit 1; fi
}

install_deps() {
  print_info "Installing system dependencies..."
  apt update
  apt install -y python3 python3-pip git sqlite3
  pip3 install --upgrade flask requests waitress
}

prompt_settings() {
  read -p "Domain (e.g. sub.example.com): " DOMAIN
  read -p "Web panel port [4321]: " WEB_PORT; WEB_PORT=${WEB_PORT:-4321}
  read -p "Main X-UI port [8868]: " MAIN_PORT; MAIN_PORT=${MAIN_PORT:-8868}
  read -p "Panel username [AyAdmin]: " PANEL_USER; PANEL_USER=${PANEL_USER:-AyAdmin}
  read -s -p "Panel password [AyPass]: " PANEL_PASS; echo; PANEL_PASS=${PANEL_PASS:-AyPass}
  read -p "Extra sub ports (comma separated) [6854,8688]: " EXTRA_PORTS
  EXTRA_PORTS=${EXTRA_PORTS:-6854,8688}
  read -p "SSL cert path [/root/cert/cert.pem]: " CERT_PATH
  CERT_PATH=${CERT_PATH:-/root/cert/cert.pem}
  read -p "SSL key path [/root/cert/privkey.pem]: " KEY_PATH
  KEY_PATH=${KEY_PATH:-/root/cert/privkey.pem}

  if [ ! -f "$CERT_PATH" ] || [ ! -f "$KEY_PATH" ]; then
    print_error "WARNING: SSL cert/key not found. The service will refuse to start."
    read -p "Continue anyway? (y/N): " cont
    [[ "$cont" =~ ^[Yy]$ ]] || exit 1
  fi

  if [ ! -f /etc/x-ui/x-ui.db ]; then
    print_error "WARNING: x-ui database not found at /etc/x-ui/x-ui.db."
    read -p "Continue anyway? (y/N): " cont
    [[ "$cont" =~ ^[Yy]$ ]] || exit 1
  fi
}

write_config() {
  print_info "Writing config.json..."
  cat > "$PROJECT_DIR/config.json" <<EOF
{
  "username": "$PANEL_USER",
  "password": "$PANEL_PASS",
  "web_port": $WEB_PORT,
  "main_port": $MAIN_PORT,
  "domain": "$DOMAIN",
  "sub_path": "sub",
  "extra_ports": [$(echo $EXTRA_PORTS | sed 's/ //g')],
  "cert_path": "$CERT_PATH",
  "key_path": "$KEY_PATH",
  "side_collection_urls": [],
  "expired_message": "⛔ اشتراک شما منقضی شده، لطفا تمدید کنید ⛔",
  "no_sub_message": "⛔ شما اشتراکی در این سرویس ندارید ⛔",
  "dummy_config_template": "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:1?type=ws#{MESSAGE}"
}
EOF
  echo "PANEL_URL=https://$DOMAIN:$WEB_PORT" > "$PANEL_INFO_FILE"
  echo "PANEL_USER=$PANEL_USER" >> "$PANEL_INFO_FILE"
  echo "PANEL_PASS=$PANEL_PASS" >> "$PANEL_INFO_FILE"
}

install_services() {
  print_info "Installing systemd units..."
  cp "$PROJECT_DIR/systemd/ay-custom-sub.service" /etc/systemd/system/
  cp "$PROJECT_DIR/systemd/ay-custom-web.service" /etc/systemd/system/
  cp "$PROJECT_DIR/systemd/ay-sync-clients.service" /etc/systemd/system/
  cp "$PROJECT_DIR/systemd/ay-sync-clients.timer"   /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable ay-custom-sub.service ay-custom-web.service
  systemctl start  ay-custom-sub.service ay-custom-web.service
  # ay-sync-clients stays disabled by default.
  systemctl disable --now ay-sync-clients.timer || true

  ln -sf "$PROJECT_DIR/setup.sh" /usr/local/bin/ay-sub
  chmod +x /usr/local/bin/ay-sub
}

install_project() {
  require_root
  install_deps

  if [ -d "$PROJECT_DIR" ]; then
    print_info "Existing installation found. Upgrading (git pull)..."
    cd "$PROJECT_DIR" && git pull --rebase
  else
    print_info "Cloning repository..."
    git clone "$REPO_URL" "$PROJECT_DIR"
  fi

  prompt_settings
  write_config
  install_services

  print_success "✅ Installation complete!"
  print_info "Web Panel: https://$DOMAIN:$WEB_PORT"
  print_info "Username:  $PANEL_USER"
  print_info "Password:  $PANEL_PASS"
  print_info "Menu:      ay-sub"
}

update_panel() {
  require_root
  cd "$PROJECT_DIR" && git pull --rebase
  pip3 install --upgrade flask requests waitress
  systemctl restart ay-custom-sub.service ay-custom-web.service
  print_success "Panel updated."
}

manage_client_sync() {
  while true; do
    clear
    echo -e "${YELLOW}=== Client Sync Timer ===${NC}"
    systemctl status ay-sync-clients.timer --no-pager | grep -E "Loaded|Active|Trigger" || true
    echo "1) Enable   2) Disable   3) Restart   4) Logs   0) Back"
    read -p "> " c
    case $c in
      1) systemctl enable --now ay-sync-clients.timer ;;
      2) systemctl disable --now ay-sync-clients.timer ;;
      3) systemctl restart ay-sync-clients.timer ;;
      4) journalctl -u ay-sync-clients.service -n 50 --no-pager ;;
      0) return ;;
    esac
    read -p "Enter..."
  done
}

manage_services() {
  while true; do
    clear
    echo -e "${YELLOW}=== Main Services ===${NC}"
    for s in ay-custom-sub ay-custom-web x-ui; do
      printf "%-18s %s / %s\n" "$s" "$(systemctl is-active $s)" "$(systemctl is-enabled $s 2>/dev/null)"
    done
    echo "1) Restart all   2) Show sub logs   3) Show web logs   0) Back"
    read -p "> " c
    case $c in
      1) systemctl restart ay-custom-sub ay-custom-web ;;
      2) journalctl -u ay-custom-sub -n 80 --no-pager ;;
      3) journalctl -u ay-custom-web -n 80 --no-pager ;;
      0) return ;;
    esac
    read -p "Enter..."
  done
}

show_live_logs() {
  read -p "Service [ay-custom-sub]: " s; s=${s:-ay-custom-sub}
  journalctl -u "$s" -f
}

uninstall_project() {
  read -p "Uninstall? (y/N): " c
  [[ "$c" =~ ^[Yy]$ ]] || return
  systemctl stop  ay-custom-sub ay-custom-web ay-sync-clients.timer || true
  systemctl disable ay-custom-sub ay-custom-web ay-sync-clients.timer || true
  rm -f /etc/systemd/system/ay-custom-sub.service
  rm -f /etc/systemd/system/ay-custom-web.service
  rm -f /etc/systemd/system/ay-sync-clients.service
  rm -f /etc/systemd/system/ay-sync-clients.timer
  systemctl daemon-reload
  rm -rf "$PROJECT_DIR"
  rm -f /usr/local/bin/ay-sub
  print_success "Uninstalled."
}

print_menu() {
  clear
  echo -e "${CYAN}"
  echo "   db    Yb  dP  Yb   dP 88\"\"Yb 88b 88 "
  echo "  dPYb    YbdP    Yb dP  88__dP 88Yb88 "
  echo " dP__Yb    8P      YbdP  88\"\"\"  88 Y88 "
  echo "dP\"\"\"\"Yb  dP        YP   88     88  Y8 "
  echo -e "${NC}"
  echo -e "${RED}        Ay Technic Panel v4.0.0${NC}"
  echo -e "${GREEN}      Developed by shammay${NC}"
  echo -e "${YELLOW}======================================${NC}"
  if [ -f "$PANEL_INFO_FILE" ]; then source "$PANEL_INFO_FILE"; echo "URL: $PANEL_URL"; fi
  echo "1) 📊 Install / Upgrade"
  echo "2) 🔄 Update Panel"
  echo "3) 🔁 Restart Main Services"
  echo "4) 🛠️  Configuration (nano config.json)"
  echo "5) ⏱️  Manage Client Sync timer"
  echo "6) ⚙️  Manage Services"
  echo "7) 📜 Live Logs"
  echo "8) 🗃️  Uninstall"
  echo "0) ❌ Exit"
}

if [[ "$1" == "install" ]]; then install_project; exit 0; fi

while true; do
  print_menu
  read -p "> " choice
  case $choice in
    1) install_project ;;
    2) update_panel ;;
    3) systemctl restart ay-custom-sub ay-custom-web && print_success "Restarted." ;;
    4) nano "$PROJECT_DIR/config.json" ;;
    5) manage_client_sync ;;
    6) manage_services ;;
    7) show_live_logs ;;
    8) uninstall_project ;;
    0) exit 0 ;;
    *) print_error "Invalid option" ;;
  esac
  read -p "Press Enter..."
done