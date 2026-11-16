#!/usr/bin/env python3
"""Valheim Status checker for the server PC.

Reads plain-text sections on stdin (so the shell wrapper needs no jq):
    ===PORT===       Valheim query port (2457)
    ===TAILSCALE===  output of `tailscale status --json` (may be empty)
    ===STATE===      state.json from the previous run (may be empty)
    ===AMP===        "true" if the amp-server container is running, "false" if not
    ===AMPLOG===     the user lines of each AMP log ("#FILE <path>", then
                     "<line no>:<line>"; may be empty)
    ===MODS===       the mod manager's presence.json (may be empty)
    ===ADMINS===     text of the server's adminlist.txt (may be empty)
    ===LOG===        text of BepInEx/LogOutput.log (may be empty)
Writes sections on stdout:
    ===STATE===  new state (one line of JSON)
    ===STATUS=== plain-text status (kept in status.txt for anyone in Konsole)
    ===JSON===   the same status as one line of JSON, read by the desktop widget
    ===NOTIFY=== one pop-up per line: title<TAB>body

The wrapper (check.sh) gathers the inputs and shows the pop-ups, so this file
only needs Python's standard library. It runs either with the PC's own python3
or inside the mod manager's container.
"""
import json
import re
import socket
import struct
import sys
import time

ADMIN_BYTES_PER_CHECK = 3000   # measured: an open AMP tab moves ~70 KB per 30 s; idle moves 0
ADMIN_IDLE_CHECKS = 2          # this many quiet checks in a row before a device counts as gone
ANON_CHECKS = 2                # a device busy this many checks with no username -> "Someone connected"
# AMP writes the signed-in user into each log line it makes for them, e.g.
#   [14:49:26] [System:alice Activity/9] : Authentication token ... for alice ...
# (seen in AMP 2.8 logs, 2026-11). "Anonymous" is a visitor who isn't signed in.
RE_AMP_USER = re.compile(r"^\[[0-9:]{8}\] \[[A-Za-z]+:([A-Za-z0-9._@-]+) [A-Za-z]+/\d+\]")
NOT_A_USER = {"Anonymous"}

# ---------- player count straight from the game server (Steam A2S_INFO) ----------

def a2s_player_count(port, timeout=2.0):
    """Returns (players, max_players) or None if the server doesn't answer."""
    query = b"\xFF\xFF\xFF\xFFTSource Engine Query\x00"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(query, ("127.0.0.1", port))
        data, _ = sock.recvfrom(4096)
        if data[4:5] == b"\x41":                       # challenge: ask again with it
            sock.sendto(query + data[5:9], ("127.0.0.1", port))
            data, _ = sock.recvfrom(4096)
        if data[4:5] != b"\x49":
            return None
        pos = 6                                          # header + protocol byte
        for _ in range(4):                               # name, map, folder, game
            pos = data.index(b"\x00", pos) + 1
        pos += 2                                         # app id (short)
        return data[pos], data[pos + 1]
    except (OSError, ValueError, IndexError):
        return None
    finally:
        sock.close()

# ---------- who is in the game, from the server log ----------

LINE_TIME = r"(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}): "
RE_HISTORY = re.compile(r"Player history entry with index \d+:\s+(.+?) \(Steam_(\d+),")
RE_CONNECT = re.compile(LINE_TIME + r"Got connection SteamID (\d+)")
RE_CHAR = re.compile(LINE_TIME + r"Got character ZDOID from (.+?) : -?\d+:\d+")
RE_CLOSE = re.compile(LINE_TIME + r"Closing socket (\d+)")
RE_START = re.compile(r"Game server connected")
RE_WORLD = re.compile(r"ZNet\.LoadWorld: (\S+)")


def players_from_log(log_text):
    """Returns (list of {"id", "steam_name", "character"}, server_started)."""
    steam_names = {}
    online = {}           # steam id -> {"character": name or None}
    order = []            # steam ids in join order, to pair with the next character line
    started = False
    for line in log_text.splitlines():
        m = RE_HISTORY.search(line)
        if m:
            steam_names[m.group(2)] = m.group(1).strip()
            continue
        if RE_START.search(line):
            started = True
            online.clear()
            order.clear()
            continue
        m = RE_CONNECT.search(line)
        if m:
            sid = m.group(2)
            online[sid] = {"character": None}
            order.append(sid)
            continue
        m = RE_CHAR.search(line)
        if m and not line.rstrip().endswith(" 0:0"):   # " : 0:0" is logged when a player dies
            # The character line has no Steam ID; give it to the oldest
            # connection that doesn't have a character yet.
            for sid in order:
                if sid in online and online[sid]["character"] is None:
                    online[sid]["character"] = m.group(2).strip()
                    break
            continue
        m = RE_CLOSE.search(line)
        if m:
            sid = m.group(2)
            online.pop(sid, None)
            if sid in order:
                order.remove(sid)
    players = [{"id": sid,
                "steam_name": steam_names.get(sid, ""),
                "character": online[sid]["character"] or "(still loading in)"}
               for sid in order if sid in online]
    return players, started


def describe(p):
    if p["steam_name"]:
        return f'{p["character"]} ({p["steam_name"]})'
    return p["character"]

# ---------- who is signed in remotely ----------

def tailscale_peers(ts):
    """{peer key: total bytes to/from that device}, or None if Tailscale didn't answer."""
    if not ts or not isinstance(ts.get("Peer"), dict):
        return None
    return {key: int(p.get("RxBytes") or 0) + int(p.get("TxBytes") or 0)
            for key, p in ts["Peer"].items()}


def amp_users(amp_text, seen):
    """Usernames in AMP log lines that are new since the last check, oldest first.

    seen is {log path: last line number already read}; it's updated in place.
    The very first run (seen is None) only records where each log is up to,
    so old sign-ins don't count as new ones.
    """
    first_run = seen is None
    old = {} if first_run else dict(seen)
    if not amp_text.strip():
        return [], seen           # couldn't read the logs this time: keep our place
    new_seen, names, path = {}, [], None
    for line in amp_text.splitlines():
        if line.startswith("#FILE "):
            path = line[6:].strip()
            new_seen[path] = old.get(path, 0)    # a log we haven't seen (AMP restarted): all new
            continue
        num, _, text = line.partition(":")
        if path is None or not num.isdigit():
            continue
        num = int(num)
        if num <= old.get(path, 0):
            continue
        new_seen[path] = max(new_seen[path], num)
        m = RE_AMP_USER.match(text)
        if m and m.group(1) not in NOT_A_USER and not first_run:
            names.append(m.group(1))
    return names, new_seen


def mods_users(mods_text, now):
    """Usernames signed in to the mod manager over Tailscale right now."""
    try:
        data = json.loads(mods_text) if mods_text and mods_text.strip() else {}
    except ValueError:
        return set()
    return {name for name, v in data.items()
            if isinstance(v, dict) and v.get("via") == "tailscale" and float(v.get("until", 0)) > now}


def track_remote(state, peers, amp_names, mods_on, now):
    """Updates state, returns (pop-ups, {name: [places]}, number of busy devices with no name).

    A device (Tailscale peer) is busy while it moves more than ADMIN_BYTES_PER_CHECK
    per check: an open AMP page polls every few seconds. New AMP log lines name the
    user; they go to a busy device that has no name yet. A device remembers the
    last name it had, for when the browser is still signed in from before.
    """
    notify = []
    devices = state.setdefault("devices", {})       # peer -> {"on", "quiet", "busy", "name", "anon_told"}
    last_bytes = state.get("peer_bytes")
    for key, total in peers.items():
        d = devices.setdefault(key, {"on": False, "quiet": 0, "busy": 0})
        prev = (last_bytes or {}).get(key)
        delta = total - prev if prev is not None and total >= prev else 0
        if delta > ADMIN_BYTES_PER_CHECK:
            if not d["on"]:
                d.update(on=True, busy=0, session_name="", anon_told=False)
            d["quiet"] = 0
            d["busy"] += 1
        elif d["on"]:
            d["quiet"] += 1
            if d["quiet"] >= ADMIN_IDLE_CHECKS:
                if d.get("anon_told") and not d.get("session_name"):
                    notify.append(["Remote connection closed",
                                   "Someone closed AMP or the mod manager (no sign-in was seen)."])
                d.update(on=False, busy=0)
    for key in list(devices):
        if key not in peers:
            del devices[key]
    state["peer_bytes"] = peers

    # Names from AMP's log go to a busy device that hasn't been named this session.
    for name in amp_names:
        busy = [d for d in devices.values() if d["on"]]
        if any(d.get("session_name") == name for d in busy):
            continue
        # A device with no name at all first, then one that only remembers an earlier user.
        target = (next((d for d in busy if not d.get("session_name") and not d.get("name")), None)
                  or next((d for d in busy if not d.get("session_name")), None))
        if target is None:
            continue              # no remote device is busy: someone on the server PC itself
        target["session_name"] = name
        target["name"] = name

    # A mod manager user who isn't on any device yet is probably the busy device with no name.
    on_devices = {d.get("session_name") or d.get("name") for d in devices.values() if d["on"]}
    for name in sorted(mods_on - on_devices):
        target = next((d for d in devices.values()
                       if d["on"] and not (d.get("session_name") or d.get("name"))), None)
        if target is not None:
            target["name"] = name

    where = {}
    anon = 0
    for d in devices.values():
        if not d["on"]:
            continue
        name = d.get("session_name") or d.get("name")
        if name:
            # session_name: seen in AMP's log this time. name only: this device's
            # last user, still signed in from before, so AMP or mods isn't known.
            where.setdefault(name, set()).add("AMP" if d.get("session_name") else "?")
        elif d["busy"] >= ANON_CHECKS:
            anon += 1
            if not d.get("anon_told"):
                d["anon_told"] = True
                notify.append(["Someone connected",
                               "Someone opened AMP or the mod manager over Tailscale (not signed in yet)."])
    for name in mods_on:
        where.setdefault(name, set()).add("Mods")
    for name, places in where.items():
        if places == {"?"}:
            where[name] = ["AMP / mods"]
        else:
            where[name] = [p for p in ("AMP", "Mods") if p in places]

    before = set(state.get("remote_on", []))
    for name in sorted(where):
        if name not in before:
            notify.append([f"{name} connected",
                           f"{name} is signed in to {' and '.join(where[name])} over Tailscale."])
    for name in sorted(before - set(where)):
        notify.append([f"{name} disconnected", f"{name} closed AMP and the mod manager."])
    if where:
        state["last_remote"] = {"name": ", ".join(sorted(where)), "when": now}
    state["remote_on"] = sorted(where)
    return notify, where, anon


def read_input(text):
    parts = re.split(r"^===(PORT|TAILSCALE|STATE|AMP|AMPLOG|MODS|ADMINS|LOG)===\n", text, flags=re.M)
    sections = dict(zip(parts[1::2], parts[2::2]))

    def as_json(s):
        try:
            return json.loads(s) if s and s.strip() else None
        except ValueError:
            return None
    port = (sections.get("PORT") or "").strip()
    return {"query_port": int(port) if port.isdigit() else 2457,
            "tailscale": as_json(sections.get("TAILSCALE")),
            "state": as_json(sections.get("STATE")) or {},
            "amp": (sections.get("AMP") or "").strip(),
            "amplog": sections.get("AMPLOG") or "",
            "mods": sections.get("MODS") or "",
            "admins": set(re.findall(r"(?m)^\s*(?:Steam_)?(\d{17})\s*$", sections.get("ADMINS") or "")),
            "log": sections.get("LOG") or ""}


def main():
    data = read_input(sys.stdin.read())
    state = data.get("state") or {}
    now = time.time()
    clock = time.strftime("%-I:%M %p", time.localtime(now))
    notify = []

    # --- Who is signed in remotely (AMP / mod manager over Tailscale) ---
    for old_key in ("admin_on", "admin_quiet", "admin_last", "admin_host", "ts_total", "ts_hosts"):
        state.pop(old_key, None)                       # from the first version (one hard-coded admin)
    amp_names, state["amp_log"] = amp_users(data.get("amplog") or "", state.get("amp_log"))
    peers = tailscale_peers(data.get("tailscale"))
    where, anon = {}, 0
    if peers is None:
        admin_lines = ["Signed in remotely: unknown - Tailscale isn't answering"]
    else:
        told, where, anon = track_remote(state, peers, amp_names, mods_users(data.get("mods"), now), now)
        notify += told
        admin_lines = [f"Signed in remotely: {n} ({', '.join(p)})" for n, p in sorted(where.items())]
        admin_lines += ["Signed in remotely: someone (not signed in yet)"] * anon
        if not admin_lines:
            last = state.get("last_remote")
            admin_lines = ["Signed in remotely: no one" + (
                f' (last: {last["name"]}, {time.strftime("%a %b %-d, %-I:%M %p", time.localtime(last["when"]))})'
                if last else "")]

    # --- Server and players ---
    log_text = data.get("log") or ""
    players, started = players_from_log(log_text)
    a2s = a2s_player_count(int(data.get("query_port") or 2457))
    amp_running = data.get("amp") != "false"          # unknown counts as running
    if a2s is not None:
        server = "running"
    elif not amp_running:
        server = "stopped"
    else:
        server = "starting"                            # AMP is up, Valheim isn't answering (yet)
    if server != "running" and not started:
        players = []                                   # an old log from before a stop
    if not amp_running:
        players = []
    worlds = RE_WORLD.findall(log_text)
    world = worlds[-1] if worlds else ""
    if a2s is not None:
        count = a2s[0]
        count_line = f"Players online: {a2s[0]} of {a2s[1]}"
    elif started:
        count = len(players)
        count_line = f"Players online: {count} (from the server log; the server didn't answer the player-count query)"
    else:
        count = 0
        count_line = "Valheim server: not running (or still starting up)"

    before = {p["id"]: p for p in state.get("players", [])}
    after = {p["id"]: p for p in players}
    for sid, p in after.items():
        old = before.get(sid)
        if old is None or (old["character"] == "(still loading in)" and p["character"] != old["character"]):
            if p["character"] != "(still loading in)":
                notify.append([f'{p["character"]} joined', f"{describe(p)} is in the game. {count} online now."])
    for sid, p in before.items():
        if sid not in after:
            notify.append([f'{p["character"]} left', f"{describe(p)} left the game. {count} online now."])
    state["players"] = players

    lines = [f"Valheim Status (checked {clock})", "", *admin_lines, "", count_line]
    lines += [f"   - {describe(p)}" for p in players]
    if a2s is not None and a2s[0] != len(players) and started:
        lines.append("   (names come from the server log and may lag a few seconds)")

    admins = data.get("admins") or set()
    entry = lambda p: {"character": p["character"], "steam": p["steam_name"],
                       "loading": p["character"] == "(still loading in)"}
    widget = {
        "updated": clock,
        "server": server,
        "world": world,
        "online": count if server == "running" else 0,
        "max": a2s[1] if a2s is not None else 0,
        "remote": {"known": peers is not None,
                   "users": [{"name": n, "where": ", ".join(p)} for n, p in sorted(where.items())],
                   "someone": anon,
                   "last": (f'{state["last_remote"]["name"]}, '
                            f'{time.strftime("%a %-I:%M %p", time.localtime(state["last_remote"]["when"]))}')
                           if state.get("last_remote") else ""},
        "admins": [entry(p) for p in players if p["id"] in admins],
        "players": [entry(p) for p in players if p["id"] not in admins],
    }

    clean = lambda s: s.replace("\t", " ").replace("\n", " ")
    out = ["===STATE===", json.dumps(state), "===STATUS===", *lines,
           "===JSON===", json.dumps(widget), "===NOTIFY==="]
    out += [f"{clean(t)}\t{clean(b)}" for t, b in notify]
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
