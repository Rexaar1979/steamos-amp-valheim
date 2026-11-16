# 1. AMP on SteamOS (Podman container)

Gets AMP itself running on the SteamOS PC. Creating the Valheim server is the next guide.

## Before you start

- The PC is in **Desktop Mode** with Konsole open.
- You have an **AMP license key** (CubeCoders, manage.cubecoders.com).
- The PC has internet access (it downloads the container image and activates the license).

## Run it

```bash
bash setup-amp-container-steamos.sh
```

It asks you to choose the AMP admin password, then:

1. checks Podman is there (SteamOS 3.5+ ships it)
2. turns on "lingering", so the container starts at boot with nobody logged in
3. creates `~/amp-data`, which holds the entire AMP install (panel, instances, world saves)
4. creates and saves a fixed MAC address for the container (see the note below)
5. downloads the AMP-dockerized image and starts the container `amp-server`
6. enables `podman-restart.service` so the container comes back after a reboot

Settings you can change by setting them in front of the command, for example
`HOST_PANEL_PORT=8091 bash setup-amp-container-steamos.sh`:

| Setting           | Default                                         |
| ----------------- | ----------------------------------------------- |
| `ADMIN_USERNAME`  | `admin`                                         |
| `HOST_PANEL_PORT` | `8090`                                          |
| `BIND_ADDR`       | `127.0.0.1` (see note 1)                        |
| `DATA_DIR`        | `~/amp-data`                                    |
| `AMP_IMAGE`       | `docker.io/mitchtalmadge/amp-dockerized:latest` |

1. Where the AMP panel and SFTP ports listen. `127.0.0.1` means only this PC (and Tailscale,
   which runs on this PC). `0.0.0.0` would open them to every device on your home network, guests
   and smart TVs included; we don't recommend it. The Valheim game ports always listen on all
   addresses, because players need them.

## First sign-in and license

1. On the PC, open `http://localhost:8090` and sign in with the admin account.
2. Configuration -> Licensing: enter your license key and check it shows as activated.
3. Create your own user accounts (Configuration -> User Management) and turn on two-factor sign-in.
   Keep the `admin` account as a break-glass account with a strong password.

## Checks

```bash
podman ps                                                        # amp-server is Up
podman inspect amp-server --format '{{.HostConfig.RestartPolicy.Name}}'   # always
loginctl show-user "$(whoami)" --property=Linger                 # Linger=yes
podman port amp-server                                           # 8080/tcp -> 127.0.0.1:8090
ls ~/amp-data                                                    # not empty
```

Then restart the PC without touching anything: AMP should come back by itself.

## Why the fixed MAC address

AMP ties its license activation to the machine's network adapter (MAC address). A container gets a
new random MAC every time it is recreated (for example to pick up a newer image), which would reset
the activation. The script makes one MAC, saves it in `~/.config/amp-container-mac`, and reuses it.

## Updating the AMP image

```bash
podman rm -f amp-server
podman pull docker.io/mitchtalmadge/amp-dockerized:latest
bash setup-amp-container-steamos.sh
```

Your data in `~/amp-data` and the pinned MAC are kept. AMP also updates itself inside the
container (`AMP_AUTO_UPDATE=true`).

## Troubleshooting

- Container won't start: `podman logs amp-server`
- Panel doesn't load: check the port is free with `ss -tlnp | grep 8090`
- "static ip/mac address can only be used with Bridge mode networking": the script already uses
  `--network bridge` for this; it happens if you change that.
