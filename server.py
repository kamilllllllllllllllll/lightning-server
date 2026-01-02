import os
import re
import time
import uuid
import traceback
from typing import Dict, Optional
from datetime import datetime, timezone

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

pwd_context = CryptContext(
    schemes=["pbkdf2_sha256", "bcrypt"],
    deprecated="auto",
)

pool: Optional[asyncpg.Pool] = None
connected_users: Dict[str, WebSocket] = {}

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


def utcnow():
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# =====================
# JWT
# =====================
def make_token(username: str) -> str:
    iat = int(time.time())
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
# DB init + migrations
# =====================
async def init_db():
    global pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with pool.acquire() as conn:
        # 1) Создаём таблицу если нет.
        # created_at сразу делаем timestamptz (это самый удобный и правильный вариант)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        # 2) MIGRATION: убрать UNIQUE с email (если было раньше)
        await conn.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key;")
        await conn.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_unique;")
        await conn.execute("DROP INDEX IF EXISTS users_email_key;")
        await conn.execute("DROP INDEX IF EXISTS idx_users_email_unique;")

        # 3) MIGRATION: если created_at был BIGINT — конвертируем в TIMESTAMPTZ
        # Проверим тип поля
        col_type = await conn.fetchval(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_name='users' AND column_name='created_at'
            """
        )

        # information_schema для timestamptz может вернуть 'timestamp with time zone'
        if col_type and col_type.lower() in ("bigint", "integer"):
            # BIGINT epoch seconds -> timestamptz
            await conn.execute(
                """
                ALTER TABLE users
                ALTER COLUMN created_at TYPE TIMESTAMPTZ
                USING to_timestamp(created_at);
                """
            )

        # 4) Индекс на email (не уникальный)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")


async def db_get_user_by_username(username: str) -> Optional[dict]:
    if pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE username=$1", username)
        return dict(row) if row else None


async def db_create_user(email: Optional[str], username: str, password: str) -> dict:
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
                email, username, password_hash, utcnow(),  # ✅ datetime, не int
            )
            return dict(row)
        except asyncpg.UniqueViolationError:
            raise ValueError("username already taken")


# =====================
# HTTP
# =====================
async def homepage(request):
    return PlainTextResponse("OK - Lightning server is running")


async def signup(request):
    try:
        body = await request.json()
        email = (body.get("email") or "").strip() or None
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""

        if not USERNAME_RE.match(username):
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
    try:
        body = await request.json()
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""

        user = await db_get_user_by_username(username)
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
# WS helpers
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
# WS
# =====================
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    authed_username = None

    try:
        while True:
            data = await websocket.receive_json()
            t = (data.get("type") or "").strip()

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
                await websocket.send_json({"type": "success", "message": "Registered", "users": list(connected_users.keys())})
                await broadcast({"type": "user_joined", "username": u}, exclude=websocket)
                continue

            if not authed_username:
                await websocket.send_json({"type": "error", "message": "Not authorized"})
                continue

            if t == "message":
                to_user = (data.get("to") or "").strip()
                text = (data.get("text") or "").strip()
                client_id = (data.get("client_id") or "").strip() or None
                if not to_user or not text:
                    continue

                msg_id = new_id("m")
                payload = {"type": "pm", "id": msg_id, "from": authed_username, "to": to_user, "text": text, "ts": int(time.time())}
                ok = await send_to(to_user, payload)

                if ok:
                    await websocket.send_json({"type": "pm_sent", "id": msg_id, "to": to_user, "ts": payload["ts"], "client_id": client_id})
                else:
                    await websocket.send_json({"type": "error", "message": f"User @{to_user} is not online", "client_id": client_id})
                continue

            if t == "voice":
                to_user = (data.get("to") or "").strip()
                b64 = data.get("b64") or ""
                sr = int(data.get("sr", 16000))
                ch = int(data.get("ch", 1))
                client_id = (data.get("client_id") or "").strip() or None
                if not to_user or not b64:
                    continue

                msg_id = new_id("v")
                payload = {"type": "voice", "id": msg_id, "from": authed_username, "to": to_user, "b64": b64, "sr": sr, "ch": ch, "ts": int(time.time())}
                ok = await send_to(to_user, payload)

                if ok:
                    await websocket.send_json({"type": "voice_sent", "id": msg_id, "to": to_user, "ts": payload["ts"], "client_id": client_id})
                else:
                    await websocket.send_json({"type": "error", "message": f"User @{to_user} is not online", "client_id": client_id})
                continue

            if t == "presence":
                kind = (data.get("kind") or "").strip()
                is_on = bool(data.get("is_on", False))
                to_user = (data.get("to") or "").strip()
                if kind not in ("typing", "recording") or not to_user:
                    continue
                await send_to(to_user, {"type": "presence", "kind": kind, "from": authed_username, "is_on": is_on, "to": to_user})
                continue

    except Exception:
        pass
    finally:
        if authed_username and connected_users.get(authed_username) is websocket:
            del connected_users[authed_username]
            await broadcast({"type": "user_left", "username": authed_username})


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
