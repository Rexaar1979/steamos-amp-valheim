#!/bin/bash
# Valheim Panel: one check. Run every 30 s by the systemd user timer
# valheim-status.timer (installed by install-valheim-panel.sh), and right
# after a button in the desktop widget is used.
# Gathers the inputs, runs valheim-status.py, saves status.json (read by the
# widget) and status.txt, and shows a desktop pop-up for anything that changed.

DIR="$HOME/valheim-status"
# shellcheck source=/dev/null
source "$DIR/config"          # BEPINEX=, ADMINLIST=, QUERY_PORT=, AMP_CONTAINER=, TAILSCALE_CONTAINER=,
                              # MODMGR_CONTAINER=, AMP_INSTANCES=, MODMGR_DATA=

# Only one check at a time (a click on the icon also runs one).
exec 9>"$DIR/.lock"
flock -w 20 9 || exit 0

input() {
    echo "===PORT==="
    echo "${QUERY_PORT:-2457}"
    echo "===TAILSCALE==="
    podman exec "${TAILSCALE_CONTAINER:-tailscale}" tailscale status --json 2>/dev/null
    echo                      # tailscale prints no final newline; without this "===STATE===" lands on its line
    echo "===STATE==="
    cat "$DIR/state.json" 2>/dev/null
    echo
    echo "===AMP==="
    podman container inspect -f '{{.State.Running}}' "${AMP_CONTAINER:-amp-server}" 2>/dev/null || echo false
    echo "===AMPLOG==="
    # The newest log of each AMP instance (the main panel and each game server),
    # only lines that name a signed-in user, with line numbers so the checker
    # knows which are new. "[14:49:26] [System:alice Activity/9] : ..."
    [[ -n "$AMP_INSTANCES" ]] && podman unshare sh -c '
        for d in "$1"/*/AMP_Logs; do
            f=$(ls -t "$d"/AMPLOG_*.log 2>/dev/null | head -n 1)
            [ -n "$f" ] || continue
            echo "#FILE $f"
            grep -n -E "^\[[0-9:]{8}\] \[[A-Za-z]+:[^] ]+ " "$f" | grep -v ":Anonymous " | tail -n 40
        done' sh "$AMP_INSTANCES" 2>/dev/null
    echo "===MODS==="
    # Who is signed in to the mod manager (written by the mod manager itself)
    [[ -n "$MODMGR_DATA" ]] && podman unshare cat "$MODMGR_DATA/presence.json" 2>/dev/null
    echo
    echo "===ADMINS==="
    [[ -n "$ADMINLIST" ]] && podman unshare cat "$ADMINLIST" 2>/dev/null
    echo
    echo "===LOG==="
    # The log belongs to the container's user; unshare reads it as that user.
    podman unshare cat "$BEPINEX/LogOutput.log" 2>/dev/null
}

if command -v python3 >/dev/null 2>&1; then
    OUT=$(input | python3 -I "$DIR/valheim-status.py")
else
    # No python3 on this PC: borrow the mod manager's (same network, so the
    # player-count query still reaches 127.0.0.1:2457).
    OUT=$(input | podman exec -i "${MODMGR_CONTAINER:-valheim-modmgr}" \
          python3 -I -c "$(cat "$DIR/valheim-status.py")")
fi
[[ -n "$OUT" ]] || { echo "valheim-status: no output from the checker" >&2; exit 1; }

section() {   # print the lines of one ===NAME=== section
    awk -v want="===$1===" '/^===[A-Z]+===$/ { on = ($0 == want); next } on' <<<"$OUT"
}

section STATE > "$DIR/state.json.new" && mv "$DIR/state.json.new" "$DIR/state.json"
section STATUS > "$DIR/status.txt"
section JSON > "$DIR/status.json.new" && mv "$DIR/status.json.new" "$DIR/status.json"

[[ "$1" == "--quiet" ]] && exit 0
while IFS=$'\t' read -r title body; do
    [[ -n "$title" ]] || continue
    # Desktop pop-up through the standard notifications service (Plasma's).
    # In Gaming Mode there is no Plasma, so this quietly does nothing.
    busctl --user call org.freedesktop.Notifications /org/freedesktop/Notifications \
        org.freedesktop.Notifications Notify susssasa\{sv\}i \
        "Valheim Status" 0 "applications-games" "$title" "$body" 0 0 10000 >/dev/null 2>&1 \
    || notify-send -a "Valheim Status" -i applications-games "$title" "$body" >/dev/null 2>&1 \
    || true
done < <(section NOTIFY)
