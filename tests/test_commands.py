import pytest

from bot import commands
from bot.rules import DELETE, REPLY
from bot.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "rules.json")


def run(store, text, chat_id=None):
    return commands.execute(store, commands.parse(text), chat_id)


def test_parse_ignores_ordinary_text():
    assert commands.parse("привет") is None
    assert commands.parse("...ну ладно") is None
    assert commands.parse(".com") is None
    assert commands.parse(".DEL 3") == commands.Command("del", "3")


def test_del_count():
    assert commands.parse_del_count("") is None
    assert commands.parse_del_count("5") == 5
    assert commands.parse_del_count("1000") == 100
    with pytest.raises(ValueError):
        commands.parse_del_count("abc")
    with pytest.raises(ValueError):
        commands.parse_del_count("0")


def test_reply_from_saved_messages_is_global(store):
    answer = run(store, ".reply Цена, прайс => Цены в закрепе")
    assert "Правило 1" in answer
    [rule] = store.rules
    assert (rule.kind, rule.keywords, rule.text, rule.chat_id) == (
        REPLY, ["цена", "прайс"], "Цены в закрепе", None,
    )


def test_reply_needs_arrow_and_answer(store):
    assert run(store, ".reply цена").startswith("Формат")
    assert run(store, ".reply цена =>").startswith("Формат")
    assert store.rules == []


def test_filter_in_chat_is_scoped_to_it(store):
    run(store, ".filter реклама", chat_id=5)
    [rule] = store.rules
    assert (rule.kind, rule.chat_id) == (DELETE, 5)


def test_mute_needs_a_chat(store):
    assert "в том чате" in run(store, ".mute")
    run(store, ".mute", chat_id=5)
    assert store.muted == {5}
    run(store, ".unmute", chat_id=5)
    assert store.muted == set()


def test_rules_and_unrule(store):
    assert run(store, ".rules") == "Правил пока нет"
    run(store, ".reply привет => занят")
    run(store, ".filter спам", chat_id=5)
    run(store, ".filter реклама", chat_id=6)
    listing = run(store, ".rules", chat_id=5)
    assert "привет" in listing and "спам" in listing and "реклама" not in listing
    assert run(store, ".unrule 1") == "Правило 1 удалено"
    assert run(store, ".unrule 1") == "Правила 1 нет"
