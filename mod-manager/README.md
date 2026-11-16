# Valheim Mod Manager

A small web page for managing the BepInEx mods on an AMP-hosted Valheim server.
It installs mods from Thunderstore (with their dependencies), enables and disables them, checks for
updates, edits mod config files, and shows the mod list players need in r2modman.

## Updating and choosing versions
- **Update all...** lists every mod with a newer version, as a checklist. Untick anything you want to skip.
- **Update... / Versions...** next to each mod opens a dropdown of every published version (newest first, with dates).
  If you pick anything but the latest, the mod is **held**: Update all leaves it unticked until you pick the latest again.
- The version list comes from the API behind Thunderstore's newer website, which isn't documented.
  If it ever breaks, the page falls back to a text box where you type the version.

## Security model
- Every account signs in with a **password (12+ characters) plus an authenticator-app code**.
  The first sign-in forces authenticator setup. Accounts an admin creates must also change their temporary password.
- 5 wrong attempts lock that username for 15 minutes. Authenticator codes can't be reused.
- Sign-ins end after 30 minutes without activity, or 4 hours at most
  (`MODMGR_IDLE_MINUTES` / `MODMGR_MAX_SESSION_HOURS`).
- The app listens on **127.0.0.1 only**:
  - On the server PC itself: `http://localhost:8095` (the traffic never leaves the machine).
  - Remotely: `https://<server-name>.<tailnet>.ts.net:8443/` via `tailscale serve` (see `../install-tailscale.sh`).
    That URL is HTTPS and only works for devices on the tailnet.
- Everything people do is written to `audit.log` in the data folder.
- Who is signed in right now (username, until when, local or over Tailscale) is kept in `presence.json`
  in the data folder, for the "Valheim Server" desktop panel.

## Folder layout it manages (inside the server's `BepInEx/`)
| Path                             | Meaning                                              |
| -------------------------------- | ---------------------------------------------------- |
| `plugins/<Author-Mod>/`          | enabled mod (`.modmanager.json` records the version) |
| `plugins-disabled/<Author-Mod>/` | disabled mod (BepInEx doesn't load it)               |
| `patchers/<Author-Mod>/`         | preloader patchers, if a mod ships any               |
| `config/*.cfg`                   | mod settings (see note 1)                            |

1. New installs only add missing defaults and never overwrite. Edits keep a `.bak`.

BepInEx itself is left to AMP (Configuration -> Updates -> Install BepInEx).
Loose files that were uploaded by hand show up under "Other files in the plugins folder".

## Installing it on the server PC
Run `bash install-mod-manager.sh` from the repo root, in SteamOS Desktop Mode. It:
1. finds the Valheim instance's `BepInEx` folder under `~/amp-data/.ampdata/instances/`
2. reads which user owns it, so the app writes files AMP and Valheim can read (`--user`)
3. builds this folder into a container image and runs it as `valheim-modmgr` on `127.0.0.1:8095`
   (`--network host`, `--restart=always`, data in `~/valheim-modmgr-data`)

Re-running it updates the app and keeps accounts and settings.

## First run
1. Open `http://localhost:8095`. It asks for the **setup token** the installer printed
   (also in `podman logs valheim-modmgr`).
2. Create the first admin account, sign in, and scan the QR code with an authenticator app.
3. Users page -> add other admins with a temporary password.

## Local development
```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
MODMGR_BEPINEX_DIR=/path/to/fake/BepInEx MODMGR_DATA_DIR=/path/to/data ./venv/bin/python app.py
```
