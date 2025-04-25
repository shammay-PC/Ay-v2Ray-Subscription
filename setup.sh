#!/bin/bash

PROJECT_DIR="/root/ay-custom-sub"
SERVICE1="ay-custom-sub.service"
SERVICE2="ay-custom-web.service"

print_menu() {
  clear
  echo "======================================"
  echo "           Ay Technic"
  echo "      Developed by shammay"
  echo " All Rights Reserved by Ay Technic"
  echo "       https://shammay.ir"
  echo "======================================"
  echo "1. Install"
  echo "2. Update Panel"
  echo "3. Restart Service"
  echo "4. Configuration"
  echo "5. Uninstall"
  echo "0. Exit"
  echo "======================================"
}

install_project() {
  DOMAIN=$1

  if [ -z "$DOMAIN" ]; then
    read -p "Enter your domain (e.g., example.com): " DOMAIN
  fi

  echo "Cloning project..."
  rm -rf $PROJECT_DIR
  git clone https://github.com/shammay-PC/Ay-v2Ray-Subscription.git $PROJECT_DIR

  echo "Installing requirements..."
  pip install flask requests

  echo "Setting up services..."
  cp $PROJECT_DIR/systemd/$SERVICE1 /etc/systemd/system/
  cp $PROJECT_DIR/systemd/$SERVICE2 /etc/systemd/system/
  systemctl daemon-reexec
  systemctl enable $SERVICE1 $SERVICE2
  systemctl start $SERVICE1 $SERVICE2

  echo "Installation complete!"
  echo "--------------------------------------"
  echo "Access your panel at: https://$DOMAIN:6854"
  echo "Username: AyAdmin"
  echo "Password: AyPass"
  echo "To run setup menu again: bash setup.sh"
  exit 0
}

# اگر با پارامتر install اجرا شد، مستقیم نصب کن
if [[ "$1" == "install" ]]; then
  install_project "$2"
fi

# در غیر این صورت منوی تعاملی اجرا میشه
update_panel() {
  echo "Updating panel..."
  cd $PROJECT_DIR && git pull
  systemctl restart $SERVICE1 $SERVICE2
  echo "Panel updated successfully."
}

restart_service() {
  systemctl restart $SERVICE1 $SERVICE2
  echo "Services restarted."
}

configuration() {
  nano $PROJECT_DIR/config.json
}

uninstall_project() {
  echo "Uninstalling..."
  systemctl stop $SERVICE1 $SERVICE2
  systemctl disable $SERVICE1 $SERVICE2
  rm /etc/systemd/system/$SERVICE1
  rm /etc/systemd/system/$SERVICE2
  systemctl daemon-reexec
  rm -rf $PROJECT_DIR
  echo "Uninstalled successfully."
}

while true; do
  print_menu
  read -p "Select an option: " choice
  case $choice in
    1) install_project;;
    2) update_panel;;
    3) restart_service;;
    4) configuration;;
    5) uninstall_project;;
    0) exit;;
    *) echo "Invalid option"; sleep 1;;
  esac
done
