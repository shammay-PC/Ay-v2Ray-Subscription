#!/bin/bash
# Ay-v2Ray-Subscription v4.0.0 Installer
set -e

# Colors
GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'

PROJECT_DIR="/root/ay-custom-sub"
SERVICE_SUB="ay-custom-sub.service"
SERVICE_WEB="ay-custom-web.service"
SERVICE_SYNC="ay-sync-clients.service"
TIMER_SYNC="ay-sync-clients.timer"

info()  { echo -e "${CYAN}$1${NC}"; }
ok()    { echo -e "${GREEN}$1${NC}"; }
warn()  { echo -e "${YELLOW}$1${NC}"; }
err()   { echo -e "${RED}$1${NC}"; }

# --- Prompts ---
prompt_config() {
    echo ""
    info "=== Ay-Custom-Sub v4.0.0 Setup ==="
    echo ""

    read -p "Domain [www.shammay.ir]: " DOMAIN
    DOMAIN=${DOMAIN:-www.shammay.ir}

    read -p "Web Panel Port [4321]: " WEB_PORT
    WEB_PORT=${WEB_PORT:-4321}

    read -p "Main X-UI Sub Port [8868]: " MAIN_PORT
    MAIN_PORT=${MAIN_PORT:-8868}

    read -p "Admin Username [AyAdmin]: " ADMIN_USER
    ADMIN_USER=${ADMIN_USER:-AyAdmin}

    read -p "Admin Password [AyPass]: " ADMIN_PASS
    ADMIN_PASS=${ADMIN_PASS:-AyPass}

    read -p "Extra Sub Ports (comma-separated) [6854,8688]: " EXTRA_PORTS
    EXTRA_PORTS=${EXTRA_PORTS:-6854,8688}
}

# --- Install ---
install_project() {
    prompt_config

    info "Cloning project..."
    rm -rf "$PROJECT_DIR"
    git clone https://github.com/shammay-PC/Ay-v2Ray-Subscription.git "$PROJECT_DIR"

    info "Installing system packages..."
    apt update -qq && apt install -y python3 python3-pip sqlite3 >/dev/null 2>&1

    info "Installing Python dependencies..."
    pip3 install --quiet flask requests waitress

    # Create config.json
    cat > "$PROJECT_DIR/config.json" <<EOF
{
  "username": "$ADMIN_USER",
  "password": "$ADMIN_PASS",
  "web_port": $WEB_PORT,
  "main_port": $MAIN_PORT,
  "domain": "$DOMAIN",
  "sub_path": "sub",
  "extra_ports": [$(echo $EXTRA_PORTS | tr ',' ', ' | sed 's/,/, /g')],
  "cert_path": "/root/cert/$DOMAIN/fullchain.pem",
  "key_path": "/root/cert/$DOMAIN/privkey.pem",
  "expired_message": "⛔ اشتراک شما منقضی شده، لطفا تمدید کنید ⛔",
  "no_sub_message": "⛔ شما اشتراکی در این سرویس ندارید ⛔",
  "dummy_config_template": "vless://00000000-0000-0000-0000-000000000000@go-to.shammay.ir:1?type=ws#{MESSAGE}"
}
EOF

    # Create empty data files
    touch "$PROJECT_DIR/Ay_Custom_Configs"
    touch "$PROJECT_DIR/Ay_SSH_Users"
    [ -f "$PROJECT_DIR/Side_Collections.json" ] || echo "[]" > "$PROJECT_DIR/Side_Collections.json"

    # Check for X-UI
    if [ ! -f /etc/x-ui/x-ui.db ]; then
        warn "X-UI database not found at /etc/x-ui/x-ui.db"
        read -p "Continue anyway? (y/n): " cont
        [ "$cont" != "y" ] && exit 1
    fi

    info "Installing systemd units..."
    cp "$PROJECT_DIR/systemd/$SERVICE_SUB" /etc/systemd/system/
    cp "$PROJECT_DIR/systemd/$SERVICE_WEB" /etc/systemd/system/
    cp "$PROJECT_DIR/systemd/$SERVICE_SYNC" /etc/systemd/system/
    cp "$PROJECT_DIR/systemd/$TIMER_SYNC" /etc/systemd/system/

    systemctl daemon-reload
    systemctl enable --now $SERVICE_SUB $SERVICE_WEB
    systemctl enable --now $TIMER_SYNC

    # Create shortcut
    ln -sf "$PROJECT_DIR/setup.sh" /usr/local/bin/aysub
    chmod +x /usr/local/bin/aysub

    # Save panel info
    cat > "$PROJECT_DIR/.panel_info" <<EOF
PANEL_URL=https://$DOMAIN:$WEB_PORT
PANEL_USER=$ADMIN_USER
PANEL_PASS=$ADMIN_PASS
EOF

    ok "Installation complete!"
    echo ""
    info "Web Panel: https://$DOMAIN:$WEB_PORT"
    info "Username:  $ADMIN_USER"
    info "Password:  $ADMIN_PASS"
    info "Command:   aysub"
    echo ""
}

# --- Menu ---
print_menu() {
    clear
    echo -e "${CYAN}"
    echo "  █████╗ ██╗   ██╗"
    echo " ██╔══██╗╚██╗ ██╔╝"
    echo " ███████║ ╚████╔╝ "
    echo " ██╔══██║  ╚██╔╝  "
    echo " ██║  ██║   ██║   "
    echo " ╚═╝  ╚═╝   ╚═╝   "
    echo -e "${NC}"
    echo -e "${YELLOW}=== Ay Technic Panel v4.0.0 ===${NC}"
    echo ""

    if [ -f "$PROJECT_DIR/.panel_info" ]; then
        source "$PROJECT_DIR/.panel_info"
        echo "URL:  $PANEL_URL"
        echo "User: $PANEL_USER"
        echo "Pass: $PANEL_PASS"
        echo -e "${YELLOW}------------------------------${NC}"
    fi

    echo "1. 📊 Install"
    echo "2. 🔄 Update Panel"
    echo "3. 🔁 Restart Services"
    echo "4. 🛠️ Edit Configuration"
    echo "5. ⏱️ Manage Client Sync Timer"
    echo "6. 📋 View Live Logs"
    echo "7. 🗃️ Uninstall"
    echo "0. ❌ Exit"
    echo -e "${YELLOW}==============================${NC}"
}

manage_sync_timer() {
    while true; do
        clear
        echo -e "${YELLOW}--- Client Sync Timer ---${NC}"
        systemctl status $TIMER_SYNC --no-pager | head -5
        echo ""
        echo "1. Start"
        echo "2. Stop"
        echo "3. Restart"
        echo "4. Edit Duration"
        echo "5. View Logs"
        echo "0. Back"
        read -p "Choice: " c
        case $c in
            1) systemctl start $TIMER_SYNC && ok "Started" ;;
            2) systemctl stop $TIMER_SYNC && ok "Stopped" ;;
            3) systemctl restart $TIMER_SYNC && ok "Restarted" ;;
            4)
                read -p "Minutes [5]: " mins
                mins=${mins:-5}
                sed -i "s/^OnUnitActiveSec=.*/OnUnitActiveSec=${mins}min/" /etc/systemd/system/$TIMER_SYNC
                systemctl daemon-reload
                systemctl restart $TIMER_SYNC
                ok "Set to $mins minutes"
                ;;
            5) journalctl -u $SERVICE_SYNC -n 50 --no-pager ;;
            0) return ;;
        esac
        read -p "Enter to continue..."
    done
}

view_live_logs() {
    echo "1. ay-custom-sub"
    echo "2. ay-custom-web"
    echo "3. x-ui"
    read -p "Choice: " c
    case $c in
        1) journalctl -u ay-custom-sub -f ;;
        2) journalctl -u ay-custom-web -f ;;
        3) journalctl -u x-ui -f ;;
    esac
}

uninstall_project() {
    read -p "Are you sure? (y/n): " c
    [ "$c" != "y" ] && return
    for svc in $SERVICE_SUB $SERVICE_WEB $TIMER_SYNC $SERVICE_SYNC; do
        systemctl stop $svc 2>/dev/null || true
        systemctl disable $svc 2>/dev/null || true
        rm -f /etc/systemd/system/$svc
    done
    systemctl daemon-reload
    rm -rf "$PROJECT_DIR"
    rm -f /usr/local/bin/aysub
    ok "Uninstalled."
}

# --- Main ---
if [[ "$1" == "install" ]]; then
    install_project
    exit 0
fi

while true; do
    print_menu
    read -p "Choice: " c
    case $c in
        1) install_project; read -p "Enter..." ;;
        2) cd "$PROJECT_DIR" && git pull && systemctl restart $SERVICE_SUB $SERVICE_WEB && ok "Updated"; read -p "Enter..." ;;
        3) systemctl restart $SERVICE_SUB $SERVICE_WEB && ok "Restarted"; read -p "Enter..." ;;
        4) nano "$PROJECT_DIR/config.json" ;;
        5) manage_sync_timer ;;
        6) view_live_logs ;;
        7) uninstall_project; read -p "Enter..." ;;
        0) exit 0 ;;
        *) err "Invalid"; sleep 1 ;;
    esac
done