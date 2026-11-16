# Valheim dedicated server on SteamOS, with AMP, a mod manager and a desktop panel

How one family runs a modded Valheim dedicated server on a SteamOS gaming PC, managed with
CubeCoders AMP, and administered remotely without opening the admin pages to the internet.

This is not the only way, and probably not the best way. It is a way that has worked well for us,
written up in case someone else is looking for the same thing. Use it, change it, share it.

## What you get

```
  SteamOS PC (Desktop or Gaming Mode)                       Remote admin (anywhere)
  +-----------------------------------------------+
  | amp-server        (Podman container)          |  <---  https://<name>.<tailnet>.ts.net/
  |   AMP panel  127.0.0.1:8090                   |        (AMP panel, tailnet only)
  |   Valheim    UDP 2456-2458  <-----------------+------  players (router forward)
  | valheim-modmgr    (Podman container)          |
  |   Mod manager 127.0.0.1:8095                  |  <---  https://<name>.<tailnet>.ts.net:8443/
  | tailscale         (Podman container)          |
  |   tailscale serve -> the two pages above      |
  | "Valheim Server" desktop panel (Plasma 6)     |
  +-----------------------------------------------+
```

- **AMP in a rootless Podman container.** SteamOS updates replace the whole root filesystem, so
  anything installed with pacman disappears. Everything here lives under `/home`, which updates
  never touch. No reinstall after SteamOS updates.
- **Remote admin over Tailscale.** The AMP panel and the mod manager are only reachable on the PC
  itself and over your own tailnet (HTTPS). The only ports open to the internet are Valheim's.
- **A small mod manager web app.** Installs BepInEx mods from Thunderstore with their
  dependencies, enables/disables them, checks for updates, edits configs, and shows the mod list
  players need. Password plus authenticator app sign-in.
- **A "Valheim Server" desktop panel** (Plasma 6 widget) for the person whose PC it is: server
  state, players online (admins listed separately), who is signed in to AMP or the mod manager
  remotely (by username), and Open/Restart/Stop/Start buttons.

## Requirements

- A PC running **SteamOS 3.5 or newer** (Podman is preinstalled). The panel needs **SteamOS 3.7+**
  (Plasma 6). Everything is installed from Desktop Mode; the server keeps running in Gaming Mode.
- An **AMP license** from CubeCoders (AMP is commercial software, not included here).
- A free **Tailscale** account, for remote admin.
- Access to your **router**, to forward UDP 2456-2458 to the PC for players.

## Install order

All scripts run in Konsole, in Desktop Mode, from this folder, as the normal user (no sudo).

| Step | Script                                         | Guide                              |
| ---- | ---------------------------------------------- | ---------------------------------- |
| 1    | `bash setup-amp-container-steamos.sh`          | `docs/1-amp-on-steamos.md`         |
| 2    | (in the AMP panel) create the Valheim instance | `docs/2-valheim-instance.md`       |
| 3    | `bash install-mod-manager.sh`                  | `mod-manager/README.md`            |
| 4    | `bash install-tailscale.sh`                    | `docs/3-remote-admin-tailscale.md` |
| 5    | `bash install-valheim-panel.sh`                | `docs/4-desktop-panel.md`          |

Every script is safe to re-run; re-running is also how you update. Things we learned the hard way
are in `docs/5-lessons-learned.md`.

## What has been tested

| Part                     | Status                                                             |
| ------------------------ | ------------------------------------------------------------------ |
| AMP container on SteamOS | In daily use since October 2026 (AMP 2.8, SteamOS with Podman 5.5) |
| Valheim + BepInEx + mods | In use (Valheim 1.0.x, BepInEx 5.4.2351 installed by AMP)          |
| Mod manager              | In use                                                             |
| Tailscale remote admin   | In use, also works while the PC is in Gaming Mode                  |
| Desktop panel            | New (November 2026); tested in a Plasma 6.8 test environment only  |

## Security and network ports

What each port is open to, with the scripts' default settings:

| Port          | What        | Listens on    | Reachable from                          |
| ------------- | ----------- | ------------- | --------------------------------------- |
| UDP 2456-2458 | Valheim     | all addresses | the internet (router forward), players  |
| TCP 8090      | AMP panel   | 127.0.0.1     | this PC; your tailnet over HTTPS (443)  |
| TCP 2224      | AMP SFTP    | 127.0.0.1     | this PC only                            |
| TCP 8095      | Mod manager | 127.0.0.1     | this PC; your tailnet over HTTPS (8443) |

- On the router, forward only UDP 2456-2458. Never forward 8090, 2224, 8095 or 8443.
- Every person and device on your tailnet can reach the AMP and mod manager sign-in pages. Only
  add people you trust, or limit it with Tailscale's access controls. Don't turn on Tailscale
  Funnel for these pages: that puts them on the public internet.
- Use a strong password for AMP's `admin` account that you don't use anywhere else, and turn on
  two-factor sign-in for your own AMP accounts (the mod manager requires it). Turn on two-factor
  sign-in for the account you sign in to Tailscale with, because it decides which devices join
  your tailnet.
- No passwords, keys or tokens are stored in these files. The AMP setup script asks for the admin
  password; the Tailscale script prints a sign-in link for you to approve. The first AMP admin
  password stays readable with `podman inspect amp-server` on the PC; change it in AMP after the
  first sign-in if that matters to you.
- What you are trusting: the community AMP-dockerized image (pulled from Docker Hub), and every
  mod you install. Mods are programs that run inside the game server, so install only mods from
  authors you trust.
- Keep SteamOS, Valheim and the Tailscale image up to date. AMP updates itself.

You are responsible for securing your own network and accounts.

## Not affiliated

Valheim is a trademark of Iron Gate AB. AMP is a product of CubeCoders Limited. Steam and SteamOS
are trademarks of Valve Corporation. Tailscale is a trademark of Tailscale Inc. This project is not
affiliated with, endorsed by or supported by any of them. None of their software is included here.
When you run the scripts, they download what they need from the official sources (CubeCoders,
Steam, Tailscale, Thunderstore). The one exception is the AMP container image, which comes from the
community AMP-dockerized project.

## License

MIT, see `LICENSE`. No warranty: it runs your game server, so read the scripts before you run them.

Built with help from Claude Code (Anthropic).
