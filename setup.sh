#!/bin/bash

clear
echo -e "\e[1;36m"
echo "    █████╗ ██╗   ██╗██████╗ ██╗███████╗ ███████╗"
echo "   ██╔══██╗██║   ██║██╔══██╗██║██╔════╝ ██╔════╝"
echo "   ███████║██║   ██║██████╔╝██║███████╗ █████╗  "
echo "   ██╔══██║██║   ██║██╔═══╝ ██║╚════██╗ ██╔══╝  "
echo "   ██║  ██║╚██████╔╝██║     ██║███████╗ ███████╗"
echo "   ╚═╝  ╚═╝ ╚═════╝ ╚═╝     ╚═╝╚══════╝ ╚══════╝"
echo -e "\e[0m"
echo "Developed by shammay"
echo "All Rights Reserved by Ay Technic"
echo "Website: shammay.ir"
echo "-------------------------------------"
echo "                    Ay-VPN"
echo "-------------------------------------"
echo "1. Install"
echo "2. Update Panel"
echo "3. Restart Service"
echo "4. Configuration"
echo "5. Uninstall"
echo "0. Exit"
echo "-------------------------------------"
read -p "Select an option: " option

case $option in
  1)
    read -p "Enter your domain (e.g. example.com): " domain

    echo "Cloning the project..."
    git clone https://github.com/YourGitHub/Ay-v2Ray-Subscription.git /root/ay-custom-sub

    echo "Setting up config..."
    cat <<EOF > /root/ay-custom-sub/config.json
{
  "username": "AyAdmin",
  "password": "AyPass",
  "web_port": 6854,
  "main_port": 8868,
  "extra_ports": [6854, 8688, 4586],
  "domain": "$domain",
  "sub_path": "sub",
  "cert_path": "",
  "key_path": ""
}
EOF

    echo "Installing requirements..."
    pip3 install flask requests

    echo "Installing systemd services..."
    cp /root/ay-custom-sub/ay-custom-sub.service /etc/systemd/system/
    cp /root/ay-custom-sub/ay-custom-web.service /etc/systemd/system/
    systemctl daemon-reexec
    systemctl daemon-reload
    systemctl enable ay-custom-sub.service
    systemctl enable ay-custom-web.service
    systemctl start ay-custom-sub.service
    systemctl start ay-custom-web.service

    echo -e "\n✅ Installation Complete"
    echo "🔗 Visit: https://$domain:6854"
    echo "🧑 Username: AyAdmin"
    echo "🔐 Password: AyPass"
    echo "To launch the menu again, run: bash setup.sh"
    ;;
  2)
    echo "Updating project..."
    cd /root/ay-custom-sub && git pull
    systemctl restart ay-custom-sub.service
    systemctl restart ay-custom-web.service
    echo "✅ Update complete."
    ;;
  3)
    systemctl restart ay-custom-sub.service
    systemctl restart ay-custom-web.service
    echo "✅ Services restarted."
    ;;
  4)
    nano /root/ay-custom-sub/config.json
    ;;
  5)
    echo "Uninstalling..."
    systemctl stop ay-custom-sub.service
    systemctl stop ay-custom-web.service
    systemctl disable ay-custom-sub.service
    systemctl disable ay-custom-web.service
    rm /etc/systemd/system/ay-custom-sub.service
    rm /etc/systemd/system/ay-custom-web.service
    rm -rf /root/ay-custom-sub
    systemctl daemon-reload
    echo "✅ Uninstalled successfully."
    ;;
  0)
    echo "Goodbye!"
    exit 0
    ;;
  *)
    echo "Invalid option."
    ;;
esac
