# 5. Lessons learned

Things that cost us time, in case they save you some.

## SteamOS

1. **Don't install server software with pacman.** The root filesystem is a read-only A/B image
   replaced on every update. Rootless Podman under `/home` survives updates.
2. **Port 8080 is taken** by the Steam client (steamwebhelper, 127.0.0.1:8080), and 8081 by a
   system service. The AMP panel is published on 8090.
3. **Lingering matters.** Without `loginctl enable-linger`, rootless containers don't start at
   boot until someone logs in.
4. **Gaming Mode is fine.** The containers keep running; only the desktop panel needs Desktop Mode.

## Podman

5. **Rootless Podman 5 uses "pasta" networking by default**, which refuses `--mac-address`. Use
   `--network bridge` for the AMP container.
6. **Files inside the containers belong to a mapped user** you can't read as yourself. Use
   `podman unshare cat <file>` (or `ls`, `find`) to look at them as that user.
7. **Write files as the same user AMP uses.** The mod manager container runs with `--user` set to
   the owner of the BepInEx folder, or AMP/Valheim may not be able to read what it writes.

## AMP

8. **Pin the container's MAC address**, or recreating the container resets the license activation.
9. **AMP rewrites `adminlist.txt`** from its "Admin Players" setting on every start.
10. **AMP's "Update" also updates the game**, which can break mods until they catch up.
11. **AMP's log times are UTC**, both on the Event Log page and in its log files.
12. **AMP keeps a daily backup of its instance list** (`~/amp-data/.ampdata/instances.json-YYYYMMDD.bak`).
    If a game instance vanishes from AMP (for example after many quick restarts), stop the container, keep the
    broken `instances.json`, and copy a good backup into place with `podman unshare cp` so the owner stays right.
    Check first that AMP's own entry (ADS01) still has the same `InstanceID` as in
    `instances/ADS01/AMPConfig.conf`; if AMP gave itself a new one, use the new ID in the restored file.
    Don't use Datastores -> Repair for this: it re-adds the instance as an unmanaged one that won't start.

## Valheim

13. **No kick button in vanilla Valheim.** Kicking is a console command (`-console` launch option,
    F5, `kick <name>`), and you must be in the admin list.
14. **Crossplay and client mods don't mix.** Xbox players can't run mods, and many mods must be on
    every client.
15. **With Public off (`-public 0`), the server doesn't answer Steam status queries** on the query port. Joining
    still works, but players' favorites show it as offline and the desktop panel's status check gets no answer.
    Turn Public on (and use a strong password) if you want both to work.

## Sharing files

16. **Gmail blocks zip files that contain `.js` files** (the mod manager has one). Share packages
    as a Google Drive (or similar) link instead of an attachment.
