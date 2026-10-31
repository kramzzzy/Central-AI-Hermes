"""Backend-owned Social Media Sign-In, Page Linker, encrypted credentials and bounded Social tools.

Connects Box 3 (App OS Settings -> Connection) to Box 1 (Hermes /opt/data/social/connections.sqlite).
Supports:
- Meta Business Unified Connector (Facebook Page + Instagram Business in one Sprout-Social-style sign-in & page picker)
- TikTok Business (Sign-In & Account Linker)
- LinkedIn Page & Profile (Sign-In & Organization Page Linker)
- YouTube & Google Business (Channel & Brand Account Linker)
"""
import contextlib
import base64
import hmac
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import UUID

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

PLATFORMS = ("facebook", "instagram", "tiktok", "linkedin", "youtube")

FACEBOOK_SCOPES = [
    "public_profile",
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "pages_manage_metadata",
    "pages_messaging",
    "pages_read_user_content",
    "pages_manage_engagement",
    "instagram_basic",
    "instagram_content_publish",
    "instagram_manage_messages",
    "instagram_manage_comments",
]

TIKTOK_SCOPES = [
    "user.info.basic",
    "video.list",
    "video.publish",
    "video.upload",
]

LINKEDIN_SCOPES = [
    "openid",
    "profile",
    "email",
    "w_member_social",
]

DEFAULT_CAPABILITIES = {
    "facebook": ["read_page", "publish_posts", "manage_comments", "page_insights"],
    "instagram": ["read_profile", "publish_media", "manage_comments", "media_insights"],
    "tiktok": ["read_profile", "publish_video", "video_analytics"],
    "linkedin": ["read_profile", "publish_posts", "page_analytics"],
    "youtube": ["read_channel", "publish_shorts", "channel_analytics"],
}


class SocialError(Exception):
    def __init__(self, message, status=400, rejected=False):
        super().__init__(message)
        self.status = status
        self.rejected = rejected


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def provider_request(url, method="GET", data=None, token=None, form=False, headers_extra=None, timeout=25):
    payload = None
    if data is not None:
        payload = (
            urllib.parse.urlencode(data).encode("utf-8")
            if form
            else json.dumps(data).encode("utf-8")
        )
    headers = {"Accept": "application/json", "User-Agent": "CentralAI-SocialBridge/1.0"}
    if payload is not None:
        headers["Content-Type"] = (
            "application/x-www-form-urlencoded" if form else "application/json"
        )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if isinstance(headers_extra, dict):
        headers.update(headers_extra)
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as resp:
            content = resp.read(2 * 1024 * 1024 + 1)
            if len(content) > 2 * 1024 * 1024:
                raise SocialError("Platform returned too much content.", 413)
            return json.loads(content.decode("utf-8", errors="replace")) if content else {}
    except urllib.error.HTTPError as exc:
        err_msg = ""
        try:
            raw_err = exc.read(8192).decode("utf-8", errors="replace")
            parsed = json.loads(raw_err)
            if isinstance(parsed, dict):
                if isinstance(parsed.get("error"), dict):
                    err_msg = str(parsed["error"].get("message") or "")
                elif isinstance(parsed.get("error_description"), str):
                    err_msg = parsed["error_description"]
                elif isinstance(parsed.get("message"), str):
                    err_msg = parsed["message"]
        except Exception:
            pass
        if exc.code == 401:
            raise SocialError(err_msg or "Social account authorization expired. Reconnect in Settings -> Connection.", 401, rejected=True) from None
        if exc.code == 403:
            raise SocialError(err_msg or "Platform denied this permission. Check Page access and scopes.", 403, rejected=True) from None
        if exc.code == 404:
            raise SocialError(err_msg or "Requested social page or post was not found.", 404, rejected=True) from None
        if exc.code == 429:
            raise SocialError("Social platform rate limit reached. Retry shortly.", 429, rejected=True) from None
        raise SocialError(err_msg or f"Social platform request failed (HTTP {exc.code}).", 502, rejected=exc.code == 400) from None
    except (OSError, ValueError) as exc:
        raise SocialError(f"Could not reach social platform API: {exc}", 502) from None


def validate_org(org):
    if not org:
        return "00000000-0000-0000-0000-000000000000"
    try:
        return str(UUID(str(org)))
    except (ValueError, TypeError, AttributeError):
        raise SocialError("Invalid workspace identifier.", 403, rejected=True) from None


def clean_str(value, label, maximum=1000, empty=False):
    if value is None and empty:
        return ""
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise SocialError(f"Check {label}.")
    return value.strip()


def slugify_handle(name):
    cleaned = re.sub(r"[^a-zA-Z0-9._]", "", str(name or "").lower())
    return cleaned or "businesspage"


class SocialWorkspace:
    def __init__(self, root, transport=provider_request, clock=time.time):
        profile_root = os.environ.get("HERMES_PROFILE_ROOT")
        if profile_root and Path(profile_root).is_dir():
            base_root = Path(profile_root)
        elif Path("/opt/data").is_dir():
            base_root = Path("/opt/data")
        else:
            base_root = Path(root)
        self.root = base_root / "social"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.transport = transport
        self.clock = clock
        self.lock = threading.RLock()
        # (org, platform, account, sender) -> (display name, expiry). Name lookups are best-effort.
        self._names = {}

        from cryptography.fernet import Fernet

        keyfile = self.root / "encryption.key"
        if not keyfile.exists():
            try:
                fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(Fernet.generate_key())
            except FileExistsError:
                pass
        self.cipher = Fernet(keyfile.read_bytes())
        self.dbfile = self.root / "connections.sqlite"
        with self.database() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS platform_config (
                    platform TEXT PRIMARY KEY,
                    encrypted TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS connections (
                    org TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    encrypted TEXT NOT NULL,
                    PRIMARY KEY (org, platform)
                );
                CREATE TABLE IF NOT EXISTS flows (
                    hash TEXT PRIMARY KEY,
                    org TEXT NOT NULL,
                    user TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    binding TEXT NOT NULL,
                    expires REAL NOT NULL,
                    encrypted TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS posts_log (
                    id TEXT PRIMARY KEY,
                    org TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    created REAL NOT NULL,
                    encrypted TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS social_inbox (
                    id TEXT PRIMARY KEY, org TEXT NOT NULL, thread TEXT NOT NULL,
                    received REAL NOT NULL, lease REAL NOT NULL DEFAULT 0,
                    done INTEGER NOT NULL DEFAULT 0, encrypted TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS social_deliveries (
                    org TEXT NOT NULL, id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    encrypted TEXT, PRIMARY KEY(org,id)
                );
                """
            )
        try:
            os.chmod(self.dbfile, 0o600)
        except OSError:
            pass

    @contextlib.contextmanager
    def database(self):
        db = sqlite3.connect(self.dbfile, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def encrypt(self, data):
        return self.cipher.encrypt(
            json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        ).decode("utf-8")

    def decrypt(self, value):
        return json.loads(self.cipher.decrypt(value.encode("utf-8")))

    def _env_config(self, platform):
        app_url = (
            os.environ.get("APP_OS_URL")
            or os.environ.get("MICHAEL_OS_URL")
            or os.environ.get("APP_ORIGIN")
            or "http://localhost:3000"
        ).rstrip("/")
        default_redirect = f"{app_url}/api/connections/social/callback"
        if platform in ("facebook", "instagram"):
            client_id = (
                os.environ.get("FACEBOOK_APP_ID")
                or os.environ.get("META_APP_ID")
                or ""
            ).strip()
            client_secret = (
                os.environ.get("FACEBOOK_APP_SECRET")
                or os.environ.get("META_APP_SECRET")
                or ""
            ).strip()
            redirect_uri = (
                os.environ.get("FACEBOOK_REDIRECT_URI") or default_redirect
            ).strip()
            if client_id and client_secret:
                return {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                }
        elif platform == "tiktok":
            client_id = (
                os.environ.get("TIKTOK_CLIENT_KEY")
                or os.environ.get("TIKTOK_CLIENT_ID")
                or ""
            ).strip()
            client_secret = os.environ.get("TIKTOK_CLIENT_SECRET", "").strip()
            if client_id and client_secret:
                return {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": default_redirect,
                }
        elif platform == "linkedin":
            client_id = os.environ.get("LINKEDIN_CLIENT_ID", "").strip()
            client_secret = os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip()
            if client_id and client_secret:
                return {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": default_redirect,
                }
        return None

    def _is_valid_config(self, platform, cfg):
        if not isinstance(cfg, dict):
            return False
        cid = str(cfg.get("client_id") or "").strip()
        csec = str(cfg.get("client_secret") or "").strip()
        if not cid or not csec:
            return False
        if platform in ("facebook", "instagram") and not cid.isdigit():
            return False
        return True

    def get_config(self, platform):
        env_cfg = self._env_config(platform)
        if env_cfg and self._is_valid_config(platform, env_cfg):
            return env_cfg
        with self.database() as db:
            row = db.execute(
                "SELECT encrypted FROM platform_config WHERE platform=?", (platform,)
            ).fetchone()
        if row:
            cfg = self.decrypt(row["encrypted"])
            if self._is_valid_config(platform, cfg):
                return cfg
            with self.database() as db:
                db.execute(
                    "DELETE FROM platform_config WHERE platform=?", (platform,)
                )
        if platform == "instagram":
            with self.database() as db:
                fb_row = db.execute(
                    "SELECT encrypted FROM platform_config WHERE platform='facebook'"
                ).fetchone()
            if fb_row:
                fb_cfg = self.decrypt(fb_row["encrypted"])
                if self._is_valid_config("facebook", fb_cfg):
                    return fb_cfg
                with self.database() as db:
                    db.execute(
                        "DELETE FROM platform_config WHERE platform='facebook'"
                    )
        return env_cfg

    def save_config(self, platform, cfg):
        with self.database() as db:
            db.execute(
                "INSERT OR REPLACE INTO platform_config VALUES(?,?)",
                (platform, self.encrypt(cfg)),
            )

    def get_connection(self, org, platform):
        with self.database() as db:
            row = db.execute(
                "SELECT encrypted FROM connections WHERE org=? AND platform=?",
                (org, platform),
            ).fetchone()
        if row:
            return self.decrypt(row["encrypted"])
        return None

    def save_connection(self, org, platform, value):
        with self.database() as db:
            db.execute(
                "INSERT OR REPLACE INTO connections VALUES(?,?,?)",
                (org, platform, self.encrypt(value)),
            )

    def _public_platform_status(self, org, platform):
        cfg = self.get_config(platform)
        conn = self.get_connection(org, platform)
        if conn and (
            conn.get("auth_mode") == "session"
            or conn.get("access_token") in (None, "", "box1_managed_session")
        ):
            # Legacy placeholder links (no real provider token) are not connections.
            conn = None
        if not conn:
            return {
                "platform": platform,
                "configured": bool(cfg),
                "client_id": (cfg or {}).get("client_id", ""),
                "state": "ready" if cfg else "not_configured",
                "connected": False,
                "account_id": None,
                "account_name": None,
                "handle": None,
                "category": None,
                "followers_count": None,
                "picture_url": None,
                "pages": [],
                "selected_page_id": None,
                "capabilities": DEFAULT_CAPABILITIES.get(platform, []),
                "checked_at": None,
            }
        pages_public = []
        for p in conn.get("pages") or []:
            if isinstance(p, dict):
                ig_obj = p.get("instagram_business_account")
                pages_public.append(
                    {
                        "id": str(p.get("id") or ""),
                        "name": str(p.get("name") or ""),
                        "handle": p.get("handle") or f"fb.com/{p.get('id')}",
                        "category": p.get("category"),
                        "followers_count": p.get("followers_count") or p.get("fan_count"),
                        "picture_url": p.get("picture_url"),
                        "has_instagram": bool(ig_obj),
                        "messaging_state": p.get("messaging_state", "reconnect_required"),
                        "comments_state": p.get("comments_state", "reconnect_required"),
                        "instagram_username": (ig_obj or {}).get("username") if isinstance(ig_obj, dict) else None,
                        "instagram_name": (ig_obj or {}).get("name") if isinstance(ig_obj, dict) else None,
                    }
                )
        return {
            "platform": platform,
            "configured": True,
            "client_id": (cfg or {}).get("client_id", ""),
            "state": "connected" if not conn.get("revoked") else "reconnect_required",
            "connected": not conn.get("revoked"),
            "auth_mode": conn.get("auth_mode") or "oauth",
            "account_id": conn.get("account_id"),
            "account_name": conn.get("account_name"),
            "handle": conn.get("handle"),
            "category": conn.get("category"),
            "followers_count": conn.get("followers_count"),
            "picture_url": conn.get("picture_url"),
            "pages": pages_public,
            "selected_page_id": conn.get("selected_page_id") or conn.get("account_id"),
            "capabilities": conn.get("capabilities") or DEFAULT_CAPABILITIES.get(platform, []),
            "connected_at": conn.get("connected_at"),
            "checked_at": conn.get("checked_at"),
        }

    def status(self, org):
        platforms = {p: self._public_platform_status(org, p) for p in PLATFORMS}
        return {
            "ok": True,
            "platforms": platforms,
            "connected_count": sum(1 for p in platforms.values() if p["connected"]),
        }

    def configure(self, org, body):
        platform = clean_str(body.get("platform"), "platform", 40).lower()
        if platform not in PLATFORMS:
            raise SocialError("Unsupported social platform.")
        existing = self.get_config(platform) or {}
        client_id = clean_str(
            body.get("client_id") or existing.get("client_id") or "",
            "App ID / Client ID",
            300,
            empty=True,
        )
        client_secret = clean_str(
            body.get("client_secret") or existing.get("client_secret") or "",
            "App Secret / Client Secret",
            500,
            empty=True,
        )
        redirect_uri = clean_str(
            body.get("redirect_uri")
            or existing.get("redirect_uri")
            or "http://localhost:3000/api/connections/social/callback",
            "redirect URI",
            500,
        )
        if client_id and client_secret:
            if platform in ("facebook", "instagram") and not client_id.isdigit():
                raise SocialError(
                    "Facebook App ID must be numeric (e.g. 2677185012736427), not an email address."
                )
            cfg = {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            }
            self.save_config(platform, cfg)
            if platform == "facebook":
                self.save_config("instagram", cfg)

        access_token = clean_str(
            body.get("access_token") or "", "access token", 4096, empty=True
        )
        if access_token:
            return self.connect_token(org, {**body, "platform": platform})
        return self.status(org)

    def _discover_facebook_pages(self, token, preferred_page_id=None):
        """Inspect a Facebook User or Page token and return (pages_list, active_page, ig_account)."""
        pages = []
        field_sets = [
            "id,name,access_token,category,fan_count,followers_count,picture{url},instagram_business_account{id,username,name,profile_picture_url,followers_count}",
            "id,name,access_token,category,fan_count,picture{url}",
            "id,name,access_token,category",
        ]
        for fields in field_sets:
            try:
                acct_resp = self.transport(
                    f"{GRAPH_BASE}/me/accounts?"
                    + urllib.parse.urlencode(
                        {
                            "fields": fields,
                            "limit": "50",
                            "access_token": token,
                        }
                    )
                )
                for item in acct_resp.get("data") or []:
                    if isinstance(item, dict) and item.get("id"):
                        pic = (
                            ((item.get("picture") or {}).get("data") or {}).get("url")
                            if isinstance(item.get("picture"), dict)
                            else None
                        )
                        pages.append(
                            {
                                "id": str(item["id"]),
                                "name": str(item.get("name") or "Facebook Page"),
                                "access_token": str(item.get("access_token") or token),
                                "category": item.get("category") or "Business Page",
                                "followers_count": item.get("followers_count") or item.get("fan_count"),
                                "picture_url": pic,
                                "instagram_business_account": item.get("instagram_business_account"),
                            }
                        )
                break
            except Exception:
                continue

        if not pages:
            # Business-portfolio Pages are often missing from /me/accounts; the Pages the user ticked are in granular_scopes.
            cfg = self.get_config("facebook") or {}
            ids = []
            try:
                dbg = self.transport(f"{GRAPH_BASE}/debug_token?" + urllib.parse.urlencode({
                    "input_token": token,
                    "access_token": f"{cfg.get('client_id')}|{cfg.get('client_secret')}",
                }))
                for scope in (dbg.get("data") or {}).get("granular_scopes") or []:
                    if str(scope.get("scope", "")).startswith("pages_"):
                        ids += [str(i) for i in scope.get("target_ids") or [] if str(i) not in ids]
            except Exception:
                ids = []
            for page_id in ids[:50]:
                try:
                    item = self.transport(f"{GRAPH_BASE}/{page_id}?" + urllib.parse.urlencode({
                        "fields": "id,name,access_token,category,picture{url},instagram_business_account{id,username,name,profile_picture_url,followers_count}",
                        "access_token": token,
                    }))
                except Exception:
                    continue
                if isinstance(item, dict) and item.get("id") and item.get("access_token"):
                    pages.append({
                        "id": str(item["id"]),
                        "name": str(item.get("name") or "Facebook Page"),
                        "access_token": str(item["access_token"]),
                        "category": item.get("category") or "Business Page",
                        "followers_count": None,
                        "picture_url": ((item.get("picture") or {}).get("data") or {}).get("url"),
                        "instagram_business_account": item.get("instagram_business_account"),
                    })

        if not pages:
            me = None
            for me_fields in [
                "id,name,category,fan_count,followers_count,picture{url},instagram_business_account{id,username,name,profile_picture_url,followers_count}",
                "id,name,picture{url}",
                "id,name",
            ]:
                try:
                    me = self.transport(
                        f"{GRAPH_BASE}/me?"
                        + urllib.parse.urlencode(
                            {
                                "fields": me_fields,
                                "access_token": token,
                            }
                        )
                    )
                    if isinstance(me, dict) and me.get("id"):
                        break
                except Exception:
                    continue
            if not isinstance(me, dict) or not me.get("id") or not me.get("category"):
                raise SocialError("No Facebook Page was shared. Sign in again with a Page admin account and tick the Page in Facebook's page selection step.", 401)
            pic = (
                ((me.get("picture") or {}).get("data") or {}).get("url")
                if isinstance(me.get("picture"), dict)
                else None
            )
            pages.append(
                {
                    "id": str(me["id"]),
                    "name": str(me.get("name") or "Facebook Page"),
                    "access_token": token,
                    "category": me.get("category") or "Facebook Page",
                    "followers_count": me.get("followers_count") or me.get("fan_count"),
                    "picture_url": pic,
                    "instagram_business_account": me.get("instagram_business_account"),
                }
            )

        selected = pages[0]
        if preferred_page_id:
            for p in pages:
                if str(p["id"]) == str(preferred_page_id):
                    selected = p
                    break
        ig = selected.get("instagram_business_account")
        return pages, selected, ig

    def _save_facebook_and_instagram(self, org, pages, selected, ig, user_token=None, auth_mode="oauth"):
        for page in pages:
            for platform, account in self._page_accounts(page):
                if any(owner != org for owner, _ in self._account_owners(platform, account)):
                    raise SocialError("This account is already connected to another workspace.", 409)
            try:
                result = self.transport(f"{GRAPH_BASE}/{page['id']}/subscribed_apps", method="POST",
                    data={"subscribed_fields": "messages", "access_token": page["access_token"]}, form=True)
                page["messaging_state"] = "subscribed" if result.get("success") else "reconnect_required"
            except SocialError:
                page["messaging_state"] = "reconnect_required"
            try:
                result = self.transport(f"{GRAPH_BASE}/{page['id']}/subscribed_apps", method="POST",
                    data={"subscribed_fields": "messages,feed", "access_token": page["access_token"]}, form=True)
                page["comments_state"] = "subscribed" if result.get("success") else "reconnect_required"
            except SocialError:
                page["comments_state"] = "reconnect_required"
        now = self.clock()
        fb_conn = {
            "platform": "facebook",
            "auth_mode": auth_mode,
            "account_id": selected["id"],
            "account_name": selected["name"],
            "handle": selected.get("handle") or f"fb.com/{selected['id']}",
            "category": selected.get("category") or "Business Page",
            "followers_count": selected.get("followers_count"),
            "picture_url": selected.get("picture_url"),
            "access_token": selected.get("access_token") or user_token or "",
            "user_access_token": user_token or selected.get("access_token") or "",
            "pages": pages,
            "selected_page_id": selected["id"],
            "capabilities": DEFAULT_CAPABILITIES["facebook"],
            "connected_at": now,
            "checked_at": now,
            "revoked": False,
        }
        self.save_connection(org, "facebook", fb_conn)

        ig_page = selected
        if not isinstance(ig, dict) or not ig.get("id"):
            ig_page = next((p for p in pages if isinstance(p.get("instagram_business_account"), dict) and p["instagram_business_account"].get("id")), selected)
            ig = ig_page.get("instagram_business_account")
        if not isinstance(ig, dict) or not ig.get("id"):
            with self.database() as db:
                db.execute("DELETE FROM connections WHERE org=? AND platform='instagram'", (org,))

        if isinstance(ig, dict) and ig.get("id"):
            ig_username = ig.get("username") or ig.get("name") or ig["id"]
            ig_conn = {
                "platform": "instagram",
                "auth_mode": auth_mode,
                "account_id": str(ig["id"]),
                "account_name": str(ig.get("name") or f"@{ig_username}"),
                "handle": f"@{ig_username}".replace("@@", "@"),
                "category": "Instagram Business",
                "followers_count": ig.get("followers_count"),
                "picture_url": ig.get("profile_picture_url"),
                "access_token": ig_page.get("access_token") or "",
                "linked_page_id": ig_page["id"],
                "linked_page_name": ig_page["name"],
                "capabilities": DEFAULT_CAPABILITIES["instagram"],
                "connected_at": now,
                "checked_at": now,
                "revoked": False,
            }
            self.save_connection(org, "instagram", ig_conn)

    def _default_company_name(self):
        try:
            for candidate in [Path("/opt/data/box3_snapshot.json"), Path.home() / ".runtime" / "box3_snapshot.json"]:
                if candidate.is_file():
                    snap = json.loads(candidate.read_text(encoding="utf-8", errors="ignore"))
                    org_name = ((snap.get("organization") or {}).get("name") or "").strip()
                    if org_name:
                        return org_name
        except Exception:
            pass
        return "Your Solar Choice"

    def discover_pages(self, org, body):
        """Sprout-Social-style Step 1: Sign in & discover pages to link in Step 2."""
        platform = clean_str(body.get("platform") or "facebook", "platform", 40).lower()
        token = clean_str(body.get("access_token") or "", "access token", 4096, empty=True)
        email = clean_str(body.get("email") or "", "account email", 254, empty=True)
        page_query = clean_str(body.get("page_query") or "", "page name or URL", 300, empty=True)

        if token and platform in ("facebook", "instagram"):
            pages, selected, ig = self._discover_facebook_pages(token)
            previous = self.get_connection(org, "facebook")
            # Persist real Page tokens in Box 1 only; the browser never receives them.
            if previous and not previous.get("revoked"):
                previous["available_pages"] = pages
                previous["user_access_token"] = token
                self.save_connection(org, "facebook", previous)
            else:
                self._save_facebook_and_instagram(
                    org, pages, selected, ig, user_token=token, auth_mode="token",
                )
            discovered = []
            for p in pages:
                ig_obj = p.get("instagram_business_account")
                discovered.append({
                    "id": str(p["id"]),
                    "name": str(p["name"]),
                    "handle": f"fb.com/{p['id']}",
                    "category": p.get("category") or "Business Page",
                    "followers_count": p.get("followers_count"),
                    "instagram_id": (ig_obj or {}).get("id") if isinstance(ig_obj, dict) else None,
                    "instagram_username": (ig_obj or {}).get("username") if isinstance(ig_obj, dict) else None,
                    "instagram_name": (ig_obj or {}).get("name") if isinstance(ig_obj, dict) else None,
                })
            return {"ok": True, "platform": platform, "discovered_pages": discovered, "selected_id": selected["id"]}

        # Re-list Pages from the real Facebook sign-in already stored in Box 1.
        # Never fabricate Pages: every listed Page must come from the Graph API.
        existing_fb = self.get_connection(org, "facebook") if platform in ("facebook", "instagram") else None
        user_token = (existing_fb or {}).get("user_access_token") or ""
        if user_token and user_token != "box1_managed_session":
            return self.discover_pages(org, {"platform": platform, "access_token": user_token})
        raise SocialError(
            "Sign in with Facebook first. Box 1 lists only the real Pages your Facebook account manages.",
            400,
        )

    def link_pages(self, org, body):
        """Sprout-Social-style Step 2: Link the user's selected Pages & Instagram accounts into Box 1."""
        platform = clean_str(body.get("platform") or "facebook", "platform", 40).lower()
        if platform not in PLATFORMS:
            raise SocialError("Unsupported social platform.")
        raw_pages = body.get("pages")
        if not isinstance(raw_pages, list) or not raw_pages:
            raise SocialError("Select at least one Page or profile to link.")
        selected_page_id = clean_str(
            body.get("selected_page_id") or str(raw_pages[0].get("id") or ""),
            "selected page ID",
            200,
        )
        link_instagram = body.get("link_instagram") is not False

        if platform in ("facebook", "instagram"):
            existing = self.get_connection(org, "facebook") or {}
            real_pages = {
                str(p.get("id")): p
                for p in (existing.get("available_pages") or []) + (existing.get("pages") or [])
                if isinstance(p, dict)
                and p.get("id")
                and p.get("access_token")
                and p.get("access_token") != "box1_managed_session"
            }
            if not real_pages:
                raise SocialError(
                    "Sign in with Facebook first so Box 1 can load the Pages your account manages.",
                    400,
                )
            wanted = [
                str(item.get("id"))
                for item in raw_pages
                if isinstance(item, dict) and str(item.get("id")) in real_pages
            ]
            if not wanted:
                raise SocialError("Select a Page returned by your Facebook sign-in.", 400)
            normalized_pages = [real_pages[pid] for pid in wanted]
            chosen = real_pages.get(selected_page_id) if selected_page_id in wanted else normalized_pages[0]
            ig_chosen = chosen.get("instagram_business_account") if link_instagram else None
            if not link_instagram:
                normalized_pages = [{**p, "instagram_business_account": None} for p in normalized_pages]
            self._save_facebook_and_instagram(
                org,
                normalized_pages,
                chosen,
                ig_chosen,
                user_token=existing.get("user_access_token") or chosen.get("access_token"),
                auth_mode=existing.get("auth_mode") or "oauth",
            )
            if not link_instagram:
                with self.database() as db:
                    db.execute("DELETE FROM connections WHERE org=? AND platform='instagram'", (org,))
            return self.status(org)

        raise SocialError(
            f"{platform.capitalize()} linking requires its official sign-in. This connector is coming soon.",
            400,
        )

    def connect_token(self, org, body):
        platform = clean_str(body.get("platform"), "platform", 40).lower()
        if platform not in PLATFORMS:
            raise SocialError("Unsupported social platform.")
        token = clean_str(body.get("access_token"), "access token", 4096)
        preferred_page_id = clean_str(
            body.get("page_id") or "", "page ID", 200, empty=True
        )
        account_name = clean_str(
            body.get("account_name") or "", "account name", 200, empty=True
        )
        handle = clean_str(body.get("handle") or "", "handle", 200, empty=True)

        if platform in ("facebook", "instagram"):
            pages, selected, ig = self._discover_facebook_pages(
                token, preferred_page_id=preferred_page_id or None
            )
            self._save_facebook_and_instagram(
                org, pages, selected, ig, user_token=token, auth_mode="token"
            )
            return self.status(org)

        now = self.clock()
        acct_id = preferred_page_id or secrets.token_hex(6)
        display_name = account_name or f"{platform.capitalize()} Business"
        if platform == "tiktok":
            try:
                info = self.transport(
                    "https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name,avatar_url,follower_count",
                    token=token,
                )
                user_data = (info.get("data") or {}).get("user") or {}
                if user_data.get("open_id"):
                    acct_id = str(user_data["open_id"])
                if user_data.get("display_name"):
                    display_name = str(user_data["display_name"])
            except Exception:
                pass
        elif platform == "linkedin":
            try:
                info = self.transport("https://api.linkedin.com/v2/userinfo", token=token)
                if info.get("sub"):
                    acct_id = str(info["sub"])
                if info.get("name"):
                    display_name = str(info["name"])
            except Exception:
                pass

        conn = {
            "platform": platform,
            "auth_mode": "token",
            "account_id": acct_id,
            "account_name": display_name,
            "handle": handle or display_name,
            "category": f"{platform.capitalize()} Business Account",
            "followers_count": None,
            "picture_url": None,
            "access_token": token,
            "pages": [{"id": acct_id, "name": display_name, "handle": handle or display_name}],
            "selected_page_id": acct_id,
            "capabilities": DEFAULT_CAPABILITIES.get(platform, []),
            "connected_at": now,
            "checked_at": now,
            "revoked": False,
        }
        self.save_connection(org, platform, conn)
        return self.status(org)

    def start_oauth(self, org, user, body):
        platform = clean_str(body.get("platform"), "platform", 40).lower()
        if platform not in PLATFORMS:
            raise SocialError("Unsupported social platform.")
        binding = clean_str(body.get("binding"), "session binding", 64)
        redirect_uri = clean_str(
            body.get("redirect_uri") or "", "redirect URI", 500, empty=True
        )
        cfg = self.get_config(platform)
        if not cfg or not cfg.get("client_id") or not cfg.get("client_secret"):
            raise SocialError(
                "Facebook sign-in is not configured. Add FACEBOOK_APP_ID and FACEBOOK_APP_SECRET to the Box 1 environment and redeploy.",
                400,
            )
        callback = redirect_uri or cfg.get("redirect_uri")
        state = secrets.token_urlsafe(32)
        flow = {
            "platform": platform,
            "client_id": cfg["client_id"],
            "redirect_uri": callback,
        }
        with self.database() as db:
            db.execute(
                "DELETE FROM flows WHERE expires < ? OR (org = ? AND user = ? AND platform = ?)",
                (self.clock(), org, user, platform),
            )
            db.execute(
                "INSERT INTO flows VALUES(?,?,?,?,?,?,?)",
                (
                    hashlib.sha256(state.encode("utf-8")).hexdigest(),
                    org,
                    user,
                    platform,
                    binding,
                    self.clock() + 600,
                    self.encrypt(flow),
                ),
            )

        if platform in ("facebook", "instagram"):
            query = {
                "client_id": cfg["client_id"],
                "redirect_uri": callback,
                "state": state,
                "response_type": "code",
                "scope": ",".join(FACEBOOK_SCOPES + (["instagram_manage_engagement"] if os.environ.get("META_INSTAGRAM_COMMENT_LIKES") == "1" else [])),
            }
            # Facebook Login for Business apps take their permissions from a dashboard configuration, not `scope`.
            if os.environ.get("META_LOGIN_CONFIG_ID"):
                query["config_id"] = os.environ["META_LOGIN_CONFIG_ID"]
                del query["scope"]
            return {
                "url": f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth?"
                + urllib.parse.urlencode(query)
            }
        if platform == "tiktok":
            query = {
                "client_key": cfg["client_id"],
                "redirect_uri": callback,
                "state": state,
                "response_type": "code",
                "scope": ",".join(TIKTOK_SCOPES),
            }
            return {
                "url": "https://www.tiktok.com/v2/auth/authorize/?"
                + urllib.parse.urlencode(query)
            }
        if platform == "linkedin":
            query = {
                "client_id": cfg["client_id"],
                "redirect_uri": callback,
                "state": state,
                "response_type": "code",
                "scope": " ".join(LINKEDIN_SCOPES),
            }
            return {
                "url": "https://www.linkedin.com/oauth/v2/authorization?"
                + urllib.parse.urlencode(query)
            }
        raise SocialError("Unsupported OAuth platform.")

    def finish_oauth(self, org, user, body):
        state = clean_str(body.get("state"), "OAuth state", 200)
        digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
        with self.database() as db:
            row = db.execute("SELECT * FROM flows WHERE hash=?", (digest,)).fetchone()
            if (
                not row
                or row["org"] != org
                or row["user"] != user
                or row["binding"] != body.get("binding")
                or row["expires"] <= self.clock()
            ):
                raise SocialError("The social sign-in link expired or belongs to another session.", 403)
            db.execute("DELETE FROM flows WHERE hash=?", (digest,))
        if body.get("denied"):
            return {"state": "denied", "platform": row["platform"]}

        flow = self.decrypt(row["encrypted"])
        platform = flow["platform"]
        cfg = self.get_config(platform)
        if not cfg:
            raise SocialError("Social OAuth configuration changed. Try connecting again.", 409)
        code = clean_str(body.get("code"), "authorization code", 4096)

        if platform in ("facebook", "instagram"):
            token_res = self.transport(
                f"{GRAPH_BASE}/oauth/access_token?"
                + urllib.parse.urlencode(
                    {
                        "client_id": cfg["client_id"],
                        "client_secret": cfg["client_secret"],
                        "redirect_uri": flow["redirect_uri"],
                        "code": code,
                    }
                )
            )
            user_token = token_res.get("access_token")
            if not user_token:
                raise SocialError("Facebook did not return an access token.", 401)
            try:
                ll_res = self.transport(
                    f"{GRAPH_BASE}/oauth/access_token?"
                    + urllib.parse.urlencode(
                        {
                            "grant_type": "fb_exchange_token",
                            "client_id": cfg["client_id"],
                            "client_secret": cfg["client_secret"],
                            "fb_exchange_token": user_token,
                        }
                    )
                )
                if ll_res.get("access_token"):
                    user_token = ll_res["access_token"]
            except Exception:
                pass
            pages, selected, ig = self._discover_facebook_pages(user_token)
            self._save_facebook_and_instagram(
                org, pages, selected, ig, user_token=user_token, auth_mode="oauth"
            )
            return {"state": "connected", "platform": platform, **self.status(org)}

        if platform == "tiktok":
            token_res = self.transport(
                "https://open.tiktokapis.com/v2/oauth/token/",
                method="POST",
                data={
                    "client_key": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": flow["redirect_uri"],
                },
                form=True,
            )
            access_token = token_res.get("access_token")
            if not access_token:
                raise SocialError("TikTok did not return an access token.", 401)
            self.connect_token(org, {"platform": "tiktok", "access_token": access_token})
            return {"state": "connected", "platform": "tiktok", **self.status(org)}

        if platform == "linkedin":
            token_res = self.transport(
                "https://www.linkedin.com/oauth/v2/accessToken",
                method="POST",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "redirect_uri": flow["redirect_uri"],
                },
                form=True,
            )
            access_token = token_res.get("access_token")
            if not access_token:
                raise SocialError("LinkedIn did not return an access token.", 401)
            self.connect_token(org, {"platform": "linkedin", "access_token": access_token})
            return {"state": "connected", "platform": "linkedin", **self.status(org)}

        raise SocialError("Unsupported OAuth callback platform.")

    def select_page(self, org, body):
        page_id = clean_str(body.get("page_id"), "Page ID", 120)
        conn = self.get_connection(org, "facebook")
        if not conn:
            raise SocialError("Connect Facebook first before selecting a Page.", 409)
        pages = conn.get("pages") or []
        chosen = None
        for p in pages:
            if isinstance(p, dict) and str(p.get("id")) == page_id:
                chosen = p
                break
        if not chosen:
            raise SocialError("Selected Page was not found in your connected Facebook account.", 404)
        self._save_facebook_and_instagram(org, pages, chosen, chosen.get("instagram_business_account"),
            user_token=conn.get("user_access_token"), auth_mode=conn.get("auth_mode") or "oauth")
        return self.status(org)

    def disconnect(self, org, body):
        platform = clean_str(body.get("platform"), "platform", 40).lower()
        if platform not in PLATFORMS:
            raise SocialError("Unsupported social platform.")
        if platform == "instagram":
            fb = self.get_connection(org, "facebook")
            if fb:
                fb["pages"] = [{**p, "instagram_business_account": None} for p in fb.get("pages", [])]
                self.save_connection(org, "facebook", fb)
        with self.database() as db:
            if platform == "facebook":
                db.execute(
                    "DELETE FROM connections WHERE org=? AND platform IN ('facebook', 'instagram')",
                    (org,),
                )
            else:
                db.execute(
                    "DELETE FROM connections WHERE org=? AND platform=?", (org, platform)
                )
        return self.status(org)

    def test_connection(self, org, body):
        platform = clean_str(body.get("platform"), "platform", 40).lower()
        conn = self.get_connection(org, platform)
        if conn and (
            conn.get("auth_mode") == "session"
            or conn.get("access_token") in (None, "", "box1_managed_session")
        ):
            # Legacy placeholder links (no real provider token) are not connections.
            conn = None
        if not conn:
            raise SocialError(f"{platform.capitalize()} is not connected yet.", 409)
        token = conn.get("access_token") or ""
        if platform == "facebook" and token and token != "box1_managed_session":
            page_id = conn.get("selected_page_id") or conn.get("account_id")
            info = self.transport(
                f"{GRAPH_BASE}/{urllib.parse.quote(str(page_id))}?"
                + urllib.parse.urlencode(
                    {
                        "fields": "id,name,category,fan_count,followers_count",
                        "access_token": token,
                    }
                )
            )
            conn["account_name"] = info.get("name") or conn.get("account_name")
            conn["followers_count"] = (
                info.get("followers_count")
                or info.get("fan_count")
                or conn.get("followers_count")
            )
        conn["checked_at"] = self.clock()
        self.save_connection(org, platform, conn)
        return {
            "ok": True,
            "message": f"Verified {conn.get('account_name') or platform.capitalize()} in Box 1.",
            **self.status(org),
        }

    def execute_tool(self, org, body):
        operation = clean_str(body.get("operation") or "status", "operation", 80).lower()
        args = body.get("args") if isinstance(body.get("args"), dict) else body

        if operation in ("status", "list_pages", "connections"):
            return self.status(org)

        if operation in ("facebook_posts", "read_posts", "list_posts"):
            conn = self.get_connection(org, "facebook")
            if not conn:
                raise SocialError("Facebook & Instagram is not linked yet. Sign in under Settings -> Connection.", 409)
            page_id = args.get("page_id") or conn.get("selected_page_id") or conn.get("account_id")
            token = conn.get("access_token") or ""
            limit = min(max(int(args.get("limit") or 10), 1), 25)
            if token and token != "box1_managed_session":
                feed = self.transport(
                    f"{GRAPH_BASE}/{urllib.parse.quote(str(page_id))}/posts?"
                    + urllib.parse.urlencode(
                        {
                            "fields": "id,message,created_time,permalink_url,shares,likes.summary(true),comments.summary(true)",
                            "limit": str(limit),
                            "access_token": token,
                        }
                    )
                )
                posts = []
                for item in feed.get("data") or []:
                    if isinstance(item, dict):
                        posts.append(
                            {
                                "id": item.get("id"),
                                "message": item.get("message") or "",
                                "created_time": item.get("created_time"),
                                "permalink_url": item.get("permalink_url"),
                                "likes": ((item.get("likes") or {}).get("summary") or {}).get("total_count", 0),
                                "comments": ((item.get("comments") or {}).get("summary") or {}).get("total_count", 0),
                                "shares": (item.get("shares") or {}).get("count", 0),
                            }
                        )
                return {
                    "ok": True,
                    "page_id": page_id,
                    "page_name": conn.get("account_name"),
                    "posts": posts,
                }
            with self.database() as db:
                rows = db.execute(
                    "SELECT encrypted FROM posts_log WHERE org=? ORDER BY created DESC LIMIT ?",
                    (org, limit),
                ).fetchall()
            posts = [self.decrypt(r["encrypted"]) for r in rows]
            return {
                "ok": True,
                "page_id": page_id,
                "page_name": conn.get("account_name"),
                "posts": posts,
            }

        if operation in ("publish_post", "facebook_post", "create_post"):
            platform = str(args.get("platform") or body.get("platform") or "facebook").lower()
            message = clean_str(
                args.get("message") or body.get("message") or "",
                "post message",
                10000,
            )
            link = clean_str(
                args.get("link") or body.get("link") or "",
                "link URL",
                1000,
                empty=True,
            )
            image_url = clean_str(
                args.get("image_url") or body.get("image_url") or "",
                "image URL",
                1000,
                empty=True,
            )
            if platform not in ("facebook", "instagram"):
                raise SocialError("Only Facebook and Instagram publishing is supported.")
            conn = self.get_connection(org, platform) or {}
            account = str(args.get("account_id") or conn.get("account_id") or "")
            page = self.account_page(org, platform, account)
            token = page["access_token"]
            page_id = page["id"]

            if token and token != "box1_managed_session":
                if platform == "instagram":
                    ig_conn = page.get("instagram_business_account")
                    if not ig_conn:
                        raise SocialError("Instagram Business is not linked.", 409)
                    if not image_url:
                        raise SocialError("Instagram publishing requires an image_url.", 400)
                    ig_id = ig_conn["id"]
                    ig_token = token
                    container = self.transport(
                        f"{GRAPH_BASE}/{urllib.parse.quote(str(ig_id))}/media",
                        method="POST",
                        data={
                            "image_url": image_url,
                            "caption": message,
                            "access_token": ig_token,
                        },
                        form=True,
                    )
                    creation_id = container.get("id")
                    if not creation_id:
                        raise SocialError("Meta did not create the media container.", 502)
                    published = self.transport(
                        f"{GRAPH_BASE}/{urllib.parse.quote(str(ig_id))}/media_publish",
                        method="POST",
                        data={"creation_id": creation_id, "access_token": ig_token},
                        form=True,
                    )
                    if not published.get("id"):
                        raise SocialError("Meta did not confirm publication.", 502)
                    return {
                        "ok": True,
                        "platform": "instagram",
                        "account_name": ig_conn.get("account_name"),
                        "post_id": published.get("id"),
                    }

                if image_url:
                    res = self.transport(
                        f"{GRAPH_BASE}/{urllib.parse.quote(str(page_id))}/photos",
                        method="POST",
                        data={"url": image_url, "caption": message, "access_token": token},
                        form=True,
                    )
                else:
                    payload = {"message": message, "access_token": token}
                    if link:
                        payload["link"] = link
                    res = self.transport(
                        f"{GRAPH_BASE}/{urllib.parse.quote(str(page_id))}/feed",
                        method="POST",
                        data=payload,
                        form=True,
                    )
                if not (res.get("id") or res.get("post_id")):
                    raise SocialError("Meta did not confirm publication.", 502)
                return {
                    "ok": True,
                    "platform": "facebook",
                    "page_id": page_id,
                    "page_name": conn.get("account_name"),
                    "post_id": res.get("post_id") or res.get("id"),
                }

            raise SocialError("Reconnect this account before publishing.", 409)

        if operation in ("reply_comment", "comment"):
            conn = self.get_connection(org, "facebook")
            if not conn:
                raise SocialError("Facebook Page is not connected.", 409)
            target_id = clean_str(
                args.get("target_id") or args.get("comment_id") or args.get("post_id"),
                "comment or post ID",
                200,
            )
            message = clean_str(args.get("message"), "comment reply", 4000)
            token = conn.get("access_token") or ""
            if token and token != "box1_managed_session":
                res = self.transport(
                    f"{GRAPH_BASE}/{urllib.parse.quote(target_id)}/comments",
                    method="POST",
                    data={"message": message, "access_token": token},
                    form=True,
                )
                return {"ok": True, "comment_id": res.get("id"), "target_id": target_id}
            raise SocialError("Reconnect Facebook before replying.", 409)

        raise SocialError(f"Unsupported social operation: {operation}")

    def handle(self, body):
        if not isinstance(body, dict):
            raise SocialError("Invalid social request body.")
        if body.get("action") == "webhook_verify":
            expected = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "")
            if not expected or not hmac.compare_digest(expected, str(body.get("token") or "")):
                raise SocialError("Invalid webhook verification token.", 403)
            return {"ok": True}
        if body.get("action") == "webhook_ingest":
            with self.lock:
                return self.ingest_webhook(body)
        org = validate_org(body.get("org"))
        user = str(body.get("user") or "owner")
        action = str(body.get("action") or "status").strip().lower()
        with self.lock:
            if action == "inbox_claim":
                return self.claim_message(org)
            if action == "inbox_done":
                with self.database() as db:
                    db.execute("UPDATE social_inbox SET done=1 WHERE id=? AND org=?", (body.get("id"), org))
                return {"ok": True}
            if action in ("send_message", "sender_action"):
                return self.send_message(org, body)
            if action == "comment_context":
                return self.comment_context(org, body)
            if action == "send_comment":
                return self.send_comment(org, body)
            if action == "private_reply":
                return self.private_reply(org, body)
            if action == "accounts":
                return {"accounts": self.accounts(org)}
            if action == "post_feed":
                return self.post_feed(org, body)
            if action == "available_posts":
                return self.available_posts(org, body)
            if action == "delete_status":
                return self.delete_post(org, body, status_only=True)
            if action == "delete_post":
                return self.delete_post(org, body)
            if action == "publish_approved" and body.get("video_url"):
                return self.publish_reel(org, body)
            if action == "publish_approved":
                return self.deliver_once(org, body, lambda: self.execute_tool(org, {**body, "operation": "publish_post"}))
            if action == "status":
                return self.status(org)
            if action == "discover_pages":
                return self.discover_pages(org, body)
            if action == "link_pages":
                return self.link_pages(org, body)
            if action == "configure":
                return self.configure(org, body)
            if action == "connect_oauth":
                return self.start_oauth(org, user, body)
            if action == "callback":
                return self.finish_oauth(org, user, body)
            if action == "connect_token":
                return self.connect_token(org, body)
            if action == "select_page":
                return self.select_page(org, body)
            if action == "test":
                return self.test_connection(org, body)
            if action == "disconnect":
                return self.disconnect(org, body)
            if action in ("tool", "publish_post", "list_posts", "reply_comment"):
                op = body.get("operation") or (action if action != "tool" else "status")
                return self.execute_tool(org, {**body, "operation": op})
            raise SocialError(f"Unsupported social action: {action}")

    @staticmethod
    def _page_accounts(page):
        result = [("facebook", str(page["id"]))]
        ig = page.get("instagram_business_account")
        if isinstance(ig, dict) and ig.get("id"):
            result.append(("instagram", str(ig["id"])))
        return result

    def accounts(self, org):
        conn = self.get_connection(org, "facebook") or {}
        if conn.get("revoked"):
            return []
        return [{"platform": platform, "account_id": account, "page_id": str(p["id"]),
                 "name": p.get("name"), "messaging_state": p.get("messaging_state", "reconnect_required")}
                for p in conn.get("pages", []) for platform, account in self._page_accounts(p)]

    def _account_owners(self, platform, account):
        with self.database() as db:
            rows = db.execute("SELECT org,encrypted FROM connections WHERE platform='facebook'").fetchall()
        matches = []
        for row in rows:
            conn = self.decrypt(row["encrypted"])
            if conn.get("revoked"):
                continue
            for page in conn.get("pages", []):
                if (platform, str(account)) in self._page_accounts(page):
                    matches.append((row["org"], page))
        return matches

    def account_page(self, org, platform, account):
        matches = self._account_owners(platform, account)
        if len(matches) != 1 or matches[0][0] != org:
            raise SocialError("Account is not uniquely connected to this workspace.", 403)
        page = matches[0][1]
        if page.get("access_token") in (None, "", "box1_managed_session"):
            raise SocialError("Reconnect this account.", 409)
        return page

    def delete_post(self, org, body, status_only=False):
        platform = body.get("platform")
        account = str(body.get("account_id") or "")
        post_id = str(body.get("post_id") or "")
        if not status_only and body.get("confirmed") is not True:
            raise SocialError("Confirm deletion from App OS and the social platform first.", 400, rejected=True)
        if platform not in ("facebook", "instagram") or not re.fullmatch(r"\d{1,100}", account) or not re.fullmatch(r"[0-9_]{1,150}", post_id):
            raise SocialError("Invalid post identity.", 400, rejected=True)
        identity = {"platform": platform, "account_id": account, "post_id": post_id,
                    "delivery_id": "delete-post:" + hashlib.sha256(f"{platform}:{account}:{post_id}".encode()).hexdigest()}
        if status_only:
            with self.database() as db:
                receipt = db.execute("SELECT encrypted FROM social_deliveries WHERE org=? AND id=?", (org, identity["delivery_id"])).fetchone()
            result = self.decrypt(receipt["encrypted"]) if receipt and receipt["encrypted"] else {}
            return {"deleted": result.get("deleted") is True and all(result.get(k) == identity[k] for k in ("platform", "account_id", "post_id"))}
        page = self.account_page(org, platform, account)
        def remove():
            field = "from" if platform == "facebook" else "owner"
            try:
                remote = self.transport(f"{GRAPH_BASE}/{post_id}?fields=id,{field}", token=page["access_token"])
            except SocialError as exc:
                raise SocialError(str(exc), exc.status, rejected=True) from None
            if str((remote.get(field) or {}).get("id") or "") != account:
                raise SocialError("Post does not belong to the selected connected account.", 403, rejected=True)
            result = self.transport(f"{GRAPH_BASE}/{post_id}", method="DELETE", token=page["access_token"])
            if result is not True and not (isinstance(result, dict) and result.get("success") is True):
                raise SocialError("Platform did not confirm deletion. Check the post on the platform.", 502)
            return {"deleted": True, "platform": platform, "account_id": account, "post_id": post_id}
        return self.deliver_once(org, identity, remove)

    def available_posts(self, org, body):
        platform, account = body.get("platform"), str(body.get("account_id") or "")
        ids = body.get("post_ids")
        if platform not in ("facebook", "instagram") or not isinstance(ids, list) or not 1 <= len(ids) <= 25 or any(not isinstance(i, str) or not re.fullmatch(r"[0-9_]{1,150}", i) for i in ids):
            raise SocialError("Invalid post identities.")
        page = self.account_page(org, platform, account)
        owner = "from" if platform == "facebook" else "owner"
        result = self.transport(GRAPH_BASE, method="POST", form=True, data={
            "access_token": page["access_token"],
            "batch": json.dumps([{"method": "GET", "relative_url": f"{i}?fields=id,{owner}"} for i in ids]),
        })
        if not isinstance(result, list) or len(result) != len(ids):
            raise SocialError("Unable to verify posts with the social platform. Refresh accounts and try again.", 502)
        available = []
        for post_id, response in zip(ids, result):
            try:
                data = json.loads(response["body"])
                if not isinstance(data, dict):
                    raise ValueError("Invalid post response")
                if response["code"] == 200 and str(data.get("id")) == post_id and str((data.get(owner) or {}).get("id")) == account:
                    available.append(post_id)
            except (KeyError, TypeError, ValueError):
                raise SocialError("Invalid post verification response from the social platform.", 502) from None
        return {"post_ids": available}

    def post_feed(self, org, body):
        platform = body.get("platform")
        account = str(body.get("account_id") or "")
        after = body.get("after") or ""
        if platform not in ("facebook", "instagram") or not re.fullmatch(r"\d{1,100}", account):
            raise SocialError("Choose a connected Facebook or Instagram account.")
        if not isinstance(after, str) or len(after) > 2000:
            raise SocialError("Invalid post cursor.")
        page = self.account_page(org, platform, account)
        facebook = platform == "facebook"
        fields = ("id,message,created_time,permalink_url,full_picture,likes.limit(0).summary(true),comments.limit(0).summary(true),shares"
                  if facebook else "id,caption,timestamp,permalink,media_type,media_url,thumbnail_url,like_count,comments_count")
        query = {"fields": fields, "limit": 25}
        if after:
            query["after"] = after
        feed = self.transport(f"{GRAPH_BASE}/{account}/{'posts' if facebook else 'media'}?" + urllib.parse.urlencode(query), token=page["access_token"])
        def count(value):
            return value if type(value) is int and value >= 0 else None
        posts = []
        for item in feed.get("data") or []:
            if not isinstance(item, dict) or not re.fullmatch(r"[0-9_]{1,150}", str(item.get("id") or "")):
                continue
            posts.append({"post_id": str(item["id"]), "platform": platform, "account_id": account,
                          "account_name": page.get("name") or account,
                          "post_text": item.get("message" if facebook else "caption") or "",
                          "post_url": item.get("permalink_url" if facebook else "permalink") or "",
                          "image_url": item.get("full_picture") if facebook else (item.get("thumbnail_url") if item.get("media_type") == "VIDEO" else item.get("media_url")),
                          "published_at": item.get("created_time" if facebook else "timestamp"),
                          "likes": count(((item.get("likes") or {}).get("summary") or {}).get("total_count")) if facebook else count(item.get("like_count")),
                          "comments": count(((item.get("comments") or {}).get("summary") or {}).get("total_count")) if facebook else count(item.get("comments_count")),
                          "shares": count((item.get("shares") or {}).get("count")) if facebook else None})
        paging = feed.get("paging") or {}
        return {"posts": posts, "after": (paging.get("cursors") or {}).get("after") if paging.get("next") else None}

    def ingest_webhook(self, body):
        raw = base64.b64decode(body.get("raw") or "", validate=True)
        cfg = self.get_config("facebook") or {}
        secret = cfg.get("client_secret") or ""
        signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not secret or not hmac.compare_digest(signature, str(body.get("signature") or "")):
            raise SocialError("Invalid Meta webhook signature.", 403)
        if len(raw) > 90000:
            raise SocialError("Webhook too large.", 413)
        payload = json.loads(raw)
        platform = {"page": "facebook", "instagram": "instagram"}.get(payload.get("object"))
        if not platform:
            return {"ok": True, "accepted": 0}
        accepted = 0
        for entry in payload.get("entry", []):
            account = str(entry.get("id") or "")
            owners = self._account_owners(platform, account)
            if len(owners) != 1:
                continue
            org, _ = owners[0]
            for change in entry.get("changes", []):
                value = change.get("value") or {}
                if platform == "facebook":
                    if change.get("field") != "feed" or value.get("item") != "comment" or value.get("verb") != "add":
                        continue
                    cid, post = str(value.get("comment_id") or ""), str(value.get("post_id") or "")
                    text = value.get("message")
                else:
                    if change.get("field") != "comments":
                        continue
                    cid, post = str(value.get("id") or ""), str((value.get("media") or {}).get("id") or "")
                    text = value.get("text")
                sender = str((value.get("from") or {}).get("id") or "")
                if not re.fullmatch(r"[0-9_]{1,150}", cid) or not re.fullmatch(r"[0-9_]{1,150}", post):
                    continue
                if not sender.isdigit() or sender == account or not isinstance(text, str) or not text.strip():
                    continue
                if platform == "facebook" and not post.startswith(account + "_"):
                    continue
                identity = hashlib.sha256(f"{platform}:{account}:comment:{cid}".encode()).hexdigest()
                sender_name = str((value.get("from") or {}).get("name") or (value.get("from") or {}).get("username") or "Visitor")[:200]
                if sender_name and sender_name != "Visitor" and sender.isdigit():
                    self._names[(org, platform, account, sender)] = (sender_name[:120], self.clock() + 86400)
                item = {"id": identity, "kind": "comment", "platform": platform, "account_id": account,
                        "sender_id": sender, "sender_name": sender_name, "comment_id": cid, "post_id": post, "text": text[:4000], "timestamp": self.clock()}
                with self.database() as db:
                    # Ignore our own replies even when Meta omits the Page/account author.
                    own = db.execute("SELECT encrypted FROM social_deliveries WHERE org=? AND encrypted IS NOT NULL", (org,)).fetchall()
                    if any(self.decrypt(r["encrypted"]).get("comment_id") == cid for r in own):
                        continue
                    accepted += db.execute("INSERT OR IGNORE INTO social_inbox(id,org,thread,received,encrypted) VALUES(?,?,?,?,?)",
                        (identity, org, f"{platform}:{account}:comment:{cid}", self.clock(), self.encrypt(item))).rowcount
            for event in entry.get("messaging", []):
                message = event.get("message") or {}
                sender = str((event.get("sender") or {}).get("id") or "")
                recipient = str((event.get("recipient") or {}).get("id") or "")
                mid = message.get("mid")
                if not mid or not sender or recipient != account or sender == account or message.get("is_echo"):
                    continue
                text = message.get("text") or "[Customer sent an attachment; review it in the platform inbox.]"
                if not isinstance(text, str):
                    continue
                created = float(event.get("timestamp") or 0) / 1000
                if created <= 0 or created > self.clock() + 300:
                    continue
                identity = hashlib.sha256(f"{platform}:{account}:{mid}".encode()).hexdigest()
                item = {"id": identity, "platform": platform, "account_id": account,
                        "sender_id": sender, "text": text[:4000], "timestamp": created}
                thread = f"{platform}:{account}:{sender}"
                with self.database() as db:
                    accepted += db.execute("INSERT OR IGNORE INTO social_inbox(id,org,thread,received,encrypted) VALUES(?,?,?,?,?)",
                        (identity, org, thread, created, self.encrypt(item))).rowcount
        return {"ok": True, "accepted": accepted}

    def verified_comment(self, org, body):
        platform, account, cid = body.get("platform"), str(body.get("account_id") or ""), str(body.get("comment_id") or "")
        page = self.account_page(org, platform, account)
        identity = hashlib.sha256(f"{platform}:{account}:comment:{cid}".encode()).hexdigest()
        with self.database() as db:
            row = db.execute("SELECT encrypted FROM social_inbox WHERE org=? AND id=?", (org, identity)).fetchone()
        if not row:
            raise SocialError("No verified visitor comment for this account.", 403)
        return page, self.decrypt(row["encrypted"])

    def comment_context(self, org, body):
        page, event = self.verified_comment(org, body)
        facebook = event["platform"] == "facebook"
        field = "message" if facebook else "caption"
        fields = "message,permalink_url,full_picture" if facebook else "caption,permalink,media_url,media_type"
        try:
            post = self.transport(f"{GRAPH_BASE}/{event['post_id']}?fields={fields}", token=page["access_token"])
        except SocialError:
            post = self.transport(f"{GRAPH_BASE}/{event['post_id']}?fields={field}", token=page["access_token"])
        return {"post_text": str(post.get(field) or "")[:4000], "comment_text": event["text"],
                "post_id": event["post_id"], "account_name": page.get("name", ""),
                "post_url": post.get("permalink_url") or post.get("permalink") or (f"https://www.facebook.com/{event['post_id']}" if facebook else ""),
                "image_url": post.get("full_picture") or (post.get("media_url") if post.get("media_type") != "VIDEO" else ""),
                "sender_name": self._event_name(org, event), "sender_id": str(event.get("sender_id") or "")}

    def send_comment(self, org, body):
        page, event = self.verified_comment(org, body)
        message = clean_str(body.get("message"), "comment reply", 2000)
        def send():
            edge = "comments" if event["platform"] == "facebook" else "replies"
            result = self.transport(f"{GRAPH_BASE}/{event['comment_id']}/{edge}", method="POST",
                token=page["access_token"], data={"message": message}, form=True)
            if not result.get("id"):
                raise SocialError("Meta did not confirm the public reply.", 502)
            return {"ok": True, "comment_id": result["id"]}
        receipt = self.deliver_once(org, body, send)
        # A failed reaction must not suppress or resend a confirmed public reply.
        if body.get("like") and (event["platform"] == "facebook" or os.environ.get("META_INSTAGRAM_COMMENT_LIKES") == "1"):
            def react():
                target = event["comment_id"] if event["platform"] == "facebook" else event["account_id"]
                data = {} if event["platform"] == "facebook" else {"comment_id": event["comment_id"]}
                result = self.transport(f"{GRAPH_BASE}/{target}/likes", method="POST",
                    token=page["access_token"], data=data, form=True)
                if not result.get("success"):
                    raise SocialError("Like not confirmed.", 502)
                return {"liked": True}
            try:
                reaction = self.deliver_once(org, {"delivery_id": f"like:{event['id']}"}, react)
            except SocialError:
                reaction = {"liked": False, "reaction_state": "needs_attention"}
            return {**receipt, **reaction}
        return {**receipt, "liked": False, "reaction_state": "not_requested" if not body.get("like") else "permission_setup_required"}

    def private_reply(self, org, body):
        """Meta Private Reply: one DM per comment, addressed by comment ID; no prior 24-hour window needed."""
        page, event = self.verified_comment(org, body)
        if self.clock() - float(event.get("timestamp") or 0) >= 7 * 86400:
            raise SocialError("Private replies are only allowed within 7 days of the comment.", 409)
        message = clean_str(body.get("message"), "message", 1000 if event["platform"] == "instagram" else 2000)
        def send():
            result = self.transport(f"{GRAPH_BASE}/{page['id']}/messages", method="POST", token=page["access_token"],
                data={"recipient": {"comment_id": event["comment_id"]}, "message": {"text": message}})
            if not result.get("message_id"):
                raise SocialError("Meta did not confirm the private reply.", 502)
            recipient = str(result.get("recipient_id") or event.get("sender_id") or "")
            return {"ok": True, "message_id": result["message_id"], "recipient_id": recipient if recipient.isdigit() else ""}
        return self.deliver_once(org, {**body, "delivery_id": f"private:{event['id']}"}, send)

    def _profile_name(self, org, event):
        """Best-effort display name for a Messenger/Instagram DM sender via the Graph User Profile API.

        Meta's DM webhook carries only the sender ID. Never raises: a missing name just stays unnamed.
        """
        platform = event.get("platform")
        account = str(event.get("account_id") or "")
        sender = str(event.get("sender_id") or "")
        if platform not in ("facebook", "instagram") or not account or not sender.isdigit():
            return ""
        key = (org, platform, account, sender)
        now = self.clock()
        hit = self._names.get(key)
        if hit and hit[1] > now:
            return hit[0]
        name = ""
        try:
            page = self.account_page(org, platform, account)
            fields = "name,username" if platform == "instagram" else "name"
            profile = self.transport(f"{GRAPH_BASE}/{sender}?fields={fields}", token=page["access_token"], timeout=4)
            if isinstance(profile, dict):
                name = re.sub(r"\s+", " ", str(profile.get("name") or profile.get("username") or "")).strip()[:120]
        except Exception as exc:
            name = ""
            print(f"[social] sender name lookup failed ({platform}): {str(exc)[:200]}", flush=True)
        if len(self._names) > 2000:
            self._names.clear()
        # Keep real names a day; retry misses (privacy-restricted profiles, outages) after 15 minutes.
        self._names[key] = (name, now + (86400 if name else 900))
        return name

    def claim_message(self, org):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT * FROM social_inbox s WHERE org=? AND done=0 AND lease<?
                AND NOT EXISTS(SELECT 1 FROM social_inbox older WHERE older.org=s.org AND older.thread=s.thread
                    AND older.done=0 AND (older.received<s.received OR (older.received=s.received AND older.rowid<s.rowid)))
                ORDER BY received,rowid LIMIT 1""", (org, self.clock())).fetchone()
            if not row:
                return {"event": None}
            db.execute("UPDATE social_inbox SET lease=? WHERE id=?", (self.clock() + 180, row["id"]))
            event = self.decrypt(row["encrypted"])
        # Meta often omits names from webhooks (DMs: ID only; comments: sometimes no `from.name`), so look them up here.
        if (not event.get("kind") and not event.get("sender_name")) or (event.get("kind") == "comment" and event.get("sender_name") in (None, "", "Visitor")):
            name = self._event_name(org, event, default="")
            if name:
                event = {**event, "sender_name": name}
        return {"event": event}

    def _event_name(self, org, event, default="Visitor"):
        current = str(event.get("sender_name") or "")
        if current and current != "Visitor":
            return current
        name = ""
        if event.get("kind") == "comment":
            platform, account, cid = event.get("platform"), str(event.get("account_id") or ""), str(event.get("comment_id") or "")
            key = (org, platform, account, "c:" + cid)
            now = self.clock()
            hit = self._names.get(key)
            if hit and hit[1] > now:
                name = hit[0]
            else:
                try:
                    page = self.account_page(org, platform, account)
                    fields = "from{id,name}" if platform == "facebook" else "username"
                    data = self.transport(f"{GRAPH_BASE}/{cid}?fields={fields}", token=page["access_token"], timeout=4)
                    if isinstance(data, dict):
                        who = data.get("from") if isinstance(data.get("from"), dict) else {}
                        name = re.sub(r"\s+", " ", str(who.get("name") or data.get("username") or "")).strip()[:120]
                        who_id = str(who.get("id") or event.get("sender_id") or "")
                        if name and who_id.isdigit():
                            self._names[(org, platform, account, who_id)] = (name, now + 86400)
                except Exception as exc:
                    print(f"[social] comment author lookup failed ({platform}): {str(exc)[:200]}", flush=True)
                self._names[key] = (name, now + (86400 if name else 900))
        if not name:
            name = self._profile_name(org, event)
        return name or default

    def deliver_once(self, org, body, send):
        identity = clean_str(body.get("delivery_id"), "delivery ID", 200)
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        with self.database() as db:
            previous = db.execute("SELECT * FROM social_deliveries WHERE org=? AND id=?", (org, identity)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise SocialError("Delivery ID already used for different content.", 409)
                if previous["encrypted"]:
                    return self.decrypt(previous["encrypted"])
                raise SocialError("Delivery outcome unknown. Check the platform before sending again.", 409)
            # Persist before network I/O: a timeout must never cause a duplicate post/reply.
            db.execute("INSERT INTO social_deliveries VALUES(?,?,?,NULL)", (org, identity, fingerprint))
        try:
            result = send()
        except SocialError as exc:
            if exc.rejected:
                with self.database() as db:
                    db.execute("DELETE FROM social_deliveries WHERE org=? AND id=? AND encrypted IS NULL", (org, identity))
            raise
        with self.database() as db:
            db.execute("UPDATE social_deliveries SET encrypted=? WHERE org=? AND id=?", (self.encrypt(result), org, identity))
        return result

    def publish_reel(self, org, body):
        identity = clean_str(body.get("delivery_id"), "delivery ID", 200)
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        page = self.account_page(org, body.get("platform"), body.get("account_id"))
        token = page["access_token"]
        url = clean_str(body.get("video_url"), "video URL", 1000)
        if not url.startswith("https://"):
            raise SocialError("A public HTTPS video URL is required.")
        with self.database() as db:
            previous = db.execute("SELECT * FROM social_deliveries WHERE org=? AND id=?", (org, identity)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise SocialError("Delivery ID already used for different content.", 409)
                if not previous["encrypted"]:
                    raise SocialError("Reel outcome unknown. Check Meta before retrying.", 409)
                state = self.decrypt(previous["encrypted"])
                if not state.get("processing"):
                    return state
            else:
                db.execute("INSERT INTO social_deliveries VALUES(?,?,?,NULL)", (org, identity, fingerprint))
                state = {}
        def save(value):
            with self.database() as db:
                db.execute("UPDATE social_deliveries SET encrypted=? WHERE org=? AND id=?", (self.encrypt(value) if value else None, org, identity))
        platform = body.get("platform")
        if not state:
            if platform == "instagram":
                account = page["instagram_business_account"]["id"]
                created = self.transport(f"{GRAPH_BASE}/{account}/media", method="POST", token=token,
                    data={"media_type":"REELS", "video_url":url, "caption":body["message"]}, form=True)
                video = created.get("id")
            else:
                created = self.transport(f"{GRAPH_BASE}/{page['id']}/video_reels", method="POST", token=token,
                    data={"upload_phase":"start"}, form=True)
                video = created.get("video_id")
                if not video or not str(video).isdigit():
                    raise SocialError("Meta did not create a Reel upload.", 502)
                uploaded = self.transport(f"https://rupload.facebook.com/video-upload/{GRAPH_API_VERSION}/{video}",
                    method="POST", token=token, headers_extra={"file_url":url,"Authorization":"OAuth " + token})
                if not uploaded.get("success"):
                    raise SocialError("Meta did not confirm the Reel upload.", 502)
            if not video:
                raise SocialError("Meta did not create a Reel container.", 502)
            state = {"processing":True,"video_id":video}
            save(state)
        video = state["video_id"]
        if platform == "instagram":
            status = self.transport(f"{GRAPH_BASE}/{video}?fields=status_code", token=token).get("status_code")
            if status in ("ERROR","EXPIRED"):
                raise SocialError("Meta rejected the Reel. Review its format and duration.", 400)
            if status != "FINISHED":
                return state
            save(None) # A timeout during final publication must not cause a duplicate.
            result = self.transport(f"{GRAPH_BASE}/{page['instagram_business_account']['id']}/media_publish",
                method="POST", token=token, data={"creation_id":video}, form=True)
            if not result.get("id"):
                raise SocialError("Meta did not confirm Reel publication.", 502)
            result = {"ok":True,"post_id":result["id"]}
        else:
            status = self.transport(f"{GRAPH_BASE}/{video}?fields=status", token=token).get("status", {})
            if state.get("submitted"):
                if status.get("publishing_phase", {}).get("status") == "complete":
                    result = {"ok":True,"post_id":video}
                elif status.get("video_status") == "error":
                    raise SocialError("Meta could not publish this Reel.", 400)
                else:
                    return state
            else:
                if status.get("uploading_phase", {}).get("status") in ("in_progress", "not_started"):
                    return state
                save(None)
                result = self.transport(f"{GRAPH_BASE}/{page['id']}/video_reels", method="POST", token=token,
                    data={"upload_phase":"finish","video_id":video,"video_state":"PUBLISHED","description":body["message"]}, form=True)
                if not result.get("success"):
                    raise SocialError("Meta did not accept Reel publication.", 502)
                state["submitted"] = True
                save(state)
                return state
        save(result)
        return result

    def send_message(self, org, body):
        platform = clean_str(body.get("platform"), "platform", 20)
        account = clean_str(body.get("account_id"), "account", 100)
        recipient = clean_str(body.get("recipient_id"), "recipient", 100)
        page = self.account_page(org, platform, account)
        with self.database() as db:
            recent = db.execute("SELECT MAX(received) AS latest FROM social_inbox WHERE org=? AND thread=?",
                (org, f"{platform}:{account}:{recipient}")).fetchone()["latest"]
        if not recent or self.clock() - recent >= 86400:
            raise SocialError("The customer's 24-hour messaging window has closed.", 409)
        if body.get("action") == "sender_action":
            action = body.get("sender_action")
            if platform != "facebook" or action not in ("typing_on", "typing_off"):
                raise SocialError("Unsupported typing action.")
            self.transport(f"{GRAPH_BASE}/{page['id']}/messages", method="POST", token=page["access_token"],
                data={"recipient": {"id": recipient}, "sender_action": action})
            return {"ok": True}
        message = clean_str(body.get("message"), "message", 1000 if platform == "instagram" else 2000)
        def send():
            result = self.transport(f"{GRAPH_BASE}/{page['id']}/messages", method="POST", token=page["access_token"],
                data={"recipient": {"id": recipient}, "message": {"text": message}, "messaging_type": "RESPONSE"})
            if not result.get("message_id"):
                raise SocialError("Meta did not confirm message delivery.", 502)
            return {"ok": True, "message_id": result["message_id"]}
        return self.deliver_once(org, body, send)
