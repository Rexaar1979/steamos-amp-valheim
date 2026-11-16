"""Thunderstore lookups and BepInEx plugin-folder management for the Valheim server.

Layout this module maintains inside the server's BepInEx folder (same idea as r2modman):
    plugins/<Namespace-Name>/            enabled mod (contains .modmanager.json)
    plugins-disabled/<Namespace-Name>/   disabled mod (BepInEx never looks here)
    patchers/<Namespace-Name>/           preloader patchers, if a mod ships any
    plugins-disabled/_patchers/<Namespace-Name>/
    plugins-disabled/_unmanaged/         loose files/folders we didn't install, when disabled
    config/                              mod .cfg files (shared, never deleted by us)
"""

import io
import json
import re
import shutil
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

API = "https://thunderstore.io/api/experimental/package"
# Version list for one package. This is the API behind Thunderstore's newer website, not a documented
# one, so the install page falls back to a plain text box if it stops working.
VERSIONS_API = "https://thunderstore.io/api/cyberstorm/package/{namespace}/{name}/versions/"
COMMUNITY = "valheim"
# AMP installs and updates BepInEx itself (Configuration -> Updates -> Install BepInEx).
BEPINEX_PACK = "denikson-BepInExPack_Valheim"
META = ".modmanager.json"
MAX_DOWNLOAD = 300 * 1024 * 1024
USER_AGENT = "valheim-mod-manager/1.0 (self-hosted)"
CACHE_SECONDS = 600


class ModError(Exception):
    pass


def parse_version(v):
    return tuple(int(x) if x.isdigit() else 0 for x in str(v).split("."))


def parse_dependency(dep):
    """'ValheimModding-Jotunn-2.30.2' -> ('ValheimModding', 'Jotunn', '2.30.2')."""
    parts = dep.rsplit("-", 2)
    if len(parts) != 3:
        raise ModError(f"Unrecognized dependency string: {dep}")
    return parts[0], parts[1], parts[2]


def parse_package_ref(text):
    """Accept a Thunderstore link, 'Namespace-Name', 'Namespace/Name' or 'Namespace-Name-1.2.3'.

    Returns (namespace, name, version_or_None).
    """
    text = text.strip()
    m = re.search(r"thunderstore\.io/(?:c/[^/]+/)?(?:p|package)/([^/]+)/([^/?#]+)(?:/v/([0-9.]+))?", text)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = re.fullmatch(r"([\w.]+)[/-]([\w.]+?)(?:-(\d+(?:\.\d+)+))?", text)
    if m:
        return m.group(1), m.group(2), m.group(3)
    raise ModError("Paste a Thunderstore link, or type it as Author-ModName (e.g. Vapok-AdventureBackpacks).")


class Thunderstore:
    def __init__(self):
        self._cache = {}

    def _get_json(self, url):
        hit = self._cache.get(url)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise ModError(f"Not found on Thunderstore: {url}") from None
            raise ModError(f"Thunderstore error {e.code} for {url}") from None
        except urllib.error.URLError as e:
            raise ModError(f"Could not reach Thunderstore: {e.reason}") from None
        self._cache[url] = (time.time(), data)
        return data

    def package(self, namespace, name):
        """Package overview, including the latest version and Valheim categories."""
        data = self._get_json(f"{API}/{namespace}/{name}/")
        listing = next((c for c in data.get("community_listings", []) if c.get("community") == COMMUNITY), None)
        if listing is None:
            raise ModError(f"{namespace}-{name} is not a Valheim mod on Thunderstore.")
        return {
            "namespace": data["namespace"],
            "name": data["name"],
            "latest": data["latest"]["version_number"],
            "deprecated": data.get("is_deprecated", False),
            "categories": listing.get("categories", []),
            "package_url": f"https://thunderstore.io/c/{COMMUNITY}/p/{data['namespace']}/{data['name']}/",
        }

    def version(self, namespace, name, version=None):
        """One specific version (or the latest): download URL, dependencies, description."""
        if version is None:
            version = self.package(namespace, name)["latest"]
        data = self._get_json(f"{API}/{namespace}/{name}/{version}/")
        return {
            "namespace": data["namespace"],
            "name": data["name"],
            "version": data["version_number"],
            "description": data.get("description", ""),
            "dependencies": data.get("dependencies", []),
            "download_url": data["download_url"],
        }

    def versions(self, namespace, name):
        """All published versions, newest first: [{'version': '2.4.5', 'date': '2026-10-18'}, ...]."""
        data = self._get_json(VERSIONS_API.format(namespace=namespace, name=name))
        try:
            out = [{"version": v["version_number"], "date": v.get("datetime_created", "")[:10]} for v in data]
        except (TypeError, KeyError):
            raise ModError("Unexpected version list format from Thunderstore.") from None
        return sorted(out, key=lambda v: parse_version(v["version"]), reverse=True)

    def download(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read(MAX_DOWNLOAD + 1)
        except urllib.error.URLError as e:
            raise ModError(f"Download failed: {e}") from None
        if len(data) > MAX_DOWNLOAD:
            raise ModError("Download is larger than 300 MB; refusing.")
        return data


class ModStore:
    def __init__(self, bepinex_dir, thunderstore=None):
        self.root = Path(bepinex_dir)
        self.plugins = self.root / "plugins"
        self.disabled = self.root / "plugins-disabled"
        self.patchers = self.root / "patchers"
        self.disabled_patchers = self.disabled / "_patchers"
        self.unmanaged_disabled = self.disabled / "_unmanaged"
        self.config = self.root / "config"
        self.staging = self.root / ".modmanager-staging"
        self.ts = thunderstore or Thunderstore()

    def check_ready(self):
        if not self.plugins.is_dir():
            raise ModError(f"BepInEx plugins folder not found at {self.plugins}. "
                           "Is BepInEx installed in AMP and the folder mounted correctly?")

    # ---------- reading ----------

    def _read_mod(self, folder, enabled):
        meta_file = folder / META
        manifest_file = folder / "manifest.json"
        if meta_file.is_file():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        elif manifest_file.is_file() and "-" in folder.name:
            # Folder copied from an r2modman profile: Namespace-Name/manifest.json
            manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
            namespace = folder.name.split("-", 1)[0]
            meta = {
                "namespace": namespace,
                "name": manifest.get("name", folder.name.split("-", 1)[1]),
                "version": manifest.get("version_number", "?"),
                "dependencies": manifest.get("dependencies", []),
                "description": manifest.get("description", ""),
                "installed_at": None,
                "source": "r2modman folder",
            }
        else:
            return None
        meta["full_name"] = f"{meta['namespace']}-{meta['name']}"
        meta["enabled"] = enabled
        meta["folder"] = folder
        return meta

    def list_mods(self):
        """Returns (managed_mods, unmanaged_items)."""
        managed, unmanaged = [], []
        for base, enabled in ((self.plugins, True), (self.disabled, False)):
            if not base.is_dir():
                continue
            for item in sorted(base.iterdir(), key=lambda p: p.name.lower()):
                if item.name.startswith("_") and base == self.disabled:
                    continue
                mod = self._read_mod(item, enabled) if item.is_dir() else None
                if mod:
                    managed.append(mod)
                elif enabled:
                    unmanaged.append({"name": item.name, "enabled": True, "is_dir": item.is_dir()})
        if self.unmanaged_disabled.is_dir():
            for item in sorted(self.unmanaged_disabled.iterdir(), key=lambda p: p.name.lower()):
                unmanaged.append({"name": item.name, "enabled": False, "is_dir": item.is_dir()})
        managed.sort(key=lambda m: m["full_name"].lower())
        return managed, unmanaged

    def get(self, full_name):
        for mod in self.list_mods()[0]:
            if mod["full_name"].lower() == full_name.lower():
                return mod
        return None

    def dependents(self, full_name):
        """Installed mods that list full_name as a dependency."""
        out = []
        for mod in self.list_mods()[0]:
            for dep in mod.get("dependencies", []):
                try:
                    ns, name, _ = parse_dependency(dep)
                except ModError:
                    continue
                if f"{ns}-{name}".lower() == full_name.lower():
                    out.append(mod["full_name"])
        return out

    def duplicate_warnings(self):
        """Loose DLLs in plugins/ that also exist inside a managed mod (would load twice)."""
        managed, unmanaged = self.list_mods()
        managed_dlls = {}
        for mod in managed:
            if mod["enabled"]:
                for dll in mod["folder"].rglob("*.dll"):
                    managed_dlls[dll.name.lower()] = mod["full_name"]
        return [(u["name"], managed_dlls[u["name"].lower()]) for u in unmanaged
                if u["enabled"] and u["name"].lower() in managed_dlls]

    def check_updates(self):
        """{full_name: latest_version} for managed mods with a newer version on Thunderstore."""
        updates, errors = {}, []
        for mod in self.list_mods()[0]:
            try:
                pkg = self.ts.package(mod["namespace"], mod["name"])
            except ModError as e:
                errors.append(str(e))
                continue
            if parse_version(pkg["latest"]) > parse_version(mod["version"]):
                updates[mod["full_name"]] = pkg["latest"]
        return updates, errors

    # ---------- planning ----------

    def plan_install(self, namespace, name, version=None):
        """Work out everything an install needs, without touching disk.

        The requested mod gets `version` (latest if None). Dependencies get the exact version the
        mod asks for, unless an equal or newer one is already installed.
        """
        steps, notes, seen = [], [], {}

        def visit(ns, nm, ver, required_by):
            full = f"{ns}-{nm}"
            if full.lower() == BEPINEX_PACK.lower():
                notes.append(f"{required_by} needs BepInExPack {ver} — BepInEx is managed by AMP, not here.")
                return
            if full.lower() in seen:
                prior = seen[full.lower()]
                if ver and parse_version(ver) > parse_version(prior["version"]):
                    notes.append(f"{required_by} wants {full} {ver}, newer than the {prior['version']} "
                                 "planned; check versions match what players have.")
                return
            info = self.ts.version(ns, nm, ver)
            pkg = self.ts.package(ns, nm)
            installed = self.get(info["namespace"] + "-" + info["name"])
            if installed is None:
                action = "install"
            elif parse_version(info["version"]) > parse_version(installed["version"]):
                action = "update"
            elif required_by is None:
                older = parse_version(info["version"]) < parse_version(installed["version"])
                action = "downgrade" if older else "reinstall"
            else:
                action = "keep" if installed["enabled"] else "enable"
            step = {
                "full_name": f"{info['namespace']}-{info['name']}",
                "namespace": info["namespace"],
                "name": info["name"],
                "version": info["version"],
                "latest": pkg["latest"],
                "installed_version": installed["version"] if installed else None,
                "action": action,
                "required_by": required_by,
                "categories": pkg["categories"],
                "deprecated": pkg["deprecated"],
                # Picking anything but the latest "holds" the mod there: Update all leaves it unchecked.
                "pinned": required_by is None and info["version"] != pkg["latest"],
            }
            seen[full.lower()] = step
            if action != "keep":
                for dep in info["dependencies"]:
                    dns, dnm, dver = parse_dependency(dep)
                    dep_installed = self.get(f"{dns}-{dnm}")
                    if dep_installed and dep_installed["enabled"] and \
                            parse_version(dep_installed["version"]) >= parse_version(dver):
                        continue
                    visit(dns, dnm, dver, step["full_name"])
            steps.append(step)  # dependencies land before the mods that need them

        visit(namespace, name, version, None)
        return steps, notes

    # ---------- changing disk ----------

    def apply_plan(self, steps):
        self.check_ready()
        done = []
        for step in steps:
            if step["action"] in ("install", "update", "downgrade", "reinstall"):
                self.install(step["namespace"], step["name"], step["version"], pinned=step["pinned"])
                done.append(f"{step['action']} {step['full_name']} {step['version']}")
            elif step["action"] == "enable":
                self.set_enabled(step["full_name"], True)
                done.append(f"enable {step['full_name']}")
        return done

    def install(self, namespace, name, version, pinned=False):
        info = self.ts.version(namespace, name, version)
        full = f"{info['namespace']}-{info['name']}"
        existing = self.get(full)
        enabled = existing["enabled"] if existing else True
        blob = self.ts.download(info["download_url"])

        shutil.rmtree(self.staging, ignore_errors=True)
        stage_plugins = self.staging / "plugins"
        stage_patchers = self.staging / "patchers"
        stage_config = self.staging / "config"
        try:
            zf = zipfile.ZipFile(io.BytesIO(blob))
        except zipfile.BadZipFile:
            raise ModError(f"{full} {info['version']}: download is not a valid zip.") from None
        for entry in zf.infolist():
            rel = self._safe_relpath(entry.filename)
            if rel is None:
                continue
            top = rel.parts[0].lower()
            if top == "bepinex" and len(rel.parts) > 1:
                rel = PurePosixPath(*rel.parts[1:])
                top = rel.parts[0].lower()
            if top == "plugins" and len(rel.parts) > 1:
                dest = stage_plugins / PurePosixPath(*rel.parts[1:])
            elif top == "patchers" and len(rel.parts) > 1:
                dest = stage_patchers / PurePosixPath(*rel.parts[1:])
            elif top == "config" and len(rel.parts) > 1:
                dest = stage_config / PurePosixPath(*rel.parts[1:])
            elif top in ("core", "monomod"):
                continue  # BepInEx internals; AMP manages BepInEx
            else:
                dest = stage_plugins / rel
            if entry.is_dir():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(entry) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)

        stage_plugins.mkdir(parents=True, exist_ok=True)
        meta = {
            "namespace": info["namespace"],
            "name": info["name"],
            "version": info["version"],
            "dependencies": info["dependencies"],
            "description": info["description"],
            "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "thunderstore",
            "has_patchers": stage_patchers.is_dir(),
            "pinned": pinned,
        }
        (stage_plugins / META).write_text(json.dumps(meta, indent=2), encoding="utf-8")

        # Swap the old version out for the new one.
        self._remove_mod_files(full)
        plugin_dest = (self.plugins if enabled else self.disabled) / full
        plugin_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stage_plugins), str(plugin_dest))
        if stage_patchers.is_dir():
            patch_dest = (self.patchers if enabled else self.disabled_patchers) / full
            patch_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stage_patchers), str(patch_dest))
        if stage_config.is_dir():
            # Default configs only; never overwrite settings someone already tuned.
            for src in stage_config.rglob("*"):
                if src.is_file():
                    dest = self.config / src.relative_to(stage_config)
                    if not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dest)
        shutil.rmtree(self.staging, ignore_errors=True)
        return meta

    @staticmethod
    def _safe_relpath(name):
        """Normalize a zip entry name; None if it is empty or tries to escape the target."""
        name = name.replace("\\", "/").strip("/")
        if not name:
            return None
        rel = PurePosixPath(name)
        if rel.is_absolute() or any(p in ("..", "") for p in rel.parts) or ":" in rel.parts[0]:
            raise ModError(f"Refusing unsafe path in zip: {name}")
        return rel

    def _remove_mod_files(self, full):
        for path in (self.plugins / full, self.disabled / full,
                     self.patchers / full, self.disabled_patchers / full):
            if path.exists():
                shutil.rmtree(path)

    def set_enabled(self, full_name, enabled):
        mod = self.get(full_name)
        if mod is None:
            raise ModError(f"{full_name} is not installed.")
        full = mod["full_name"]
        src_base, dst_base = (self.disabled, self.plugins) if enabled else (self.plugins, self.disabled)
        psrc, pdst = (self.disabled_patchers, self.patchers) if enabled else (self.patchers, self.disabled_patchers)
        if (src_base / full).exists():
            dst_base.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_base / full), str(dst_base / full))
        if (psrc / full).exists():
            pdst.mkdir(parents=True, exist_ok=True)
            shutil.move(str(psrc / full), str(pdst / full))

    def uninstall(self, full_name):
        mod = self.get(full_name)
        if mod is None:
            raise ModError(f"{full_name} is not installed.")
        self._remove_mod_files(mod["full_name"])

    # ---------- unmanaged loose files ----------

    def _unmanaged_path(self, name, enabled):
        if "/" in name or "\\" in name or name in ("", ".", ".."):
            raise ModError("Bad file name.")
        return (self.plugins if enabled else self.unmanaged_disabled) / name

    def set_unmanaged_enabled(self, name, enabled):
        src = self._unmanaged_path(name, not enabled)
        dst = self._unmanaged_path(name, enabled)
        if not src.exists():
            raise ModError(f"{name} not found.")
        if dst.exists():
            raise ModError(f"{name} already exists at the destination.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))

    def delete_unmanaged(self, name, enabled):
        path = self._unmanaged_path(name, enabled)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        else:
            raise ModError(f"{name} not found.")

    # ---------- config files ----------

    def list_configs(self):
        if not self.config.is_dir():
            return []
        return sorted(p.name for p in self.config.glob("*.cfg"))

    def config_path(self, filename):
        if filename not in self.list_configs():
            raise ModError("Unknown config file.")
        return self.config / filename

    def save_config(self, filename, text):
        path = self.config_path(filename)
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        path.write_text(text.replace("\r\n", "\n"), encoding="utf-8")
