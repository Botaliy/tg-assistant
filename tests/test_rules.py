import pytest

from bot.rules import DELETE, REPLY, Cooldown, Rule


def test_matches_whole_words_case_insensitive():
    rule = Rule(id=1, kind=REPLY, keywords=["цена", "сколько стоит"], text="x")
    assert rule.matches("Какая ЦЕНА?")
    assert rule.matches("а сколько стоит доставка")
    assert not rule.matches("бесценно")


def test_global_rule_applies_only_to_private_chats():
    rule = Rule(id=1, kind=DELETE, keywords=["x"])
    assert rule.applies_to(42, is_private=True)
    assert not rule.applies_to(-100, is_private=False)


def test_chat_rule_applies_only_to_its_chat():
    rule = Rule(id=1, kind=DELETE, keywords=["x"], chat_id=-100)
    assert rule.applies_to(-100, is_private=False)
    assert not rule.applies_to(42, is_private=True)


def test_cooldown():
    now = [0.0]
    cooldown = Cooldown(60, clock=lambda: now[0])
    assert cooldown.ready("a")
    assert not cooldown.ready("a")
    assert cooldown.ready("b")
    now[0] = 61
    assert cooldown.ready("a")


@pytest.mark.parametrize("text", [
    "кaзинo",  # Latin a and o
    "К А З И Н О",
    "к.а.з.и.н.о",
    "к-а-з-и-н-о!",
    "играй в к а з и н о",
    "казиииино",
    "каз\u200bино",  # a zero-width space inside
    "к@зин0",
])
def test_sees_through_disguises(text):
    assert Rule(id=1, kind=DELETE, keywords=["казино"]).matches(text)


def test_star_is_a_prefix():
    rule = Rule(id=1, kind=DELETE, keywords=["казин*"])
    assert rule.matches("казиношка")
    assert rule.matches("КАЗИНО")
    assert not rule.matches("в магазине")


def test_disguises_dont_widen_whole_words():
    rule = Rule(id=1, kind=REPLY, keywords=["да", "сколько стоит", "casino"], text="x")
    assert not rule.matches("когда")
    assert not rule.matches("я в шоке")
    assert rule.matches("СКОЛЬКО   стоит")
    assert rule.matches("CASINO")
    assert rule.matches("сasinо")  # Cyrillic с and о
