from app.config import Settings


def test_settings_tolerates_unrecognized_dotenv_keys(tmp_path):
    """Regression test: the deploy env template includes TRADE_LOG_PATH,
    which is intentionally read directly via os.environ in
    app.services.trade_log rather than being a Settings field. Any key in
    the dotenv file with no matching field must not crash Settings() at
    import time - this is exactly what took the LXC deployment down."""
    env_file = tmp_path / "rolling-papers-bot.env"
    env_file.write_text(
        "ALPACA_API_KEY=test-key\n"
        "TRADE_LOG_PATH=/var/lib/rolling-papers-bot/trade_log.db\n"
        "SOME_FUTURE_DEPLOY_ONLY_VAR=whatever\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.alpaca_api_key == "test-key"
