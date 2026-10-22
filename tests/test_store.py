from bot.rules import DELETE, REPLY
from bot.store import Store


def test_rules_and_mutes_survive_reload(tmp_path):
    path = tmp_path / "rules.json"
    store = Store(path)
    store.add_rule(REPLY, ["привет"], "Я занят", chat_id=None)
    store.set_muted(7, True, "@vasya")
    store.set_panel_started()

    reloaded = Store(path)
    assert reloaded.muted == {7: "@vasya"}
    assert reloaded.panel_started
    assert reloaded.reply_for(1, True, "Привет!").text == "Я занят"
    assert reloaded.should_delete(7, True, "что угодно")
    assert reloaded.add_rule(DELETE, ["x"]).id == 2


def test_chat_rule_wins_over_global(tmp_path):
    store = Store(tmp_path / "rules.json")
    store.add_rule(REPLY, ["привет"], "всем")
    store.add_rule(REPLY, ["привет"], "лично тебе", chat_id=5)
    assert store.reply_for(5, True, "привет").text == "лично тебе"
    assert store.reply_for(6, True, "привет").text == "всем"


def test_filter_and_unmute(tmp_path):
    store = Store(tmp_path / "rules.json")
    rule = store.add_rule(DELETE, ["казино"])
    assert store.should_delete(1, True, "Лучшее казино")
    assert not store.should_delete(1, True, "обычный текст")
    assert store.remove_rule(rule.id)
    assert not store.remove_rule(rule.id)

    assert store.set_muted(1, True)
    assert not store.set_muted(1, True)
    assert store.set_muted(1, False)
    assert not store.should_delete(1, True, "текст")


def test_chat_model_survives_reload(tmp_path):
    path = tmp_path / "rules.json"
    store = Store(path)
    assert store.set_model(5, "deepseek")
    assert not store.set_model(5, "deepseek")
    assert Store(path).chat_models == {5: "deepseek"}
    store.set_model(5, None)
    assert Store(path).chat_models == {}
