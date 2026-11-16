#!/bin/bash
# Tailscale for remote admin - installer for the server PC (SteamOS, rootless Podman)
#
# Lets a remote admin reach the AMP panel and the Valheim Mods page from
# anywhere over HTTPS, without either one being open to the internet:
#     https://<TS_NAME>.<tailnet>.ts.net/        -> AMP panel      (127.0.0.1:8090)
#     https://<TS_NAME>.<tailnet>.ts.net:8443/   -> Valheim Mods   (127.0.0.1:8095)
# Only devices signed into the admin's tailnet can open those addresses.
# Players don't use Tailscale; Valheim still uses the normal UDP 2456-2458
# router forward.
#
# Runs in a container, like AMP, so SteamOS updates never wipe it (SteamOS
# has no official Tailscale package). It uses Tailscale's own image and
# "userspace" mode: only the two pages above are reachable through Tailscale,
# not the rest of this PC.
#
# Run in SteamOS desktop mode (Konsole), from the folder this file is in:
#     bash install-tailscale.sh
# or, to pick this PC's name in the tailnet:
#     TS_NAME=my-valheim bash install-tailscale.sh
# Safe to re-run; the login is kept in ~/tailscale-data.
#
# BEFORE RUNNING (the tailnet owner, in https://login.tailscale.com/admin/dns):
# turn on MagicDNS and HTTPS Certificates.
#
# During the run it shows a sign-in link. Whoever is at this PC sends it to
# the tailnet owner, who opens it (a private/incognito window helps if they
# use several accounts) and approves it. That adds this PC to their tailnet.
# No keys or passwords are stored in this script.

set -e

# =============================================
IMAGE="docker.io/tailscale/tailscale:stable"   # Tailscale's official image; confirmed on Docker Hub 2026-10-20
CONTAINER="tailscale"
TS_NAME="${TS_NAME:-valheim-server}"          # this PC's name in the tailnet (part of the https address)
STATE_DIR="$HOME/tailscale-data"              # the login lives here — survives re-runs and SteamOS updates
AMP_PORT=8090                                 # AMP panel on this PC (setup-amp-container-steamos.sh)
MODS_PORT=8095                                # Valheim Mods page (install-mod-manager.sh)
MODS_HTTPS_PORT=8443
LOGIN_WAIT_MINUTES=15
# =============================================

fail() {
    echo ""
    echo "  ERROR: $1"
    echo "  Take a photo of this screen for whoever is helping you."
    echo ""
    exit 1
}

ts() { podman exec "$CONTAINER" tailscale "$@"; }

# Prints one "Key": "value" string field from `tailscale status --json` (first match).
status_field() {
    ts status --json 2>/dev/null | grep -m1 -o "\"$1\": *\"[^\"]*\"" | sed -E 's/.*: *"(.*)"/\1/'
}

echo "=== Tailscale (remote admin) setup ==="
echo ""

# --- Step 1: Basics ---
echo "[1/5] Checking this PC..."
[[ $EUID -ne 0 ]] || fail "Don't run this with sudo. Just: bash install-tailscale.sh"
command -v podman &>/dev/null || fail "podman not found. Is this the PC that runs AMP?"
podman container exists amp-server || fail "AMP's container (amp-server) wasn't found."
if ! podman container exists valheim-modmgr; then
    echo "  Note: the Valheim Mods page isn't installed yet. Its address will start"
    echo "  working once install-mod-manager.sh has been run."
fi
echo "  OK: $(podman --version)"

# --- Step 2: Start Tailscale ---
# --network host: Tailscale needs to reach the two pages on 127.0.0.1.
# TS_AUTH_ONCE: after the first sign-in, restarts reuse the saved login.
echo ""
echo "[2/5] Starting Tailscale (downloads it the first time)..."
mkdir -p "$STATE_DIR"
if podman container exists "$CONTAINER"; then
    echo "  Replacing the existing Tailscale container (the login is kept)."
    podman rm -f "$CONTAINER" >/dev/null
fi
podman pull -q "$IMAGE" >/dev/null || fail "Couldn't download Tailscale. Is the internet connected?"
podman run -d \
    --name "$CONTAINER" \
    --restart=always \
    --network host \
    -v "$STATE_DIR:/var/lib/tailscale" \
    -e TS_STATE_DIR=/var/lib/tailscale \
    -e TS_USERSPACE=true \
    -e TS_HOSTNAME="$TS_NAME" \
    -e TS_AUTH_ONCE=true \
    "$IMAGE" >/dev/null
loginctl enable-linger "$(whoami)"
systemctl --user enable --now podman-restart.service >/dev/null 2>&1 || true

# --- Step 3: Sign in (the tailnet owner approves) ---
echo ""
echo "[3/5] Connecting to the tailnet..."
SHOWN=""
for _ in $(seq 1 $((LOGIN_WAIT_MINUTES * 30))); do
    STATE=$(status_field BackendState)
    [[ "$STATE" == "Running" ]] && break
    URL=$(status_field AuthURL)
    if [[ -n "$URL" && "$URL" != "$SHOWN" ]]; then
        echo ""
        echo "  >>> Send this link to the tailnet owner to approve: <<<"
        echo ""
        echo "      $URL"
        echo ""
        echo "  Waiting for approval (up to $LOGIN_WAIT_MINUTES minutes)..."
        SHOWN="$URL"
    fi
    sleep 2
done
[[ "$STATE" == "Running" ]] || fail "Tailscale wasn't approved within $LOGIN_WAIT_MINUTES minutes (state: ${STATE:-unknown}).
  Just run this script again to get a fresh link."
DNS_NAME=$(status_field DNSName)
DNS_NAME="${DNS_NAME%.}"
echo "  Connected as $DNS_NAME"

# --- Step 4: Publish the two pages over HTTPS (tailnet only) ---
# --bg keeps these settings saved, so they come back after restarts.
# timeout: if HTTPS Certificates aren't enabled for the tailnet yet, serve
# would otherwise sit waiting for someone to enable them.
echo ""
echo "[4/5] Setting up HTTPS for the AMP panel and the Mods page..."
timeout 120 podman exec "$CONTAINER" tailscale serve --bg --https=443 "http://127.0.0.1:$AMP_PORT" >/dev/null || \
    fail "Couldn't set up HTTPS. In the Tailscale admin console (DNS page), turn on
  MagicDNS and HTTPS Certificates, then run this script again."
timeout 120 podman exec "$CONTAINER" tailscale serve --bg --https=$MODS_HTTPS_PORT "http://127.0.0.1:$MODS_PORT" >/dev/null || \
    fail "Couldn't set up HTTPS for the Mods page (port $MODS_HTTPS_PORT)."
echo "  Done"

# --- Step 5: Summary ---
echo ""
echo "[5/5] Current settings:"
ts serve status 2>/dev/null | sed 's/^/  /'

echo ""
echo "======================================="
echo "  Done"
echo "======================================="
echo ""
echo "  From a device on the same tailnet (with Tailscale on), open:"
echo "    AMP panel     : https://$DNS_NAME/"
echo "    Valheim Mods  : https://$DNS_NAME:$MODS_HTTPS_PORT/"
echo ""
echo "  On this PC, keep using http://localhost:8090 and http://localhost:8095."
echo ""
echo "  Key expiry: by default this PC's Tailscale key expires in about 180 days."
echo "  Either note the date and renew before it (on this PC:"
echo "  podman exec tailscale tailscale up --force-reauth), or turn it off in the"
echo "  admin console (Machines -> '$TS_NAME' -> Disable key expiry)."
echo "  See docs/3-remote-admin-tailscale.md."
echo ""
