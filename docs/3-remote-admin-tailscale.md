# 3. Remote admin over Tailscale

Lets an admin somewhere else use the AMP panel and the mod manager over HTTPS, without forwarding
either one on the router. Players are not affected and don't need Tailscale.

## Before you start (tailnet owner)

1. Create a free Tailscale account if you don't have one, and install Tailscale on the device you
   will admin from.
2. In the admin console, DNS page: turn on **MagicDNS** and **HTTPS Certificates**.

## Run it (on the server PC)

```bash
TS_NAME=valheim-server bash install-tailscale.sh
```

`TS_NAME` becomes part of the address. The script:

1. starts Tailscale's official image as the container `tailscale`, in "userspace" mode, so only
   the two pages below are reachable through Tailscale, not the rest of the PC
2. prints a sign-in link; send it to the tailnet owner, who approves it
3. publishes the pages with `tailscale serve`:

| Page        | Address                                    |
| ----------- | ------------------------------------------ |
| AMP panel   | `https://<TS_NAME>.<tailnet>.ts.net/`      |
| Mod manager | `https://<TS_NAME>.<tailnet>.ts.net:8443/` |

The login is kept in `~/tailscale-data`, so restarts and SteamOS updates don't need it again. It
keeps working while the PC is in Gaming Mode.

## Key expiry

By default a device's Tailscale key expires 180 days after it was approved. When it expires, the
remote pages stop working until someone renews it (the game is not affected). You can choose:

- **Leave expiry on** (what we do, as an extra safety net). Put a reminder in your calendar a few
  days before the date shown in the admin console (Machines). To renew, at the server PC:

  ```bash
  podman exec tailscale tailscale up --force-reauth
  ```

  If it complains that changing settings requires mentioning all non-default flags, run the command
  it suggests with `--force-reauth` added. It prints a sign-in link for the tailnet owner to
  approve. If the key already expired, the owner can use "Temporarily extend key" on the Machines
  page (30 minutes) first.
- **Turn expiry off** for this one device (Machines -> the device -> Disable key expiry). Tailscale
  suggests this for servers. You can still remove the device from the console at any time.

Reference: https://tailscale.com/kb/1028/key-expiry
