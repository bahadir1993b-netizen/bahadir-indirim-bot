import os

# .env içinde SUPABASE_URL yanlışlıkla /rest/v1 ile bitiyorsa tüm alt modüllerden önce düzelt.
# Böylece telegram_sources.py tekrar /rest/v1 eklediğinde /rest/v1/rest/v1 oluşmaz.
_supabase = os.environ.get('SUPABASE_URL', '').rstrip('/')
if _supabase.endswith('/rest/v1'):
    _supabase = _supabase[:-8].rstrip('/')
    os.environ['SUPABASE_URL'] = _supabase

import bot
import run_serper
import telegram_sources

# Tek eşik: yalnızca %15 ve üzeri gerçek indirimleri paylaş.
bot.MIN_DISCOUNT = 15.0
telegram_sources.MIN_DISCOUNT = 15.0
telegram_sources.MAX_AGE = max(45, int(os.environ.get('TELEGRAM_MAX_AGE', '45')))

# Amazon ortaklık etiketini tüm kaynaklarda aynı değişkenden kullan.
amazon_tag = (os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or '').strip()
run_serper.AMAZON_TAG = amazon_tag
telegram_sources.AMAZON_TAG = amazon_tag

print('=== Tüm kaynaklar başlıyor | Serper + Telegram | eşik=%15 ===')
try:
    run_serper.main()
except Exception as e:
    print(f'Serper tur hata: {type(e).__name__}: {e}')

try:
    telegram_sources.main()
except Exception as e:
    print(f'Telegram tur hata: {type(e).__name__}: {e}')

print('=== Tüm kaynaklar tamamlandı ===')
