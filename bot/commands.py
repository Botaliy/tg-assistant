"""Commands I type from my own account, e.g. ".mute" or ".reply цена => ...".

Typed in a chat, a command acts on that chat; typed in Saved Messages, rules
apply to every private chat. Everything here is pure — the Telegram side
(deleting messages, sending answers) lives in bot.main.
"""

from dataclasses import dataclass

from bot.rules import DELETE, REPLY
from bot.store import Store

PREFIX = "."
NAMES = {"help", "del", "mute", "unmute", "reply", "filter", "rules", "unrule"}

HELP = """Команды (пишутся от тебя, начинаются с точки):

.del — ответом на сообщение: удалить его у обоих
.del N — удалить последние N сообщений собеседника в этом чате
.mute / .unmute — удалять все новые сообщения в этом чате / перестать

.reply ключ1, ключ2 => ответ — автоответ на сообщения с этими словами
.filter ключ1, ключ2 — удалять сообщения с этими словами
.rules — список правил
.unrule N — удалить правило N

В чате .reply и .filter действуют только на этот чат,
в «Избранном» — на все личные чаты."""


@dataclass
class Command:
    name: str
    arg: str


def parse(text: str) -> Command | None:
    if not text.startswith(PREFIX):
        return None
    name, _, arg = text[len(PREFIX):].partition(" ")
    name = name.strip().lower()
    if name not in NAMES:
        return None
    return Command(name=name, arg=arg.strip())


def split_keywords(raw: str) -> list[str]:
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


def parse_del_count(arg: str) -> int | None:
    """N from ".del N"; None when there's no number (".del" as a reply)."""
    if not arg:
        return None
    if not arg.isdigit() or int(arg) < 1:
        raise ValueError("После .del нужно число, например .del 5")
    return min(int(arg), 100)


def execute(store: Store, cmd: Command, chat_id: int | None) -> str:
    """Runs a rule/mute command. chat_id is None when typed in Saved Messages."""
    scope = "во всех личных чатах" if chat_id is None else "в этом чате"

    if cmd.name == "help":
        return HELP

    if cmd.name in ("mute", "unmute"):
        if chat_id is None:
            return f".{cmd.name} пишется в том чате, который нужно заглушить"
        muted = cmd.name == "mute"
        changed = store.set_muted(chat_id, muted)
        if muted:
            return "Удаляю все новые сообщения в этом чате" if changed else "Чат уже заглушён"
        return "Больше не удаляю сообщения в этом чате" if changed else "Чат и не был заглушён"

    if cmd.name == "reply":
        keys, sep, answer = cmd.arg.partition("=>")
        keywords, answer = split_keywords(keys), answer.strip()
        if not sep or not keywords or not answer:
            return "Формат: .reply ключ1, ключ2 => ответ"
        rule = store.add_rule(REPLY, keywords, answer, chat_id)
        return f"Правило {rule.id}: отвечаю {scope} на «{', '.join(keywords)}»"

    if cmd.name == "filter":
        keywords = split_keywords(cmd.arg)
        if not keywords:
            return "Формат: .filter ключ1, ключ2"
        rule = store.add_rule(DELETE, keywords, chat_id=chat_id)
        return f"Правило {rule.id}: удаляю {scope} сообщения с «{', '.join(keywords)}»"

    if cmd.name == "rules":
        return format_rules(store, chat_id)

    if cmd.name == "unrule":
        if not cmd.arg.isdigit():
            return "Формат: .unrule N (номер из .rules)"
        if store.remove_rule(int(cmd.arg)):
            return f"Правило {cmd.arg} удалено"
        return f"Правила {cmd.arg} нет"

    raise ValueError(f"Unhandled command: {cmd.name}")


def format_rules(store: Store, chat_id: int | None) -> str:
    # From Saved Messages show everything; from a chat — what applies there.
    rules = [r for r in store.rules if chat_id is None or r.chat_id in (None, chat_id)]
    lines = []
    for r in rules:
        where = "все лички" if r.chat_id is None else f"чат {r.chat_id}"
        keys = ", ".join(r.keywords)
        if r.kind == REPLY:
            lines.append(f"{r.id}. [{where}] «{keys}» → {r.text}")
        else:
            lines.append(f"{r.id}. [{where}] «{keys}» → удалить")
    muted = store.muted if chat_id is None else store.muted & {chat_id}
    if muted:
        lines.append("Заглушены: " + ", ".join(str(c) for c in sorted(muted)))
    return "\n".join(lines) or "Правил пока нет"
