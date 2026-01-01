import os
import time
import uuid
from typing import Dict, Optional

import jwt
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

JWT_SECRET = os.environ.get("JWT_SECRET", "dev_secret_change_me")
JWT_ALG = os.environ.get("JWT_ALG", "HS256")

connected_users: Dict[str, WebSocket] = {}


def ts() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


async def homepage(request):
    return PlainTextResponse("OK - Lightning WS server running")


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


def username_from_token(token: str) -> Optional[str]:
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        # у тебя в токене sub = username (как было в логах)
        return (data.get("sub") or "").strip() or None
    except Exception:
        return None


async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    username = None

    try:
        while True:
            data = await websocket.receive_json()
            t = (data.get("type") or "").strip()

            # ---------- AUTH ----------
            if t == "auth":
                token = (data.get("token") or "").strip()
                username = username_from_token(token)
                if not username:
                    await websocket.send_json({"type": "error", "message": "Invalid token"})
                    continue

                if username in connected_users:
                    await websocket.send_json({"type": "error", "message": "User already online"})
                    continue

                connected_users[username] = websocket

                await websocket.send_json({
                    "type": "success",
                    "message": "Registered",
                    "users": list(connected_users.keys())
                })
                await broadcast({"type": "user_joined", "username": username}, exclude=websocket)
                continue

            if not username:
                await websocket.send_json({"type": "error", "message": "Not authorized"})
                continue

            # ---------- TEXT MESSAGE ----------
            # client -> {type:"message", to:"alex", text:"hi", client_id:"optional"}
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
                    "from": username,
                    "to": to_user,
                    "text": text,
                    "ts": ts()
                }

                ok = await send_to(to_user, payload)
                if ok:
                    await websocket.send_json({
                        "type": "pm_sent",
                        "id": msg_id,
                        "to": to_user,
                        "ts": payload["ts"],
                        "client_id": client_id
                    })
                else:
                    await websocket.send_json({"type": "error", "message": f"User @{to_user} is not online"})
                continue

            # ---------- VOICE ----------
            # client -> {type:"voice", to:"alex", b64:"...", sr, ch, client_id}
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
                    "from": username,
                    "to": to_user,
                    "b64": b64,
                    "sr": sr,
                    "ch": ch,
                    "ts": ts()
                }

                ok = await send_to(to_user, payload)
                if ok:
                    await websocket.send_json({
                        "type": "voice_sent",
                        "id": msg_id,
                        "to": to_user,
                        "ts": payload["ts"],
                        "client_id": client_id
                    })
                else:
                    await websocket.send_json({"type": "error", "message": f"User @{to_user} is not online"})
                continue

            # ---------- EDIT TEXT (for both) ----------
            # {type:"edit", to:"alex", id:"m_...", new_text:"..."}
            if t == "edit":
                to_user = (data.get("to") or "").strip()
                msg_id = (data.get("id") or "").strip()
                new_text = (data.get("new_text") or "").strip()

                if not to_user or not msg_id or not new_text:
                    continue

                payload = {
                    "type": "edit",
                    "id": msg_id,
                    "from": username,
                    "to": to_user,
                    "new_text": new_text,
                    "edited_ts": ts()
                }

                await send_to(to_user, payload)
                await websocket.send_json(payload)  # ack sender too
                continue

            # ---------- DELETE (for both) ----------
            # {type:"delete_for_both", to:"alex", id:"m_..." or "v_..."}
            if t == "delete_for_both":
                to_user = (data.get("to") or "").strip()
                msg_id = (data.get("id") or "").strip()
                if not to_user or not msg_id:
                    continue

                payload = {"type": "delete_for_both", "id": msg_id, "from": username, "to": to_user}
                await send_to(to_user, payload)
                await websocket.send_json(payload)
                continue

            # ---------- READ RECEIPT ----------
            # reader -> {type:"read", to:"alex", upto_id:"m_..." }
            # server forwards to sender
            if t == "read":
                to_user = (data.get("to") or "").strip()
                upto_id = (data.get("upto_id") or "").strip()
                if not to_user or not upto_id:
                    continue

                payload = {"type": "read", "from": username, "to": to_user, "upto_id": upto_id, "ts": ts()}
                await send_to(to_user, payload)
                continue

            # ---------- presence ----------
            if t == "presence":
                kind = (data.get("kind") or "").strip()
                is_on = bool(data.get("is_on", False))
                to_user = (data.get("to") or "").strip()

                if kind not in ("typing", "recording"):
                    continue
                if not to_user:
                    continue

                payload = {"type": "presence", "kind": kind, "from": username, "is_on": is_on, "to": to_user}
                await send_to(to_user, payload)
                continue

    except Exception:
        pass
    finally:
        if username and connected_users.get(username) is websocket:
            del connected_users[username]
            await broadcast({"type": "user_left", "username": username})


app = Starlette(routes=[
    Route("/", homepage),
    WebSocketRoute("/ws", ws_endpoint),
])
