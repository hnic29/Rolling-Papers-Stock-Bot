from app.config import Settings, reload_settings, settings
from app.services import fmp


def test_reload_settings_is_visible_through_a_stale_from_import(monkeypatch, tmp_path):
    """Regression test: app.services.fmp (like several other modules) does
    `from app.config import settings`, binding its own reference to the
    object's identity at import time. Saving a new API key via the
    dashboard calls reload_settings() - if that rebinds app.config's name
    to a brand new object instead of mutating the existing one in place,
    fmp.settings (and every other module's copy) never sees the change,
    which is exactly why the Test Connection button kept reporting
    "not configured" even after a successful save."""
    env_file = tmp_path / "rolling-papers-bot.env"
    env_file.write_text("FMP_API_KEY=\n", encoding="utf-8")
    monkeypatch.setitem(Settings.model_config, "env_file", str(env_file))
    reload_settings()
    assert fmp.settings.fmp_api_key == ""

    env_file.write_text("FMP_API_KEY=newly-saved-key\n", encoding="utf-8")
    reload_settings()

    assert fmp.settings.fmp_api_key == "newly-saved-key"
    assert fmp.settings is settings  # same object identity, not a replacement


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
