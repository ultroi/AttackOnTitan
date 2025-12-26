import pytest
from telegram import Message, User
from types import SimpleNamespace
import asyncio

from game.command_utils import parse_target_user

class DummyMessage:
    def __init__(self, from_user=None, reply_to_message=None):
        self.from_user = from_user
        self.reply_to_message = reply_to_message

class DummyUpdate:
    def __init__(self, message=None):
        self.message = message
        self.effective_message = message

@pytest.mark.asyncio
async def test_parse_target_user_from_reply():
    user = SimpleNamespace(id=123, first_name="Alice")
    reply_user = SimpleNamespace(id=456, first_name="Bob")
    reply_msg = DummyMessage(from_user=reply_user)
    msg = DummyMessage(from_user=user, reply_to_message=reply_msg)
    update = DummyUpdate(message=msg)

    res = await parse_target_user(update, (), default_to_sender=True)
    assert res == 456

@pytest.mark.asyncio
async def test_parse_target_user_from_args():
    user = SimpleNamespace(id=123, first_name="Alice")
    msg = DummyMessage(from_user=user)
    update = DummyUpdate(message=msg)

    res = await parse_target_user(update, ("789",), default_to_sender=True)
    assert res == 789

@pytest.mark.asyncio
async def test_parse_target_user_default_to_sender():
    user = SimpleNamespace(id=123, first_name="Alice")
    msg = DummyMessage(from_user=user)
    update = DummyUpdate(message=msg)

    res = await parse_target_user(update, (), default_to_sender=True)
    assert res == 123

@pytest.mark.asyncio
async def test_parse_target_user_no_default():
    user = SimpleNamespace(id=123, first_name="Alice")
    msg = DummyMessage(from_user=user)
    update = DummyUpdate(message=msg)

    res = await parse_target_user(update, (), default_to_sender=False)
    assert res is None
