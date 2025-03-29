from getpass import getpass

# Prompt for Ngrok token securely
ngrok_token = getpass("Enter your Ngrok authtoken: ")

# Define the bash script with Ngrok token injected
script = f"""#!/bin/bash

# Exit on any error
set -e

# Update package list and install dependencies
echo "Updating package list and installing dependencies..."
sudo apt update
sudo apt -y install software-properties-common curl apt-transport-https ca-certificates gnupg jq unzip cron

# Add repositories for PHP, Redis, and MariaDB with fixes
echo "Adding repositories..."
sudo LC_ALL=C.UTF-8 add-apt-repository -y ppa:ondrej/php

# Redis repo setup with overwrite and error check
echo "Setting up Redis repository..."
if ! curl -fsSL https://packages.redis.io/gpg -o /tmp/redis.gpg; then
    echo "Failed to fetch Redis GPG key. Continuing without it..."
else
    sudo gpg --batch --dearmor -o /usr/share/keyrings/redis-archive-keyring.gpg /tmp/redis.gpg || echo "GPG dearmor failed, file may exist."
    rm -f /tmp/redis.gpg
fi
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/redis.list

# MariaDB repo setup
echo "Setting up MariaDB repository..."
if ! curl -LsS https://r.mariadb.com/downloads/mariadb_repo_setup -o /tmp/mariadb_repo_setup; then
    echo "Failed to download MariaDB repo setup script."
    exit 1
fi
sudo bash /tmp/mariadb_repo_setup
rm -f /tmp/mariadb_repo_setup

# Update package list again
echo "Updating package list after adding repos..."
sudo apt update

# Install PHP 8.3 with required extensions, including FPM explicitly
echo "Installing PHP 8.3 and extensions..."
sudo apt -y install php8.3 php8.3-{{common,cli,gd,mysql,mbstring,bcmath,xml,fpm,curl,zip}}

# Install MariaDB
echo "Installing MariaDB..."
sudo debconf-set-selections <<< "mariadb-server mysql-server/root_password password rootpassword"
sudo debconf-set-selections <<< "mariadb-server mysql-server/root_password_again password rootpassword"
sudo apt -y install mariadb-server

# Start MariaDB manually (no systemd in Colab)
echo "Starting MariaDB server..."
sudo mkdir -p /var/run/mysqld
sudo chown mysql:mysql /var/run/mysqld
sudo nohup mariadbd --user=mysql > /var/log/mariadb.log 2>&1 &
echo "Waiting for MariaDB to be ready..."
for i in {{1..30}}; do
    if mysqladmin -u root -prootpassword ping &> /dev/null; then
        echo "MariaDB is ready."
        break
    fi
    echo "Waiting... ($i/30)"
    sleep 1
done
if ! mysqladmin -u root -prootpassword ping &> /dev/null; then
    echo "MariaDB failed to start. Check /var/log/mariadb.log."
    cat /var/log/mariadb.log
    exit 1
fi

# Install NGINX, Redis, and other tools
echo "Installing NGINX, Redis, and utilities..."
sudo apt -y install nginx redis-server tar unzip git

# Start Redis manually
echo "Starting Redis server..."
sudo nohup redis-server > /var/log/redis.log 2>&1 &
for i in {{1..30}}; do
    if redis-cli ping &> /dev/null; then
        echo "Redis is ready."
        break
    fi
    echo "Waiting... ($i/30)"
    sleep 1
done
if ! redis-cli ping &> /dev/null; then
    echo "Redis failed to start. Check /var/log/redis.log."
    cat /var/log/redis.log
    exit 1
fi

# Install Composer
echo "Installing Composer..."
curl -sS https://getcomposer.org/installer | sudo php -- --install-dir=/usr/local/bin --filename=composer

# Step 1: Check virtualization type (Wings requirement)
echo "Checking virtualization type..."
VIRT_TYPE=$(systemd-detect-virt)
echo "Virtualization type: $VIRT_TYPE"
if [[ "$VIRT_TYPE" == *"openvz"* ]] || [[ "$VIRT_TYPE" == *"lxc"* ]]; then
    echo "Warning: OpenVZ or LXC detected. Docker may not work properly in this environment."
    echo "Colab typically uses KVM or none, so this should be fine, but double-check with your provider if running elsewhere."
else
    echo "Virtualization type is compatible with Docker."
fi

# Skip dmidecode check in Colab since /dev/mem is not available
echo "Skipping system manufacturer check (dmidecode) in Colab due to lack of /dev/mem access."
echo "This check is informational and not critical for Colab, as we already confirmed Docker compatibility."

# Step 2: Install Docker (Wings requirement)
echo "Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -sSL https://get.docker.com/ | CHANNEL=stable bash
else
    echo "Docker is already installed."
fi

# Start Docker daemon with settings suitable for Colab
echo "Starting Docker daemon..."
# Use --bridge=none to disable default bridge network creation
# Use --iptables=false and --ip6tables=false to avoid network permission issues
# Use --storage-driver=vfs to avoid overlayfs issues in Colab
sudo nohup dockerd --bridge=none --iptables=false --ip6tables=false --storage-driver=vfs > /var/log/dockerd.log 2>&1 &
sleep 10
if ! sudo docker info &> /dev/null; then
    echo "Docker failed to start. Check /var/log/dockerd.log."
    cat /var/log/dockerd.log
    exit 1
fi
echo "Docker is running."

# Step 3: Check kernel (Wings requirement)
echo "Checking kernel version..."
KERNEL_VERSION=$(uname -r)
echo "Kernel version: $KERNEL_VERSION"
if [[ "$KERNEL_VERSION" == *"-grs-ipv6-64" ]] || [[ "$KERNEL_VERSION" == *"-mod-std-ipv6-64" ]]; then
    echo "Warning: Kernel $KERNEL_VERSION may not support important Docker features."
    echo "You may need to modify your kernel. Check Pterodactyl's Kernel Modifications guide."
else
    echo "Kernel appears compatible with Docker."
fi

# Step 4: Enable swap (Wings recommendation, Colab workaround)
echo "Checking for swap support..."
sudo docker info --format '{{.LoggingDriver}}' > /tmp/docker_info.log 2>&1
if grep -q "WARNING: No swap limit support" /tmp/docker_info.log; then
    echo "Swap limit support is not enabled."
    echo "In Colab, enabling swap via GRUB is not possible due to lack of direct kernel access."
    echo "Creating a swap file as a workaround..."
    if ! [ -f /swapfile ]; then
        echo "Creating a 1GB swap file..."
        sudo fallocate -l 1G /swapfile
        sudo chmod 600 /swapfile
        sudo mkswap /swapfile
        sudo swapon /swapfile
        echo "Swap file created and enabled."
    else
        echo "Swap file already exists."
    fi
    echo "Current swap status:"
    swapon --show
else
    echo "Swap limit support is already enabled."
fi

# Set up Pterodactyl Panel
echo "Setting up Pterodactyl Panel..."
sudo mkdir -p /var/www/pterodactyl
cd /var/www/pterodactyl
sudo curl -Lo panel.tar.gz https://github.com/pterodactyl/panel/releases/latest/download/panel.tar.gz
sudo tar -xzvf panel.tar.gz
sudo chmod -R 755 storage/* bootstrap/cache/

# Generate random passwords and PRINT THEM IMMEDIATELY
DB_PASSWORD=$(openssl rand -base64 12)
ADMIN_PASSWORD=$(openssl rand -base64 12)
echo "DATABASE PASSWORD IS: $DB_PASSWORD"
echo "ADMIN PASSWORD IS: $ADMIN_PASSWORD"
echo "WRITE THIS DOWN NOW - ADMIN PASSWORD: $ADMIN_PASSWORD"

# Configure MariaDB
echo "Configuring MariaDB database and user..."
sudo mariadb -u root -prootpassword <<EOF
CREATE USER 'pterodactyl'@'127.0.0.1' IDENTIFIED BY '$DB_PASSWORD';
CREATE DATABASE panel;
GRANT ALL PRIVILEGES ON panel.* TO 'pterodactyl'@'127.0.0.1';
FLUSH PRIVILEGES;
EOF

# Verify database user
echo "Verifying database user..."
if ! mysql -u pterodactyl -p"$DB_PASSWORD" -h 127.0.0.1 panel -e "SHOW TABLES;" &> /dev/null; then
    echo "Failed to connect with pterodactyl user."
    mysql -u pterodactyl -p"$DB_PASSWORD" -h 127.0.0.1 panel -e "SHOW TABLES;"
    exit 1
fi

# Create .env file with valid temporary APP_KEY
echo "Creating .env file with temporary APP_KEY..."
sudo tee .env > /dev/null <<EOF
APP_ENV=production
APP_KEY=base64:MTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTI=
APP_URL=http://127.0.0.1
DB_HOST=127.0.0.1
DB_PORT=3306
DB_DATABASE=panel
DB_USERNAME=pterodactyl
DB_PASSWORD=$DB_PASSWORD
CACHE_DRIVER=redis
SESSION_DRIVER=redis
QUEUE_CONNECTION=redis
REDIS_HOST=127.0.0.1
REDIS_PASSWORD=null
REDIS_PORT=6379
EOF

# Install PHP dependencies
echo "Installing PHP dependencies..."
COMPOSER_ALLOW_SUPERUSER=1 sudo composer install --no-dev --optimize-autoloader

# Regenerate secure APP_KEY
echo "Regenerating secure application key..."
sudo php artisan key:generate --force

# Verify .env
echo "Verifying .env file contents..."
cat .env

# Clear Laravel caches
echo "Clearing Laravel caches..."
sudo php artisan config:clear
sudo php artisan cache:clear

# Run database migrations
echo "Running database migrations..."
sudo php artisan migrate --seed --force

# Create admin user with the password
echo "Creating admin user..."
sudo php artisan p:user:make --email="admin@example.com" --username="admin" --name-first="Admin" --name-last="User" --password="$ADMIN_PASSWORD" --admin=1
echo "Admin user created with password: $ADMIN_PASSWORD"

# Set permissions for Nginx
sudo chown -R www-data:www-data /var/www/pterodactyl/*

# Start PHP-FPM explicitly
echo "Starting PHP-FPM..."
sudo mkdir -p /run/php
sudo nohup php-fpm8.3 -F > /var/log/php-fpm.log 2>&1 &
sleep 5
if ! ps aux | grep -q "[p]hp-fpm8.3"; then
    echo "PHP-FPM failed to start. Check /var/log/php-fpm.log."
    cat /var/log/php-fpm.log
    exit 1
fi
echo "PHP-FPM is running."

# Configure Nginx
echo "Configuring Nginx..."
sudo rm -f /etc/nginx/sites-enabled/default
sudo tee /etc/nginx/sites-available/pterodactyl.conf > /dev/null <<EOL
server {{
    listen 80;
    server_name _;
    root /var/www/pterodactyl/public;
    index index.php;
    access_log /var/log/nginx/pterodactyl.app-access.log;
    error_log /var/log/nginx/pterodactyl.app-error.log error;
    client_max_body_size 100m;
    client_body_timeout 120s;
    sendfile off;
    location / {{
        try_files \$uri \$uri/ /index.php?\$query_string;
    }}
    location ~ \.php\$ {{
        fastcgi_pass unix:/run/php/php8.3-fpm.sock;
        fastcgi_index index.php;
        include fastcgi_params;
        fastcgi_param PHP_VALUE "upload_max_filesize = 100M \\n post_max_size=100M";
        fastcgi_param SCRIPT_FILENAME \$document_root\$fastcgi_script_name;
        fastcgi_param HTTP_PROXY "";
        fastcgi_intercept_errors off;
        fastcgi_buffer_size 16k;
        fastcgi_buffers 4 16k;
        fastcgi_connect_timeout 300;
        fastcgi_send_timeout 300;
        fastcgi_read_timeout 300;
    }}
    location ~ /\.ht {{
        deny all;
    }}
}}
EOL
sudo ln -sf /etc/nginx/sites-available/pterodactyl.conf /etc/nginx/sites-enabled/pterodactyl.conf
sudo nginx -t
if [ $? -ne 0 ]; then
    echo "Nginx config test failed."
    exit 1
fi
sudo nohup nginx -g "daemon off;" > /var/log/nginx.log 2>&1 &
sleep 5
if ! ps aux | grep -q "[n]ginx"; then
    echo "Nginx failed to start. Check /var/log/nginx.log."
    cat /var/log/nginx.log
    exit 1
fi
echo "Nginx is running."

# Test local access
echo "Testing local access..."
curl -s http://127.0.0.1 > /tmp/local_test.html
if ! grep -q "html" /tmp/local_test.html; then
    echo "Local test failed. Check Nginx and PHP-FPM logs."
    cat /var/log/nginx/pterodactyl.app-error.log
    cat /var/log/php-fpm.log
    exit 1
fi
echo "Local access confirmed."

# Install and configure Ngrok
echo "Setting up Ngrok..."
wget https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz
tar -xzf ngrok-v3-stable-linux-amd64.tgz
sudo mv ngrok /usr/local/bin/
echo "Ngrok version:"
/usr/local/bin/ngrok --version
sudo mkdir -p /root/.config/ngrok
sudo tee /root/.config/ngrok/ngrok.yml > /dev/null <<EOF
version: 2
authtoken: {ngrok_token}
EOF
echo "Starting Ngrok..."
/usr/local/bin/ngrok http 80 > ngrok.log 2>&1 &
sleep 20
PUBLIC_URL=$(curl -s http://127.0.0.1:4040/api/tunnels | jq -r '.tunnels[0].public_url')
if [ -z "$PUBLIC_URL" ]; then
    echo "Failed to retrieve Ngrok URL. Check ngrok.log:"
    cat ngrok.log
    exit 1
fi
echo "Ngrok URL retrieved: $PUBLIC_URL"
sudo tee -a .env > /dev/null <<EOF
APP_URL=$PUBLIC_URL
EOF

# Start queue worker
echo "Starting queue worker..."
sudo nohup php artisan queue:work --queue=high,standard,low --sleep=3 --tries=3 > /var/log/pteroq.log 2>&1 &
echo "Queue worker started."

# Set up cron job (non-critical, continue on failure)
echo "Setting up cron job..."
if command -v crontab &> /dev/null; then
    (crontab -l 2>/dev/null; echo "* * * * * php /var/www/pterodactyl/artisan schedule:run >> /dev/null 2>&1") | crontab -
    echo "Cron job set."
else
    echo "crontab not found, skipping cron job setup."
fi

# Debug: Verify panel is accessible
echo "Debug: Testing panel accessibility at $PUBLIC_URL..."
curl -s -o /tmp/panel_test.html "$PUBLIC_URL"
if ! grep -q "html" /tmp/panel_test.html; then
    echo "Panel is not accessible at $PUBLIC_URL. Check logs."
    cat /var/log/nginx/pterodactyl.app-error.log
    cat /var/log/php-fpm.log
    exit 1
fi
echo "Panel is accessible."

# Step 5: Set up directory structure and download Wings (Wings requirement)
echo "Setting up directory structure for Wings..."
sudo mkdir -p /etc/pterodactyl /var/lib/pterodactyl/volumes /var/log/pterodactyl
if [ ! -d "/etc/pterodactyl" ]; then
    echo "Failed to create /etc/pterodactyl directory."
    exit 1
fi
echo "Downloading Wings executable..."
ARCH=$([[ "$(uname -m)" == "x86_64" ]] && echo "amd64" || echo "arm64")
curl -L -o /usr/local/bin/wings "https://github.com/pterodactyl/wings/releases/latest/download/wings_linux_$ARCH"
sudo chmod u+x /usr/local/bin/wings
if [ -f /usr/local/bin/wings ]; then
    echo "Wings executable downloaded and permissions set."
else
    echo "Failed to download Wings executable."
    exit 1
fi

# Step 6: Configure Wings to use host network mode (to avoid network creation issues)
echo "Configuring Wings to use host network mode..."
sudo tee /etc/pterodactyl/config.yml > /dev/null <<EOF
docker:
  network:
    mode: host
    interface: pterodactyl0
    create_interface: false
EOF
echo "Wings configuration updated to use host network mode."

# Final output
echo "Setup complete!"
echo "-----------------------------------------"
echo "Pterodactyl Panel is accessible at: $PUBLIC_URL"
echo "Admin email: admin@example.com"
echo "YOUR ADMIN PASSWORD IS: $ADMIN_PASSWORD"
echo "Database password: $DB_PASSWORD"
echo "AGAIN, YOUR ADMIN PASSWORD IS: $ADMIN_PASSWORD"
echo "-----------------------------------------"
echo "Wings is ready to be configured and started."
echo "You can now create a new cell to configure and start Wings, and set up nodes/API keys."
echo "Example command to configure Wings:"
echo "cd /etc/pterodactyl && sudo wings configure --panel-url $PUBLIC_URL --token <your_node_token> --node <node_id>"
echo "Then create another cell to start Wings in the foreground."
echo "-----------------------------------------"
"""

# Write and execute
with open("install_pterodactyl.sh", "w") as f:
    f.write(script)
!bash install_pterodactyl.sh
