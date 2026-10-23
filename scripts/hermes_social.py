"""Backend-owned Social Media OAuth, encrypted Page/Account credentials and bounded Social tools.

Connects Box 3 (App OS Settings -> Connection) to Box 1 (Hermes /opt/data/social/connections.sqlite).
Supports:
- Facebook Page (OAuth 2.0 Sign-In + Direct Page/User Access Token + Multi-Page selection + Graph API tools)
- Instagram Business (Auto-linked via Facebook Page Graph API or direct token)
- TikTok Business (OAuth 2.0 & Token connector bridge)
- LinkedIn Page / Profile (OAuth 2.0 & Token connector bridge)
"""
import contextlib
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

PLATFORMS = ("facebook", "instagram", "tiktok", "linkedin")

FACEBOOK_SCOPES = [
    "public_profile",
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "pages_manage_metadata",
    "pages_read_user_content",
    "business_management",
]

INSTAGRAM_SCOPES = [
    *FACEBOOK_SCOPES,
    "instagram_basic",
    "instagram_content_publish",
    "instagram_manage_comments",
    "instagram_manage_insights",
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
}


class SocialError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def provider_request(url, method="GET", data=None, token=None, form=False, headers_extra=None):
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
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=25) as resp:
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
            raise SocialError(err_msg or "Social account authorization expired. Reconnect in Settings -> Connection.", 401) from None
        if exc.code == 403:
            raise SocialError(err_msg or "Platform denied this permission. Check Page access and scopes.", 403) from None
        if exc.code == 404:
            raise SocialError(err_msg or "Requested social page or post was not found.", 404) from None
        if exc.code == 429:
            raise SocialError("Social platform rate limit reached. Retry shortly.", 429) from None
        raise SocialError(err_msg or f"Social platform request failed (HTTP {exc.code}).", 502) from None
    except (OSError, ValueError) as exc:
        raise SocialError(f"Could not reach social platform API: {exc}", 502) from None


def validate_org(org):
    if not org:
        return "00000000-0000-0000-0000-000000000000"
    try:
        return str(UUID(str(org)))
    except (ValueError, TypeError, AttributeError):
        raise SocialError("Invalid workspace identifier.", 403) from None


def clean_str(value, label, maximum=1000, empty=False):
    if value is None and empty:
        return ""
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise SocialError(f"Check {label}.")
    return value.strip()


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

    def get_config(self, platform):
        with self.database() as db:
            row = db.execute(
                "SELECT encrypted FROM platform_config WHERE platform=?", (platform,)
            ).fetchone()
        if row:
            return self.decrypt(row["encrypted"])
        if platform == "instagram":
            with self.database() as db:
                fb_row = db.execute(
                    "SELECT encrypted FROM platform_config WHERE platform='facebook'"
                ).fetchone()
            if fb_row:
                return self.decrypt(fb_row["encrypted"])
        return self._env_config(platform)

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
        # Fallback: if a connection exists under any org in single-tenant Box 1, return it for tool calls
        with self.database() as db:
            any_row = db.execute(
                "SELECT encrypted FROM connections WHERE platform=? LIMIT 1",
                (platform,),
            ).fetchone()
        return self.decrypt(any_row["encrypted"]) if any_row else None

    def save_connection(self, org, platform, value):
        with self.database() as db:
            db.execute(
                "INSERT OR REPLACE INTO connections VALUES(?,?,?)",
                (org, platform, self.encrypt(value)),
            )

    def _public_platform_status(self, org, platform):
        cfg = self.get_config(platform)
        conn = self.get_connection(org, platform)
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
                pages_public.append(
                    {
                        "id": str(p.get("id") or ""),
                        "name": str(p.get("name") or ""),
                        "category": p.get("category"),
                        "followers_count": p.get("followers_count") or p.get("fan_count"),
                        "picture_url": p.get("picture_url"),
                        "has_instagram": bool(p.get("instagram_business_account")),
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
            cfg = {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            }
            self.save_config(platform, cfg)
            if platform == "facebook" and not self.get_config("instagram"):
                self.save_config("instagram", cfg)

        # If an access token was also supplied in configure, connect it immediately
        access_token = clean_str(
            body.get("access_token") or "", "access token", 4096, empty=True
        )
        if access_token:
            return self.connect_token(org, {**body, "platform": platform})
        return self.status(org)

    def _discover_facebook_pages(self, token, preferred_page_id=None):
        """Inspect a Facebook User or Page token and return (pages_list, active_page, ig_account)."""
        pages = []
        # 1. Try /me/accounts (works when token is a User Access Token or System User Token)
        try:
            acct_resp = self.transport(
                f"{GRAPH_BASE}/me/accounts?"
                + urllib.parse.urlencode(
                    {
                        "fields": "id,name,access_token,category,fan_count,followers_count,picture{url},instagram_business_account{id,username,name,profile_picture_url,followers_count}",
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
        except Exception:
            pass

        # 2. Also inspect /me directly (works when token is already a Page Access Token)
        if not pages:
            me = self.transport(
                f"{GRAPH_BASE}/me?"
                + urllib.parse.urlencode(
                    {
                        "fields": "id,name,category,fan_count,followers_count,picture{url},instagram_business_account{id,username,name,profile_picture_url,followers_count}",
                        "access_token": token,
                    }
                )
            )
            if not isinstance(me, dict) or not me.get("id"):
                raise SocialError("Facebook did not return a valid Page or account for this token.", 401)
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
        if not ig:
            for p in pages:
                if isinstance(p.get("instagram_business_account"), dict):
                    ig = p["instagram_business_account"]
                    break
        return pages, selected, ig

    def _save_facebook_and_instagram(self, org, pages, selected, ig, user_token=None, auth_mode="oauth"):
        now = self.clock()
        fb_conn = {
            "platform": "facebook",
            "auth_mode": auth_mode,
            "account_id": selected["id"],
            "account_name": selected["name"],
            "handle": f"fb.com/{selected['id']}",
            "category": selected.get("category") or "Business Page",
            "followers_count": selected.get("followers_count"),
            "picture_url": selected.get("picture_url"),
            "access_token": selected["access_token"],
            "user_access_token": user_token or selected["access_token"],
            "pages": pages,
            "selected_page_id": selected["id"],
            "capabilities": DEFAULT_CAPABILITIES["facebook"],
            "connected_at": now,
            "checked_at": now,
            "revoked": False,
        }
        self.save_connection(org, "facebook", fb_conn)

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
                "access_token": selected["access_token"],
                "linked_page_id": selected["id"],
                "linked_page_name": selected["name"],
                "capabilities": DEFAULT_CAPABILITIES["instagram"],
                "connected_at": now,
                "checked_at": now,
                "revoked": False,
            }
            self.save_connection(org, "instagram", ig_conn)

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
            "pages": [],
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
                f"Configure your {platform.capitalize()} App ID & Secret first, or use 'Connect with Token' for instant access without OAuth app setup.",
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
            scopes = INSTAGRAM_SCOPES if platform == "instagram" else FACEBOOK_SCOPES
            query = {
                "client_id": cfg["client_id"],
                "redirect_uri": callback,
                "state": state,
                "response_type": "code",
                "scope": ",".join(scopes),
            }
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
            # Upgrade to 60-day long-lived token so Page tokens do not expire
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
        conn["selected_page_id"] = chosen["id"]
        conn["account_id"] = chosen["id"]
        conn["account_name"] = chosen["name"]
        conn["handle"] = f"fb.com/{chosen['id']}"
        conn["category"] = chosen.get("category") or "Business Page"
        conn["followers_count"] = chosen.get("followers_count")
        conn["picture_url"] = chosen.get("picture_url")
        conn["access_token"] = chosen["access_token"]
        conn["checked_at"] = self.clock()
        self.save_connection(org, "facebook", conn)

        ig = chosen.get("instagram_business_account")
        if isinstance(ig, dict) and ig.get("id"):
            ig_username = ig.get("username") or ig.get("name") or ig["id"]
            self.save_connection(
                org,
                "instagram",
                {
                    "platform": "instagram",
                    "auth_mode": conn.get("auth_mode") or "oauth",
                    "account_id": str(ig["id"]),
                    "account_name": str(ig.get("name") or f"@{ig_username}"),
                    "handle": f"@{ig_username}".replace("@@", "@"),
                    "category": "Instagram Business",
                    "followers_count": ig.get("followers_count"),
                    "picture_url": ig.get("profile_picture_url"),
                    "access_token": chosen["access_token"],
                    "linked_page_id": chosen["id"],
                    "linked_page_name": chosen["name"],
                    "capabilities": DEFAULT_CAPABILITIES["instagram"],
                    "connected_at": self.clock(),
                    "checked_at": self.clock(),
                    "revoked": False,
                },
            )
        return self.status(org)

    def disconnect(self, org, body):
        platform = clean_str(body.get("platform"), "platform", 40).lower()
        if platform not in PLATFORMS:
            raise SocialError("Unsupported social platform.")
        with self.database() as db:
            db.execute(
                "DELETE FROM connections WHERE org=? AND platform=?", (org, platform)
            )
        return self.status(org)

    def test_connection(self, org, body):
        platform = clean_str(body.get("platform"), "platform", 40).lower()
        conn = self.get_connection(org, platform)
        if not conn:
            raise SocialError(f"{platform.capitalize()} is not connected yet.", 409)
        if platform == "facebook":
            page_id = conn.get("selected_page_id") or conn.get("account_id")
            token = conn.get("access_token")
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
            self.save_connection(org, "facebook", conn)
            return {
                "ok": True,
                "message": f"Verified Facebook Page '{conn['account_name']}' (ID {page_id}).",
                **self.status(org),
            }
        if platform == "instagram":
            ig_id = conn.get("account_id")
            token = conn.get("access_token")
            info = self.transport(
                f"{GRAPH_BASE}/{urllib.parse.quote(str(ig_id))}?"
                + urllib.parse.urlencode(
                    {
                        "fields": "id,username,name,followers_count,media_count",
                        "access_token": token,
                    }
                )
            )
            conn["checked_at"] = self.clock()
            conn["followers_count"] = info.get("followers_count") or conn.get("followers_count")
            self.save_connection(org, "instagram", conn)
            return {
                "ok": True,
                "message": f"Verified Instagram Business '{conn.get('handle') or conn.get('account_name')}'.",
                **self.status(org),
            }
        conn["checked_at"] = self.clock()
        self.save_connection(org, platform, conn)
        return {
            "ok": True,
            "message": f"{platform.capitalize()} connector credentials verified in Box 1.",
            **self.status(org),
        }

    # --- Agent / MCP Tool Operations ---
    def execute_tool(self, org, body):
        operation = clean_str(body.get("operation") or "status", "operation", 80).lower()
        args = body.get("args") if isinstance(body.get("args"), dict) else {}

        if operation in ("status", "list_pages", "connections"):
            return self.status(org)

        if operation in ("facebook_posts", "read_posts", "list_posts"):
            conn = self.get_connection(org, "facebook")
            if not conn:
                raise SocialError("Facebook Page is not connected. Connect it in Settings -> Connection.", 409)
            page_id = args.get("page_id") or conn.get("selected_page_id") or conn.get("account_id")
            token = conn.get("access_token")
            limit = min(max(int(args.get("limit") or 10), 1), 25)
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
            if platform == "instagram":
                ig_conn = self.get_connection(org, "instagram")
                if not ig_conn:
                    raise SocialError("Instagram Business is not connected.", 409)
                if not image_url:
                    raise SocialError("Instagram publishing requires an image_url.", 400)
                ig_id = ig_conn["account_id"]
                token = ig_conn["access_token"]
                container = self.transport(
                    f"{GRAPH_BASE}/{urllib.parse.quote(str(ig_id))}/media",
                    method="POST",
                    data={
                        "image_url": image_url,
                        "caption": message,
                        "access_token": token,
                    },
                    form=True,
                )
                creation_id = container.get("id")
                if not creation_id:
                    raise SocialError("Instagram did not create a media container.", 502)
                published = self.transport(
                    f"{GRAPH_BASE}/{urllib.parse.quote(str(ig_id))}/media_publish",
                    method="POST",
                    data={"creation_id": creation_id, "access_token": token},
                    form=True,
                )
                return {
                    "ok": True,
                    "platform": "instagram",
                    "account_name": ig_conn.get("account_name"),
                    "post_id": published.get("id"),
                }

            conn = self.get_connection(org, "facebook")
            if not conn:
                raise SocialError("Facebook Page is not connected. Connect it in Settings -> Connection.", 409)
            page_id = args.get("page_id") or conn.get("selected_page_id") or conn.get("account_id")
            token = conn.get("access_token")
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
            return {
                "ok": True,
                "platform": "facebook",
                "page_id": page_id,
                "page_name": conn.get("account_name"),
                "post_id": res.get("id") or res.get("post_id"),
            }

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
            res = self.transport(
                f"{GRAPH_BASE}/{urllib.parse.quote(target_id)}/comments",
                method="POST",
                data={"message": message, "access_token": conn["access_token"]},
                form=True,
            )
            return {"ok": True, "comment_id": res.get("id"), "target_id": target_id}

        raise SocialError(f"Unsupported social operation: {operation}")

    def handle(self, body):
        if not isinstance(body, dict):
            raise SocialError("Invalid social request body.")
        org = validate_org(body.get("org"))
        user = str(body.get("user") or "owner")
        action = str(body.get("action") or "status").strip().lower()
        with self.lock:
            if action == "status":
                return self.status(org)
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
