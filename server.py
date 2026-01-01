import os
import re
import time
import uuid
import traceback
from typing import Dict, Optional

import asyncpg
import jwt
from passlib.context import CryptContext
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket


# =====================
# ENV
# =====================
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
JWT_SECRET = os.environ.get("JWT_SECRET", "dev_secret_change_me")
JWT_ALG = os.environ.get("JWT_ALG", "HS256")
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", "2592000"))  # 30 days

# =====================
# PASSWORD HASHING
# - pbkdf2_sha256: no 72-byte limit
# - bcrypt: keep compatibility if you already have bcrypt hashes in DB
# =====================
pwd_context = CryptContext(
    schemes=["pbkdf2_sha256", "bcrypt"],
    deprecated="auto",
)

# =====================
# WS STATE
# =====================
connected_users: Dict[str, WebSocket] = {}


def now_ts() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# =====================
# DB
# =====================
pool: Optional[asyncpg.Pool] = None


async def init_db():
    """
    Creates users table if not exists.
    """
    global pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at BIGINT NOT NULL
            );
            """
        )


async def db_create_user(email: str, username: str, password: str) -> dict:
    if pool is None:
        raise RuntimeError("DB pool is not initialized")

    password_hash = pwd_context.hash(password)

    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO users(email, username, password_hash, created_at)
                VALUES($1, $2, $3, $4)
                RETURNING id, email, username, created_at
                """,
                email, username, password_hash, now_ts(),
            )
            return dict(row)
        except asyncpg.UniqueViolationError:
            raise ValueError("email or username already taken")


async def db_get_user_by_email(email: str) -> Optional[dict]:
    if pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE email=$1", email)
        return dict(row) if row else None


async def db_get_user_by_username(username: str) -> Optional[dict]:
    if pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE username=$1", username)
        return dict(row) if row else None


# =====================
# JWT
# =====================
def make_token(username: str) -> str:
    iat = now_ts()
    exp = iat + TOKEN_TTL_SECONDS
    payload = {"sub": username, "iat": iat, "exp": exp}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def username_from_token(token: str) -> Optional[str]:
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        u = (data.get("sub") or "").strip()
        return u or None
    except Exception:
        return None


# =====================
# HTTP ROUTES
# =====================
async def homepage(request):
    return PlainTextResponse("OK - Lightning server is running")


async def signup(request):
    """
    POST /signup
    JSON: { "email": "...", "username": "...", "password": "..." }
    """
    try:
        body = await request.json()
        email = (body.get("email") or "").strip().lower()
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""

        if not EMAIL_RE.match(email):
            return JSONResponse({"ok": False, "error": "invalid email"}, status_code=400)
        if not (3 <= len(username) <= 20) or not re.match(r"^[A-Za-z0-9_]+$", username):
            return JSONResponse({"ok": False, "error": "invalid username (3-20, A-Z 0-9 _)"}, status_code=400)
        if len(password) < 6:
            return JSONResponse({"ok": False, "error": "password too short (min 6)"}, status_code=400)

        user = await db_create_user(email, username, password)
        token = make_token(user["username"])
        return JSONResponse({"ok": True, "token": token, "username": user["username"]}, status_code=200)

    except ValueError as ve:
        return JSONResponse({"ok": False, "error": str(ve)}, status_code=409)
    except Exception:
        return JSONResponse({"ok": False, "error": "signup failed", "detail": traceback.format_exc()}, status_code=500)


async def login(request):
    """
    POST /login
    JSON: { "email": "...", "password": "..." }
    """
    try:
        body = await request.json()
        email = (body.get("email") or "").strip().lower()
        password = body.get("password") or ""

        user = await db_get_user_by_email(email)
        if not user:
            return JSONResponse({"ok": False, "error": "invalid credentials"}, status_code=401)

        ok = pwd_context.verify(password, user["password_hash"])
        if not ok:
            return JSONResponse({"ok": False, "error": "invalid credentials"}, status_code=401)

        token = make_token(user["username"])
        return JSONResponse({"ok": True, "token": token, "username": user["username"]}, status_code=200)

    except Exception:
        return JSONResponse({"ok": False, "error": "login failed", "detail": traceback.format_exc()}, status_code=500)


# =====================
# WS HELPERS
# =====================
async def send_to(username: str, payload: dict) -> bool:
    ws = connected_users.get(username)
    if ws:
        await ws.send_json(payload)
        return True
    return False


async def broadcast(payload: dict, exclude: Optional[WebSocket] = None):
    for ws in list(connected_users.values()):
        if ws is not exclude:
            await ws.send_json(payload)


# =====================
# WS ENDPOINT
# =====================
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()

    authed_username = None

    try:
        while True:
            data = await websocket.receive_json()
            t = (data.get("type") or "").strip()

            # ---- AUTH ----
            if t == "auth":
                token = (data.get("token") or "").strip()
                u = username_from_token(token)
                if not u:
                    await websocket.send_json({"type": "error", "message": "Invalid token"})
                    continue

                if u in connected_users:
                    await websocket.send_json({"type": "error", "message": "User already online"})
                    continue

                authed_username = u
                connected_users[u] = websocket

                await websocket.send_json({
                    "type": "success",
                    "message": "Registered",
                    "users": list(connected_users.keys()),
                })
                await broadcast({"type": "user_joined", "username": u}, exclude=websocket)
                continue

            # block everything until auth
            if not authed_username:
                await websocket.send_json({"type": "error", "message": "Not authorized"})
                continue

            # ---- TEXT ----
            if t == "message":
                to_user = (data.get("to") or "").strip()
                text = (data.get("text") or "").strip()
                client_id = (data.get("client_id") or "").strip() or None

                if not to_user or not text:
                    continue

                msg_id = new_id("m")
                payload = {
                    "type": "pm",
                    "id": msg_id,
                    "from": authed_username,
                    "to": to_user,
                    "text": text,
                    "ts": now_ts(),
                }

                ok = await send_to(to_user, payload)
                if ok:
                    await websocket.send_json({
                        "type": "pm_sent",
                        "id": msg_id,
                        "to": to_user,
                        "ts": payload["ts"],
                        "client_id": client_id,
                    })
                else:
                    await websocket.send_json({"type": "error", "message": f"User @{to_user} is not online"})
                continue

            # ---- VOICE ----
            if t == "voice":
                to_user = (data.get("to") or "").strip()
                b64 = data.get("b64") or ""
                sr = int(data.get("sr", 16000))
                ch = int(data.get("ch", 1))
                client_id = (data.get("client_id") or "").strip() or None

                if not to_user or not b64:
                    continue

                msg_id = new_id("v")
                payload = {
                    "type": "voice",
                    "id": msg_id,
                    "from": authed_username,
                    "to": to_user,
                    "b64": b64,
                    "sr": sr,
                    "ch": ch,
                    "ts": now_ts(),
                }

                ok = await send_to(to_user, payload)
                if ok:
                    await websocket.send_json({
                        "type": "voice_sent",
                        "id": msg_id,
                        "to": to_user,
                        "ts": payload["ts"],
                        "client_id": client_id,
                    })
                else:
                    await websocket.send_json({"type": "error", "message": f"User @{to_user} is not online"})
                continue

            # ---- PRESENCE ----
            if t == "presence":
                kind = (data.get("kind") or "").strip()
                is_on = bool(data.get("is_on", False))
                to_user = (data.get("to") or "").strip()
                if kind not in ("typing", "recording") or not to_user:
                    continue
                await send_to(to_user, {
                    "type": "presence",
                    "kind": kind,
                    "from": authed_username,
                    "is_on": is_on,
                    "to": to_user,
                })
                continue

            # ---- EDIT TEXT (for both) ----
            if t == "edit":
                to_user = (data.get("to") or "").strip()
                msg_id = (data.get("id") or "").strip()
                new_text = (data.get("new_text") or "").strip()
                if not to_user or not msg_id or not new_text:
                    continue

                payload = {
                    "type": "edit",
                    "id": msg_id,
                    "from": authed_username,
                    "to": to_user,
                    "new_text": new_text,
                    "edited_ts": now_ts(),
                }
                await send_to(to_user, payload)
                await websocket.send_json(payload)
                continue

            # ---- DELETE FOR BOTH ----
            if t == "delete_for_both":
                to_user = (data.get("to") or "").strip()
                msg_id = (data.get("id") or "").strip()
                if not to_user or not msg_id:
                    continue
                payload = {"type": "delete_for_both", "id": msg_id, "from": authed_username, "to": to_user}
                await send_to(to_user, payload)
                await websocket.send_json(payload)
                continue

            # ---- READ RECEIPT ----
            if t == "read":
                to_user = (data.get("to") or "").strip()
                upto_id = (data.get("upto_id") or "").strip()
                if not to_user or not upto_id:
                    continue
                await send_to(to_user, {
                    "type": "read",
                    "from": authed_username,
                    "to": to_user,
                    "upto_id": upto_id,
                    "ts": now_ts(),
                })
                continue

    except Exception:
        pass
    finally:
        if authed_username and connected_users.get(authed_username) is websocket:
            del connected_users[authed_username]
            await broadcast({"type": "user_left", "username": authed_username})


# =====================
# LIFESPAN
# =====================
async def on_startup():
    await init_db()


async def on_shutdown():
    global pool
    if pool:
        await pool.close()
        pool = None


app = Starlette(
    routes=[
        Route("/", homepage),
        Route("/signup", signup, methods=["POST"]),
        Route("/login", login, methods=["POST"]),
        WebSocketRoute("/ws", ws_endpoint),
    ],
    on_startup=[on_startup],
    on_shutdown=[on_shutdown],
)
