# Define the bash script to start Wings in the foreground and get the IP address
script = """#!/bin/bash

# Get the IP address of the Colab instance
echo "Getting the IP address of the Colab instance..."
IP_ADDRESS=$(hostname -I | awk '{print $1}')
if [ -n "$IP_ADDRESS" ]; then
    echo "IP Address: $IP_ADDRESS"
else
    echo "Failed to retrieve IP address."
fi

# Ensure the log directory exists (for Wings logs, though foreground logs go to terminal)
sudo mkdir -p /var/log/pterodactyl

# Start Wings in the foreground
echo "Starting Wings in the foreground..."
echo "Wings will keep running, and this cell will remain active until you stop it manually (click the stop button in Colab)."
echo "To stop Wings, click the stop button in Colab, or the cell will stop when the Colab instance resets (e.g., due to inactivity)."
sudo wings
# Note: The script will not proceed past this point because sudo wings runs in the foreground and doesn't exit.
# The cell will remain running as long as Wings is active.
"""

# Write and execute
with open("start_wings_foreground.sh", "w") as f:
    f.write(script)
!bash start_wings_foreground.sh
