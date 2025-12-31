from starlette.applications import Starlette

from starlette.responses import PlainTextResponse

from starlette.routing import Route, WebSocketRoute

from starlette.websockets import WebSocket



connected_users = {}  # username -> WebSocket





async def homepage(request):

    return PlainTextResponse("OK - Lightning server is running")





async def ws_endpoint(websocket: WebSocket):

    await websocket.accept()

    username = None

    try:

        while True:

            data = await websocket.receive_json()



            if data.get("type") == "register":

                username = (data.get("username") or "").strip()

                if not username:

                    await websocket.send_json({"type": "error", "message": "Username required"})

                    continue



                if username in connected_users:

                    await websocket.send_json({"type": "error", "message": "Username already taken"})

                    continue



                connected_users[username] = websocket

                await websocket.send_json({"type": "success", "message": "Registered"})



            elif data.get("type") == "message":

                text = data.get("text", "")

                for u, ws in list(connected_users.items()):

                    if ws is not websocket:

                        await ws.send_json({"type": "message", "from": username, "text": text})



    except Exception:

        pass

    finally:

        if username and connected_users.get(username) is websocket:

            del connected_users[username]





app = Starlette(routes=[

    Route("/", homepage),

    WebSocketRoute("/ws", ws_endpoint),

])
