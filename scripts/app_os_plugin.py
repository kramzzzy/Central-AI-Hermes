"""Install the bundled native extension into an explicitly selected OS profile."""
import contextlib
import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4

NAME = "central-ai-app-os"
FILES = ("plugin.yaml", "__init__.py", "client.py")
SOURCE = Path(__file__).resolve().parent.parent / "hermes_plugins" / "central_ai_app_os"


def profile_home(home):
    original = Path(home)
    home = original.resolve(strict=True)
    if (original.is_symlink() or home.name == "default" or
            not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", home.name) or
            home.parent.name != "profiles" or not (home / "config.yaml").is_file() or
            (home / "config.yaml").is_symlink()):
        raise RuntimeError("Choose an existing named Hermes profile")
    return home


@contextlib.contextmanager
def profile_lock(home):
    # The adapter runs on Linux. The selected-profile file lock serializes our
    # installer and conversation configuration; it does not lock other profiles.
    home = profile_home(home)
    lock_path = home / ".app-os-install.lock"
    if lock_path.is_symlink():
        raise RuntimeError("The selected-profile lock must be local")
    with lock_path.open("a+b") as lock:
        os.chmod(lock.name, 0o600)
        if os.name == "nt":
            import msvcrt
            if lock.tell() == 0:
                lock.write(b"0"); lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield home
        finally:
            if os.name == "nt":
                lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def install_files(home, config, allow_enable=False):
    """Caller holds profile_lock and writes config atomically after this returns.

    Only the bundled three public files are installed. No native identity,
    session, provider, memory or voice files are copied or modified.
    """
    import yaml
    plugins = config.setdefault("plugins", {})
    enabled = plugins.setdefault("enabled", [])
    disabled = plugins.setdefault("disabled", [])
    entries = plugins.setdefault("entries", {})
    entry = entries.setdefault(NAME, {})
    if not isinstance(enabled, list) or not isinstance(disabled, list) or not isinstance(entry, dict):
        raise RuntimeError("Unsupported native plugin configuration")
    if (NAME in disabled or entry.get("enabled") is False) and not allow_enable:
        raise RuntimeError("App OS plugin is disabled; enable it explicitly before reconnecting")
    folder = home / "plugins"
    if folder.is_symlink():
        raise RuntimeError("The selected profile's plugin directory must be local")
    folder.mkdir(mode=0o700, exist_ok=True)
    destination = folder / NAME
    if destination.is_symlink():
        raise RuntimeError("The App OS plugin destination must be local")
    if destination.exists():
        manifest = yaml.safe_load((destination / "plugin.yaml").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("name") != NAME:
            raise RuntimeError("Existing App OS plugin identity needs operator review")
    source_manifest = yaml.safe_load((SOURCE / "plugin.yaml").read_text(encoding="utf-8"))
    if source_manifest.get("name") != NAME or source_manifest.get("version") != "1.0.0":
        raise RuntimeError("Bundled App OS plugin is invalid")
    destination.mkdir(mode=0o700, exist_ok=True)
    backup = None
    for filename in FILES:
        source = SOURCE / filename
        target = destination / filename
        if source.is_symlink() or target.is_symlink():
            raise RuntimeError("Plugin files must be regular local files")
        content = source.read_bytes()
        if target.exists() and target.read_bytes() == content:
            continue
        if target.exists():
            if backup is None:
                backup = home / "app-os-backups" / uuid4().hex
                backup.mkdir(parents=True, mode=0o700)
            (backup / filename).write_bytes(target.read_bytes())
            os.chmod(backup / filename, 0o600)
        temporary = target.with_name(filename + "." + uuid4().hex + ".tmp")
        temporary.write_bytes(content)
        os.chmod(temporary, 0o600)
        temporary.replace(target)
    if NAME not in enabled:
        enabled.append(NAME)
    if allow_enable:
        disabled[:] = [value for value in disabled if value != NAME]
        entry.pop("enabled", None)
    if "michael_os" in config.get("agent", {}).get("disabled_toolsets", []):
        raise RuntimeError("App OS workspace tools are disabled in this profile")
    platforms = config.setdefault("platform_toolsets", {})
    toolsets = platforms.setdefault("cli", ["hermes-cli"])
    if not isinstance(toolsets, list):
        raise RuntimeError("Unsupported native CLI tool selection")
    for ts in ["browser", "web", "memory", "todo", "skills", "michael_os", "laya"]:
        if ts not in toolsets:
            toolsets.append(ts)
    browser_cfg = config.setdefault("browser", {})
    if isinstance(browser_cfg, dict):
        browser_cfg["backend"] = "off"
    return destination


def installed(home):
    """Local package/config check only; does not assert model or service health."""
    import yaml
    try:
        home = profile_home(home)
        config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        plugins = config.get("plugins", {})
        if NAME not in plugins.get("enabled", []) or NAME in plugins.get("disabled", []):
            return False
        if plugins.get("entries", {}).get(NAME, {}).get("enabled") is False:
            return False
        target = home / "plugins" / NAME
        return all(not (target / name).is_symlink() and
            hashlib.sha256((target / name).read_bytes()).digest() ==
            hashlib.sha256((SOURCE / name).read_bytes()).digest() for name in FILES)
    except (OSError, ValueError, AttributeError):
        return False
