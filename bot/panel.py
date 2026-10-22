"""The panel bot: menus and buttons for managing the userbot.

Only the account the userbot is logged into may use it. Besides buttons it
understands the same text commands (".mute @vasya"), and a forwarded message
brings up actions for its author.

Multi-step actions ("add an auto-reply": whom → keywords → text) keep their
progress in memory; a restart just drops a half-done one.
"""

from dataclasses import dataclass, field

from telethon import Button, TelegramClient, events, utils
from telethon.errors import MessageNotModifiedError

from bot import commands
from bot.commands import Answer, Command
from bot.store import Store

ASK_TARGET = "Для кого? Пришли @username, +телефон или перешли сюда его сообщение."
NO_FORWARD_AUTHOR = (
    "Не вижу, чьё это сообщение: человек скрыл аккаунт в настройках пересылки. "
    "Пришли его @username."
)


@dataclass
class Wizard:
    kind: str  # "reply" | "filter" | "mute" | "del" | "ai"
    step: str  # "target" | "keywords" | "text" | "count"
    chat_id: int | None = None
    label: str = ""
    keywords: list[str] = field(default_factory=list)


def menu_buttons():
    return [
        [Button.inline("📋 Правила", b"rules"), Button.inline("🔇 Заглушённые", b"muted")],
        [Button.inline("💬 Автоответ", b"new:reply"), Button.inline("🚫 Фильтр слов", b"new:filter")],
        [Button.inline("🤖 ИИ-собеседник", b"ai"), Button.inline("🗑 Удалить сообщения", b"new:del")],
        [Button.inline("❓ Справка", b"help")],
    ]


BACK = [Button.inline("⬅️ Меню", b"menu")]
CANCEL = [Button.inline("✖️ Отмена", b"menu")]


def rules_screen(store: Store):
    buttons = [Button.inline(f"🗑 {r.id}", f"unrule:{r.id}".encode()) for r in store.rules]
    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    text = commands.format_rules(store, None)
    if store.rules:
        text += "\n\nКнопка с номером удаляет правило."
    return text, rows + [BACK]


def muted_screen(store: Store):
    rows = [
        [Button.inline(f"🔊 {f'{label} #{c}'.strip()}", f"unmute:{c}".encode())]
        for c, label in sorted(store.muted.items())
    ]
    text = "Заглушённые: удаляю все их новые сообщения.\nКнопка снимает заглушку." if rows else "Никто не заглушён."
    return text, rows + [[Button.inline("➕ Заглушить", b"new:mute")], BACK]


def ai_screen(store: Store):
    toggle = (
        Button.inline("⏸ Выключить ИИ везде", b"aioff") if store.ai_enabled
        else Button.inline("▶️ Включить ИИ", b"aion")
    )
    rows = [
        [Button.inline(f"🔕 {'🆕 ' if a.auto else ''}{f'{a.label} #{c}'.strip()}", f"noai:{c}".encode())]
        for c, a in sorted(store.ai_chats.items())
    ]
    text = "🤖 ИИ " + ("включён." if store.ai_enabled else "выключен.") + (
        " Сам отвечает незнакомцам (не из контактов), которые написали первыми, "
        "и людям из списка. Каждый разговор кончается отчётом здесь."
    )
    if rows:
        text += "\n\nСписок (🆕 — незнакомец, подхвачен сам). Кнопка убирает из списка навсегда."
    text += (
        "\n\nХарактер ответов: .persona пишу коротко, на «ты», шучу"
        "\nДля одного человека: .persona @user язвительно, односложно"
    )
    if store.persona:
        text += f"\nСейчас: {store.persona}"
    return text, [[toggle]] + rows + [[Button.inline("➕ Добавить в список", b"new:ai")], BACK]


def person_screen(store: Store, chat_id: int, label: str):
    muted = chat_id in store.muted
    mute = (
        Button.inline("🔊 Разглушить", f"unmute:{chat_id}".encode()) if muted
        else Button.inline("🔇 Заглушить", f"act:mute:{chat_id}".encode())
    )
    ai = (
        Button.inline("🔕 Выключить ИИ", f"noai:{chat_id}".encode()) if chat_id in store.ai_chats
        else Button.inline("🤖 Включить ИИ", f"act:ai:{chat_id}".encode())
    )
    rules = [r for r in store.rules if r.chat_id == chat_id]
    text = f"{label or chat_id} #{chat_id}"
    if chat_id in store.chat_personas:
        text += f"\nХарактер ИИ с ним: {store.chat_personas[chat_id]}"
    if rules:
        text += "\n" + commands.format_rules(store, chat_id)
    return text, [
        [mute, Button.inline("🗑 Удалить последние", f"act:del:{chat_id}".encode())],
        [Button.inline("💬 Автоответ ему", f"act:reply:{chat_id}".encode()),
         Button.inline("🚫 Фильтр слов", f"act:filter:{chat_id}".encode())],
        [ai, Button.inline("✍️ Ответить ИИ", f"ans:{chat_id}".encode())],
        [Button.inline("♻️ Сбросить лимит ИИ", f"reset:{chat_id}".encode())],
        BACK,
    ]


async def edit(event, screen: tuple[str, list]) -> None:
    text, buttons = screen
    try:
        await event.edit(text, buttons=buttons)
    except MessageNotModifiedError:
        pass  # the same button pressed twice


async def reply(event, screen: tuple[str, list]) -> None:
    text, buttons = screen
    await event.reply(text, buttons=buttons)


def setup(bot: TelegramClient, assistant, store: Store, owner_id: int) -> None:
    # `assistant` is bot.main.Assistant (not imported: main imports this module).
    wizard: Wizard | None = None
    labels: dict[int, str] = {}  # what a chat from a forward/target is called

    def start(kind: str, chat_id: int | None = None, label: str = "") -> tuple[str, list]:
        nonlocal wizard
        if chat_id is None:
            wizard = Wizard(kind, "target")
            # "Every private chat" only makes sense for rules.
            extra = [Button.inline("👥 Во всех личках", b"all")] if kind in ("reply", "filter") else []
            return ASK_TARGET, [extra, CANCEL] if extra else [CANCEL]
        wizard = Wizard(kind, "target", chat_id, label)
        return advance(None)

    def advance(text: str | None) -> tuple[str, list]:
        """Feeds the next answer to the wizard; returns what to show."""
        nonlocal wizard
        w = wizard
        assert w is not None
        who = f"для {w.label}" if w.chat_id is not None else "во всех личках"

        if w.step == "target":
            # The target is already filled in by the caller.
            if w.kind in ("mute", "ai"):
                wizard = None
                if w.kind == "ai" and not assistant.ai_available:
                    return finish(commands.error(commands.NO_AI))
                return finish(commands.execute(store, Command(w.kind, ""), w.chat_id, w.label))
            if w.kind == "del":
                w.step = "count"
                return f"Сколько последних сообщений {w.label} удалить? (1–100)", [CANCEL]
            w.step = "keywords"
            return f"Ключевые слова через запятую ({who}):", [CANCEL]

        if w.step == "keywords":
            w.keywords = commands.split_keywords(text or "")
            if not w.keywords:
                return "Нужно хотя бы одно слово. Ещё раз:", [CANCEL]
            if w.kind == "filter":
                wizard = None
                cmd = Command("filter", ", ".join(w.keywords))
                return finish(commands.execute(store, cmd, w.chat_id, w.label))
            w.step = "text"
            return f"Что отвечать на «{', '.join(w.keywords)}»?", [CANCEL]

        if w.step == "text":
            if not text:
                return "Пришли текст ответа:", [CANCEL]
            wizard = None
            cmd = Command("reply", f"{', '.join(w.keywords)} => {text}")
            return finish(commands.execute(store, cmd, w.chat_id, w.label))

        raise ValueError(f"Unexpected step: {w.step}")

    def finish(answer: Answer) -> tuple[str, list]:
        return ("⚠️ " if answer.is_error else "✅ ") + answer.text, [BACK]

    async def set_target(chat_id: int, label: str) -> tuple[str, list]:
        assert wizard is not None
        wizard.chat_id, wizard.label = chat_id, label
        return advance(None)

    @bot.on(events.CallbackQuery())
    async def on_button(event) -> None:
        nonlocal wizard
        if event.sender_id != owner_id:
            await event.answer("Это не твой пульт", alert=True)
            return
        data = event.data.decode()
        name, _, arg = data.partition(":")

        if name == "menu":
            wizard = None
            await event.edit("Что делаем?", buttons=menu_buttons())
        elif name == "help":
            await event.edit(commands.HELP, buttons=[BACK])
        elif name == "rules":
            await edit(event, rules_screen(store))
        elif name == "unrule":
            store.remove_rule(int(arg))
            await event.answer(f"Правило {arg} удалено")
            await edit(event, rules_screen(store))
        elif name == "ai":
            await edit(event, ai_screen(store))
        elif name in ("aion", "aioff"):
            if name == "aion" and not assistant.ai_available:
                await event.answer(commands.NO_AI, alert=True)
                return
            store.set_ai_enabled(name == "aion")
            await edit(event, ai_screen(store))
        elif name == "noai":
            store.set_ai(int(arg), False)
            await event.answer("ИИ выключен")
            await edit(event, ai_screen(store))
        elif name == "muted":
            await edit(event, muted_screen(store))
        elif name == "unmute":
            store.set_muted(int(arg), False)
            await event.answer("Больше не удаляю")
            await edit(event, muted_screen(store))
        elif name == "new":
            await edit(event, start(arg))
        elif name == "all":
            if wizard is None or wizard.step != "target":
                await event.answer()
                return
            wizard.chat_id, wizard.label = None, ""
            await edit(event, advance(None))
        elif name == "reset":
            if assistant.ai is None:
                await event.answer(commands.NO_AI, alert=True)
                return
            assistant.ai.reset_limit(int(arg))
            await event.answer("Лимит ИИ сброшен")
        elif name == "ans":
            chat_id = int(arg)
            label = labels.get(chat_id) or await assistant.label_for(chat_id)
            answer = assistant.answer_with_ai(chat_id, label)
            await event.answer(answer.text, alert=answer.is_error)
        elif name == "act":
            kind, _, chat = arg.partition(":")
            chat_id = int(chat)
            label = labels.get(chat_id) or await assistant.label_for(chat_id)
            await edit(event, start(kind, chat_id, label))
        else:
            await event.answer()

    @bot.on(events.NewMessage(incoming=True))
    async def on_message(event) -> None:
        nonlocal wizard
        if event.sender_id != owner_id:
            await event.reply("Это личный бот, он слушается только хозяина.")
            return
        msg = event.message
        text = (msg.raw_text or "").strip()

        if msg.fwd_from is not None:
            fwd = msg.fwd_from
            if fwd.from_id is None:
                await event.reply(NO_FORWARD_AUTHOR)
                return
            chat_id = utils.get_peer_id(fwd.from_id)
            label = await assistant.label_for(chat_id) or fwd.from_name or ""
            labels[chat_id] = label
            if wizard is not None and wizard.step == "target":
                await reply(event, await set_target(chat_id, label))
            else:
                wizard = None
                await reply(event, person_screen(store, chat_id, label))
            return

        cmd = commands.parse(text)
        if cmd is not None:
            wizard = None
            answer = await assistant.run_command(cmd, None, "")
            await event.reply(answer.text, buttons=[BACK])
            return

        if wizard is None or text.startswith("/"):
            # /start, /menu or anything unexpected: show the menu.
            wizard = None
            await event.reply("Что делаем?", buttons=menu_buttons())
            return

        if wizard.step == "target":
            if not commands.is_target(text):
                await event.reply(ASK_TARGET, buttons=[CANCEL])
                return
            try:
                chat_id, label = await assistant.resolve(text)
            except ValueError:
                await event.reply(f"Не нашёл {text}. {ASK_TARGET}", buttons=[CANCEL])
                return
            labels[chat_id] = label
            await reply(event, await set_target(chat_id, label))
        elif wizard.step == "count":
            if not text.isdigit() or not 1 <= int(text) <= 100:
                await event.reply("Нужно число от 1 до 100:", buttons=[CANCEL])
                return
            w, wizard = wizard, None
            answer = await assistant.delete_last(w.chat_id, int(text), w.label)
            await reply(event, finish(answer))
        else:
            await reply(event, advance(text))

