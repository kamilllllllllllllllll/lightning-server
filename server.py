import os

import time

from typing import Optional, Dict



import asyncpg

import jwt

from passlib.context import CryptContext



from starlette.applications import Starlette

from starlette.responses import PlainTextResponse, JSONResponse

from starlette.routing import Route, WebSocketRoute

from starlette.websockets import WebSocket, WebSocketDisconnect



DATABASE_URL = os.getenv("DATABASE_URL", "")

JWT_SECRET = os.getenv("JWT_SECRET", "change-me")

JWT_ALG = "HS256"



pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")



connected_users: Dict[str, WebSocket] = {}

db_pool: Optional[asyncpg.Pool] = None





# ---------------- DB ----------------

async def init_db():

    global db_pool

    if not DATABASE_URL:

        raise RuntimeError("DATABASE_URL is not set")

    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)



    async with db_pool.acquire() as conn:

        await conn.execute("""

        CREATE TABLE IF NOT EXISTS users (

            id BIGSERIAL PRIMARY KEY,

            email TEXT UNIQUE NOT NULL,

            username TEXT UNIQUE NOT NULL,

            password_hash TEXT NOT NULL,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()

        );

        """)





def create_token(username: str) -> str:

    now = int(time.time())

    payload = {"sub": username, "iat": now, "exp": now + 60 * 60 * 24 * 30}

    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)





def verify_token(token: str) -> Optional[str]:

    try:

        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])

        return payload.get("sub")

    except Exception:

        return None





async def get_user_by_username(username: str):

    async with db_pool.acquire() as conn:

        return await conn.fetchrow("SELECT id, email, username FROM users WHERE username=$1", username)





async def get_user_by_email(email: str):

    async with db_pool.acquire() as conn:

        return await conn.fetchrow(

            "SELECT id, email, username, password_hash FROM users WHERE email=$1",

            email

        )





async def create_user(email: str, username: str, password: str):

    password_hash = pwd_context.hash(password)

    async with db_pool.acquire() as conn:

        return await conn.fetchrow(

            "INSERT INTO users(email, username, password_hash) VALUES($1,$2,$3) RETURNING id, email, username",

            email, username, password_hash

        )





# ---------------- HTTP ----------------

async def homepage(request):

    return PlainTextResponse("OK - Lightning server is running")





async def signup(request):

    data = await request.json()

    email = (data.get("email") or "").strip().lower()

    username = (data.get("username") or "").strip()

    password = (data.get("password") or "")



    if not email or not username or not password:

        return JSONResponse({"ok": False, "error": "email/username/password required"}, status_code=400)



    if "@" not in email or len(username) < 3 or len(password) < 6:

        return JSONResponse({"ok": False, "error": "invalid input"}, status_code=400)



    try:

        user = await create_user(email, username, password)

    except asyncpg.UniqueViolationError:

        return JSONResponse({"ok": False, "error": "email or username already taken"}, status_code=409)



    token = create_token(user["username"])

    return JSONResponse({"ok": True, "token": token, "username": user["username"]})





async def login(request):

    data = await request.json()

    email = (data.get("email") or "").strip().lower()

    password = (data.get("password") or "")



    if not email or not password:

        return JSONResponse({"ok": False, "error": "email/password required"}, status_code=400)



    row = await get_user_by_email(email)

    if not row:

        return JSONResponse({"ok": False, "error": "invalid credentials"}, status_code=401)



    if not pwd_context.verify(password, row["password_hash"]):

        return JSONResponse({"ok": False, "error": "invalid credentials"}, status_code=401)



    token = create_token(row["username"])

    return JSONResponse({"ok": True, "token": token, "username": row["username"]})





# ---------------- WS helpers ----------------

async def send_to(username: str, payload: dict) -> bool:

    ws = connected_users.get(username)

    if ws:

        await ws.send_json(payload)

        return True

    return False





async def broadcast(payload: dict, exclude: WebSocket | None = None):

    for ws in list(connected_users.values()):

        if ws is not exclude:

            await ws.send_json(payload)





# ---------------- WS endpoint ----------------

async def ws_endpoint(websocket: WebSocket):

    await websocket.accept()

    username: Optional[str] = None



    try:

        # first message must be auth

        first = await websocket.receive_json()

        if first.get("type") != "auth":

            await websocket.send_json({"type": "error", "message": "First message must be auth"})

            await websocket.close()

            return



        token = first.get("token") or ""

        username = verify_token(token)

        if not username:

            await websocket.send_json({"type": "error", "message": "Invalid token"})

            await websocket.close()

            return



        # ensure user exists in DB

        user_row = await get_user_by_username(username)

        if not user_row:

            await websocket.send_json({"type": "error", "message": "User not found"})

            await websocket.close()

            return



        # prevent duplicate online sessions

        if username in connected_users:

            await websocket.send_json({"type": "error", "message": "Already online"})

            await websocket.close()

            return



        connected_users[username] = websocket



        await websocket.send_json({

            "type": "success",

            "message": "Authorized",

            "username": username,

            "users": list(connected_users.keys())

        })

        await broadcast({"type": "user_joined", "username": username}, exclude=websocket)



        while True:

            data = await websocket.receive_json()

            t = data.get("type")



            if t == "message":

                text = (data.get("text") or "").strip()

                to_user = (data.get("to") or "").strip()

                if text and to_user:

                    await send_to(to_user, {"type": "pm", "from": username, "text": text})



            elif t == "voice":

                to_user = (data.get("to") or "").strip()

                b64 = data.get("b64", "")

                if to_user and b64:

                    payload = {

                        "type": "voice",

                        "from": username,

                        "b64": b64,

                        "sr": int(data.get("sr", 16000)),

                        "ch": int(data.get("ch", 1)),

                    }

                    await send_to(to_user, payload)



            elif t == "presence":

                kind = (data.get("kind") or "").strip()

                is_on = bool(data.get("is_on", False))

                to_user = (data.get("to") or "").strip()

                if kind in ("typing", "recording") and to_user:

                    await send_to(to_user, {

                        "type": "presence",

                        "kind": kind,

                        "from": username,

                        "is_on": is_on,

                        "to": to_user

                    })



    except WebSocketDisconnect:

        pass

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





@app.on_event("startup")

async def on_startup():

    await init_db()
