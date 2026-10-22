"""The panel as a Telegram Mini App: a web page inside Telegram, opened from
the panel bot's menu button.

It runs in the bot's own process and event loop (aiohttp), because the
settings live in memory in bot.store and a second process would race it.
nginx in front of it (expense-tracker's) serves it at WEBAPP_URL over HTTPS.

Every API request carries Telegram's initData, signed with the bot token:
only my account gets in, and only with initData under a day old. The API can
do what the panel can (it runs the same commands) — no "send anything" or
"read any chat".
"""

import datetime
import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

from bot.commands import Command

log = logging.getLogger("tg-assistant.webapp")

PAGE = Path(__file__).with_name("webapp.html")
MAX_AUTH_AGE_SECONDS = 24 * 3600
# What the page may run: the panel's commands, minus deleting messages.
COMMANDS = {
    "ai", "noai", "model", "persona", "reply", "filter", "unrule", "mute", "unmute",
    "reset", "ans", "draft", "sum", "backup",
}


def check_init_data(init_data: str, bot_token: str, owner_id: int, now: float | None = None) -> bool:
    """Whether initData is Telegram's, fresh and mine.

    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received = fields.pop("hash", "")
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not received or not hmac.compare_digest(received, expected):
        return False
    try:
        age = (now or time.time()) - int(fields.get("auth_date", "0"))
        user_id = json.loads(fields.get("user", "{}")).get("id")
    except ValueError:
        return False
    return 0 <= age <= MAX_AUTH_AGE_SECONDS and user_id == owner_id


def build_app(assistant, bot_token: str, owner_id: int) -> web.Application:
    # `assistant` is bot.main.Assistant (not imported: main imports this module).
    store, journal = assistant.store, assistant.journal

    @web.middleware
    async def auth(request: web.Request, handler):
        if request.path.startswith("/api/"):
            scheme, _, init_data = request.headers.get("Authorization", "").partition(" ")
            if scheme != "tma" or not check_init_data(init_data, bot_token, owner_id):
                return web.json_response({"error": "Это не твой пульт"}, status=401)
        return await handler(request)

    async def page(request: web.Request) -> web.Response:
        return web.FileResponse(PAGE, headers={"Cache-Control": "no-cache"})

    def label(chat_id: int) -> str:
        chat = store.ai_chats.get(chat_id)
        return (chat.label if chat else "") or store.muted.get(chat_id, "")

    async def state(request: web.Request) -> web.Response:
        ai = assistant.ai
        day = datetime.date.today()

        def spend(since: str) -> dict:
            rows = journal.spend(since)
            return {
                "total": sum(r[4] for r in rows),
                "models": [
                    {"model": m, "requests": n, "input": i, "output": o, "cost": c}
                    for m, n, i, o, c in rows
                ],
            }

        return web.json_response({
            "ai_available": assistant.ai_available,
            "deepseek_available": assistant.deepseek_available,
            "ai_enabled": store.ai_enabled,
            "persona": store.persona,
            "replies_today": journal.replies_today(),
            "daily_limit": ai.total_limit if ai else 0,
            "chats": [
                {
                    "id": c, "label": a.label, "auto": a.auto, "drafts": a.drafts, "replies": a.replies,
                    "model": store.chat_models.get(c) or "claude", "persona": store.chat_personas.get(c, ""),
                }
                for c, a in sorted(store.ai_chats.items(), key=lambda item: item[1].label.lower())
            ],
            "rules": [r.to_dict() for r in store.rules],
            "muted": [{"id": c, "label": lbl} for c, lbl in sorted(store.muted.items())],
            "usage": {
                "today": spend(day.isoformat()),
                "month": spend(day.replace(day=1).isoformat()),
                "top": [
                    {"id": c, "label": label(c), "requests": n, "cost": cost}
                    for c, n, cost in journal.top_chats(day.replace(day=1).isoformat())
                ],
            },
            "drafts": [
                {"id": d.id, "chat_id": d.chat_id, "label": d.label, "text": d.text, "context": d.context}
                for d in journal.list_drafts()
            ],
            "reports": [
                {"ts": ts, "chat_id": c, "label": lbl, "reason": reason, "text": text}
                for ts, c, lbl, reason, text in journal.recent_reports()
            ],
        })

    async def command(request: web.Request) -> web.Response:
        """{"name": "ai", "arg": "draft", "chat_id": 123} or with "target": "@user"."""
        body = await request.json()
        name = str(body.get("name", ""))
        if name not in COMMANDS:
            return web.json_response({"error": f"Нет такой команды: {name}"}, status=400)
        target = str(body.get("target") or "").strip() or None
        chat_id = body.get("chat_id")
        chat_id = int(chat_id) if chat_id is not None else None
        cmd = Command(name, str(body.get("arg", "")).strip(), target)
        name_for = label(chat_id) if chat_id is not None else ""
        if chat_id is not None and not name_for:
            name_for = await assistant.label_for(chat_id)
        answer = await assistant.run_command(cmd, chat_id, name_for)
        log.info("Mini App: .%s %s %s: %s", name, target or chat_id or "", cmd.arg[:40], answer.text[:80])
        return web.json_response({"text": answer.text, "error": answer.is_error})

    async def draft_action(request: web.Request) -> web.Response:
        draft_id = int(request.match_info["id"])
        action = request.match_info["action"]
        if assistant.draft(draft_id) is None:
            return web.json_response({"text": "Этого черновика уже нет", "error": True})
        if action == "send":
            body = await request.json() if request.can_read_body else {}
            answer = await assistant.send_draft(draft_id, (body.get("text") or "").strip() or None)
        elif action == "redo":
            answer = assistant.redraft(draft_id)
        else:
            journal.drop_draft(draft_id)
            return web.json_response({"text": "Черновик отброшен", "error": False})
        return web.json_response({"text": answer.text, "error": answer.is_error})

    app = web.Application(middlewares=[auth], client_max_size=64 * 1024)
    app.router.add_get("/", page)
    app.router.add_get("/api/state", state)
    app.router.add_post("/api/command", command)
    app.router.add_post(r"/api/drafts/{id:\d+}/{action:send|redo|drop}", draft_action)
    return app


async def serve(app: web.Application, port: int) -> web.AppRunner:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Mini App listening on :%s", port)
    return runner
