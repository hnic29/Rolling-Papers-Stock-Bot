from app.services import bot_registry


def test_get_bot_returns_the_same_instance_on_repeated_calls():
    first = bot_registry.get_bot(1)
    second = bot_registry.get_bot(1)

    assert first is second


def test_get_bot_returns_a_distinct_instance_per_user():
    alice_bot = bot_registry.get_bot(1)
    bob_bot = bot_registry.get_bot(2)

    assert alice_bot is not bob_bot
    assert alice_bot.user_id == 1
    assert bob_bot.user_id == 2
