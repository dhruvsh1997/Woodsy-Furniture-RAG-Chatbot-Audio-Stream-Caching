"""
consumers.py — WebSocket handler.

Receives JSON: {"message": "...", "session_id": "..."}
Pushes back:
  Text frames  → JSON {"type": ..., ...}
  Binary frames → raw MP3 bytes
"""

import json
from channels.generic.websocket import AsyncWebsocketConsumer
from .rag_logic.pipeline import run_pipeline


class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        await self.accept()

    async def disconnect(self, code):
        pass

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return

        payload = json.loads(text_data)
        raw_query = payload.get("message", "").strip()
        session_id = payload.get("session_id", "anonymous")

        if not raw_query:
            return

        try:
            async for event in run_pipeline(raw_query, session_id):
                if event["type"] == "audio":
                    # Send binary frame
                    await self.send(bytes_data=event["data"])
                else:
                    # Send JSON text frame
                    await self.send(text_data=json.dumps(event))
        except Exception as exc:
            await self.send(
                text_data=json.dumps({"type": "error", "message": str(exc)})
            )
