#!/bin/bash
# Valheim Panel: what the widget's buttons run.
#     control.sh open-amp | open-mods | start | stop | restart
# start/stop/restart act on the whole AMP container (amp-server), which takes
# the Valheim server with it. The widget asks "Are you sure?" before stop and
# restart; this script doesn't ask again.

DIR="$HOME/valheim-status"
# shellcheck source=/dev/null
source "$DIR/config"
AMP="${AMP_CONTAINER:-amp-server}"
BUSY="$DIR/busy"

case "$1" in
    open-amp)  exec xdg-open "http://localhost:8090" ;;
    open-mods) exec xdg-open "http://localhost:8095" ;;
    start|stop|restart) ;;
    *) echo "usage: $0 open-amp|open-mods|start|stop|restart" >&2; exit 2 ;;
esac

# While this runs, the widget shows "Starting...", "Stopping..." or "Restarting...".
echo "$1" > "$BUSY"
trap 'rm -f "$BUSY"; "$DIR/check.sh"' EXIT

# 120 s gives Valheim time to save the world before it is forced to quit
# (podman's default of 10 s is too short).
case "$1" in
    start)   podman start "$AMP" ;;
    stop)    podman stop -t 120 "$AMP" ;;
    restart) podman restart -t 120 "$AMP" ;;
esac
status=$?

if [[ $status -ne 0 ]]; then
    busctl --user call org.freedesktop.Notifications /org/freedesktop/Notifications \
        org.freedesktop.Notifications Notify susssasa\{sv\}i \
        "Valheim Panel" 0 "dialog-error" "Couldn't $1 the server" \
        "podman $1 $AMP failed (exit $status). Try the AMP web panel, or ask Dad." 0 0 15000 >/dev/null 2>&1 || true
fi
exit $status
