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
