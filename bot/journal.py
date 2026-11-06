"""What happens over time, in SQLite (data/assistant.db): daily AI reply
counters, reports waiting to be sent, past reports, drafts waiting for my
decision, tokens spent and memes with their votes.

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
CREATE TABLE IF NOT EXISTS memes (
    token INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, meme_id INTEGER NOT NULL,
    author_id INTEGER, author TEXT NOT NULL, ts REAL NOT NULL,
    rich INTEGER NOT NULL DEFAULT 0, media_id INTEGER, repeat_of INTEGER
);
CREATE INDEX IF NOT EXISTS memes_chat_ts ON memes (chat_id, ts);
CREATE TABLE IF NOT EXISTS meme_votes (
    token INTEGER NOT NULL, user_id INTEGER NOT NULL, score INTEGER, joke INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (token, user_id)
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
        # Columns added after a table was first made.
        for table, column in (
            ("memes", "rich INTEGER NOT NULL DEFAULT 0"),
            ("memes", "media_id INTEGER"),
            ("memes", "repeat_of INTEGER"),
            ("meme_votes", "name TEXT NOT NULL DEFAULT ''"),
        ):
            try:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
            except sqlite3.OperationalError:
                pass  # already there

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

    # Memes and their votes: a score from 1 to 10 and the joke button, one
    # of each per person; a new click changes the score.

    def add_meme(
        self, token: int, chat_id: int, meme_id: int, author_id: int | None, author: str,
        ts: float | None = None, media_id: int | None = None, repeat_of: int | None = None,
    ) -> None:
        """media_id: the photo's or the file's id, to spot the same meme again;
        repeat_of: the token of the meme this one repeats."""
        self.db.execute(
            "INSERT OR REPLACE INTO memes (token, chat_id, meme_id, author_id, author, ts, media_id, repeat_of) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (token, chat_id, meme_id, author_id, author, time.time() if ts is None else ts, media_id, repeat_of),
        )

    def first_meme_with(self, chat_id: int, media_id: int) -> int | None:
        """The token of the first meme in a chat with this photo or file."""
        row = self.db.execute(
            "SELECT token FROM memes WHERE chat_id = ? AND media_id = ? ORDER BY ts LIMIT 1", (chat_id, media_id),
        ).fetchone()
        return row[0] if row else None

    def get_meme(self, token: int) -> tuple[int | None, float, int | None] | None:
        """Author id, when, the token it repeats; None: no such meme."""
        return self.db.execute("SELECT author_id, ts, repeat_of FROM memes WHERE token = ?", (token,)).fetchone()

    def set_meme_rich(self, token: int, rich: bool) -> None:
        """Whether the rating went out as a rich message (or as plain text)."""
        self.db.execute("UPDATE memes SET rich = ? WHERE token = ?", (int(rich), token))

    def meme_is_rich(self, token: int) -> bool | None:
        """None: no such meme."""
        row = self.db.execute("SELECT rich FROM memes WHERE token = ?", (token,)).fetchone()
        return None if row is None else bool(row[0])

    def meme_scores(self, token: int) -> dict[int, list[tuple[int, str]]]:
        """Score -> who gave it (user id, name), in the order they voted."""
        scores: dict[int, list[tuple[int, str]]] = {}
        for score, user_id, name in self.db.execute(
            "SELECT score, user_id, name FROM meme_votes WHERE token = ? AND score IS NOT NULL ORDER BY rowid",
            (token,),
        ):
            scores.setdefault(score, []).append((user_id, name or "кто-то"))
        return scores

    def vote_meme(self, token: int, user_id: int, score: int, name: str = "") -> None:
        self.db.execute(
            "INSERT INTO meme_votes (token, user_id, score, name) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (token, user_id) DO UPDATE SET score = excluded.score, name = excluded.name",
            (token, user_id, score, name),
        )

    def toggle_meme_joke(self, token: int, user_id: int) -> bool:
        """Returns whether the joke is now pressed by this person."""
        self.db.execute(
            "INSERT INTO meme_votes (token, user_id, joke) VALUES (?, ?, 1) "
            "ON CONFLICT (token, user_id) DO UPDATE SET joke = 1 - joke",
            (token, user_id),
        )
        row = self.db.execute(
            "SELECT joke FROM meme_votes WHERE token = ? AND user_id = ?", (token, user_id),
        ).fetchone()
        return bool(row[0])

    def meme_tally(self, token: int) -> tuple[float | None, int, int]:
        """The average score (None: no scores yet), how many scored, how many pressed the joke."""
        avg, voters, jokes = self.db.execute(
            "SELECT AVG(score), COUNT(score), COALESCE(SUM(joke), 0) FROM meme_votes WHERE token = ?", (token,),
        ).fetchone()
        return avg, voters, jokes

    def best_memes(self, chat_id: int, since: float, limit: int = 3) -> list[tuple[int, str, float, int]]:
        """Scored memes since a time, the best first: meme id, author, average, voters."""
        return self.db.execute(
            "SELECT m.meme_id, m.author, AVG(v.score), COUNT(v.score) FROM memes m "
            "JOIN meme_votes v ON v.token = m.token AND v.score IS NOT NULL "
            "WHERE m.chat_id = ? AND m.ts >= ? GROUP BY m.token "
            "ORDER BY AVG(v.score) DESC, COUNT(v.score) DESC, m.ts LIMIT ?",
            (chat_id, since, limit),
        ).fetchall()

    def best_meme_authors(self, chat_id: int, since: float, limit: int = 3) -> list[tuple[str, float, int]]:
        """Authors by the average of their memes' averages: author, average, scored memes."""
        return self.db.execute(
            "SELECT MAX(author), AVG(score), COUNT(*) FROM ("
            "  SELECT m.author_id, m.author, AVG(v.score) AS score FROM memes m "
            "  JOIN meme_votes v ON v.token = m.token AND v.score IS NOT NULL "
            "  WHERE m.chat_id = ? AND m.ts >= ? GROUP BY m.token"
            ") GROUP BY COALESCE(author_id, author) ORDER BY AVG(score) DESC, COUNT(*) DESC LIMIT ?",
            (chat_id, since, limit),
        ).fetchall()

    # Nominations for the weekly top.

    def meme_voters(self, chat_id: int, since: float, min_votes: int = 3) -> list[tuple[str, float, int]]:
        """Who scored at least min_votes memes: name, the average they give, how many."""
        return self.db.execute(
            "SELECT MAX(v.name), AVG(v.score), COUNT(*) FROM meme_votes v JOIN memes m ON m.token = v.token "
            "WHERE m.chat_id = ? AND m.ts >= ? AND v.score IS NOT NULL "
            "GROUP BY v.user_id HAVING COUNT(*) >= ? ORDER BY AVG(v.score)",
            (chat_id, since, min_votes),
        ).fetchall()

    def joke_magnet(self, chat_id: int, since: float) -> tuple[str, int] | None:
        """The author whose memes got the joke button the most: author, presses."""
        return self.db.execute(
            "SELECT MAX(m.author), SUM(v.joke) FROM memes m JOIN meme_votes v ON v.token = m.token "
            "WHERE m.chat_id = ? AND m.ts >= ? GROUP BY COALESCE(m.author_id, m.author) "
            "HAVING SUM(v.joke) > 0 ORDER BY SUM(v.joke) DESC LIMIT 1",
            (chat_id, since),
        ).fetchone()

    def top_reposter(self, chat_id: int, since: float) -> tuple[str, int] | None:
        """Who posted the most memes the chat had already seen: author, how many."""
        return self.db.execute(
            "SELECT MAX(author), COUNT(*) FROM memes WHERE chat_id = ? AND ts >= ? AND repeat_of IS NOT NULL "
            "GROUP BY COALESCE(author_id, author) ORDER BY COUNT(*) DESC LIMIT 1",
            (chat_id, since),
        ).fetchone()

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
