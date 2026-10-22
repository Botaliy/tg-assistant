import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

REPLY = "reply"
DELETE = "delete"


@dataclass
class Rule:
    id: int
    kind: str  # REPLY | DELETE
    keywords: list[str]
    text: str = ""  # the canned answer, for REPLY rules
    # None means "every private chat"; otherwise the rule works only in that
    # one chat (which may also be a group).
    chat_id: int | None = None
    _patterns: list[re.Pattern] = field(default=None, init=False, repr=False, compare=False)

    def matches(self, message: str) -> bool:
        if self._patterns is None:
            # Whole words only, so "да" doesn't fire on "когда".
            self._patterns = [
                re.compile(rf"(?<!\w){re.escape(k)}(?!\w)", re.IGNORECASE)
                for k in self.keywords
            ]
        return any(p.search(message) for p in self._patterns)

    def applies_to(self, chat_id: int, is_private: bool) -> bool:
        if self.chat_id is None:
            return is_private
        return self.chat_id == chat_id

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "keywords": self.keywords,
            "text": self.text,
            "chat_id": self.chat_id,
        }


class Cooldown:
    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic):
        self.seconds = seconds
        self._clock = clock
        self._last: dict[object, float] = {}

    def ready(self, key: object) -> bool:
        """True (and starts the cooldown) if `key` hasn't fired recently."""
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last < self.seconds:
            return False
        self._last[key] = now
        return True
