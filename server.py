from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

import os
import time
import base64
import hashlib
import hmac


connected_users: dict[str, WebSocket] = {}

# -------------------------
# SIMPLE IN-MEMORY AUTH
# -------------------------
# username -> {"email": str, "salt": bytes, "hash": bytes, "created_at": int}
USERS: dict[str, dict] = {}


def _hash_password(password: str, salt: bytes) -> bytes:
    # stable + safe enough for MVP without extra deps
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)


def _make_token(username: str) -> str:
    # token сейчас клиенту нужен только чтобы "был"
    raw = f"{username}:{int(time.time())}:{base64.urlsafe_b64encode(os.urandom(16)).decode('ascii')}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


async def homepage(request):
    return PlainTextResponse("OK - Lightning server is running")


async def signup(request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)

    email = (data.get("email") or "").strip()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return JSONResponse({"ok": False, "error": "username and password required"}, status_code=400)

    # email НЕ проверяем на уникальность (как ты просил), но username — должен быть уникальный
    if username in USERS:
        return JSONResponse({"ok": False, "error": "username already taken"}, status_code=409)

    salt = os.urandom(16)
    pwd_hash = _hash_password(password, salt)

    USERS[username] = {
        "email": email,
        "salt": salt,
        "hash": pwd_hash,
        "created_at": int(time.time()),
    }

    return JSONResponse({"ok": True, "token": _make_token(username), "username": username}, status_code=200)


async def login(request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)

    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    user = USERS.get(username)
    if not user:
        return JSONResponse({"ok": False, "error": "invalid credentials"}, status_code=401)

    salt = user["salt"]
    expected = user["hash"]
    got = _hash_password(password, salt)

    if not hmac.compare_digest(expected, got):
        return JSONResponse({"ok": False, "error": "invalid credentials"}, status_code=401)

    return JSONResponse({"ok": True, "token": _make_token(username), "username": username}, status_code=200)


async def broadcast(payload: dict, exclude: WebSocket | None = None):
    for ws in list(connected_users.values()):
        if ws is not exclude:
            await ws.send_json(payload)


async def send_to(username: str, payload: dict) -> bool:
    ws = connected_users.get(username)
    if ws:
        await ws.send_json(payload)
        return True
    return False


async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    username = None

    try:
        while True:
            data = await websocket.receive_json()
            t = data.get("type")

            # ---------- register ----------
            if t == "register":
                username = (data.get("username") or "").strip()
                if not username:
                    await websocket.send_json({"type": "error", "message": "Username required"})
                    continue
                if username in connected_users:
                    await websocket.send_json({"type": "error", "message": "Username already taken"})
                    continue

                connected_users[username] = websocket

                await websocket.send_json({
                    "type": "success",
                    "message": "Registered",
                    "users": list(connected_users.keys()),
                })
                await broadcast({"type": "user_joined", "username": username}, exclude=websocket)

            # ---------- text message (PM only) ----------
            elif t == "pm":
                to_user = (data.get("to") or "").strip()
                text = (data.get("text") or "").strip()
                msg_id = (data.get("id") or "").strip()
                ts = int(data.get("ts", 0) or 0)

                if not to_user or not text or not msg_id:
                    continue

                payload = {"type": "pm", "from": username, "to": to_user, "id": msg_id, "text": text, "ts": ts}

                ok = await send_to(to_user, payload)
                if ok:
                    await websocket.send_json({"type": "pm_sent", "to": to_user, "id": msg_id, "ts": ts})
                else:
                    await websocket.send_json({"type": "error", "message": f"User @{to_user} is not online"})

            # ---------- voice (PM only) ----------
            elif t == "voice":
                to_user = (data.get("to") or "").strip()
                msg_id = (data.get("id") or "").strip()
                ts = int(data.get("ts", 0) or 0)

                payload = {
                    "type": "voice",
                    "from": username,
                    "to": to_user,
                    "id": msg_id,
                    "b64": data.get("b64", ""),
                    "sr": int(data.get("sr", 16000)),
                    "ch": int(data.get("ch", 1)),
                    "ts": ts,
                }
                if not to_user or not payload["b64"] or not msg_id:
                    await websocket.send_json({"type": "error", "message": "Empty voice payload"})
                    continue

                ok = await send_to(to_user, payload)
                if ok:
                    await websocket.send_json({"type": "voice_sent", "to": to_user, "id": msg_id, "ts": ts})
                else:
                    await websocket.send_json({"type": "error", "message": f"User @{to_user} is not online"})

            # ---------- edit text for both ----------
            elif t == "pm_edit":
                to_user = (data.get("to") or "").strip()
                msg_id = (data.get("id") or "").strip()
                new_text = (data.get("text") or "").strip()
                edited_ts = int(data.get("edited_ts", 0) or 0)

                if not to_user or not msg_id or not new_text:
                    continue

                payload = {
                    "type": "pm_edit",
                    "from": username,
                    "to": to_user,
                    "id": msg_id,
                    "text": new_text,
                    "edited_ts": edited_ts,
                }

                ok = await send_to(to_user, payload)
                if ok:
                    await websocket.send_json({"type": "pm_edit_ok", "to": to_user, "id": msg_id, "edited_ts": edited_ts})
                else:
                    await websocket.send_json({"type": "error", "message": f"User @{to_user} is not online"})

            # ---------- delete for both (text OR voice) ----------
            elif t == "delete_for_both":
                to_user = (data.get("to") or "").strip()
                msg_id = (data.get("id") or "").strip()

                if not to_user or not msg_id:
                    continue

                payload = {
                    "type": "delete_for_both",
                    "from": username,
                    "to": to_user,
                    "id": msg_id
                }

                ok = await send_to(to_user, payload)
                if ok:
                    await websocket.send_json({"type": "delete_for_both_ok", "to": to_user, "id": msg_id})
                else:
                    await websocket.send_json({"type": "error", "message": f"User @{to_user} is not online"})

            # ---------- presence typing/recording ----------
            elif t == "presence":
                kind = (data.get("kind") or "").strip()
                is_on = bool(data.get("is_on", False))
                to_user = (data.get("to") or "").strip()

                if kind not in ("typing", "recording"):
                    continue
                if not to_user:
                    continue

                payload = {
                    "type": "presence",
                    "kind": kind,
                    "from": username,
                    "to": to_user,
                    "is_on": is_on,
                }

                await send_to(to_user, payload)

    except Exception:
        pass
    finally:
        if username and connected_users.get(username) is websocket:
            del connected_users[username]
            await broadcast({"type": "user_left", "username": username})


app = Starlette(routes=[
    Route("/", homepage),
    Route("/signup", signup, methods=["POST"]),
    Route("/login", login, methods=["POST"]),
    WebSocketRoute("/ws", ws_endpoint),
])
