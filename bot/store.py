import json
from dataclasses import asdict, dataclass
from pathlib import Path

from bot.rules import DELETE, REPLY, Rule

# The model a chat can be switched to (".model deepseek"); Claude otherwise.
DEEPSEEK = "deepseek"


@dataclass
class AiChat:
    """A chat where Claude answers for me."""

    label: str = ""
    # True: a stranger who wrote first, picked up automatically. False: I
    # added the chat myself (".ai @user").
    auto: bool = False
    replies: int = 0  # AI replies here, ever: strangers get a limited number
    # The AI doesn't send here, it brings a draft to the panel (".ai draft").
    drafts: bool = False
    reported_up_to: int = 0  # last message id covered by a report to the panel


class Store:
    """Rules, muted chats and AI chats, kept in one small JSON file."""

    def __init__(self, path: Path):
        self.path = path
        self.rules: list[Rule] = []
        self.muted: dict[int, str] = {}  # chat id -> label for .rules
        # Whether the userbot has already pressed /start in the panel bot for me.
        self.panel_started = False
        self.ai_chats: dict[int, AiChat] = {}
        # The master switch for AI chats; off until I turn it on in the panel.
        self.ai_enabled = False
        # Chats where I turned the AI off: never picked up automatically again.
        self.ai_blocked: set[int] = set()
        # How Claude should sound in AI chats: set by ".persona", empty = default.
        self.persona = ""
        # A style for one chat (".persona @user ..."), on top of the general one.
        self.chat_personas: dict[int, str] = {}
        # Chats answered by another model than Claude: chat id -> DEEPSEEK.
        self.chat_models: dict[int, str] = {}
        self._next_id = 1
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.rules = [Rule(**r) for r in data.get("rules", [])]
            self.muted = {int(c): label for c, label in data.get("muted", {}).items()}
            self.panel_started = data.get("panel_started", False)
            fields = set(AiChat.__dataclass_fields__)
            self.ai_chats = {
                int(c): AiChat(**{k: v for k, v in a.items() if k in fields})
                for c, a in data.get("ai_chats", {}).items()
            }
            self.ai_enabled = data.get("ai_enabled", False)
            self.ai_blocked = set(data.get("ai_blocked", []))
            self.persona = data.get("persona", "")
            self.chat_personas = {int(c): t for c, t in data.get("chat_personas", {}).items()}
            self.chat_models = {int(c): m for c, m in data.get("chat_models", {}).items()}
            self._next_id = data.get("next_id", 1)

    def add_rule(
        self, kind: str, keywords: list[str], text: str = "",
        chat_id: int | None = None, chat_label: str = "",
    ) -> Rule:
        rule = Rule(
            id=self._next_id, kind=kind, keywords=keywords, text=text,
            chat_id=chat_id, chat_label=chat_label,
        )
        self._next_id += 1
        self.rules.append(rule)
        self.save()
        return rule

    def remove_rule(self, rule_id: int) -> bool:
        before = len(self.rules)
        self.rules = [r for r in self.rules if r.id != rule_id]
        if len(self.rules) == before:
            return False
        self.save()
        return True

    def set_muted(self, chat_id: int, muted: bool, label: str = "") -> bool:
        """Returns False if the chat was already in that state."""
        if (chat_id in self.muted) == muted:
            return False
        if muted:
            self.muted[chat_id] = label
        else:
            del self.muted[chat_id]
        self.save()
        return True

    def set_ai(self, chat_id: int, on: bool, label: str = "", auto: bool = False) -> bool:
        """Returns False if the chat was already in that state."""
        existing = self.ai_chats.get(chat_id)
        if on and existing is not None and existing.auto and not auto:
            existing.auto = False  # a picked-up stranger I now keep on purpose
            self.save()
            return True
        if (existing is not None) == on:
            return False
        if on:
            self.ai_chats[chat_id] = AiChat(label=label, auto=auto)
            self.ai_blocked.discard(chat_id)
        else:
            del self.ai_chats[chat_id]
            self.ai_blocked.add(chat_id)
        self.save()
        return True

    def set_drafts(self, chat_id: int, drafts: bool) -> bool:
        """For an AI chat: drafts in the panel instead of sending. Returns
        False if nothing changed."""
        chat = self.ai_chats.get(chat_id)
        if chat is None or chat.drafts == drafts:
            return False
        chat.drafts = drafts
        self.save()
        return True

    def set_ai_enabled(self, enabled: bool) -> None:
        self.ai_enabled = enabled
        self.save()

    def set_persona(self, persona: str, chat_id: int | None = None) -> None:
        """chat_id None: the general style; otherwise that chat's own."""
        if chat_id is None:
            self.persona = persona
        elif persona:
            self.chat_personas[chat_id] = persona
        else:
            self.chat_personas.pop(chat_id, None)
        self.save()

    def set_model(self, chat_id: int, model: str | None) -> bool:
        """model None: back to Claude. Returns False if nothing changed."""
        if self.chat_models.get(chat_id) == model:
            return False
        if model:
            self.chat_models[chat_id] = model
        else:
            del self.chat_models[chat_id]
        self.save()
        return True

    def set_panel_started(self) -> None:
        self.panel_started = True
        self.save()

    def should_delete(self, chat_id: int, is_private: bool, message: str) -> bool:
        if chat_id in self.muted:
            return True
        return any(
            r.kind == DELETE and r.applies_to(chat_id, is_private) and r.matches(message)
            for r in self.rules
        )

    def reply_for(self, chat_id: int, is_private: bool, message: str) -> Rule | None:
        # A rule made for this very chat wins over an "every private chat" one.
        candidates = [
            r for r in self.rules
            if r.kind == REPLY and r.applies_to(chat_id, is_private) and r.matches(message)
        ]
        candidates.sort(key=lambda r: r.chat_id is None)
        return candidates[0] if candidates else None

    def save(self) -> None:
        data = {
            "next_id": self._next_id,
            "panel_started": self.panel_started,
            "rules": [r.to_dict() for r in self.rules],
            "muted": {str(c): label for c, label in sorted(self.muted.items())},
            "ai_enabled": self.ai_enabled,
            "ai_chats": {str(c): asdict(a) for c, a in sorted(self.ai_chats.items())},
            "ai_blocked": sorted(self.ai_blocked),
            "persona": self.persona,
            "chat_personas": {str(c): t for c, t in sorted(self.chat_personas.items())},
            "chat_models": {str(c): m for c, m in sorted(self.chat_models.items())},
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
