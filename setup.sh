#!/bin/bash

# Color Definitions
GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Project Variables
PROJECT_DIR="/root/ay-custom-sub"
PANEL_INFO_FILE="$PROJECT_DIR/.panel_info"
SERVICE1="ay-custom-sub.service"
SERVICE2="ay-custom-web.service"
RESTARTER_SERVICE="Ay-Custom-Sub-Restarter.service"
RESTARTER_TIMER="Ay-Custom-Sub-Restarter.timer"

# --- Helper Functions ---
print_success() {
  echo -e "${GREEN}$1${NC}"
}

print_error() {
  echo -e "${RED}$1${NC}"
}

print_info() {
  echo -e "${CYAN}$1${NC}"
}

# --- Main Menu and Logic ---
print_menu() {
  clear

echo -e "${NC}"
echo -e "${CYAN}"
echo "   db    Yb  dP  Yb   dP 88\"\"Yb 88b 88 "
echo "  dPYb    YbdP    Yb dP  88__dP 88Yb88 "
echo " dP__Yb    8P      YbdP  88\"\"\"  88 Y88 "
echo "dP\"\"\"\"Yb  dP        YP   88     88  Y8 "
echo -e "${NC}"
  echo -e "${YELLOW}======================================${NC}"
  echo -e "${RED}        Ay Technic Panel${NC}"
  echo -e "${GREEN}      Developed by shammay${NC}"
  echo -e "${GREEN} All Rights Reserved by Ay Technic${NC}"
  echo -e "${CYAN}       https://shammay.ir${NC}"
  echo -e "${YELLOW}======================================${NC}"

  if [ -f "$PANEL_INFO_FILE" ]; then
    print_info "Panel Information:"
    source "$PANEL_INFO_FILE"
    echo "URL: $PANEL_URL"
    echo "Username: $PANEL_USER"
    echo "Password: $PANEL_PASS"
    echo -e "${YELLOW}--------------------------------------${NC}"
  fi

  echo "1. 📊 Install"
  echo "2. 🔄 Update Panel"
  echo "3. 🔁 Restart Main Services"
  echo "4. 🛠️ Configuration"
  echo "5. ⚙️ Manage Restarter"
  echo "6. 🗃️ Uninstall"
  echo "0. ❌ Exit"
  echo -e "${YELLOW}======================================${NC}"
}

install_project() {
  DOMAIN=$1

  if [ -z "$DOMAIN" ]; then
    read -p "Enter your domain (e.g., example.com): " DOMAIN
  fi

  print_info "Cloning project..."
  rm -rf $PROJECT_DIR
  git clone https://github.com/shammay-PC/Ay-v2Ray-Subscription.git $PROJECT_DIR

  print_info "Installing requirements (python3, pip, flask, requests)..."
  apt update && apt install python3 python3-pip -y
  pip3 install flask requests

  print_info "Setting up main services..."
  cp $PROJECT_DIR/systemd/$SERVICE1 /etc/systemd/system/
  cp $PROJECT_DIR/systemd/$SERVICE2 /etc/systemd/system/
  
  print_info "Setting up restarter service and timer..."
  cp $PROJECT_DIR/systemd/$RESTARTER_SERVICE /etc/systemd/system/
  cp $PROJECT_DIR/systemd/$RESTARTER_TIMER /etc/systemd/system/
  
  systemctl daemon-reload
  systemctl enable $SERVICE1 $SERVICE2
  systemctl start $SERVICE1 $SERVICE2
  
  print_info "Enabling and starting the restarter timer..."
  systemctl enable --now $RESTARTER_TIMER

  print_info "Creating ay-sub shortcut command..."
  ln -sf $PROJECT_DIR/setup.sh /usr/local/bin/ay-sub
  chmod +x /usr/local/bin/ay-sub

  # Save panel info for menu display
  echo "PANEL_URL=https://s$DOMAIN:4321" > $PANEL_INFO_FILE
  echo "PANEL_USER=AyAdmin" >> $PANEL_INFO_FILE
  echo "PANEL_PASS=AyPass" >> $PANEL_INFO_FILE

  print_success "Installation complete!"
  echo -e "${YELLOW}--------------------------------------${NC}"
  print_info "Web Panel: https://$DOMAIN:4321"
  print_info "Username: AyAdmin"
  print_info "Password: AyPass"
  echo -e "${YELLOW}--------------------------------------${NC}"
  print_info "To run menu anytime, just type: ay-sub"
}

update_panel() {
  print_info "Updating panel from GitHub..."
  cd $PROJECT_DIR && git pull
  systemctl restart $SERVICE1 $SERVICE2
  print_success "Panel updated successfully."
}

restart_main_services() {
  systemctl restart $SERVICE1 $SERVICE2
  print_success "Main services restarted successfully."
}

configuration() {
  nano $PROJECT_DIR/config.json
}

manage_restarter_menu() {
  while true; do
    clear
    echo -e "${YELLOW}--- Restarter Service Management ---${NC}"
    # Display current status
    systemctl status $RESTARTER_TIMER --no-pager | grep -E "Loaded|Active|Trigger"
    echo -e "${YELLOW}------------------------------------${NC}"
    echo "1. Start Timer"
    echo "2. Stop Timer"
    echo "3. Restart Timer"
    echo "4. View Status & Logs"
    echo "5. Edit Timer Schedule (in minutes)"
    echo "0. Return to Main Menu"
    echo -e "${YELLOW}------------------------------------${NC}"
    read -p "Select an option: " restarter_choice
    case $restarter_choice in
      1)
        systemctl start $RESTARTER_TIMER
        print_success "Timer started."
        ;;
      2)
        systemctl stop $RESTARTER_TIMER
        print_success "Timer stopped."
        ;;
      3)
        systemctl restart $RESTARTER_TIMER
        print_success "Timer restarted."
        ;;
      4)
        clear
        print_info "--- Timer Status ---"
        systemctl status $RESTARTER_TIMER
        print_info "\n--- Last 20 Service Logs ---"
        journalctl -u $RESTARTER_SERVICE -n 20 --no-pager
        ;;
      5)
        local timer_file="/etc/systemd/system/$RESTARTER_TIMER"
        local current_val_sec=$(grep -Po 'OnUnitActiveSec=\K[0-9]+' $timer_file)
        local current_val_min=$((current_val_sec / 60))
        
        print_info "Current schedule: Every $current_val_min minutes."
        read -p "Enter new schedule in minutes (e.g., 15): " new_minutes
        
        if [[ "$new_minutes" =~ ^[0-9]+$ ]] && [ "$new_minutes" -gt 0 ]; then
          sed -i "s/^OnUnitActiveSec=.*/OnUnitActiveSec=${new_minutes}min/" $timer_file
          systemctl daemon-reload
          systemctl restart $RESTARTER_TIMER
          print_success "Timer schedule updated to every $new_minutes minutes."
        else
          print_error "Invalid input. Please enter a positive number."
        fi
        ;;
      0)
        return
        ;;
      *)
        print_error "Invalid option"
        ;;
    esac
    read -p "Press Enter to continue..."
  done
}

uninstall_project() {
    read -p "Are you sure you want to uninstall? This is irreversible. (y/n): " confirm
    if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
        print_info "Uninstalling..."
        systemctl stop $SERVICE1 $SERVICE2 $RESTARTER_TIMER
        systemctl disable $SERVICE1 $SERVICE2 $RESTARTER_TIMER
        rm -f /etc/systemd/system/$SERVICE1
        rm -f /etc/systemd/system/$SERVICE2
        rm -f /etc/systemd/system/$RESTARTER_SERVICE
        rm -f /etc/systemd/system/$RESTARTER_TIMER
        systemctl daemon-reload
        rm -rf $PROJECT_DIR
        rm -f /usr/local/bin/ay-sub
        print_success "Uninstalled successfully."
    else
        print_info "Uninstallation cancelled."
    fi
}

# اگر با پارامتر install اجرا شد، مستقیم نصب کن
if [[ "$1" == "install" ]]; then
  install_project "$2"
  exit 0
fi

# در غیر این صورت منوی تعاملی اجرا میشه
while true; do
  print_menu
  read -p "Select an option: " choice
  case $choice in
    1)
      install_project
      read -p "Press Enter to return to menu..."
      ;;
    2)
      update_panel
      read -p "Press Enter to return to menu..."
      ;;
    3)
      restart_main_services
      read -p "Press Enter to return to menu..."
      ;;
    4)
      configuration
      # No pause needed, nano handles it
      ;;
    5)
      manage_restarter_menu
      ;;
    6)
      uninstall_project
      read -p "Press Enter to return to menu..."
      ;;
    0)
      exit 0
      ;;
    *)
      print_error "Invalid option"
      sleep 1
      ;;
  esac
done
