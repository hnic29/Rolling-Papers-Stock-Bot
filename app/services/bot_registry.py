"""One TradingBot instance per logged-in user, lazily constructed and cached for the
life of the process - replaces the old module-level `bot = TradingBot()` singleton
now that more than one person can be running the bot at once. Each instance owns its
own broker (via AlpacaBroker.for_user), scanner, and in-memory position-management
state, fully isolated from every other user's."""

from app.services.bot import TradingBot

_bots: dict[int, TradingBot] = {}


def get_bot(user_id: int) -> TradingBot:
    bot = _bots.get(user_id)
    if bot is None:
        bot = TradingBot(user_id)
        _bots[user_id] = bot
    return bot
