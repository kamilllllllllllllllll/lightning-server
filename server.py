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

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")

pwd_context = CryptContext(
    schemes=["pbkdf2_sha256", "bcrypt"],
    deprecated="auto",
)

pool: Optional[asyncpg.Pool] = None

# online map: username -> websocket
connected_users: Dict[str, WebSocket] = {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ts_int() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# =====================
# JWT
# =====================
def make_token(username: str) -> str:
    iat = ts_int()
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
        # users
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen TIMESTAMPTZ
            );
            """
        )

        # email НЕ уникальный
        await conn.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key;")
        await conn.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_unique;")
        await conn.execute("DROP INDEX IF EXISTS users_email_key;")
        await conn.execute("DROP INDEX IF EXISTS idx_users_email_unique;")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")

        # messages: хранит и текст и голосовые + состояние edit/delete
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,              -- "text" | "voice"
                from_user TEXT NOT NULL,
                to_user TEXT NOT NULL,
                text TEXT,
                b64 TEXT,
                sr INT,
                ch INT,
                ts TIMESTAMPTZ NOT NULL,
                edited_ts TIMESTAMPTZ,
                deleted BOOLEAN NOT NULL DEFAULT FALSE,
                delivered_ts TIMESTAMPTZ,
                needs_sync BOOLEAN NOT NULL DEFAULT TRUE
            );
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_to_user ON messages(to_user);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);")


async def db_get_user_by_username(username: str) -> Optional[dict]:
    if pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE username=$1", username)
        return dict(row) if row else None


async def db_create_user(email: Optional[str], username: str, password: str) -> dict:
    if pool is None:
        raise RuntimeError("DB pool is not initialized")

    if len(password.encode("utf-8")) > 72:
        raise ValueError("password too long (max ~72 bytes)")

    password_hash = pwd_context.hash(password)

    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO users(email, username, password_hash, created_at, last_seen)
                VALUES($1, $2, $3, $4, $5)
                RETURNING id, email, username, created_at, last_seen
                """,
                email, username, password_hash, utcnow(), utcnow(),
            )
            return dict(row)
        except asyncpg.UniqueViolationError:
            raise ValueError("username already taken")


async def db_set_last_seen(username: str):
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET last_seen=$2 WHERE username=$1", username, utcnow())


async def db_insert_message(payload: dict):
    """
    payload fields:
      id, kind, from_user, to_user, text?, b64?, sr?, ch?, ts(datetime), edited_ts?, deleted(bool), delivered_ts?, needs_sync(bool)
    """
    if pool is None:
        raise RuntimeError("DB pool is not initialized")

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO messages(id, kind, from_user, to_user, text, b64, sr, ch, ts, edited_ts, deleted, delivered_ts, needs_sync)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            ON CONFLICT (id) DO UPDATE SET
              kind=EXCLUDED.kind,
              from_user=EXCLUDED.from_user,
              to_user=EXCLUDED.to_user,
              text=EXCLUDED.text,
              b64=EXCLUDED.b64,
              sr=EXCLUDED.sr,
              ch=EXCLUDED.ch,
              ts=EXCLUDED.ts,
              edited_ts=EXCLUDED.edited_ts,
              deleted=EXCLUDED.deleted,
              delivered_ts=EXCLUDED.delivered_ts,
              needs_sync=EXCLUDED.needs_sync
            """,
            payload["id"], payload["kind"], payload["from_user"], payload["to_user"],
            payload.get("text"), payload.get("b64"), payload.get("sr"), payload.get("ch"),
            payload["ts"], payload.get("edited_ts"), payload.get("deleted", False),
            payload.get("delivered_ts"), payload.get("needs_sync", True),
        )


async def db_mark_delivered_and_clear_sync(msg_id: str):
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE messages SET delivered_ts=$2, needs_sync=FALSE WHERE id=$1",
            msg_id, utcnow()
        )


async def db_update_edit(msg_id: str, new_text: str):
    if pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE messages
            SET text=$2, edited_ts=$3, needs_sync=TRUE
            WHERE id=$1
            """,
            msg_id, new_text, utcnow()
        )


async def db_update_delete(msg_id: str):
    if pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE messages
            SET deleted=TRUE, needs_sync=TRUE
            WHERE id=$1
            """,
            msg_id
        )


async def db_get_pending_for_user(username: str, limit: int = 200) -> list[dict]:
    """
    Отдаём:
      - все, что needs_sync=TRUE (включая те, что уже были доставлены, но редактировались/удалялись)
    """
    if pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT *
            FROM messages
            WHERE to_user=$1 AND needs_sync=TRUE
            ORDER BY ts ASC
            LIMIT $2
            """,
            username, limit
        )
        return [dict(r) for r in rows]


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
        if len(password.encode("utf-8")) > 72:
            return JSONResponse({"ok": False, "error": "password too long (max ~72 bytes)"}, status_code=400)

        user = await db_create_user(email, username, password)
        token = make_token(user["username"])
        return JSONResponse({"ok": True, "token": token, "username": user["username"]}, status_code=200)

    except ValueError as ve:
        msg = str(ve)
        code = 409 if "taken" in msg else 400
        return JSONResponse({"ok": False, "error": msg}, status_code=code)
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


# =====================
# WS endpoint
# =====================
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    me: Optional[str] = None

    try:
        while True:
            data = await websocket.receive_json()
            t = (data.get("type") or "").strip()

            # ---------- AUTH ----------
            if t == "auth":
                token = (data.get("token") or "").strip()
                u = username_from_token(token)
                if not u:
                    await websocket.send_json({"type": "error", "message": "Invalid token"})
                    continue
                if u in connected_users:
                    await websocket.send_json({"type": "error", "message": "User already online"})
                    continue

                me = u
                connected_users[me] = websocket

                # отдадим pending (offline доставку + edit/delete синк)
                pending = await db_get_pending_for_user(me, limit=300)
                for row in pending:
                    kind = row["kind"]
                    base = {
                        "id": row["id"],
                        "from": row["from_user"],
                        "to": row["to_user"],
                        "ts": int(row["ts"].timestamp()),
                        "deleted": bool(row["deleted"]),
                    }
                    if row.get("edited_ts"):
                        base["edited_ts"] = int(row["edited_ts"].timestamp())

                    if kind == "text":
                        base.update({"type": "pm", "text": row.get("text") or ""})
                    else:
                        base.update({
                            "type": "voice",
                            "b64": row.get("b64") or "",
                            "sr": int(row.get("sr") or 16000),
                            "ch": int(row.get("ch") or 1),
                        })

                    await websocket.send_json(base)
                    await db_mark_delivered_and_clear_sync(row["id"])

                await websocket.send_json({"type": "success", "message": "Online", "username": me})
                continue

            if not me:
                await websocket.send_json({"type": "error", "message": "Not authorized"})
                continue

            # ---------- PRESENCE (только личка) ----------
            if t == "presence":
                to_user = (data.get("to") or "").strip()
                kind = (data.get("kind") or "").strip()
                is_on = bool(data.get("is_on", False))

                if not to_user or kind not in ("typing", "recording"):
                    continue

                payload = {"type": "presence", "from": me, "to": to_user, "kind": kind, "is_on": is_on}
                await send_to(to_user, payload)
                continue

            # ---------- SEND TEXT (всегда вставляем в БД, даже если оффлайн) ----------
            if t == "message":
                to_user = (data.get("to") or "").strip()
                text = (data.get("text") or "").strip()
                msg_id = (data.get("id") or "").strip() or new_id("m")
                if not to_user or not text:
                    continue

                now = utcnow()
                await db_insert_message({
                    "id": msg_id,
                    "kind": "text",
                    "from_user": me,
                    "to_user": to_user,
                    "text": text,
                    "ts": now,
                    "deleted": False,
                    "needs_sync": True,
                })

                payload = {
                    "type": "pm",
                    "id": msg_id,
                    "from": me,
                    "to": to_user,
                    "text": text,
                    "ts": int(now.timestamp()),
                    "deleted": False,
                }

                if await send_to(to_user, payload):
                    await db_mark_delivered_and_clear_sync(msg_id)

                await websocket.send_json({"type": "pm_sent", "id": msg_id, "to": to_user, "ts": int(now.timestamp())})
                continue

            # ---------- SEND VOICE ----------
            if t == "voice":
                to_user = (data.get("to") or "").strip()
                b64 = data.get("b64") or ""
                sr = int(data.get("sr", 16000))
                ch = int(data.get("ch", 1))
                msg_id = (data.get("id") or "").strip() or new_id("v")

                if not to_user or not b64:
                    continue

                now = utcnow()
                await db_insert_message({
                    "id": msg_id,
                    "kind": "voice",
                    "from_user": me,
                    "to_user": to_user,
                    "b64": b64,
                    "sr": sr,
                    "ch": ch,
                    "ts": now,
                    "deleted": False,
                    "needs_sync": True,
                })

                payload = {
                    "type": "voice",
                    "id": msg_id,
                    "from": me,
                    "to": to_user,
                    "b64": b64,
                    "sr": sr,
                    "ch": ch,
                    "ts": int(now.timestamp()),
                    "deleted": False,
                }

                if await send_to(to_user, payload):
                    await db_mark_delivered_and_clear_sync(msg_id)

                await websocket.send_json({"type": "voice_sent", "id": msg_id, "to": to_user, "ts": int(now.timestamp())})
                continue

            # ---------- EDIT (text only) ----------
            if t == "edit":
                to_user = (data.get("to") or "").strip()
                msg_id = (data.get("id") or "").strip()
                new_text = (data.get("new_text") or "").strip()
                if not to_user or not msg_id or not new_text:
                    continue

                await db_update_edit(msg_id, new_text)
                edited_ts = ts_int()

                payload = {
                    "type": "edit",
                    "id": msg_id,
                    "from": me,
                    "to": to_user,
                    "new_text": new_text,
                    "edited_ts": edited_ts,
                }

                # online — сразу, offline — придёт через sync при входе
                await send_to(to_user, payload)
                await websocket.send_json(payload)
                continue

            # ---------- DELETE FOR BOTH (text/voice) ----------
            if t == "delete_for_both":
                to_user = (data.get("to") or "").strip()
                msg_id = (data.get("id") or "").strip()
                if not to_user or not msg_id:
                    continue

                await db_update_delete(msg_id)

                payload = {
                    "type": "delete_for_both",
                    "id": msg_id,
                    "from": me,
                    "to": to_user,
                    "ts": ts_int(),
                }

                await send_to(to_user, payload)
                await websocket.send_json(payload)
                continue

    except Exception:
        pass
    finally:
        if me and connected_users.get(me) is websocket:
            del connected_users[me]
            await db_set_last_seen(me)


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
