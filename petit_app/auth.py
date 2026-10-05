"""Users, sessions, auth gate, first-run setup and login."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import config

# ===================== Users & auth (Phase B) =====================
# `users.json` is the source of truth for the human side, the same way
# characters/*/config/config.json is for the character side (design
# principle: "people are users.json, not an env var"). Each user has:
#   id, name, color, password_hash ("scrypt$n$r$p$salt_hex$hash_hex"),
#   characters ("all" | [character_id, ...])
#
# Sessions are a signed cookie (HMAC-SHA256, not encrypted — there's nothing
# secret in the payload beyond the user id + expiry) so there's no server-side
# session store to manage. The signing key lives in `.session_secret` next to
# users.json, generated on first use and written 0600.


def _load_users_doc() -> dict:
    if not config.USERS_FILE.exists():
        return {"users": []}
    try:
        doc = json.loads(config.USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"users": []}
    if not isinstance(doc, dict) or not isinstance(doc.get("users"), list):
        return {"users": []}
    return doc


def _save_users_doc(doc: dict) -> None:
    config.USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.USERS_FILE.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        config.USERS_FILE.chmod(0o600)
    except OSError:
        pass  # best-effort (e.g. unsupported on some filesystems)


def list_users() -> list[dict]:
    """Public user list — never includes password_hash."""
    return [{k: v for k, v in u.items() if k != "password_hash"} for u in _load_users_doc()["users"]]


def find_user(user_id: str) -> dict | None:
    for u in _load_users_doc()["users"]:
        if u.get("id") == user_id:
            return u
    return None


def users_exist() -> bool:
    return bool(_load_users_doc()["users"])


def _hash_password(password: str, *, n: int = 2**14, r: int = 8, p: int = 1) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, n_s, r_s, p_s, salt_hex, hash_hex = stored_hash.split("$")
        if algo != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n_s), r=int(r_s), p=int(p_s), dklen=32)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def create_user(user_id: str, name: str, password: str, color: str | None = None,
                 characters: str | list[str] = "all") -> dict:
    if not config._ID_RE.match(user_id):
        raise ValueError("user id must be alphanumeric/underscore")
    doc = _load_users_doc()
    if any(u.get("id") == user_id for u in doc["users"]):
        raise ValueError(f"user already exists: {user_id}")
    user = {
        "id": user_id,
        "name": name or user_id,
        "color": color or config.DEFAULT_USER_COLOR,
        "password_hash": _hash_password(password),
        "characters": characters,
    }
    doc["users"].append(user)
    _save_users_doc(doc)
    return user


def authenticate(user_id: str, password: str) -> dict | None:
    user = find_user(user_id)
    if user and _verify_password(password, user.get("password_hash", "")):
        return user
    return None


def _get_session_secret() -> bytes:
    if config.SESSION_SECRET_FILE.exists():
        try:
            secret = bytes.fromhex(config.SESSION_SECRET_FILE.read_text(encoding="utf-8").strip())
            if secret:
                return secret
        except Exception:
            pass
    secret = secrets.token_bytes(32)
    config.SESSION_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.SESSION_SECRET_FILE.write_text(secret.hex(), encoding="utf-8")
    try:
        config.SESSION_SECRET_FILE.chmod(0o600)
    except OSError:
        pass
    return secret


def make_session_token(user_id: str) -> str:
    expires = int(time.time()) + config.SESSION_MAX_AGE
    payload = f"{user_id}:{expires}"
    sig = hmac.new(_get_session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(token: str) -> str | None:
    """Return the user id if the token is well-formed, unexpired, and correctly
    signed — otherwise None."""
    try:
        user_id, expires_str, sig = token.split(":", 2)
        payload = f"{user_id}:{expires_str}"
        expected = hmac.new(_get_session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(expires_str) < int(time.time()):
            return None
        return user_id
    except Exception:
        return None


def get_internal_token() -> str:
    """The shared secret local tools send in `X-Petit-Internal-Token`.
    Created (0600) the first time it is needed."""
    f = config.INTERNAL_TOKEN_FILE
    if f.exists():
        try:
            token = f.read_text(encoding="utf-8").strip()
            if token:
                return token
        except OSError:
            pass
    token = secrets.token_hex(32)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(token, encoding="utf-8")
    try:
        f.chmod(0o600)
    except OSError:
        pass
    return token


# The internal token only opens the house-wide media routes — nothing else.
_INTERNAL_PREFIXES = ("/api/album/", "/api/voice_memo/")
INTERNAL_CALLER = {"id": "_internal", "name": "internal", "internal": True}


def is_internal_request(request: Request) -> bool:
    sent = request.headers.get(config.INTERNAL_TOKEN_HEADER, "")
    if not sent or not request.url.path.startswith(_INTERNAL_PREFIXES):
        return False
    return hmac.compare_digest(sent, get_internal_token())


def get_caller(request: Request) -> dict:
    """FastAPI dependency for the house-wide media routes: a local tool holding
    the internal token, or the authenticated user. 401 otherwise."""
    if is_internal_request(request):
        return INTERNAL_CALLER
    return get_current_user(request)


def get_current_user(request: Request) -> dict:
    """FastAPI dependency: the authenticated user, or 401."""
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    user_id = verify_session_token(token) if token else None
    user = find_user(user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


def user_public(user: dict) -> dict:
    return {"id": user["id"], "name": user.get("name") or user["id"], "color": user.get("color") or config.DEFAULT_USER_COLOR}


def _user_allowed_characters(user: dict) -> str | list[str]:
    return user.get("characters", "all")


def _user_can_access(user: dict, character_id: str) -> bool:
    allowed = _user_allowed_characters(user)
    return allowed == "all" or character_id in allowed


router = APIRouter()


# ===================== Auth / setup (Phase B) =====================
# Two states, mutually exclusive:
#  - users.json doesn't exist (or is empty) -> only /setup and POST /api/setup
#    work; everything else redirects there (design decision: no hand-editing
#    users.json to get started, same spirit as the M5 firmware's QR
#    provisioning flow).
#  - users.json has at least one user -> /setup 302s to /login; every other
#    path requires a valid session cookie, via this middleware.

_PUBLIC_PATHS = {"/login", "/setup"}
_PUBLIC_API_PATHS = {"/api/auth/login", "/api/setup"}


async def auth_gate(request: Request, call_next):
    path = request.url.path
    is_api = path.startswith("/api/")

    if not users_exist():
        if path == "/setup" or path == "/api/setup":
            return await call_next(request)
        if is_api:
            return JSONResponse({"error": "setup required", "setup_required": True}, status_code=503)
        return RedirectResponse("/setup")

    if path in _PUBLIC_PATHS or path in _PUBLIC_API_PATHS or path.startswith("/api/auth/"):
        # users.json already has someone in it — /setup no longer applies.
        if path == "/setup" or path == "/api/setup":
            if is_api:
                return JSONResponse({"error": "already set up"}, status_code=409)
            return RedirectResponse("/login")
        return await call_next(request)

    if is_internal_request(request):
        return await call_next(request)

    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    user_id = verify_session_token(token) if token else None
    if not user_id or not find_user(user_id):
        if is_api:
            return JSONResponse({"error": "authentication required"}, status_code=401)
        return RedirectResponse("/login")

    return await call_next(request)


class SetupRequest(BaseModel):
    id: str
    name: str
    password: str
    color: str | None = None


@router.post("/api/setup")
async def api_setup(body: SetupRequest):
    if users_exist():
        return JSONResponse({"error": "already set up"}, status_code=409)
    if not body.id or not body.password:
        return JSONResponse({"error": "id and password are required"}, status_code=400)
    try:
        # The first account created gets every character (admin-equivalent —
        # there's no separate role system, "all characters" is the privilege).
        create_user(body.id, body.name, body.password, color=body.color, characters="all")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True}


class LoginRequest(BaseModel):
    id: str
    password: str


@router.post("/api/auth/login")
async def api_login(body: LoginRequest):
    user = authenticate(body.id, body.password)
    if user is None:
        return JSONResponse({"error": "invalid id or password"}, status_code=401)
    token = make_session_token(user["id"])
    resp = JSONResponse({"ok": True, "user": user_public(user)})
    resp.set_cookie(
        config.SESSION_COOKIE_NAME, token, max_age=config.SESSION_MAX_AGE,
        httponly=True, samesite="lax",
    )
    return resp


@router.post("/api/auth/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(config.SESSION_COOKIE_NAME)
    return resp


@router.get("/api/me")
async def api_me(user: dict = Depends(get_current_user)):
    return user_public(user)


@router.get("/setup", response_class=HTMLResponse)
async def setup_page():
    return HTMLResponse(_SETUP_HTML)


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(_LOGIN_HTML)


# ===================== Login / setup pages (Phase B) =====================
# Plain HTML forms (no framework) — bilingual labels, mobile-friendly single
# column, same visual language as the main app (#4a7c59 green).

_AUTH_PAGE_STYLE = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: sans-serif; background: #f5f5f5; color: #333; min-height: 100vh;
       display: flex; align-items: center; justify-content: center; padding: 16px; }
.box { background: white; border-radius: 12px; padding: 28px 24px; box-shadow: 0 2px 12px rgba(0,0,0,.1);
       width: 100%; max-width: 360px; }
h1 { font-size: 18px; color: #4a7c59; margin-bottom: 4px; }
h1 .en { display: block; font-size: 12px; font-weight: normal; color: #999; }
p.hint { font-size: 12px; color: #888; margin: 4px 0 16px; }
label { display: block; font-size: 13px; color: #555; margin-top: 14px; }
label .en { color: #999; font-weight: normal; }
input { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 15px; margin-top: 4px; }
button { width: 100%; background: #4a7c59; color: white; border: none; padding: 12px; border-radius: 6px;
         cursor: pointer; font-size: 15px; margin-top: 20px; }
button:hover { background: #3a6449; }
#error { color: #c0392b; font-size: 13px; margin-top: 12px; display: none; }
.swap { text-align: center; font-size: 12px; margin-top: 16px; }
.swap a { color: #4a7c59; }
"""

_SETUP_HTML = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>はじめての設定 — M5 Petit</title>
<style>{_AUTH_PAGE_STYLE}</style>
</head>
<body>
<div class="box">
  <h1>はじめての設定<span class="en">First-time setup</span></h1>
  <p class="hint">最初のユーザー（管理者）を作成します。<br>Create the first user account (gets access to every character).</p>
  <label>ユーザーID (半角英数字) <span class="en">User ID (alphanumeric)</span>
    <input id="su-id" autocomplete="username">
  </label>
  <label>表示名 <span class="en">Display name</span>
    <input id="su-name" autocomplete="nickname">
  </label>
  <label>パスワード <span class="en">Password</span>
    <input id="su-password" type="password" autocomplete="new-password">
  </label>
  <label>色 (任意) <span class="en">Color (optional)</span>
    <input id="su-color" type="color" value="#7da8f5" style="padding:2px;height:40px">
  </label>
  <button onclick="doSetup()">作成してログインへ / Create &amp; go to login</button>
  <div id="error"></div>
</div>
<script>
async function doSetup() {{
  const id = document.getElementById('su-id').value.trim();
  const name = document.getElementById('su-name').value.trim();
  const password = document.getElementById('su-password').value;
  const color = document.getElementById('su-color').value;
  const errEl = document.getElementById('error');
  errEl.style.display = 'none';
  const r = await fetch('/api/setup', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{id, name, password, color}})
  }});
  const j = await r.json();
  if (r.ok && j.ok) {{
    window.location.href = '/login';
  }} else {{
    errEl.textContent = j.error || 'エラーが発生しました / Something went wrong';
    errEl.style.display = 'block';
  }}
}}
document.getElementById('su-password').addEventListener('keydown', e => {{ if (e.key === 'Enter') doSetup(); }});
</script>
</body>
</html>
"""

_LOGIN_HTML = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ログイン — M5 Petit</title>
<style>{_AUTH_PAGE_STYLE}</style>
</head>
<body>
<div class="box">
  <h1>ログイン<span class="en">Log in</span></h1>
  <label>ユーザーID <span class="en">User ID</span>
    <input id="li-id" autocomplete="username">
  </label>
  <label>パスワード <span class="en">Password</span>
    <input id="li-password" type="password" autocomplete="current-password">
  </label>
  <button onclick="doLogin()">ログイン / Log in</button>
  <div id="error"></div>
</div>
<script>
async function doLogin() {{
  const id = document.getElementById('li-id').value.trim();
  const password = document.getElementById('li-password').value;
  const errEl = document.getElementById('error');
  errEl.style.display = 'none';
  const r = await fetch('/api/auth/login', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{id, password}})
  }});
  const j = await r.json();
  if (r.ok && j.ok) {{
    window.location.href = '/';
  }} else {{
    errEl.textContent = j.error || 'IDまたはパスワードが違います / Invalid ID or password';
    errEl.style.display = 'block';
  }}
}}
document.getElementById('li-password').addEventListener('keydown', e => {{ if (e.key === 'Enter') doLogin(); }});
</script>
</body>
</html>
"""
