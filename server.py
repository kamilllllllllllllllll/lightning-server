import asyncio

import json

import websockets



clients = {}  # username -> websocket





async def handler(websocket):

    username = None

    try:

        async for message in websocket:

            data = json.loads(message)



            if data["type"] == "register":

                username = data["username"]

                clients[username] = websocket

                print(f"{username} connected")



            elif data["type"] == "send":

                to_user = data["to"]

                if to_user in clients:

                    await clients[to_user].send(json.dumps({

                        "type": "message",

                        "from": data["from"],

                        "text": data["text"]

                    }))



    except websockets.exceptions.ConnectionClosed:

        print("Connection closed")



    finally:

        if username and username in clients:

            del clients[username]

            print(f"{username} disconnected")





async def main():

    async with websockets.serve(handler, "0.0.0.0", 8000):

        print("Server started on port 8000")

        await asyncio.Future()





if __name__ == "__main__":

    asyncio.run(main())

                    