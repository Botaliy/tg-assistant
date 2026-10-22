import pytest

from bot import commands
from bot.commands import Command
from bot.rules import DELETE, REPLY
from bot.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "rules.json")


def run(store, text, chat_id=None, label=""):
    return commands.execute(store, commands.parse(text), chat_id, label)


def test_parse_ignores_ordinary_text():
    assert commands.parse("привет") is None
    assert commands.parse("...ну ладно") is None
    assert commands.parse(".com") is None
    assert commands.parse(".DEL 3") == Command("del", "3")


@pytest.mark.parametrize("text, expected", [
    (".mute @vasya", Command("mute", "", "@vasya")),
    (".del @vasya 5", Command("del", "5", "@vasya")),
    (".del 5", Command("del", "5")),
    (".reply @vasya цена => в закрепе", Command("reply", "цена => в закрепе", "@vasya")),
    (".reply цена => в закрепе", Command("reply", "цена => в закрепе")),
    (".filter +79991234567 спам", Command("filter", "спам", "+79991234567")),
    (".rules #-1001234", Command("rules", "", "#-1001234")),
    (".unrule 3", Command("unrule", "3")),
    (".filter # спам", Command("filter", "# спам")),
    (".filter @ спам", Command("filter", "@ спам")),
])
def test_parse_target(text, expected):
    assert commands.parse(text) == expected


def test_del_count():
    assert commands.parse_del_count("") is None
    assert commands.parse_del_count("5") == 5
    assert commands.parse_del_count("1000") == 100
    with pytest.raises(ValueError):
        commands.parse_del_count("abc")
    with pytest.raises(ValueError):
        commands.parse_del_count("0")


def test_reply_without_target_is_global(store):
    answer = run(store, ".reply Цена, прайс => Цены в закрепе")
    assert not answer.is_error and "Правило 1" in answer.text
    [rule] = store.rules
    assert (rule.kind, rule.keywords, rule.text, rule.chat_id) == (
        REPLY, ["цена", "прайс"], "Цены в закрепе", None,
    )


def test_reply_needs_arrow_and_answer(store):
    assert run(store, ".reply цена").is_error
    assert run(store, ".reply цена =>").is_error
    assert store.rules == []


def test_filter_for_a_chat_keeps_its_label(store):
    run(store, ".filter реклама", chat_id=5, label="@vasya")
    [rule] = store.rules
    assert (rule.kind, rule.chat_id, rule.chat_label) == (DELETE, 5, "@vasya")
    assert "@vasya #5" in run(store, ".rules").text


def test_mute_needs_a_target(store):
    assert run(store, ".mute").is_error
    run(store, ".mute", chat_id=5, label="@vasya")
    assert store.muted == {5: "@vasya"}
    assert "Заглушены: @vasya #5" in run(store, ".rules").text
    run(store, ".unmute", chat_id=5)
    assert store.muted == {}


def test_rules_and_unrule(store):
    assert run(store, ".rules").text == "Правил пока нет"
    run(store, ".reply привет => занят")
    run(store, ".filter спам", chat_id=5)
    run(store, ".filter реклама", chat_id=6)
    listing = run(store, ".rules", chat_id=5).text
    assert "привет" in listing and "спам" in listing and "реклама" not in listing
    assert run(store, ".unrule 1").text == "Правило 1 удалено"
    assert run(store, ".unrule 1").is_error


def test_ai_warns_when_switched_off(store):
    answer = run(store, ".ai", chat_id=5, label="@vasya")
    assert answer.is_error and ".ai on" in answer.text
    assert 5 in store.ai_chats
    run(store, ".ai on")
    assert store.ai_enabled
    assert not run(store, ".ai", chat_id=6, label="@petya").is_error


def test_persona_general_and_per_chat(store):
    run(store, ".persona на ты, коротко")
    run(store, ".persona язвительно", chat_id=5, label="@vasya")
    assert store.persona == "на ты, коротко"
    assert store.chat_personas == {5: "язвительно"}
    assert "язвительно" in run(store, ".persona", chat_id=5).text
    run(store, ".persona -", chat_id=5)
    assert store.chat_personas == {}
    assert store.persona == "на ты, коротко"


def test_ai_on_a_picked_up_stranger_keeps_them(store):
    store.set_ai(5, True, "@vasya", auto=True)
    run(store, ".ai on")
    run(store, ".ai", chat_id=5, label="@vasya")
    assert store.ai_chats[5].auto is False
