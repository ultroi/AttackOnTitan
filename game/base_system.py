from typing import Optional
from telegram import Update
from telegram.ext import ContextTypes
from .command_utils import ensure_db, fetch_player, fetch_character, send_reply, send_log

class BaseSystem:
    def __init__(self, context: ContextTypes.DEFAULT_TYPE):
        self.context = context
        self.db = None

    async def ensure_db(self):
        if self.db is None:
            self.db = await ensure_db(self.context)
        return self.db

    async def get_player(self, user_id: int, raise_on_missing: bool = False):
        db = await self.ensure_db()
        return await fetch_player(db, user_id, raise_on_missing=raise_on_missing)

    async def get_character(self, user_id: int, name: str, raise_on_missing: bool = False):
        db = await self.ensure_db()
        return await fetch_character(db, user_id, name, raise_on_missing=raise_on_missing)

    async def reply(self, update: Update, text: str, **kwargs):
        await send_reply(update, text, **kwargs)

    async def log(self, channel: int, text: str, **kwargs):
        await send_log(self.context, channel, text, **kwargs)
