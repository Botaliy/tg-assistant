"""Text commands, e.g. ".mute @vasya" or ".reply цена => ...".

They work in the panel bot (next to its buttons, see bot.panel) and name the
chat they act on with @username. Without a target, .reply/.filter/.rules work
on every private chat.

Typed right in a conversation from my account, a command acts on that
conversation and is deleted silently: no confirmation, only errors and
requested output (.rules, .help) go to the panel. ".del" is the one meant
for conversations.

Everything here is pure — the Telegram side lives in bot.main and bot.panel.
"""

from dataclasses import dataclass

from bot.rules import DELETE, REPLY
from bot.store import DEEPSEEK, Store

PREFIX = "."
NAMES = {"help", "del", "mute", "unmute", "reply", "filter", "rules", "unrule", "ai", "noai", "persona", "ans", "reset", "model", "usage", "draft"}
NO_AI = "ИИ выключен: впиши ANTHROPIC_API_KEY в .env на сервере и перезапусти бота"
NO_DEEPSEEK = "DeepSeek не настроен: впиши DEEPSEEK_API_KEY в .env на сервере и перезапусти бота"
# What ".model" understands; None is Claude.
MODELS = {"deepseek": DEEPSEEK, "ds": DEEPSEEK, "claude": None, "-": None}
# Answers to these are what was asked for, not a confirmation, so they're
# sent to the panel even when the command was typed in a conversation.
# ".draft N" reads at most this many messages.
MAX_DRAFT_CONTEXT = 50
OUTPUT_COMMANDS = {"help", "rules", "persona", "model", "usage"}

HELP = """Всё есть на кнопках в /menu. Перешли сюда сообщение человека, и появятся кнопки для него.

То же самое текстом:
.mute @user / .unmute @user — удалять все новые сообщения от него / перестать
.reply ключ1, ключ2 => ответ — автоответ во всех личках
.reply @user ключ1, ключ2 => ответ — автоответ только ему
.filter [@user] ключ1, ключ2 — удалять сообщения с этими словами
.del @user N — удалить его последние N сообщений
.rules [@user] — список правил
.unrule N — удалить правило N
.ai on / .ai off — включить / выключить ИИ-собеседника целиком (только здесь, в пульте)
.ai @user / .noai @user — добавить в список ИИ / убрать (и не подхватывать сам)
.ai on @user / .ai off @user — то же, что .ai @user / .noai @user
.ai в группе — ИИ отвечает там, когда тебя упомянут или ответят тебе
.ans @user — ИИ разово отвечает ему, даже если ИИ для него выключен
.draft @user [N] — черновик ответа ему сюда, по последним N сообщениям (по умолчанию 10, до 50)
.ai draft @user — ИИ ведёт чат, но не отправляет сам, а присылает сюда черновики
.reset @user — сбросить ему дневной лимит ответов ИИ (незнакомцу — и общий)
.persona текст — общий характер ИИ (без текста — показать, «-» — сбросить)
.persona @user текст — характер ИИ с ним, важнее общего
.model @user deepseek / .model @user claude — чем ИИ отвечает ему (без модели — показать)
.usage — сколько ИИ потратил сегодня и за месяц
Вместо @user можно +телефон из контактов или #id из .rules.

В переписке:
.del — ответом на сообщение: удалить его у обоих
.del N — удалить последние N сообщений собеседника
.ans — ИИ разово отвечает на последние сообщения (ответом на сообщение — реплаем на него)
.draft [N] — то же, но черновиком в пульт, по последним N сообщениям
.ai draft — ИИ в этом чате только присылает черновики в пульт
.ai on / .ai off — включить / выключить ИИ только в этом чате (.ai off = .noai)
.model deepseek / .model claude — чем ИИ отвечает в этом чате
Остальные команды там тоже работают (без @user, для этого чата),
но удаляются молча, без подтверждения."""


@dataclass
class Command:
    name: str
    arg: str
    target: str | None = None  # as typed: "@username", "+phone" or "#chat_id"


@dataclass
class Answer:
    text: str
    is_error: bool = False


def parse(text: str) -> Command | None:
    if not text.startswith(PREFIX):
        return None
    name, _, arg = text[len(PREFIX):].partition(" ")
    name = name.strip().lower()
    if name not in NAMES:
        return None
    first, _, rest = arg.strip().partition(" ")
    if is_target(first):
        return Command(name=name, arg=rest.strip(), target=first)
    return Command(name=name, arg=arg.strip())


def is_target(token: str) -> bool:
    """@username, +phone (from contacts) or #chat_id (as .rules shows it)."""
    if len(token) < 2:
        return False
    if token[0] == "@":
        return True
    if token[0] in "+#":
        return token[1:].lstrip("-").isdigit()
    return False


def split_keywords(raw: str) -> list[str]:
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


def parse_del_count(arg: str) -> int | None:
    """N from ".del N"; None when there's no number (".del" as a reply)."""
    if not arg:
        return None
    if not arg.isdigit() or int(arg) < 1:
        raise ValueError("После .del нужно число, например .del 5")
    return min(int(arg), 100)


def parse_draft_context(arg: str) -> int | None:
    """N from ".draft N", capped at MAX_DRAFT_CONTEXT; None: the usual context."""
    if not arg:
        return None
    if not arg.isdigit() or int(arg) < 1:
        raise ValueError("После .draft нужно число сообщений, например .draft 30")
    return min(int(arg), MAX_DRAFT_CONTEXT)


def error(text: str) -> Answer:
    return Answer(text, is_error=True)


def execute(store: Store, cmd: Command, chat_id: int | None, label: str = "") -> Answer:
    """Runs a rule/mute command against chat_id (None: every private chat).

    label is how to call that chat in answers and in .rules, e.g. "@vasya".
    """
    scope = "во всех личках" if chat_id is None else f"для {label or chat_id}"

    if cmd.name == "help":
        return Answer(HELP)

    if cmd.name in ("mute", "unmute"):
        if chat_id is None:
            return error(f"Кого? Формат: .{cmd.name} @user")
        muted = cmd.name == "mute"
        changed = store.set_muted(chat_id, muted, label)
        if muted:
            return Answer(f"Удаляю все новые сообщения {scope}" if changed else f"{label} уже заглушён")
        return Answer(f"Больше не удаляю сообщения {scope}" if changed else f"{label} и не был заглушён")

    # The master switch only from the panel: in a conversation (or with
    # @user) ".ai on/off" is about that one chat.
    if cmd.name == "ai" and cmd.arg in ("on", "off") and chat_id is None:
        store.set_ai_enabled(cmd.arg == "on")
        return Answer("ИИ включён" if store.ai_enabled else "ИИ выключен везде")

    if cmd.name in ("ai", "noai"):
        if chat_id is None:
            return error(f"Для кого? Формат: .{cmd.name} @user")
        on = cmd.name == "ai" and cmd.arg != "off"
        changed = store.set_ai(chat_id, on, label)
        if on:
            # ".ai draft": drafts in the panel; ".ai" / ".ai on": answers by itself.
            changed = store.set_drafts(chat_id, cmd.arg == "draft") or changed
        if on and not store.ai_enabled:
            # An error so that it reaches the panel even from a conversation.
            return error(f"{label or chat_id} в списке ИИ, но сам ИИ выключен: включи его в пульте (.ai on или кнопкой)")
        if on and cmd.arg == "draft":
            return Answer(f"ИИ присылает черновики {scope}" if changed else f"ИИ уже присылает черновики {scope}")
        if on:
            return Answer(f"ИИ отвечает {scope}" if changed else f"ИИ уже отвечает {scope}")
        return Answer(f"ИИ больше не отвечает {scope}" if changed else f"ИИ и не отвечал {scope}")

    if cmd.name == "persona":
        # Without a target: the general style; for a chat: that chat's own.
        current = store.persona if chat_id is None else store.chat_personas.get(chat_id, "")
        whose = "Общий характер ИИ" if chat_id is None else f"Характер ИИ {scope}"
        if not cmd.arg:
            return Answer(f"{whose}:\n{current}" if current else f"{whose} не задан")
        store.set_persona("" if cmd.arg == "-" else cmd.arg, chat_id)
        return Answer(f"{whose} сброшен" if cmd.arg == "-" else f"{whose} сохранён")

    if cmd.name == "model":
        if chat_id is None:
            return error("Для кого? Формат: .model @user deepseek")
        if not cmd.arg:
            return Answer(f"ИИ {scope} отвечает через {model_name(store.chat_models.get(chat_id))}")
        if cmd.arg.lower() not in MODELS:
            return error("Формат: .model deepseek или .model claude")
        model = MODELS[cmd.arg.lower()]
        changed = store.set_model(chat_id, model)
        answer = f"ИИ {scope} {'теперь' if changed else 'и так'} отвечает через {model_name(model)}"
        if chat_id not in store.ai_chats:
            answer += ", но сам ИИ тут не включён: .ai — отвечать всегда, .ans — разово"
        return Answer(answer)

    if cmd.name == "reply":
        keys, sep, answer = cmd.arg.partition("=>")
        keywords, answer = split_keywords(keys), answer.strip()
        if not sep or not keywords or not answer:
            return error("Формат: .reply [@user] ключ1, ключ2 => ответ")
        rule = store.add_rule(REPLY, keywords, answer, chat_id, label)
        return Answer(f"Правило {rule.id}: отвечаю {scope} на «{', '.join(keywords)}»")

    if cmd.name == "filter":
        keywords = split_keywords(cmd.arg)
        if not keywords:
            return error("Формат: .filter [@user] ключ1, ключ2")
        rule = store.add_rule(DELETE, keywords, chat_id=chat_id, chat_label=label)
        return Answer(f"Правило {rule.id}: удаляю {scope} сообщения с «{', '.join(keywords)}»")

    if cmd.name == "rules":
        return Answer(format_rules(store, chat_id))

    if cmd.name == "unrule":
        if not cmd.arg.isdigit():
            return error("Формат: .unrule N (номер из .rules)")
        if store.remove_rule(int(cmd.arg)):
            return Answer(f"Правило {cmd.arg} удалено")
        return error(f"Правила {cmd.arg} нет")

    raise ValueError(f"Unhandled command: {cmd.name}")


def model_name(model: str | None) -> str:
    return "DeepSeek" if model == DEEPSEEK else "Claude"


def short(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def format_rules(store: Store, chat_id: int | None) -> str:
    # Without a target show everything; for a chat — what applies there.
    rules = [r for r in store.rules if chat_id is None or r.chat_id in (None, chat_id)]
    lines = []
    for r in rules:
        where = "все лички" if r.chat_id is None else f"{r.chat_label} #{r.chat_id}".strip()
        keys = ", ".join(r.keywords)
        action = r.text if r.kind == REPLY else "удалить"
        lines.append(f"{r.id}. [{where}] «{keys}» → {action}")
    muted = [
        f"{label} #{c}".strip() for c, label in sorted(store.muted.items())
        if chat_id is None or c == chat_id
    ]
    if muted:
        lines.append("Заглушены: " + ", ".join(muted))
    ai = [
        f"{'🆕 ' if a.auto else ''}{'📝 ' if a.drafts else ''}{a.label} #{c}".strip()
        for c, a in sorted(store.ai_chats.items())
        if chat_id is None or c == chat_id
    ]
    if ai:
        state = "" if store.ai_enabled else " (сейчас ИИ выключен)"
        lines.append(f"ИИ отвечает{state}: " + ", ".join(ai))
    deepseek = [
        f"{store.ai_chats[c].label if c in store.ai_chats else ''} #{c}".strip()
        for c, m in sorted(store.chat_models.items()) if m == DEEPSEEK and (chat_id is None or c == chat_id)
    ]
    if deepseek:
        lines.append("Через DeepSeek: " + ", ".join(deepseek))
    if chat_id is None and store.persona:
        lines.append(f"Общий характер ИИ: {short(store.persona)}")
    for c, persona in sorted(store.chat_personas.items()):
        if chat_id is None or c == chat_id:
            label = store.ai_chats[c].label if c in store.ai_chats else store.muted.get(c, "")
            lines.append(f"Характер ИИ для {f'{label} #{c}'.strip()}: {short(persona)}")
    return "\n".join(lines) or "Правил пока нет"
