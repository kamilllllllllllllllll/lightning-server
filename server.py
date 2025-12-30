import asyncio

from starlette.applications import Starlette

from starlette.routing import WebSocketRoute

from starlette.websockets import WebSocket



connected_users = {}



async def websocket_endpoint(websocket: WebSocket):

    await websocket.accept()

    username = None

    try:

        while True:

            data = await websocket.receive_json()

            if data["type"] == "register":

                username = data["username"]

                if username in connected_users:

                    await websocket.send_json({"type": "error", "message": "Username taken"})

                    continue

                connected_users[username] = websocket

                await websocket.send_json({"type": "success", "message": "Registered"})

            elif data["type"] == "message":

                for user, ws in connected_users.items():

                    if ws != websocket:

                        await ws.send_json({"type": "message", "from": username, "text": data["text"]})

    except Exception:

        pass

    finally:

        if username in connected_users:

            del connected_users[username]



app = Starlette(routes=[

    WebSocketRoute("/ws", websocket_endpoint)

])
