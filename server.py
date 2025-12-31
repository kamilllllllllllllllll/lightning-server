from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

connected_users: dict[str, WebSocket] = {}

MAX_HISTORY = 100
public_history: list[dict] = []


async def homepage(request):
    return PlainTextResponse("OK - Lightning server is running")


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


def add_to_history(item: dict):
    public_history.append(item)
    if len(public_history) > MAX_HISTORY:
        del public_history[0 : len(public_history) - MAX_HISTORY]


async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    username = None

    try:
        while True:
            data = await websocket.receive_json()
            t = data.get("type")

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
                    "history": public_history
                })

                await broadcast({"type": "user_joined", "username": username}, exclude=websocket)

            elif t == "message":
                text = (data.get("text") or "").strip()
                to_user = (data.get("to") or "").strip()
                if not text:
                    continue

                if to_user:
                    ok = await send_to(to_user, {"type": "pm", "from": username, "text": text})
                    if ok:
                        await websocket.send_json({"type": "pm_sent", "to": to_user, "text": text})
                    else:
                        await websocket.send_json({"type": "error", "message": f"User @{to_user} is not online"})
                else:
                    payload = {"type": "message", "from": username, "text": text}
                    add_to_history(payload)
                    await broadcast(payload, exclude=websocket)

            elif t == "typing":
                to_user = (data.get("to") or "").strip()
                is_typing = bool(data.get("is_typing", False))
                payload = {"type": "typing", "from": username, "is_typing": is_typing}

                if to_user:
                    await send_to(to_user, payload)
                else:
                    await broadcast(payload, exclude=websocket)

            elif t == "voice":
                # ✅ голосовое: всегда даём подтверждение отправителю
                to_user = (data.get("to") or "").strip()

                payload = {
                    "type": "voice",
                    "from": username,
                    "b64": data.get("b64", ""),
                    "sr": int(data.get("sr", 16000)),
                    "ch": int(data.get("ch", 1)),
                }

                if not payload["b64"]:
                    await websocket.send_json({"type": "error", "message": "Empty voice payload"})
                    continue

                if to_user:
                    ok = await send_to(to_user, payload)
                    if ok:
                        await websocket.send_json({"type": "voice_sent", "to": to_user})
                    else:
                        await websocket.send_json({"type": "error", "message": f"User @{to_user} is not online"})
                else:
                    await broadcast(payload, exclude=websocket)
                    await websocket.send_json({"type": "voice_sent", "to": ""})

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
