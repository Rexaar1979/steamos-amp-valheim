#!/bin/bash
# AMP (CubeCoders) in a rootless Podman container on SteamOS - survives OS updates
#
# WHY A CONTAINER: SteamOS's root filesystem is a read-only A/B image that is
# swapped wholesale on every update, so anything installed with pacman into
# /usr is wiped. Rootless Podman (preinstalled on SteamOS 3.5+) keeps
# everything under /home, which updates never touch. So the AMP install,
# every instance and every world save survive SteamOS updates, with no
# reinstall step.
#
# Run in SteamOS Desktop Mode (Konsole), not Gaming Mode:
#     bash setup-amp-container-steamos.sh
# It asks for the AMP admin password (nothing is stored in this file).
#
# What it does NOT do:
#   - Enter the AMP license key. AMP is commercial software; buy a license
#     from CubeCoders and enter it after the first login (Configuration ->
#     Licensing). The container image has no setting for it.
#   - Create the game server. Do that in the AMP web panel afterwards
#     (see docs/2-valheim-instance.md).
#
# The image is the community-maintained AMP-dockerized project
# (https://github.com/MitchTalmadge/AMP-dockerized). CubeCoders does not
# publish an official image for AMP itself.

set -e

# =============================================
# Settings (override any of these as environment variables)
# =============================================
AMP_IMAGE="${AMP_IMAGE:-docker.io/mitchtalmadge/amp-dockerized:latest}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
AMP_WEB_PORT=8080                    # port INSIDE the container
HOST_PANEL_PORT="${HOST_PANEL_PORT:-8090}"
# On SteamOS, 127.0.0.1:8080 is held by the Steam client (steamwebhelper) and
# 8081 by a system service, so the panel is published on 8090 instead.
BIND_ADDR="${BIND_ADDR:-127.0.0.1}"
# BIND_ADDR is where the panel and SFTP ports listen. 127.0.0.1 = this PC only
# (Tailscale still reaches them). 0.0.0.0 would open them to every device on
# your home network. The Valheim game ports always listen on all addresses.
DATA_DIR="${DATA_DIR:-$HOME/amp-data}"            # the entire AMP install lives here
MAC_FILE="$HOME/.config/amp-container-mac"        # pinned MAC, see step 4
# =============================================

echo "=== AMP-in-a-container setup for SteamOS ==="
echo ""

# --- Step 1: Podman ---
echo "[1/6] Checking for Podman..."
if command -v podman &>/dev/null; then
    echo "  Found $(podman --version). SteamOS 3.5+ ships it in the base image."
else
    echo ""
    echo "  ERROR: podman not found."
    echo "  SteamOS 3.5+ ships Podman preinstalled; check Settings > System for your"
    echo "  version. On older builds, Distrobox documents a Podman install under"
    echo "  \$HOME that also survives updates: https://distrobox.it/posts/steamdeck_guide/"
    exit 1
fi

if ! podman container exists amp-server; then
    echo ""
    echo "  Choose the password for the AMP admin account '$ADMIN_USERNAME'."
    echo "  (Use a long, unique one. AMP stores it; this script does not.)"
    while true; do
        read -r -s -p "  Password: " ADMIN_PASSWORD; echo
        read -r -s -p "  Again   : " AGAIN; echo
        if [[ "$ADMIN_PASSWORD" != "$AGAIN" ]]; then
            echo "  They don't match, try again."
        elif [[ ${#ADMIN_PASSWORD} -lt 12 ]]; then
            echo "  Please use at least 12 characters."
        else
            break
        fi
    done
    unset AGAIN
fi

# --- Step 2: Lingering ---
# Rootless systemd --user services don't start at boot unless the account
# has an active login session; lingering starts them regardless.
echo ""
echo "[2/6] Enabling lingering for $(whoami)..."
loginctl enable-linger "$(whoami)"

# --- Step 3: Persistent data directory ---
echo ""
echo "[3/6] Creating the data folder..."
mkdir -p "$DATA_DIR"
echo "  $DATA_DIR - panel, every instance, world saves, license state."

# --- Step 4: Pin a MAC address ---
# AMP ties its license activation to the machine's MAC address. Recreating
# the container (for example after pulling a newer image) would otherwise
# get a new random MAC and reset the activation.
echo ""
echo "[4/6] Pinning a container MAC address..."
mkdir -p "$(dirname "$MAC_FILE")"
if [[ ! -f "$MAC_FILE" ]]; then
    printf '02:%02x:%02x:%02x:%02x:%02x\n' \
        $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256)) > "$MAC_FILE"
fi
CONTAINER_MAC=$(cat "$MAC_FILE")
echo "  Using $CONTAINER_MAC (saved in $MAC_FILE and reused on every re-run)"

# --- Step 5: Run the container ---
# --network bridge: rootless Podman 5 defaults to "pasta" networking, which
# rejects --mac-address ("static ip/mac address can only be used with Bridge
# mode networking"). Seen with Podman 5.5.2.
echo ""
echo "[5/6] Starting the AMP container (downloads it the first time)..."
if podman container exists amp-server; then
    echo "  Container 'amp-server' already exists - leaving it as is."
    echo "  To pick up a newer image: 'podman rm -f amp-server', then re-run this"
    echo "  script (the MAC above is reused and $DATA_DIR is untouched)."
    if podman port amp-server 2>/dev/null | grep -Eq "/tcp -> (0\.0\.0\.0|\[::\]):"; then
        echo "  NOTE: its panel/SFTP ports are open to your whole home network."
        echo "  Recreating it as above limits them to $BIND_ADDR."
    fi
else
    podman run -d \
        --name amp-server \
        --restart=always \
        --network bridge \
        --mac-address "$CONTAINER_MAC" \
        -p "$BIND_ADDR:$HOST_PANEL_PORT:$AMP_WEB_PORT" \
        -p 2456-2458:2456-2458/udp \
        -p "$BIND_ADDR:2224:2224" \
        -v "$DATA_DIR:/home/amp" \
        -e PORT=$AMP_WEB_PORT \
        -e USERNAME="$ADMIN_USERNAME" \
        -e PASSWORD="$ADMIN_PASSWORD" \
        -e AMP_AUTO_UPDATE=true \
        "$AMP_IMAGE" >/dev/null
    unset ADMIN_PASSWORD
    # Note: the image takes the first password as an environment variable, so
    # it stays readable in `podman inspect amp-server` on this PC. Change it
    # in AMP after the first sign-in if that matters to you.
fi

# --- Step 6: Survive reboots ---
echo ""
echo "[6/6] Enabling podman-restart.service..."
systemctl --user enable --now podman-restart.service

echo ""
echo "======================================="
echo "  Done"
echo "======================================="
echo ""
echo "  AMP panel : http://localhost:$HOST_PANEL_PORT  (listens on $BIND_ADDR)"
echo "  Login     : $ADMIN_USERNAME / the password you just chose"
echo "  Data      : $DATA_DIR (survives SteamOS updates)"
echo ""
echo "  Next:"
echo "    1. Sign in and enter your AMP license key (Configuration -> Licensing)."
echo "    2. Create the game server instance (docs/2-valheim-instance.md)."
echo "    3. On the router, forward ONLY UDP 2456-2458 to this PC for players."
echo "       Don't forward the panel ($HOST_PANEL_PORT) or SFTP (2224) to the"
echo "       internet; use install-tailscale.sh for remote admin instead."
echo ""
