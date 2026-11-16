#!/bin/bash
# Valheim Mod Manager - installer for the server PC (SteamOS, rootless Podman)
#
# Installs the web page for managing the Valheim server's mods (see
# mod-manager/README.md) as a container next to AMP, the same way AMP itself
# runs: everything lives under /home, so SteamOS updates never wipe it.
#
# Run in SteamOS desktop mode (Konsole), from the folder this file is in.
# The mod-manager/ folder must be next to it:
#     bash install-mod-manager.sh
#
# Safe to re-run: that's also how the app gets updated (get the new files,
# re-run this). Accounts and settings are kept in ~/valheim-modmgr-data.
#
# Finds two things by itself, so nothing needs editing first:
#   - the Valheim server's BepInEx folder inside ~/amp-data
#   - which user owns the files in it, so files the app writes stay readable
#     by AMP/Valheim (the container writes as that same user)

set -e

# =============================================
SRC_DIR="$(cd "$(dirname "$0")" && pwd)/mod-manager"
APP_DIR="$HOME/valheim-modmgr"              # copy of the app's code; the container image is built from here
DATA_DIR="$HOME/valheim-modmgr-data"        # accounts, audit log — survives re-runs and SteamOS updates
AMP_DATA="$HOME/amp-data"                   # created by setup-amp-container-steamos.sh
CONTAINER="valheim-modmgr"
IMAGE="localhost/valheim-modmgr:latest"
PORT=8095                                   # 8080/8081 are taken on SteamOS and 8090 is AMP; 8082-8099 were free on 2026-10-03
URL="http://localhost:$PORT"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
# =============================================

fail() {
    echo ""
    echo "  ERROR: $1"
    echo "  Nothing else was changed. Take a photo of this screen for whoever is helping you."
    echo ""
    exit 1
}

echo "=== Valheim Mod Manager setup ==="
echo ""

# --- Step 1: Basics ---
echo "[1/7] Checking this PC..."
[[ $EUID -ne 0 ]] || fail "Don't run this with sudo. Just: bash install-mod-manager.sh"
command -v podman &>/dev/null || fail "podman not found. Is this the PC that runs AMP?"
[[ -f "$SRC_DIR/app.py" && -f "$SRC_DIR/Containerfile" ]] || \
    fail "The mod-manager folder isn't next to this script. Unzip the whole package, then run it from that folder."
podman container exists amp-server || fail "AMP's container (amp-server) wasn't found. Is AMP set up on this PC?"
echo "  OK: $(podman --version), AMP container found"

# --- Step 2: Find the server's BepInEx folder ---
# podman unshare: AMP's files are owned by the container's user, which the
# normal deck account may not be able to look inside. unshare looks as that
# user. The path is normally
# ~/amp-data/.ampdata/instances/Valheim01/Valheim/896660/BepInEx
echo ""
echo "[2/7] Finding the Valheim server's BepInEx folder..."
mapfile -t FOUND < <(podman unshare find "$AMP_DATA/.ampdata/instances" -maxdepth 5 -type d \
    -path '*/896660/BepInEx' 2>/dev/null | sort)
if [[ ${#FOUND[@]} -eq 0 ]]; then
    fail "No BepInEx folder found under $AMP_DATA/.ampdata/instances.
  In AMP: Valheim instance -> Configuration -> Updates -> tick 'Install BepInEx',
  click Update, start the server once, then run this again."
elif [[ ${#FOUND[@]} -eq 1 ]]; then
    BEPINEX="${FOUND[0]}"
else
    echo "  More than one Valheim server found:"
    for i in "${!FOUND[@]}"; do echo "    $((i + 1))) ${FOUND[$i]}"; done
    read -r -p "  Type the number of the one to manage and press Enter: " pick
    [[ "$pick" =~ ^[0-9]+$ && $pick -ge 1 && $pick -le ${#FOUND[@]} ]] || fail "Not a valid choice."
    BEPINEX="${FOUND[$((pick - 1))]}"
fi
podman unshare test -d "$BEPINEX/plugins" || \
    fail "$BEPINEX has no plugins folder. Start the Valheim server once in AMP so BepInEx creates it, then re-run."
echo "  Found: $BEPINEX"

# --- Step 3: Who owns the mod files ---
# Numbers as seen inside containers (e.g. 1000:1000). The app's container
# runs as this same user, so AMP can read and replace whatever the app writes.
echo ""
echo "[3/7] Checking who owns the mod files..."
OWNER=$(podman unshare stat -c '%u:%g' "$BEPINEX/plugins")
[[ "$OWNER" =~ ^[0-9]+:[0-9]+$ ]] || fail "Couldn't read the owner of $BEPINEX/plugins (got '$OWNER')."
echo "  Owner: $OWNER (the app will write files as this user)"

# --- Step 4: Remove an older copy, check the port ---
echo ""
echo "[4/7] Checking port $PORT..."
if podman container exists "$CONTAINER"; then
    echo "  Replacing the existing Mod Manager (accounts and settings are kept)."
    podman rm -f "$CONTAINER" >/dev/null
fi
if ss -ltnH "sport = :$PORT" | grep -q .; then
    fail "Something else is already using port $PORT:
$(ss -ltnpH "sport = :$PORT")"
fi
echo "  Port $PORT is free"

# --- Step 5: Build the app ---
# Downloads a small official Python image plus 4 libraries the first time
# (a few minutes on the first run, quicker on re-runs).
echo ""
echo "[5/7] Building the app (first time takes a few minutes)..."
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SRC_DIR/app.py" "$SRC_DIR/mods.py" "$SRC_DIR/requirements.txt" "$SRC_DIR/Containerfile" \
      "$SRC_DIR/templates" "$SRC_DIR/static" "$APP_DIR/"
podman build -q -t "$IMAGE" -f "$APP_DIR/Containerfile" "$APP_DIR" >/dev/null || fail "Building the app failed (see the messages above). Is the internet connected?"
echo "  Built $IMAGE"

# --- Step 6: Start it ---
# --network host: the app listens on 127.0.0.1:$PORT of this PC itself, so it's
#   reachable from this PC's browser (and later from Tailscale on this PC), but
#   not from other computers on the network or the internet.
# --restart=always + podman-restart.service: starts by itself after a reboot,
#   the same way AMP does.
echo ""
echo "[6/7] Starting the Mod Manager..."
mkdir -p "$DATA_DIR"
podman unshare chown -R "$OWNER" "$DATA_DIR"
podman run -d \
    --name "$CONTAINER" \
    --restart=always \
    --network host \
    --user "$OWNER" \
    -v "$BEPINEX:/bepinex" \
    -v "$DATA_DIR:/data" \
    -e MODMGR_PORT=$PORT \
    "$IMAGE" >/dev/null
loginctl enable-linger "$(whoami)"
systemctl --user enable --now podman-restart.service >/dev/null 2>&1 || true

for _ in $(seq 1 30); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then READY=1; break; fi
    sleep 1
done
[[ -n "$READY" ]] || fail "The Mod Manager didn't start. Its log:
$(podman logs --tail 30 "$CONTAINER" 2>&1)"
echo "  Running at $URL"

# --- Step 7: Desktop icon ---
echo ""
echo "[7/7] Adding a 'Valheim Mods' icon to the desktop..."
if [[ -x "$HOME/valheim-status/control.sh" ]]; then
    # The Valheim Server desktop panel (install-valheim-panel.sh) has an
    # "Open Mods" button, so it replaced this icon.
    echo "  Skipped: the Valheim Server panel's 'Open Mods' button does this."
else
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/valheim-mods.desktop" <<DESKTOP_EOF
[Desktop Entry]
Type=Application
Name=Valheim Mods
Comment=Manage the Valheim server's mods
Exec=xdg-open $URL
Icon=applications-games
Terminal=false
DESKTOP_EOF
chmod +x "$DESKTOP_DIR/valheim-mods.desktop"
echo "  Added. The first time you double-click it, choose to run it (Execute)."
fi

# --- Done ---
TOKEN=$(podman unshare cat "$DATA_DIR/setup-token.txt" 2>/dev/null || true)
echo ""
echo "======================================="
echo "  Done"
echo "======================================="
echo ""
echo "  Mod Manager : $URL  (or the 'Valheim Mods' desktop icon)"
echo "  Mods folder : $BEPINEX"
echo "  Data        : $DATA_DIR (accounts — survives SteamOS updates)"
echo ""
if [[ -n "$TOKEN" ]]; then
    echo "  FIRST-TIME SETUP — open $URL and enter this setup token:"
    echo ""
    echo "      $TOKEN"
    echo ""
    echo "  Then create your account, and scan the QR code with an"
    echo "  authenticator app on your phone (Microsoft Authenticator, Google"
    echo "  Authenticator, etc.)."
else
    echo "  Accounts already exist; sign in as usual."
fi
echo ""
