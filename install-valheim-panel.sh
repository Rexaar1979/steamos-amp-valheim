#!/bin/bash
# One-time installer: the "Valheim Server" panel on the server PC's SteamOS desktop
# (Desktop Mode, Plasma 6). The panel stays on the desktop and shows, without
# any clicking:
#   - whether the Valheim server is running
#   - who has AMP or the mod manager open over Tailscale, by username
#   - who is in the game, split into admins and players
# and has buttons: Open AMP, Open Mods, Restart server, Stop/Start server.
# It replaces the old "Valheim Mods" and "AMP Server Control" desktop icons.
#
# Run once in Konsole, from the folder this file is in (the valheim-panel
# folder must be next to it):
#     bash install-valheim-panel.sh
#
# What it creates (all in the home folder, so SteamOS updates don't touch it):
#   ~/valheim-status/                     - checker, button script, settings, last status
#   ~/.config/systemd/user/valheim-status.{service,timer} - runs a check every 30 s
#   ~/.local/share/plasma/plasmoids/org.valheimpanel.server - the panel itself
#
# No passwords are stored. It reads:
#   - Tailscale's traffic counters (an open AMP page sends data every few
#     seconds; a closed one sends nothing - measured 2026-11-16)
#   - AMP's own logs, for the username of whoever signs in
#   - the mod manager's presence.json, for who is signed in there
#   - the Valheim server's player-count query on 127.0.0.1:2457
#   - the server log (BepInEx/LogOutput.log) for player names
#   - the server's adminlist.txt, to tell admins from players
#
# Safe to re-run (that's also how to update it). To remove it:
#     systemctl --user disable --now valheim-status.timer
#     kpackagetool6 -t Plasma/Applet --remove org.valheimpanel.server
#     rm -r ~/valheim-status ~/.config/systemd/user/valheim-status.*
# then right-click the panel on the desktop and remove it.

set -e

PKG_DIR="$(cd "$(dirname "$0")" && pwd)/valheim-panel"
DIR="$HOME/valheim-status"
UNIT_DIR="$HOME/.config/systemd/user"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
AMP_DATA="$HOME/amp-data"
PLASMOID_ID="org.valheimpanel.server"
QUERY_PORT=2457

fail() { echo ""; echo "ERROR: $1"; exit 1; }

echo "=== Installing the Valheim Server panel ==="

# --- Step 1: Checks ---
echo ""
echo "[1/7] Checking this PC..."
[[ -f "$PKG_DIR/checker/valheim-status.py" && -f "$PKG_DIR/plasmoid/metadata.json" ]] || \
    fail "The valheim-panel folder isn't next to this script. Unzip the whole package, then run it from that folder."
command -v podman &>/dev/null || fail "podman not found - run this on the PC that hosts AMP."
podman container exists amp-server || fail "AMP's container (amp-server) wasn't found. Is AMP set up on this PC?"
if podman container exists tailscale; then
    echo "  OK: Tailscale container found"
else
    echo "  WARNING: no 'tailscale' container - remote sign-ins will show as 'unknown'."
fi
if command -v python3 &>/dev/null; then
    echo "  OK: python3 found ($(python3 --version 2>&1))"
elif podman container exists valheim-modmgr; then
    echo "  OK: no python3 on this PC, will use the mod manager's"
else
    fail "Neither python3 nor the mod manager container (valheim-modmgr) was found."
fi
if command -v kpackagetool6 &>/dev/null; then
    echo "  OK: Plasma 6 found"
else
    echo "  NOTE: kpackagetool6 not found - will copy the panel into place by hand."
fi

# --- Step 2: Find the server's log and admin list ---
echo ""
echo "[2/7] Finding the Valheim server's files..."
mapfile -t FOUND < <(podman unshare find "$AMP_DATA/.ampdata/instances" -maxdepth 5 -type d \
    -path '*/896660/BepInEx' 2>/dev/null | sort)
if [[ ${#FOUND[@]} -eq 0 ]]; then
    fail "No BepInEx folder found under $AMP_DATA/.ampdata/instances."
elif [[ ${#FOUND[@]} -eq 1 ]]; then
    BEPINEX="${FOUND[0]}"
else
    echo "  More than one Valheim server found:"
    for i in "${!FOUND[@]}"; do echo "    $((i + 1))) ${FOUND[$i]}"; done
    read -r -p "  Type the number of the one to watch and press Enter: " pick
    [[ "$pick" =~ ^[0-9]+$ && $pick -ge 1 && $pick -le ${#FOUND[@]} ]] || fail "Not a valid choice."
    BEPINEX="${FOUND[$((pick - 1))]}"
fi
echo "  Server log : $BEPINEX/LogOutput.log"
# adminlist.txt lives in the same AMP instance (its saves folder)
INSTANCE="${BEPINEX%%/Valheim/896660/BepInEx}"
[[ "$INSTANCE" != "$BEPINEX" ]] || INSTANCE="$(dirname "$(dirname "$(dirname "$BEPINEX")")")"
mapfile -t LISTS < <(podman unshare find "$INSTANCE" -maxdepth 6 -name adminlist.txt 2>/dev/null)
ADMINLIST=""
for f in "${LISTS[@]}"; do [[ "$f" == */saves/* ]] && { ADMINLIST="$f"; break; }; done
[[ -n "$ADMINLIST" || ${#LISTS[@]} -eq 0 ]] || ADMINLIST="${LISTS[0]}"
if [[ -n "$ADMINLIST" ]]; then
    echo "  Admin list : $ADMINLIST"
else
    echo "  WARNING: adminlist.txt not found - everyone will be listed under Players."
fi

# AMP's logs: one AMP_Logs folder per AMP instance (the main panel and each
# game server). Signed-in usernames come from these.
AMP_INSTANCES="$AMP_DATA/.ampdata/instances"
N_LOGS=$(podman unshare sh -c 'ls -d "$1"/*/AMP_Logs 2>/dev/null' sh "$AMP_INSTANCES" | wc -l)
if [[ $N_LOGS -gt 0 ]]; then
    echo "  AMP logs   : $N_LOGS log folders (for usernames)"
else
    echo "  WARNING: AMP's log folders weren't found - remote sign-ins will show as 'Someone'."
fi
MODMGR_DATA="$HOME/valheim-modmgr-data"

# --- Step 3: Copy the checker and button script ---
echo ""
echo "[3/7] Installing to $DIR..."
mkdir -p "$DIR"
cp "$PKG_DIR/checker/valheim-status.py" "$PKG_DIR/checker/check.sh" "$PKG_DIR/checker/control.sh" "$DIR/"
chmod +x "$DIR/check.sh" "$DIR/control.sh"
cat > "$DIR/config" <<CONFIG_EOF
BEPINEX="$BEPINEX"
ADMINLIST="$ADMINLIST"
QUERY_PORT=$QUERY_PORT
AMP_CONTAINER=amp-server
TAILSCALE_CONTAINER=tailscale
MODMGR_CONTAINER=valheim-modmgr
AMP_INSTANCES="$AMP_INSTANCES"
MODMGR_DATA="$MODMGR_DATA"
CONFIG_EOF
rm -f "$DIR/show.sh" "$DIR/busy"
echo "  Done"

# --- Step 4: Check every 30 seconds ---
# A user timer, like the containers' restart service. Lingering (turned on by
# the AMP setup) keeps it running after a reboot even before anyone logs in.
echo ""
echo "[4/7] Setting up the 30-second check..."
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/valheim-status.service" <<UNIT_EOF
[Unit]
Description=Valheim panel check (server state, players online, remote sign-ins)

[Service]
Type=oneshot
ExecStart=%h/valheim-status/check.sh
UNIT_EOF
cat > "$UNIT_DIR/valheim-status.timer" <<UNIT_EOF
[Unit]
Description=Run the Valheim panel check every 30 seconds

[Timer]
OnBootSec=1min
OnUnitActiveSec=30s
AccuracySec=1s

[Install]
WantedBy=timers.target
UNIT_EOF
systemctl --user daemon-reload
systemctl --user enable --now valheim-status.timer
"$DIR/check.sh" --quiet || true
echo "  Running"

# --- Step 5: Install the panel (Plasma widget) ---
echo ""
echo "[5/7] Installing the panel..."
if command -v kpackagetool6 &>/dev/null; then
    if kpackagetool6 -t Plasma/Applet --show "$PLASMOID_ID" &>/dev/null; then
        kpackagetool6 -t Plasma/Applet --upgrade "$PKG_DIR/plasmoid" >/dev/null
        echo "  Updated"
    else
        kpackagetool6 -t Plasma/Applet --install "$PKG_DIR/plasmoid" >/dev/null
        echo "  Installed"
    fi
else
    TARGET="$HOME/.local/share/plasma/plasmoids/$PLASMOID_ID"
    rm -rf "$TARGET"; mkdir -p "$(dirname "$TARGET")"; cp -r "$PKG_DIR/plasmoid" "$TARGET"
    echo "  Copied to $TARGET"
fi

# --- Step 6: Put it on the desktop, top right ---
# Uses Plasma's own desktop scripting. If that doesn't work, adding it by hand
# takes 10 seconds (see the message below).
echo ""
echo "[6/7] Placing the panel on the desktop..."
read -r -d '' SCRIPT <<JS_EOF || true
var placed = 0;
var ds = desktops();
for (var i = 0; i < ds.length; i++) {
    var d = ds[i];
    if (d.screen !== 0) continue;
    if (d.widgets("$PLASMOID_ID").length > 0) { placed = 2; continue; }
    var g = screenGeometry(d.screen);
    d.addWidget("$PLASMOID_ID", g.width - 360, 40, 320, 420);
    placed = 1;
}
print(placed);
JS_EOF
PLACED=$(busctl --user call org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell evaluateScript s "$SCRIPT" 2>/dev/null || true)
if [[ -n "$PLACED" ]]; then
    echo "  Done - look at the top right of the desktop"
    AUTO_PLACED=1
else
    echo "  Couldn't place it automatically (are you in Desktop Mode?)."
    AUTO_PLACED=0
fi

# --- Step 7: Retire the old desktop icons ---
# Moved, not deleted, in case they're wanted back.
echo ""
echo "[7/7] Moving the old desktop icons out of the way..."
OLD="$DIR/old-desktop-icons"
moved=0
for f in valheim-mods.desktop amp-server-control.desktop valheim-status.desktop; do
    if [[ -f "$DESKTOP_DIR/$f" ]]; then
        mkdir -p "$OLD"; mv "$DESKTOP_DIR/$f" "$OLD/"; moved=$((moved + 1))
        echo "  Moved $f to $OLD/"
    fi
done
[[ $moved -gt 0 ]] || echo "  None found"
echo "  (The AMP Server Control entry in the app menu, if installed, is left alone.)"

echo ""
echo "======================================="
echo "  Done - current status:"
echo "======================================="
echo ""
cat "$DIR/status.txt" 2>/dev/null || echo "  (no status yet)"
echo ""
if [[ "$AUTO_PLACED" != 1 ]]; then
    echo "  To add the panel: right-click the desktop -> Add or Manage Widgets ->"
    echo "  search 'Valheim' -> drag 'Valheim Server' onto the desktop."
    echo ""
fi
echo "  Pop-ups appear when someone signs in to AMP or the mod manager over"
echo "  Tailscale (shown by username), and when a player joins or leaves"
echo "  (Desktop Mode only)."
echo ""
