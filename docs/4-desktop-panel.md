# 4. The "Valheim Server" desktop panel

A small Plasma 6 widget for the desktop of the server PC (Desktop Mode, SteamOS 3.7+). It updates
itself every 30 seconds and shows:

- whether the server is running, the world name and how many players are on
- who is signed in to AMP or the mod manager **remotely**, by username
  ("alice is connected (AMP, Mods)"), with a pop-up when someone connects or leaves
- who is in the game, admins and players listed separately, with a pop-up on join/leave
- buttons: Open AMP, Open Mods, Restart server, Stop/Start server
  (Restart and Stop ask first; they restart or stop the whole AMP container)

## Install

Install the mod manager first (the panel reads its sign-ins), then:

```bash
bash install-valheim-panel.sh
```

It installs to your home folder, so SteamOS updates leave it alone:

| Path                                                      | What                             |
| --------------------------------------------------------- | -------------------------------- |
| `~/valheim-status/`                                       | checker, button script, settings |
| `~/.config/systemd/user/valheim-status.{service,timer}`   | runs a check every 30 seconds    |
| `~/.local/share/plasma/plasmoids/org.valheimpanel.server` | the widget                       |

It tries to place the widget at the top right. If it doesn't appear: right-click the desktop ->
Add or Manage Widgets -> search "Valheim" -> drag "Valheim Server" onto the desktop.

## How it knows (no passwords are stored)

| Shown             | Source                                                                   |
| ----------------- | ------------------------------------------------------------------------ |
| Server running    | Steam query (A2S_INFO) to 127.0.0.1:2457, and the AMP container's state  |
| Players and names | The server's `BepInEx/LogOutput.log` (join/leave lines)                  |
| Admin or player   | The server's `adminlist.txt`                                             |
| Remote sign-ins   | Tailscale traffic per device, plus usernames from AMP's own logs and the |
|                   | mod manager's `presence.json` (see note 1)                               |

1. An open AMP page polls every few seconds, so a device on the tailnet that moves more than about
   3 KB per check is "busy". New lines in AMP's logs name the user (AMP writes the signed-in user
   into every log line it makes for them). A device remembers its last user, for when the browser
   is still signed in from before; that shows as "(AMP / mods)". A busy device with no name for a
   minute shows as "Someone is connected". Use on the server PC itself is not shown.

## Limits

- If two people sign in from different devices within the same 30 seconds, their names can land on
  the wrong devices.
- The panel and its pop-ups only exist in Desktop Mode (Gaming Mode has no Plasma desktop).
- With Public off in the Valheim settings, the server never answers the status query, so the panel
  stays on "Starting..." while the server is really running. Turn Public on (see lessons learned).

## Remove it

Right-click the panel and remove it, then:

```bash
systemctl --user disable --now valheim-status.timer
kpackagetool6 -t Plasma/Applet --remove org.valheimpanel.server
rm -r ~/valheim-status ~/.config/systemd/user/valheim-status.*
```
