import asyncio

import pytest

from bot.journal import Journal, Usage, claude_cost, format_usage


def test_claude_cost_counts_the_cache():
    usage = Usage(input=1_000_000, output=1_000_000, cache_read=1_000_000, cache_write=1_000_000)
    # Haiku 5.5: 0.10 in, 0.50 out, reads 0.1x, writes 1.25x.
    assert claude_cost("claude-haiku-5-5", usage) == pytest.approx(0.10 + 0.50 + 0.01 + 0.125)
    assert claude_cost("some-new-model", usage) is None


def test_usage_by_model_and_chat(tmp_path):
    journal = Journal(tmp_path / "assistant.db")
    journal.add_usage("claude-haiku-5-5", "reply", Usage(input=100, output=5), 0.002, chat_id=7)
    journal.add_usage("claude-haiku-5-5", "report", Usage(input=50, output=5, cache_read=50), 0.001, chat_id=7)
    journal.add_usage("deepseek-flash", "reply", Usage(input=10, output=1), 0.0, chat_id=8)
    text = format_usage(journal, {7: "@vasya"})
    assert "Сегодня: $0.0030" in text
    assert "claude-haiku-5-5: 2 запр., 200 / 10 ток." in text
    assert "@vasya #7: 2 запр." in text


def test_reports_waiting_survive_a_restart(tmp_path, monkeypatch):
    from tests.test_ai_limits import FRIEND, make

    ai, _ = make(tmp_path, known_unlimited=True)
    del ai._schedule_report  # the real one this time
    monkeypatch.setattr("bot.ai.REPORT_AFTER_SECONDS", 3600)
    reports = []

    async def report(chat_id, chat, reason):
        reports.append((chat_id, reason))

    async def go():
        ai._schedule_report(FRIEND)
        ai._reports[FRIEND].cancel()  # the bot stops
        await asyncio.sleep(0)
        [(chat_id, due_at)] = Journal(tmp_path / "assistant.db").reports_due()
        assert chat_id == FRIEND

        again, _ = make(tmp_path, known_unlimited=True)  # and starts again
        again._report = report
        again.journal.set_report_due(FRIEND, due_at - 3600)  # it's overdue by now
        again.resume_reports()
        await asyncio.sleep(0.01)
        assert again.journal.reports_due() == []

    asyncio.run(go())
    assert reports == [(FRIEND, "Разговор затих")]
