"""Valheim Mod Manager: a small web UI for the BepInEx mods on the AMP-hosted Valheim server.

Every account signs in with a password plus an authenticator-app code (TOTP).
Listens on 127.0.0.1 only: used on the server PC itself, and reached remotely
through `tailscale serve` (HTTPS, tailnet-only).
"""

import functools
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyotp
import qrcode
import qrcode.image.svg
from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

from mods import ModError, ModStore, parse_package_ref

BEPINEX_DIR = os.environ.get("MODMGR_BEPINEX_DIR", "/bepinex")
DATA_DIR = Path(os.environ.get("MODMGR_DATA_DIR", "/data"))
HOST = os.environ.get("MODMGR_HOST", "127.0.0.1")
PORT = int(os.environ.get("MODMGR_PORT", "8095"))
SECURE_COOKIES = os.environ.get("MODMGR_SECURE_COOKIES", "0") == "1"

MIN_PASSWORD = 12
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
PRE_AUTH_SECONDS = 5 * 60
# Signed out after this long without clicking anything, and after this long no matter what.
IDLE_MINUTES = int(os.environ.get("MODMGR_IDLE_MINUTES", "30"))
MAX_SESSION_HOURS = int(os.environ.get("MODMGR_MAX_SESSION_HOURS", "4"))
TOTP_ISSUER = "Valheim Mod Manager"

DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"
AUDIT_FILE = DATA_DIR / "audit.log"
SECRET_FILE = DATA_DIR / "secret.key"
SETUP_TOKEN_FILE = DATA_DIR / "setup-token.txt"
# Who is signed in right now, read by the "Valheim Server" desktop panel on
# the server PC. Usernames and times only.
PRESENCE_FILE = DATA_DIR / "presence.json"
PRESENCE_EVERY = 20           # seconds between updates for the same user


def _load_secret():
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32))
        SECRET_FILE.chmod(0o600)
    return SECRET_FILE.read_text().strip()


app = Flask(__name__)
app.config.update(
    SECRET_KEY=_load_secret(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=SECURE_COOKIES,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=MAX_SESSION_HOURS),
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)
store = ModStore(BEPINEX_DIR)
lock = threading.Lock()  # one disk-changing action at a time
failures = {}  # key -> [timestamps]


# ---------- users ----------

def load_users():
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    return {}


def save_users(users):
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(users, indent=2), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(USERS_FILE)


def audit(action, user=None):
    who = user or session.get("user") or "-"
    via = request.headers.get("Tailscale-User-Login", "local") if request else "-"
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {who} ({via}) {action}\n"
    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(line)


_presence_lock = threading.Lock()
_presence_written = {}


def note_presence(name, signed_out=False, force=False):
    """Records that name is signed in (until when, and whether over Tailscale), or signed out."""
    now = time.time()
    if not (signed_out or force) and now - _presence_written.get(name, 0) < PRESENCE_EVERY:
        return
    with _presence_lock:
        try:
            data = json.loads(PRESENCE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = {k: v for k, v in data.items() if isinstance(v, dict) and v.get("until", 0) > now}
        if signed_out:
            data.pop(name, None)
            _presence_written.pop(name, None)
        else:
            data[name] = {
                "last": int(now),
                # The same limits that sign people out in before().
                "until": int(min(now + IDLE_MINUTES * 60,
                                 session.get("login_at", now) + MAX_SESSION_HOURS * 3600)),
                "via": "tailscale" if request.headers.get("Tailscale-User-Login") else "local",
            }
            _presence_written[name] = now
        try:
            tmp = PRESENCE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(PRESENCE_FILE)
        except OSError:
            pass                  # only the desktop panel uses this; never break a page for it


def ensure_setup_token():
    """While no accounts exist, the first one can only be created with this token (shown in the logs)."""
    if load_users():
        SETUP_TOKEN_FILE.unlink(missing_ok=True)
        return None
    if not SETUP_TOKEN_FILE.exists():
        SETUP_TOKEN_FILE.write_text(secrets.token_urlsafe(16))
        SETUP_TOKEN_FILE.chmod(0o600)
    token = SETUP_TOKEN_FILE.read_text().strip()
    print(f"*** No accounts yet. First-run setup token: {token} ***", flush=True)
    return token


def check_password_rules(password, confirm):
    if password != confirm:
        return "Passwords don't match."
    if len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    return None


def locked_out(key):
    now = time.time()
    recent = [t for t in failures.get(key, []) if now - t < LOCKOUT_SECONDS]
    failures[key] = recent
    return len(recent) >= MAX_FAILURES


def record_failure(*keys):
    for key in keys:
        failures.setdefault(key, []).append(time.time())


def totp_step(secret, code, after=-1):
    """Which 30-second step a code belongs to (allowing +/-1 step of clock drift), or None.

    Steps at or before `after` are refused, so the same code can't be used twice.
    """
    totp = pyotp.TOTP(secret)
    code = re.sub(r"\s", "", code or "")
    now_step = int(time.time() // 30)
    for step in (now_step - 1, now_step, now_step + 1):
        if step > after and hmac.compare_digest(totp.at(step * 30), code):
            return step
    return None


def verify_totp(users, username, code):
    user = users[username]
    step = totp_step(user["totp_secret"], code, user.get("totp_last_step", -1))
    if step is None:
        return False
    user["totp_last_step"] = step
    save_users(users)
    return True


# ---------- request plumbing ----------

@app.before_request
def before():
    if request.endpoint == "static":
        return
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        if not hmac.compare_digest(sent, session["csrf"]):
            abort(400, "Form expired; go back, reload the page and try again.")
    users = load_users()
    if not users and request.endpoint != "first_run":
        return redirect(url_for("first_run"))
    g.user = None
    name = session.get("user")
    if name and name in users:
        now = time.time()
        if now - session.get("last_seen", 0) > IDLE_MINUTES * 60:
            reason = f"Signed out after {IDLE_MINUTES} minutes without activity."
        elif now - session.get("login_at", 0) > MAX_SESSION_HOURS * 3600:
            reason = f"Signed out: sign-ins last at most {MAX_SESSION_HOURS} hours."
        else:
            reason = None
            session["last_seen"] = now
            g.user = dict(users[name], username=name)
            note_presence(name)
        if reason:
            note_presence(name, signed_out=True)
            session.clear()
            session["csrf"] = secrets.token_hex(16)
            flash(reason)


@app.after_request
def security_headers(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.context_processor
def template_globals():
    return {"csrf_token": session.get("csrf", ""), "current_user": g.get("user")}


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        if g.user.get("must_change_password") and request.endpoint != "account":
            flash("Please choose a new password before continuing.")
            return redirect(url_for("account"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user.get("is_admin"):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


# ---------- first run / login ----------

@app.route("/first-run", methods=["GET", "POST"])
def first_run():
    if load_users():
        return redirect(url_for("login"))
    token = ensure_setup_token()
    if request.method == "POST":
        if locked_out("first-run"):
            flash("Too many wrong attempts. Wait 15 minutes.")
        elif not hmac.compare_digest(request.form.get("token", "").strip(), token):
            record_failure("first-run")
            flash("Setup token is wrong. It's printed in the container log (podman logs valheim-modmgr).")
        else:
            username = request.form.get("username", "").strip().lower()
            error = check_password_rules(request.form.get("password", ""), request.form.get("confirm", ""))
            if not re.fullmatch(r"[a-z0-9_.-]{2,32}", username):
                error = "Username: 2-32 characters, letters/numbers/._- only."
            if error:
                flash(error)
            else:
                save_users({username: {
                    "password_hash": generate_password_hash(request.form["password"]),
                    "is_admin": True,
                    "totp_secret": None,
                    "must_change_password": False,
                }})
                SETUP_TOKEN_FILE.unlink(missing_ok=True)
                audit("created first admin account", username)
                flash("Account created. Sign in, then set up your authenticator app.")
                return redirect(url_for("login"))
    return render_template("first_run.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        # Lockout is per username: every request arrives from 127.0.0.1 (local or via tailscale serve).
        username = request.form.get("username", "").strip().lower()
        if locked_out(username):
            flash("Too many failed sign-ins. Wait 15 minutes and try again.")
            return render_template("login.html")
        users = load_users()
        user = users.get(username)
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["csrf"] = secrets.token_hex(16)
            session["pre_auth"] = username
            session["pre_auth_at"] = time.time()
            if user.get("totp_secret"):
                return redirect(url_for("login_2fa"))
            return redirect(url_for("setup_2fa"))
        record_failure(username)
        audit(f"failed sign-in for '{username}'", "-")
        flash("Wrong username or password.")
    return render_template("login.html")


def pending_user():
    name = session.get("pre_auth")
    if not name or time.time() - session.get("pre_auth_at", 0) > PRE_AUTH_SECONDS:
        return None
    return name


def finish_login(username):
    session.pop("pre_auth", None)
    session.pop("pre_auth_at", None)
    session.pop("new_totp", None)
    session["user"] = username
    session["login_at"] = session["last_seen"] = time.time()
    session.permanent = True
    audit("signed in", username)
    note_presence(username, force=True)


@app.route("/login/2fa", methods=["GET", "POST"])
def login_2fa():
    username = pending_user()
    if not username:
        return redirect(url_for("login"))
    if request.method == "POST":
        users = load_users()
        if locked_out(username):
            flash("Too many failed attempts. Wait 15 minutes.")
        elif verify_totp(users, username, request.form.get("code")):
            finish_login(username)
            return redirect(url_for("index"))
        else:
            record_failure(username)
            audit("wrong 2FA code", username)
            flash("That code didn't work. Codes change every 30 seconds — try the current one.")
    return render_template("login_2fa.html")


@app.route("/login/setup-2fa", methods=["GET", "POST"])
def setup_2fa():
    username = pending_user()
    if not username:
        return redirect(url_for("login"))
    users = load_users()
    if users[username].get("totp_secret"):
        return redirect(url_for("login_2fa"))
    if "new_totp" not in session:
        session["new_totp"] = pyotp.random_base32()
    secret = session["new_totp"]
    if request.method == "POST":
        step = totp_step(secret, request.form.get("code"))
        if step is not None:
            users[username]["totp_secret"] = secret
            users[username]["totp_last_step"] = step
            save_users(users)
            audit("enrolled authenticator app", username)
            finish_login(username)
            flash("Authenticator app set up. You'll need a code from it every time you sign in.")
            return redirect(url_for("index"))
        flash("That code didn't match. Check the phone's clock is set automatically, then try again.")
    uri = pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=TOTP_ISSUER)
    svg = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8).to_string(encoding="unicode")
    return render_template("setup_2fa.html", qr=Markup(svg), secret=secret)


@app.post("/logout")
def logout():
    if session.get("user"):
        audit("signed out")
        note_presence(session["user"], signed_out=True)
    session.clear()
    return redirect(url_for("login"))


# ---------- account / users ----------

@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        users = load_users()
        me = users[g.user["username"]]
        error = check_password_rules(request.form.get("password", ""), request.form.get("confirm", ""))
        if not check_password_hash(me["password_hash"], request.form.get("current", "")):
            error = "Current password is wrong."
        if error:
            flash(error)
        else:
            me["password_hash"] = generate_password_hash(request.form["password"])
            me["must_change_password"] = False
            save_users(users)
            audit("changed own password")
            flash("Password changed.")
            return redirect(url_for("index"))
    return render_template("account.html")


@app.route("/users", methods=["GET", "POST"])
@admin_required
def users_page():
    users = load_users()
    if request.method == "POST":
        action = request.form.get("action")
        target = request.form.get("username", "").strip().lower()
        if action == "add":
            error = check_password_rules(request.form.get("password", ""), request.form.get("password", ""))
            if not re.fullmatch(r"[a-z0-9_.-]{2,32}", target):
                error = "Username: 2-32 characters, letters/numbers/._- only."
            elif target in users:
                error = "That username already exists."
            if error:
                flash(error)
            else:
                users[target] = {
                    "password_hash": generate_password_hash(request.form["password"]),
                    "is_admin": request.form.get("is_admin") == "on",
                    "totp_secret": None,
                    "must_change_password": True,
                }
                save_users(users)
                audit(f"added user {target}")
                flash(f"Added {target}. They set up their authenticator app and a new password on first sign-in.")
        elif target not in users:
            flash("No such user.")
        elif target == g.user["username"]:
            flash("Use the Account page for your own account.")
        elif action == "reset_2fa":
            users[target]["totp_secret"] = None
            save_users(users)
            audit(f"reset 2FA for {target}")
            flash(f"{target} will set up a new authenticator app at next sign-in.")
        elif action == "reset_password":
            error = check_password_rules(request.form.get("password", ""), request.form.get("password", ""))
            if error:
                flash(error)
            else:
                users[target]["password_hash"] = generate_password_hash(request.form["password"])
                users[target]["must_change_password"] = True
                save_users(users)
                audit(f"reset password for {target}")
                flash(f"Temporary password set for {target}.")
        elif action == "delete":
            del users[target]
            save_users(users)
            audit(f"deleted user {target}")
            flash(f"Deleted {target}.")
        return redirect(url_for("users_page"))
    return render_template("users.html", users=users)


# ---------- mods ----------

def with_store(action_name, fn):
    """Run a disk-changing action, flash the outcome, and remind about the restart."""
    with lock:
        try:
            store.check_ready()
            result = fn()
        except ModError as e:
            flash(f"Error: {e}")
            return False
    audit(action_name)
    flash(f"Done: {action_name}. Restart the Valheim server in AMP for this to take effect.")
    return result if result is not None else True


@app.route("/")
@login_required
def index():
    error = None
    managed, unmanaged, dupes = [], [], []
    try:
        store.check_ready()
        managed, unmanaged = store.list_mods()
        dupes = store.duplicate_warnings()
    except (ModError, OSError, ValueError) as e:
        error = str(e)
    updates = session.pop("updates", None)
    return render_template("index.html", managed=managed, unmanaged=unmanaged, dupes=dupes,
                           updates=updates, error=error)


@app.post("/check-updates")
@login_required
def check_updates():
    try:
        updates, errors = store.check_updates()
    except ModError as e:
        flash(f"Error: {e}")
        return redirect(url_for("index"))
    for err in errors:
        flash(f"Couldn't check: {err}")
    session["updates"] = updates
    if not updates and not errors:
        flash("Everything is up to date.")
    return redirect(url_for("index"))


@app.route("/install", methods=["GET", "POST"])
@login_required
def install():
    """GET with ?ref= shows the plan; POST with confirm=1 carries it out."""
    ref = request.values.get("ref", "").strip()
    version = request.values.get("version", "").strip() or None
    plan, notes, versions = None, [], None
    if ref:
        try:
            ns, name, ref_version = parse_package_ref(ref)
            version = version or ref_version
            plan, notes = store.plan_install(ns, name, version)
        except ModError as e:
            flash(f"Error: {e}")
        if plan:
            try:
                versions = store.ts.versions(plan[-1]["namespace"], plan[-1]["name"])
            except ModError:
                versions = None  # the page falls back to a text box
    if request.method == "POST" and request.form.get("confirm") == "1" and plan:
        top = plan[-1]
        done = with_store(f"{top['action']} {top['full_name']} {top['version']} (with dependencies)",
                          lambda: store.apply_plan(plan))
        if done is not False:
            return redirect(url_for("index"))
    return render_template("install.html", ref=ref, version=version or "", plan=plan, notes=notes,
                           versions=versions)


@app.route("/update-all", methods=["GET", "POST"])
@login_required
def update_all():
    """Checklist of every mod with a newer version. Held (pinned) mods start unchecked."""
    try:
        updates, errors = store.check_updates()
    except ModError as e:
        flash(f"Error: {e}")
        return redirect(url_for("index"))
    for err in errors:
        flash(f"Couldn't check: {err}")
    candidates = []
    for mod in store.list_mods()[0]:
        if mod["full_name"] in updates:
            candidates.append(dict(mod, latest=updates[mod["full_name"]]))
    if request.method == "POST":
        chosen = [c for c in candidates if c["full_name"] in request.form.getlist("mods")]
        if not chosen:
            flash("Nothing selected.")
            return redirect(url_for("update_all"))

        def run():
            done = []
            for c in chosen:  # re-plan each one, so later mods see what earlier ones changed
                steps, _ = store.plan_install(c["namespace"], c["name"], None)
                done += store.apply_plan(steps)
            return done
        names = ", ".join(f"{c['name']} {c['latest']}" for c in chosen)
        if with_store(f"update all: {names}", run) is not False:
            return redirect(url_for("index"))
    return render_template("update_all.html", candidates=candidates)


@app.post("/mod/<full_name>/<action>")
@login_required
def mod_action(full_name, action):
    if action in ("disable", "uninstall") and request.form.get("confirm") != "1":
        deps = store.dependents(full_name)
        return render_template("confirm.html", full_name=full_name, action=action, dependents=deps)
    if action == "enable":
        with_store(f"enable {full_name}", lambda: store.set_enabled(full_name, True))
    elif action == "disable":
        with_store(f"disable {full_name}", lambda: store.set_enabled(full_name, False))
    elif action == "uninstall":
        with_store(f"uninstall {full_name}", lambda: store.uninstall(full_name))
    else:
        abort(404)
    return redirect(url_for("index"))


@app.post("/unmanaged/<path:name>/<action>")
@login_required
def unmanaged_action(name, action):
    enabled = request.form.get("enabled") == "1"
    if action == "enable":
        with_store(f"enable loose item {name}", lambda: store.set_unmanaged_enabled(name, True))
    elif action == "disable":
        with_store(f"disable loose item {name}", lambda: store.set_unmanaged_enabled(name, False))
    elif action == "delete":
        if request.form.get("confirm") != "1":
            return render_template("confirm.html", full_name=name, action="delete", dependents=[],
                                   unmanaged_enabled=enabled)
        with_store(f"delete loose item {name}", lambda: store.delete_unmanaged(name, enabled))
    else:
        abort(404)
    return redirect(url_for("index"))


@app.route("/configs")
@login_required
def configs():
    try:
        files = store.list_configs()
    except OSError as e:
        flash(f"Error: {e}")
        files = []
    return render_template("configs.html", files=files)


@app.route("/configs/<filename>", methods=["GET", "POST"])
@login_required
def config_edit(filename):
    try:
        path = store.config_path(filename)
    except ModError:
        abort(404)
    if request.method == "POST":
        with_store(f"edit config {filename}", lambda: store.save_config(filename, request.form.get("text", "")))
        return redirect(url_for("config_edit", filename=filename))
    return render_template("config_edit.html", filename=filename, text=path.read_text(encoding="utf-8-sig"))


@app.route("/player-list")
@login_required
def player_list():
    managed, _ = store.list_mods()
    return render_template("player_list.html", mods=[m for m in managed if m["enabled"]])


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
def http_error(e):
    return render_template("error.html", error=e), e.code


if __name__ == "__main__":
    from waitress import serve
    ensure_setup_token()
    print(f"Valheim Mod Manager on http://{HOST}:{PORT}  (BepInEx: {BEPINEX_DIR})", flush=True)
    serve(app, host=HOST, port=PORT, threads=4)
