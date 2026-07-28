import asyncio
import json
import logging
import websockets
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

class DerivClient:
    def __init__(self, app_id: str, token: str, mode: str = "sandbox"):
        self.app_id = app_id
        self.token = token
        self.mode = mode
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.callbacks: Dict[str, Callable] = {}
        self._listener_task: Optional[asyncio.Task] = None
        self._stop = False
        self.url = f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"

    async def connect(self):
        self._stop = False
        self.ws = await websockets.connect(self.url)
        auth_req = {"authorize": self.token, "req_id": 1}
        await self.ws.send(json.dumps(auth_req))
        response = await self.ws.recv()
        data = json.loads(response)
        if data.get("error"):
            raise Exception(f"Auth failed: {data['error']['message']}")
        logger.info("Connected and authorised to Deriv")
        self._listener_task = asyncio.create_task(self._listen())

    async def _listen(self):
        try:
            async for message in self.ws:
                if self._stop:
                    break
                data = json.loads(message)
                if "msg_type" in data and data["msg_type"] in self.callbacks:
                    await self.callbacks[data["msg_type"]](data)
                if "tick" in data or "proposal" in data or "buy" in data:
                    if "data" in self.callbacks:
                        await self.callbacks["data"](data)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Deriv WebSocket closed.")
            if not self._stop:
                await self.reconnect()

    async def reconnect(self):
        if self._stop:
            return
        await asyncio.sleep(2)
        await self.connect()

    async def send(self, message: dict):
        if self.ws:
            await self.ws.send(json.dumps(message))

    def on(self, event: str, callback: Callable):
        self.callbacks[event] = callback

    async def close(self):
        self._stop = True
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self.ws:
            await self.ws.close()