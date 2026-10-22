"""What happens over time, in SQLite (data/assistant.db): daily AI reply
counters, reports waiting to be sent, past reports, drafts waiting for my
decision and tokens spent.

Settings stay in rules.json (bot.store); this is the part that grows or used
to be lost on a restart. Calls are synchronous: each is a tiny local query.
"""

import datetime
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS replies (
    day TEXT NOT NULL, chat_id INTEGER NOT NULL, count INTEGER NOT NULL,
    PRIMARY KEY (day, chat_id)
);
CREATE TABLE IF NOT EXISTS pending_reports (
    chat_id INTEGER PRIMARY KEY, due_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, chat_id INTEGER NOT NULL,
    label TEXT NOT NULL, reason TEXT NOT NULL, text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, day TEXT NOT NULL, chat_id INTEGER,
    model TEXT NOT NULL, kind TEXT NOT NULL,
    input INTEGER NOT NULL, output INTEGER NOT NULL,
    cache_read INTEGER NOT NULL, cache_write INTEGER NOT NULL, cost REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_day ON usage (day);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, chat_id INTEGER NOT NULL, label TEXT NOT NULL,
    text TEXT NOT NULL, reply_to INTEGER, context INTEGER NOT NULL
);
"""

# $ per 1M tokens: input, output. Cache reads cost a tenth of input (Opus 5.5:
# a twentieth), 5-minute cache writes 1.25x. Haiku 5.5 doubles past 100K
# tokens of prompt, which a 10-message chat never gets near.
CLAUDE_PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-fable-5-1": (10.00, 50.00),
}
CACHE_READ_SHARE = {"claude-opus-5-5": 0.05, "claude-fable-5-1": 0.025}


@dataclass
class Usage:
    """Tokens of one request. input is the uncached part only."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0


@dataclass
class Draft:
    """An AI answer waiting in the panel for me to send, redo or drop."""

    id: int
    chat_id: int
    label: str
    text: str
    reply_to: int | None  # the message it answers, as a reply
    context: int  # how many messages it read


def claude_cost(model: str, usage: Usage) -> float | None:
    """In $; None for a model without a known price."""
    prices = next((p for m, p in CLAUDE_PRICES.items() if model.startswith(m)), None)
    if prices is None:
        return None
    price_in, price_out = prices
    read_share = next((s for m, s in CACHE_READ_SHARE.items() if model.startswith(m)), 0.1)
    return (
        usage.input * price_in + usage.cache_read * price_in * read_share
        + usage.cache_write * price_in * 1.25 + usage.output * price_out
    ) / 1_000_000


def today() -> str:
    return datetime.date.today().isoformat()


class Journal:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path), isolation_level=None)  # autocommit
        self.db.executescript(SCHEMA)

    # Daily AI reply counters: what the limits are checked against.

    def replies_today(self, chat_id: int | None = None) -> int:
        """In one chat, or in all of them together."""
        if chat_id is None:
            row = self.db.execute("SELECT SUM(count) FROM replies WHERE day = ?", (today(),)).fetchone()
        else:
            row = self.db.execute(
                "SELECT count FROM replies WHERE day = ? AND chat_id = ?", (today(), chat_id),
            ).fetchone()
        return (row[0] if row else 0) or 0

    def count_reply(self, chat_id: int) -> None:
        self.db.execute(
            "INSERT INTO replies (day, chat_id, count) VALUES (?, ?, 1) "
            "ON CONFLICT (day, chat_id) DO UPDATE SET count = count + 1",
            (today(), chat_id),
        )

    def reset_replies(self, chat_id: int) -> None:
        self.db.execute("DELETE FROM replies WHERE day = ? AND chat_id = ?", (today(), chat_id))

    # Reports waiting for a conversation to go quiet: survive a restart.

    def set_report_due(self, chat_id: int, due_at: float) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO pending_reports (chat_id, due_at) VALUES (?, ?)", (chat_id, due_at),
        )

    def drop_report_due(self, chat_id: int) -> None:
        self.db.execute("DELETE FROM pending_reports WHERE chat_id = ?", (chat_id,))

    def reports_due(self) -> list[tuple[int, float]]:
        return self.db.execute("SELECT chat_id, due_at FROM pending_reports").fetchall()

    def add_report(self, chat_id: int, label: str, reason: str, text: str) -> None:
        self.db.execute(
            "INSERT INTO reports (ts, chat_id, label, reason, text) VALUES (?, ?, ?, ?, ?)",
            (time.time(), chat_id, label, reason, text),
        )

    # Small things to remember, e.g. when the last backup went out.

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    # Drafts.

    def save_draft(
        self, chat_id: int, label: str, text: str, reply_to: int | None, context: int,
        draft_id: int | None = None,
    ) -> int:
        """A new draft, or another version of draft_id. Returns its id."""
        if draft_id is not None:
            self.db.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
        cursor = self.db.execute(
            "INSERT INTO drafts (id, ts, chat_id, label, text, reply_to, context) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (draft_id, time.time(), chat_id, label, text, reply_to, context),
        )
        return cursor.lastrowid

    def get_draft(self, draft_id: int) -> Draft | None:
        row = self.db.execute(
            "SELECT id, chat_id, label, text, reply_to, context FROM drafts WHERE id = ?", (draft_id,),
        ).fetchone()
        return Draft(*row) if row else None

    def drop_draft(self, draft_id: int) -> None:
        self.db.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))

    def list_drafts(self) -> list[Draft]:
        rows = self.db.execute(
            "SELECT id, chat_id, label, text, reply_to, context FROM drafts ORDER BY id DESC",
        ).fetchall()
        return [Draft(*row) for row in rows]

    def recent_reports(self, limit: int = 30) -> list[tuple[float, int, str, str, str]]:
        """ts, chat id, label, reason, text; the newest first."""
        return self.db.execute(
            "SELECT ts, chat_id, label, reason, text FROM reports ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()

    # Tokens and money.

    def add_usage(
        self, model: str, kind: str, usage: Usage, cost: float, chat_id: int | None = None,
    ) -> None:
        """kind: what the request was for, e.g. "reply" or "report"."""
        self.db.execute(
            "INSERT INTO usage (ts, day, chat_id, model, kind, input, output, cache_read, cache_write, cost) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), today(), chat_id, model, kind, usage.input, usage.output,
             usage.cache_read, usage.cache_write, cost),
        )

    def spend(self, since: str) -> list[tuple[str, int, int, int, float]]:
        """Per model since a day (inclusive): model, requests, input tokens
        (cached ones included), output tokens, $."""
        return self.db.execute(
            "SELECT model, COUNT(*), SUM(input + cache_read + cache_write), SUM(output), SUM(cost) "
            "FROM usage WHERE day >= ? GROUP BY model ORDER BY SUM(cost) DESC",
            (since,),
        ).fetchall()

    def top_chats(self, since: str, limit: int = 5) -> list[tuple[int, int, float]]:
        """The chats that cost the most: chat id, requests, $."""
        return self.db.execute(
            "SELECT chat_id, COUNT(*), SUM(cost) FROM usage WHERE day >= ? AND chat_id IS NOT NULL "
            "GROUP BY chat_id ORDER BY SUM(cost) DESC LIMIT ?",
            (since, limit),
        ).fetchall()


def format_usage(journal: Journal, labels: dict[int, str]) -> str:
    """The ".usage" answer: today and this month, by model, plus the costliest chats."""
    day = datetime.date.today()
    lines = []
    for title, since in (("Сегодня", day.isoformat()), ("С начала месяца", day.replace(day=1).isoformat())):
        rows = journal.spend(since)
        if not rows:
            lines.append(f"{title}: ничего")
            continue
        total = sum(r[4] for r in rows)
        lines.append(f"{title}: ${total:.4f}")
        for model, requests, tokens_in, tokens_out, cost in rows:
            lines.append(f"  {model}: {requests} запр., {tokens_in} / {tokens_out} ток., ${cost:.4f}")
    top = journal.top_chats(day.replace(day=1).isoformat())
    if top:
        lines.append("Дороже всех в этом месяце:")
        for chat_id, requests, cost in top:
            name = f"{labels.get(chat_id, '')} #{chat_id}".strip()
            lines.append(f"  {name}: {requests} запр., ${cost:.4f}")
    return "\n".join(lines)
