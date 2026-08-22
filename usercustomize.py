"""Runtime safety preferences loaded automatically by Python startup."""
try:
    import telegram_sources as _ts
    _ts.SOURCES.pop('FirsatZ', None)
except Exception:
    pass
