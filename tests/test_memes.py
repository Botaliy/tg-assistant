import asyncio
import datetime
from types import SimpleNamespace

from telethon import types

from bot import commands
from bot.journal import Journal
from bot.memes import (
    MemeRatings, MemeView, ago, bar, card, format_top, header, is_meme, joke_label, link, nominations, plural, verdict,
)
from bot.store import Store

OWNER = 1


def message(chat_id=5, msg_id=1, photo=True, grouped_id=None, sender="Вася", sender_id=11, media_id=None, **media):
    if photo is True:
        photo = SimpleNamespace(id=media_id or 1000 + msg_id)
    fields = {
        "photo": photo, "document": None, "gif": False, "video": False, "sticker": False, "video_note": False,
        "voice": False,
    }
    fields.update(media)

    async def get_sender():
        return types.User(id=sender_id, first_name=sender) if sender else None

    return SimpleNamespace(
        chat_id=chat_id, id=msg_id, grouped_id=grouped_id, sender_id=sender_id, get_sender=get_sender, **fields,
    )


class FakeUserbot:
    """The userbot: asks the panel bot inline and sends what it gives."""

    def __init__(self, inline_fails=False, rich_fails=False):
        self.queries = []
        self.clicks = []
        self.sent = []
        self.inline_fails = inline_fails
        self.rich_fails = rich_fails

    async def inline_query(self, bot, query, entity=None):
        if self.inline_fails:
            raise ValueError("inline is off")
        self.queries.append((bot, query, entity))
        client = self

        class Result:
            async def click(self, chat_id, reply_to=None):
                if client.rich_fails and not query.endswith(" plain"):
                    raise ValueError("no rich messages for you")
                client.clicks.append((chat_id, query, reply_to))
                return SimpleNamespace(id=100 + len(client.clicks))

        return [Result()]

    async def send_message(self, chat_id, message="", link_preview=True):
        self.sent.append((chat_id, message))
        return SimpleNamespace(id=500 + len(self.sent))


class FakeBot:
    """The panel bot: keeps its handlers so a test can call them."""

    def __init__(self):
        self.handlers = []
        self.edits = []
        self.requests = []
        self.session = SimpleNamespace(dc_id=2)

    async def __call__(self, request):
        self.requests.append(request)

    def build_reply_markup(self, buttons):
        return buttons

    def on(self, event):
        def register(handler):
            self.handlers.append(handler)
            return handler
        return register

    async def edit_message(self, msg_id, text, buttons=None):
        self.edits.append((msg_id, text, buttons))


def make(tmp_path, joke="", inline_fails=False, rich_fails=False, rich=True):
    store = Store(tmp_path / "rules.json")
    store.set_memes(5, True, "Мемы")
    journal = Journal(tmp_path / "assistant.db")
    userbot, bot, notes = FakeUserbot(inline_fails, rich_fails), FakeBot(), []

    async def notify(text):
        notes.append(text)

    ratings = MemeRatings(userbot, store, journal, "panel_bot", notify, joke, rich=rich)
    ratings.setup_bot(bot, OWNER)
    return ratings, userbot, bot, journal, notes


class Inline:
    def __init__(self, text, sender_id=OWNER):
        self.text, self.sender_id = text, sender_id
        self.query = SimpleNamespace(query_id=77)
        self.answers = None
        self.builder = SimpleNamespace(article=lambda title, **kw: SimpleNamespace(title=title, **kw))

    async def answer(self, results, cache_time=0, private=False):
        self.answers = results


class Click:
    def __init__(self, data, sender_id, name="Петя"):
        self.data, self.sender_id, self.name = data.encode(), sender_id, name
        self.query = SimpleNamespace(msg_id=types.InputBotInlineMessageID(dc_id=2, id=sender_id, access_hash=3))
        self.toast = None

    async def answer(self, text=None):
        self.toast = text

    async def get_sender(self):
        return types.User(id=self.sender_id, first_name=self.name)


def token_of(userbot):
    return int(userbot.queries[-1][1].split()[1])


def labels(markup):
    return [[b.text for b in row] for row in markup]


def colors(markup):
    def color(b):
        st = b.style
        return "r" if st and st.bg_danger else "g" if st and st.bg_success else "b" if st and st.bg_primary else "-"
    return ["".join(color(b) for b in row) for row in markup]


def test_what_counts_as_a_meme():
    assert is_meme(message())
    assert is_meme(message(photo=False, gif=True, video=True))
    assert is_meme(message(photo=False, video=True))
    assert not is_meme(message(photo=False))
    assert not is_meme(message(photo=False, video=True, video_note=True))
    assert not is_meme(message(photo=False, video=True, sticker=True))


def test_every_meme_gets_a_rating_through_the_panel_bot(tmp_path):
    ratings, userbot, _, journal, _ = make(tmp_path)
    asyncio.run(ratings.on_message(message(msg_id=7)))
    asyncio.run(ratings.on_message(message(msg_id=8)))
    assert [(c, r) for c, _, r in userbot.clicks] == [(5, 7), (5, 8)]
    bot, query, entity = userbot.queries[0]
    assert (bot, entity) == ("panel_bot", 5) and query.startswith("meme ")
    assert journal.meme_is_rich(token_of(userbot)) is True
    assert ratings.is_own(SimpleNamespace(chat_id=5, id=102))
    assert not ratings.is_own(SimpleNamespace(chat_id=5, id=7))


def test_other_chats_text_and_album_tails_get_nothing(tmp_path):
    ratings, userbot, _, _, _ = make(tmp_path)
    asyncio.run(ratings.on_message(message(chat_id=6)))
    asyncio.run(ratings.on_message(message(photo=False)))
    for i in range(3):
        asyncio.run(ratings.on_message(message(msg_id=i, grouped_id=42)))
    assert len(userbot.clicks) == 1


def test_inline_answer_is_a_rich_card_with_colored_buttons_and_only_for_me(tmp_path):
    ratings, _, bot, _, _ = make(tmp_path, joke="шутка")
    on_inline = bot.handlers[0]
    asyncio.run(on_inline(Inline("meme 123")))
    [request] = bot.requests
    assert request.query_id == 77 and request.private
    [result] = request.results
    message = result.send_message
    assert message.rich_message.html == "<p><b>Оцени мем</b> 👇</p>"
    assert labels(message.reply_markup) == [["1", "2", "3", "4", "5"], ["шутка"]]
    assert colors(message.reply_markup) == ["rr-gg", "b"]

    plain = Inline("meme 123 plain")
    asyncio.run(on_inline(plain))
    [article] = plain.answers
    assert article.text == "Оцени мем 👇" and labels(article.buttons)[1] == ["шутка"]

    for stranger in (Inline("meme 123", sender_id=2), Inline("котики"), Inline("meme 12x plain")):
        asyncio.run(on_inline(stranger))
        assert stranger.answers == []
    assert len(bot.requests) == 1


def test_clicks_change_the_card_one_vote_per_person(tmp_path):
    ratings, userbot, bot, journal, _ = make(tmp_path, joke="шутка")
    asyncio.run(ratings.on_message(message()))
    token = token_of(userbot)
    on_click = bot.handlers[-1]

    for data, who, name in ((f"mr:{token}:5", 21, "Петя"), (f"mr:{token}:4", 22, "Оля"), (f"mr:{token}:2", 21, "Петя")):
        click = Click(data, who, name)
        asyncio.run(on_click(click))
    assert click.toast == "Твоя оценка: 2"
    html = bot.requests[-1].rich_message.html
    assert html.startswith("<p><b>🙂 Норм</b> ⭐ <b>3.0</b> ▰▰▰▱▱ 👥 2</p><details>")
    assert "<b>4</b></td><td>Оля</td>" in html and "<b>2</b></td><td>Петя</td>" in html

    joke = Click(f"mr:{token}:j", 23)
    asyncio.run(on_click(joke))
    assert joke.toast.startswith(("😏", "📝", "🫡", "🤝", "🗿"))
    assert labels(bot.requests[-1].reply_markup)[1] == ["шутка · 1"]
    assert "3.0" in bot.requests[-1].rich_message.html  # the joke isn't a score
    asyncio.run(on_click(Click(f"mr:{token}:j", 23)))
    assert labels(bot.requests[-1].reply_markup)[1] == ["шутка"]
    assert bot.edits == []  # all rich

    gone = Click("mr:999:5", 21)
    asyncio.run(on_click(gone))
    assert gone.toast == "Этот мем уже не оценить"


def test_falls_back_to_plain_text_and_edits_it_as_such(tmp_path):
    ratings, userbot, bot, journal, notes = make(tmp_path, rich_fails=True)
    asyncio.run(ratings.on_message(message(msg_id=7)))
    [(_, query, reply_to)] = userbot.clicks
    assert query.endswith(" plain") and reply_to == 7 and notes == []
    token = token_of(userbot)
    asyncio.run(bot.handlers[-1](Click(f"mr:{token}:5", 21)))
    assert bot.edits[-1][1] == "🔥 Шедевр ⭐ 5.0 · 👥 1" and bot.requests == []


def test_rich_can_be_turned_off(tmp_path):
    ratings, userbot, _, _, _ = make(tmp_path, rich=False)
    asyncio.run(ratings.on_message(message()))
    assert [q for _, q, _ in userbot.queries] == [f"meme {token_of(userbot)} plain"]


def test_card():
    assert card(MemeView()) == "<p><b>Оцени мем</b> 👇</p>"
    html = card(MemeView({5: ["Петя", "Оля"], 4: ["Вася"], 2: ["<Тимофей>"]}))
    assert html.startswith("<p><b>😂 Годнота</b> ⭐ <b>4.0</b> ▰▰▰▰▱ 👥 4</p><details>")
    assert html.index("<b>5</b>") < html.index("<b>4</b>") < html.index("<b>2</b>")
    assert "<td>Петя, Оля</td>" in html and "<td>&lt;Тимофей&gt;</td>" in html
    repeat = MemeView(repeat="🔁 Баян · вчера")
    assert card(repeat) == "<p><b>Оцени мем</b> 👇</p><p>🔁 Баян · вчера</p>"
    assert header(repeat) == "Оцени мем 👇\n🔁 Баян · вчера"
    assert card(MemeView(), closed=True) == "<p>😶 Никто не оценил</p>"
    assert header(MemeView(), closed=True) == "😶 Никто не оценил"


def test_verdicts_bars_and_the_joke_getting_louder():
    assert [verdict(a) for a in (1, 1.7, 2, 3, 3.5, 4, 4.5, 5)] == [
        "💀 Кринж", "💀 Кринж", "😐 Так себе", "🙂 Норм", "😂 Годнота", "😂 Годнота", "🔥 Шедевр", "🔥 Шедевр"]
    assert verdict(5, {5: ["a", "b", "c"]}) == "🤯 Единогласный шедевр"
    assert verdict(5, {5: ["a", "b"]}) == "🔥 Шедевр"  # two isn't everybody yet
    assert verdict(1, {1: ["a", "b", "c"]}) == "⚰️ Похороны мема"
    assert verdict(3, {5: ["a"], 1: ["b"], 3: ["c"]}) == "⚔️ Мем раскола"
    assert [ago(s) for s in (60, 86400 + 5, 3 * 86400, 5 * 86400)] == ["сегодня", "вчера", "3 дня назад", "5 дней назад"]
    assert bar(3.6) == "▰▰▰▰▱" and bar(5) == "▰" * 5 and bar(1.2) == "▰" + "▱" * 4
    assert [joke_label("шутка", n) for n in (0, 1, 3, 5)] == ["шутка", "шутка · 1", "шутка · 3 🔥", "шутка · 5 🚨"]


def test_inline_mode_off_tells_the_panel_once(tmp_path):
    ratings, userbot, _, _, notes = make(tmp_path, inline_fails=True)
    asyncio.run(ratings.on_message(message(msg_id=1)))
    asyncio.run(ratings.on_message(message(msg_id=2)))
    assert len(notes) == 1 and "/setinline" in notes[0]


VOTERS = ["Петя", "Оля", "Вася", "Тимофей", "Аня"]


def scored(journal, chat_id, meme_id, author_id, author, ts, scores, repeat_of=None, jokes=0):
    token = abs(chat_id) * 1000 + meme_id
    journal.add_meme(token, chat_id, meme_id, author_id, author, ts=ts, repeat_of=repeat_of)
    for voter, score in enumerate(scores):
        journal.vote_meme(token, 100 + voter, score, VOTERS[voter])
    for voter in range(jokes):
        journal.toggle_meme_joke(token, 200 + voter)
    return token


def test_top_of_the_week(tmp_path):
    journal = Journal(tmp_path / "assistant.db")
    now = 30 * 86400
    day = 86400
    scored(journal, -1001234, 1, 1, "Вася", now - day, [10, 9, 9, 10])
    scored(journal, -1001234, 2, 1, "Вася", now - 2 * day, [6, 6, 6])
    scored(journal, -1001234, 3, 2, "Петя", now - 3 * day, [8, 8, 8, 8, 8])
    scored(journal, -1001234, 4, 3, "Оля", now - 10 * day, [10])  # older than a week
    scored(journal, -1001234, 5, 3, "Оля", now - day, [7, 7])
    journal.add_meme(1, -1001234, 6, 3, "Оля", ts=now)  # nobody voted
    assert format_top(journal, -1001234, now) == "\n".join([
        "🏆 Лучшие мемы недели",
        "1. 9.5/5 — Вася (4 голоса) https://t.me/c/1234/1",
        "2. 8.0/5 — Петя (5 голосов) https://t.me/c/1234/3",
        "3. 7.0/5 — Оля (2 голоса) https://t.me/c/1234/5",
        "",
        "👑 Лучшие авторы",
        "1. Петя — 8.0 в среднем, 1 мем",
        "2. Вася — 7.8 в среднем, 2 мема",
        "3. Оля — 7.0 в среднем, 1 мем",
        "",
        "🎖 Номинации",
        "🥶 Душнила недели: Оля — ставит в среднем 7.5",
        "🤡 Щедрая душа: Петя — ставит в среднем 7.8",
    ])
    assert format_top(journal, 5, now) == "За неделю ещё ни один мем не получил оценок"


def test_weekly_top_on_sunday_afternoon_once(tmp_path):
    ratings, userbot, _, journal, _ = make(tmp_path)
    ratings.top_hour = 14
    sunday = datetime.datetime(2026, 11, 8, 14, 5, tzinfo=datetime.UTC)
    scored(journal, 5, 9, 11, "Вася", sunday.timestamp() - 3600, [8, 9])
    ratings.store.set_memes(6, True, "Тихий чат")  # no scores this week: no top there

    asyncio.run(ratings.weekly_top(sunday.replace(hour=13)))  # too early
    asyncio.run(ratings.weekly_top(sunday - datetime.timedelta(days=1)))  # Saturday
    assert userbot.sent == []
    asyncio.run(ratings.weekly_top(sunday))
    asyncio.run(ratings.weekly_top(sunday.replace(hour=20)))  # already sent
    [(chat_id, text)] = userbot.sent
    assert chat_id == 5 and text.startswith("🏆 Лучшие мемы недели\n1. 8.5/5 — Вася")
    asyncio.run(ratings.weekly_top(sunday + datetime.timedelta(days=7)))  # next week, no new scores
    assert len(userbot.sent) == 1


def test_words():
    assert [plural(n, "голос", "голоса", "голосов") for n in (1, 2, 5, 11, 21, 22, 112)] == [
        "голос", "голоса", "голосов", "голосов", "голос", "голоса", "голосов"]
    assert header(MemeView({4: ["a", "b"], 3: ["c"]})) == "😂 Годнота ⭐ 3.7 · 👥 3"
    assert link(5, 3) == ""


def test_memes_command(tmp_path):
    store = Store(tmp_path / "rules.json")
    run = lambda text, chat_id=5: commands.execute(store, commands.parse(text), chat_id, "Мемы")
    assert run(".memes").text == "Оцениваем мемы для Мемы"
    assert run(".memes on").text == "Мемы для Мемы уже оцениваются"
    assert "Оцениваем мемы: Мемы #5" in run(".rules").text
    assert run(".memes off").text == "Больше не оцениваем мемы для Мемы"
    assert run(".memes", chat_id=None).is_error
    assert run(".memes лол").is_error
    assert Store(store.path).memes == {}


def test_a_repost_is_called_out_and_the_author_rating_themselves_gets_a_smirk(tmp_path):
    ratings, userbot, bot, journal, _ = make(tmp_path)
    asyncio.run(ratings.on_message(message(msg_id=1, media_id=77)))
    first = token_of(userbot)
    asyncio.run(bot.handlers[-1](Click(f"mr:{first}:4", 21)))
    asyncio.run(bot.handlers[-1](Click(f"mr:{first}:3", 11, "Вася")))  # Вася posted it
    assert "<td>Вася 😏</td>" in bot.requests[-1].rich_message.html

    asyncio.run(ratings.on_message(message(msg_id=2, media_id=78)))  # another meme
    assert ratings.view(token_of(userbot)).repeat == ""
    asyncio.run(ratings.on_message(message(msg_id=3, media_id=77, sender_id=12)))  # the first one again
    again = token_of(userbot)
    assert ratings.view(again).repeat == "🔁 Баян · сегодня · ⭐ 3.5"
    assert ratings.view(again, now=journal.get_meme(first)[1] + 2 * 86400).repeat.startswith("🔁 Баян · 2 дня")


def test_nominations(tmp_path):
    journal = Journal(tmp_path / "assistant.db")
    now, day = 30 * 86400, 86400
    first = scored(journal, 5, 1, 1, "Вася", now - day, [2, 9, 5], jokes=2)
    scored(journal, 5, 2, 2, "Петя", now - day, [3, 10, 5], jokes=1)
    scored(journal, 5, 3, 1, "Вася", now - day, [1, 9, 5], repeat_of=first)
    assert nominations(journal, 5, 0, "шутка") == [
        "🥶 Душнила недели: Петя — ставит в среднем 2.0",
        "🤡 Щедрая душа: Оля — ставит в среднем 9.3",
        "🫵 «шутка» чаще всех под мемами: Вася ×2",
        "🔁 Баянист недели: Вася ×1",
    ]
    assert nominations(journal, 5, now, "шутка") == []  # nothing this recent


def test_a_rating_closes_after_an_hour(tmp_path):
    ratings, userbot, bot, journal, _ = make(tmp_path, joke="шутка")
    on_inline_sent, on_click = bot.handlers[1], bot.handlers[-1]
    asyncio.run(ratings.on_message(message(msg_id=1)))
    clicked = token_of(userbot)
    asyncio.run(ratings.on_message(message(msg_id=2)))
    sent = token_of(userbot)  # nobody clicks, the inline feedback brings its id
    asyncio.run(on_inline_sent(types.UpdateBotInlineSend(
        user_id=OWNER, query="", id=str(sent), msg_id=types.InputBotInlineMessageID(dc_id=2, id=9, access_hash=3),
    )))
    asyncio.run(on_inline_sent(types.UpdateBotInlineSend(user_id=2, query="", id="1", msg_id=None)))  # not mine
    asyncio.run(ratings.on_message(message(msg_id=3)))
    unknown = token_of(userbot)  # no clicks, no feedback: closes on its next click
    asyncio.run(on_click(Click(f"mr:{clicked}:5", 21)))
    born = journal.get_meme(clicked)[1]

    bot.requests.clear()
    asyncio.run(ratings.close_old(now=born + 60))
    assert bot.requests == []  # too early
    asyncio.run(ratings.close_old(now=born + 3600 + 60))
    closed = {r.id.id: r for r in bot.requests}
    assert closed.keys() == {21, 9}
    assert all(r.reply_markup is None for r in closed.values())
    assert closed[21].rich_message.html.startswith("<p><b>🔥 Шедевр</b> ⭐ <b>5.0</b>")
    assert closed[9].rich_message.html == "<p>😶 Никто не оценил</p>"
    asyncio.run(ratings.close_old(now=born + 7200))
    assert len(bot.requests) == 2  # once

    journal.db.execute("UPDATE memes SET ts = ts - 7200")
    late = Click(f"mr:{unknown}:5", 22)
    asyncio.run(on_click(late))
    assert late.toast == "⏰ Голосование уже закрыто"
    assert bot.requests[-1].reply_markup is None and journal.meme_tally(unknown)[1] == 0


def test_old_ten_point_votes_and_buttons_turn_into_five(tmp_path):
    journal = Journal(tmp_path / "old.db")
    journal.add_meme(1, 5, 1, 11, "Вася")
    for user, score in ((1, 10), (2, 9), (3, 8), (4, 2), (5, 1)):
        journal.vote_meme(1, user, score)
    journal.set_meta("meme_scale", "10")  # as it was before
    journal = Journal(tmp_path / "old.db")
    assert sorted(journal.meme_scores(1)) == [1, 4, 5]
    assert {s: len(v) for s, v in journal.meme_scores(1).items()} == {5: 2, 4: 1, 1: 2}
    journal = Journal(tmp_path / "old.db")  # once
    assert sorted(journal.meme_scores(1)) == [1, 4, 5]

    ratings, userbot, bot, journal, _ = make(tmp_path)
    asyncio.run(ratings.on_message(message()))
    old_button = Click(f"mr:{token_of(userbot)}:8", 21)  # a 1–10 card still in the chat
    asyncio.run(bot.handlers[-1](old_button))
    assert old_button.toast == "Твоя оценка: 4"


def test_a_text_with_the_joke_in_it_is_rated_too(tmp_path):
    ratings, userbot, _, _, _ = make(tmp_path, joke="Шутка дня")
    for msg_id, text in ((1, "ну это ШУТКА ДНЯ, конечно"), (2, "просто текст"), (3, "")):
        asyncio.run(ratings.on_message(message(msg_id=msg_id, photo=False, raw_text=text)))
    assert [r for _, _, r in userbot.clicks] == [1]

    (tmp_path / "x").mkdir()
    plain, userbot, _, _, _ = make(tmp_path / "x", joke="")
    asyncio.run(plain.on_message(message(photo=False, raw_text="что угодно")))
    assert userbot.clicks == []
