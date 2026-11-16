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

## Valheim

12. **No kick button in vanilla Valheim.** Kicking is a console command (`-console` launch option,
    F5, `kick <name>`), and you must be in the admin list.
13. **Crossplay and client mods don't mix.** Xbox players can't run mods, and many mods must be on
    every client.

## Sharing files

14. **Gmail blocks zip files that contain `.js` files** (the mod manager has one). Share packages
    as a Google Drive (or similar) link instead of an attachment.
