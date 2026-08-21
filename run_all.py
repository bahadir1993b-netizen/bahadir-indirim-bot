import os
import re

# .env içinde SUPABASE_URL yanlışlıkla /rest/v1 ile bitiyorsa tüm alt modüllerden önce düzelt.
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

# Başka Telegram kanallarından gelen reklam/kanal adlarını kullanıcıya taşımama.
_original_extract_title = telegram_sources.extract_title
_original_send = telegram_sources.send

def _sanitize_title(text):
    t = re.sub(r'https?://\S+|t\.me/\S+|www\.\S+', ' ', text or '', flags=re.I)
    t = re.sub(r'@[A-Za-z0-9_]{3,}', ' ', t)
    # Kaynak kanalların kendi promosyon metinlerini ve sonrasını kes.
    t = re.split(r'\b(?:sohbet\s+grubumuz|telegram\s+kanalımız|telegram\s+kanalimiz|kanalımıza\s+katıl|kanalimiz|takip\s+et|bizi\s+takip|reklam|sponsor|duyuru)\b', t, maxsplit=1, flags=re.I)[0]
    # Fırsat butonu / kampanya açıklaması başlıyorsa başlığı orada bitir.
    t = re.split(r'\b(?:fırsata\s+git|firsata\s+git|kampanya\s*:|sepete\s+\d+\s+adet|indirim\s+ödeme|indirim\s+odeme)\b', t, maxsplit=1, flags=re.I)[0]
    # Fiyat ve sonrasını başlıktan çıkar; fiyat ayrıca mesajda gösteriliyor.
    t = re.split(r'\b\d[\d.,]*\s*(?:TL|₺)\b', t, maxsplit=1, flags=re.I)[0]
    t = re.sub(r'[🔗📣🎯🧮💰🏷️🔥🎟️]+', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip(' -•|:')
    return t[:180]

def _clean_extract_title(raw):
    # Önce ham metinden ürün adını temizlemeyi dene; boş kalırsa eski çıkarıcıya dön.
    t = _sanitize_title(raw)
    if len(t) >= 6:
        return t
    return _sanitize_title(_original_extract_title(raw)) or 'Ürün'

def _clean_send(s,u,t,c,p,source,post_id,signal,coupon=None):
    t = _sanitize_title(t) or _clean_extract_title(signal)
    return _original_send(s,u,t,c,p,source,post_id,signal,coupon)

telegram_sources.extract_title = _clean_extract_title
telegram_sources.send = _clean_send

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
