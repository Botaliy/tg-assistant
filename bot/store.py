import json
from dataclasses import asdict, dataclass
from pathlib import Path

from bot.rules import DELETE, REPLY, Rule


@dataclass
class AiChat:
    """A chat where Claude answers for me.

    Messages up to summarized_up_to are folded into `summary`; the ones after
    it are sent to the model verbatim.
    """

    label: str = ""
    summary: str = ""
    summarized_up_to: int = 0


class Store:
    """Rules, muted chats and AI chats, kept in one small JSON file."""

    def __init__(self, path: Path):
        self.path = path
        self.rules: list[Rule] = []
        self.muted: dict[int, str] = {}  # chat id -> label for .rules
        # Whether the userbot has already pressed /start in the panel bot for me.
        self.panel_started = False
        self.ai_chats: dict[int, AiChat] = {}
        # How Claude should sound in AI chats: set by ".persona", empty = default.
        self.persona = ""
        self._next_id = 1
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.rules = [Rule(**r) for r in data.get("rules", [])]
            self.muted = {int(c): label for c, label in data.get("muted", {}).items()}
            self.panel_started = data.get("panel_started", False)
            self.ai_chats = {int(c): AiChat(**a) for c, a in data.get("ai_chats", {}).items()}
            self.persona = data.get("persona", "")
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

    def set_ai(self, chat_id: int, on: bool, label: str = "") -> bool:
        """Returns False if the chat was already in that state."""
        if (chat_id in self.ai_chats) == on:
            return False
        if on:
            self.ai_chats[chat_id] = AiChat(label=label)
        else:
            del self.ai_chats[chat_id]
        self.save()
        return True

    def set_persona(self, persona: str) -> None:
        self.persona = persona
        self.save()

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
            "ai_chats": {str(c): asdict(a) for c, a in sorted(self.ai_chats.items())},
            "persona": self.persona,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
