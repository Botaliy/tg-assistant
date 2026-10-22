import json
from pathlib import Path

from bot.rules import DELETE, REPLY, Rule


class Store:
    """Rules and muted chats, kept in one small JSON file."""

    def __init__(self, path: Path):
        self.path = path
        self.rules: list[Rule] = []
        self.muted: set[int] = set()
        self._next_id = 1
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.rules = [Rule(**r) for r in data.get("rules", [])]
            self.muted = set(data.get("muted", []))
            self._next_id = data.get("next_id", 1)

    def add_rule(self, kind: str, keywords: list[str], text: str = "", chat_id: int | None = None) -> Rule:
        rule = Rule(id=self._next_id, kind=kind, keywords=keywords, text=text, chat_id=chat_id)
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

    def set_muted(self, chat_id: int, muted: bool) -> bool:
        """Returns False if the chat was already in that state."""
        if (chat_id in self.muted) == muted:
            return False
        if muted:
            self.muted.add(chat_id)
        else:
            self.muted.discard(chat_id)
        self.save()
        return True

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
            "rules": [r.to_dict() for r in self.rules],
            "muted": sorted(self.muted),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
