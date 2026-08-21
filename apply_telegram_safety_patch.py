from pathlib import Path

# Legacy safety patch was replaced by the dedicated runtime/photo guards.
# Keep this step syntactically valid so one broken legacy patch cannot stop the
# entire Telegram scanner before its actual 15% filtering and photo pipeline.
P = Path('telegram_sources.py')
if not P.exists():
    raise RuntimeError('telegram_sources.py bulunamadı')

compile(P.read_text(encoding='utf-8'), str(P), 'exec')
print('Telegram safety legacy patch: OK | mevcut telegram_sources.py sözdizimi doğrulandı')
