import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

REPLY = "reply"
DELETE = "delete"

# Latin letters and digits that pass for Cyrillic ones: "кaзинo", "к0нкурс".
# Applied to keywords too, so a Latin keyword still matches itself.
LOOKALIKES = str.maketrans("aceopxykmthb036@", "асеорхукмтнвозба")
INVISIBLE = re.compile("[\u00ad\u200b-\u200f\u2060\ufeff]")
REPEATS = re.compile(r"(\w)\1+")
# Single letters split by spaces or dots: "к а з и н о", "к.а.з.и.н.о".
# Shorter keywords aren't looked for inside them: too easy to hit by chance.
MIN_SPACED_KEYWORD = 3
SPACED = re.compile(r"(?<!\w)\w(?:[\W_]{1,3}\w(?!\w)){2,}")


def normalize(text: str) -> str:
    """Lowercase, Cyrillic lookalikes, no invisible characters, no stretched
    letters ("казиииино")."""
    text = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    text = INVISIBLE.sub("", text).translate(LOOKALIKES)
    return REPEATS.sub(r"\1", text)


def spaced_runs(text: str) -> list[str]:
    """Words spelled out letter by letter, glued: "играй в к а з и н о" gives
    "вказино" (the preposition can't be told apart, so keywords are looked
    for inside a run)."""
    return [REPEATS.sub(r"\1", re.sub(r"[\W_]", "", m.group())) for m in SPACED.finditer(text)]


def keyword_pattern(keyword: str) -> re.Pattern:
    # Whole words only, so "да" doesn't fire on "когда"; "казин*" is a prefix.
    prefix = keyword.endswith("*")
    words = normalize(keyword.rstrip("*")).split()
    body = r"\s+".join(re.escape(w) for w in words)
    end = "" if prefix else r"(?!\w)"
    return re.compile(rf"(?<!\w){body}{end}")


@dataclass
class Rule:
    id: int
    kind: str  # REPLY | DELETE
    keywords: list[str]
    text: str = ""  # the canned answer, for REPLY rules
    # None means "every private chat"; otherwise the rule works only in that
    # one chat (which may also be a group).
    chat_id: int | None = None
    chat_label: str = ""  # e.g. "@vasya", only for showing in .rules
    _patterns: list[re.Pattern] | None = field(default=None, init=False, repr=False, compare=False)
    _letters: list[str] = field(default_factory=list, init=False, repr=False, compare=False)

    def matches(self, message: str) -> bool:
        if self._patterns is None:
            keywords = [k for k in self.keywords if k.rstrip("*").strip()]
            self._patterns = [keyword_pattern(k) for k in keywords]
            self._letters = [
                w for k in keywords
                if len(w := re.sub(r"\W", "", normalize(k.rstrip("*")))) >= MIN_SPACED_KEYWORD
            ]
        text = normalize(message)
        if any(p.search(text) for p in self._patterns):
            return True
        return any(k in run for run in spaced_runs(text) for k in self._letters)

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
            "chat_label": self.chat_label,
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
