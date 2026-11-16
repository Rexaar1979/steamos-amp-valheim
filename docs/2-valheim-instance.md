# 2. The Valheim server in AMP

Done in the AMP web panel at `http://localhost:8090`, after guide 1.

## Create the instance

1. **Create New Instance** -> application **Valheim Dedicated Server**.
2. Give it a name (for example `Valheim01`) and choose **Update and Start Once**. It downloads the
   server and starts it once.
   If the first build fails with "Missing configuration", click Update again.

## Settings

In the instance's Configuration:

| Setting         | Notes                                                                        |
| --------------- | ---------------------------------------------------------------------------- |
| Server name     | What players see                                                             |
| World name      | A new name creates a new world; to use an existing world see below           |
| Password        | Valheim requires one (at least 5 characters, not containing the server name) |
| Admin Players   | Steam64 IDs of the admins, one per entry (see note 1)                        |
| Crossplay       | See note 2                                                                   |
| Install BepInEx | Configuration -> Updates, tick it, then click Update (see note 3)            |

1. AMP rewrites the server's `adminlist.txt` from this setting every time the server starts, so
   edit the list in AMP, not in the file.
2. Crossplay (Xbox / Game Pass players) uses Valheim's PlayFab join codes. In our experience it did
   not work reliably after Valheim 1.0, and Xbox players can't run mods, so any mod that must be on
   every client (most gameplay mods) shuts them out. We run with crossplay off.
3. AMP's own BepInEx installer is the easiest way to get BepInEx on the server. Leave "BepInEx
   Version" empty for the latest. Clicking Update in AMP also updates Valheim itself, and a game
   update can break mods until the mod authors catch up.

## Using an existing world

1. Stop the instance.
2. In the instance's File Manager, find the world save folder (`worlds_local`).
3. Upload your `<World>.db` and `<World>.fwl`. The file names must match the World name setting.
   If the web upload gives 0 KB files, copy them on the PC directly instead; the files live under
   `~/amp-data/.ampdata/instances/<instance name>/`.
4. Start the instance and check in the console that it loads your world, not a new one.

## Router

Forward **UDP 2456-2458** to the PC's LAN address (give the PC a DHCP reservation so the address
doesn't change). Players join with your public IP and port 2456. Nothing else needs forwarding.

## Mods

Install `install-mod-manager.sh` next (see `mod-manager/README.md`). Players need the same mods and
versions on their side (r2modman or Thunderstore Mod Manager); the mod manager shows the list.

## Admin tips

- Kicking a player is console-only in vanilla Valheim: add `-console` to the game's launch options,
  press F5 in game, then `kick <name or Steam ID>`. You must be in the server's admin list.
