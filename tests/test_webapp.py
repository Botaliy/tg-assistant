import asyncio
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from aiohttp.test_utils import TestClient, TestServer

from bot.journal import Journal
from bot.main import Assistant
from bot.store import Store
from bot.webapp import build_app, check_init_data

TOKEN, OWNER = "123:abc", 42


def signed(user_id=OWNER, auth_date=None, token=TOKEN):
    fields = {
        "auth_date": str(int(auth_date or time.time())),
        "query_id": "AAA",
        "user": json.dumps({"id": user_id, "first_name": "Я"}),
    }
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_init_data_must_be_signed_fresh_and_mine():
    assert check_init_data(signed(), TOKEN, OWNER)
    assert not check_init_data(signed(user_id=7), TOKEN, OWNER)
    assert not check_init_data(signed(token="999:other"), TOKEN, OWNER)
    assert not check_init_data(signed(auth_date=time.time() - 2 * 24 * 3600), TOKEN, OWNER)
    assert not check_init_data(signed().replace("query_id=AAA", "query_id=BBB"), TOKEN, OWNER)
    assert not check_init_data("", TOKEN, OWNER)


class FakeClient:
    async def get_entity(self, chat_id):
        raise ValueError("unknown")


def with_app(tmp_path, test):
    store = Store(tmp_path / "rules.json")
    journal = Journal(tmp_path / "assistant.db")
    assistant = Assistant(FakeClient(), store, journal, ai_available=False)

    async def go():
        async with TestClient(TestServer(build_app(assistant, TOKEN, OWNER))) as client:
            await test(client, store, journal)

    asyncio.run(go())


def auth(init_data=None):
    return {"Authorization": "tma " + (init_data or signed())}


def test_api_needs_my_init_data(tmp_path):
    async def test(client, store, journal):
        assert (await client.get("/api/state")).status == 401
        assert (await client.get("/api/state", headers=auth(signed(user_id=7)))).status == 401
        response = await client.get("/api/state", headers=auth())
        assert response.status == 200
        assert (await response.json())["ai_enabled"] is False
        # The page itself is open: it has no data, the API does.
        assert (await client.get("/")).status == 200

    with_app(tmp_path, test)


def test_commands_from_the_page(tmp_path):
    async def test(client, store, journal):
        response = await client.post("/api/command", headers=auth(), json={"name": "reply", "arg": "цена => в закрепе"})
        assert (await response.json()) == {"text": "Правило 1: отвечаю во всех личках на «цена»", "error": False}
        await client.post("/api/command", headers=auth(), json={"name": "mute", "chat_id": 5})
        state = await (await client.get("/api/state", headers=auth())).json()
        assert [r["keywords"] for r in state["rules"]] == [["цена"]]
        assert state["muted"] == [{"id": 5, "label": ""}]
        # Deleting messages isn't something the page does.
        response = await client.post("/api/command", headers=auth(), json={"name": "del", "arg": "5", "chat_id": 5})
        assert response.status == 400

    with_app(tmp_path, test)


def test_dropping_a_draft(tmp_path):
    async def test(client, store, journal):
        draft_id = journal.save_draft(5, "@vasya", "привет", None, 10)
        state = await (await client.get("/api/state", headers=auth())).json()
        assert state["drafts"][0]["text"] == "привет"
        response = await client.post(f"/api/drafts/{draft_id}/drop", headers=auth(), json={})
        assert (await response.json())["error"] is False
        assert journal.get_draft(draft_id) is None
        response = await client.post(f"/api/drafts/{draft_id}/send", headers=auth(), json={})
        assert (await response.json()) == {"text": "Этого черновика уже нет", "error": True}

    with_app(tmp_path, test)
