from pathlib import Path
import re

P = Path('telegram_sources.py')
s = P.read_text(encoding='utf-8')

old = '''    try:
        live_current, live_old = marketplace_price_check(s,u,c)
        if live_current is None:
            print(f'ATLANDI | {source}:{post_id} | canlı fiyat okunamadı'); return False
        if special:
            if c > live_current*1.05:
                print(f'ATLANDI | {source}:{post_id} | kampanya fiyatı canlı fiyattan yüksek | kaynak={c:.2f} | canlı={live_current:.2f}'); remember(key); return False
            if live_current > c*1.02:
                p=live_current; disc=(p-c)/p*100
            else:
                c=live_current
        else:
            drift=abs(live_current-c)/max(c,1)
            if drift>0.05:
                print(f'ATLANDI | {source}:{post_id} | fiyat değişti | kaynak={c:.2f} | canlı={live_current:.2f} | fark=%{drift*100:.1f}'); remember(key); return False
            c=live_current
    except Exception as e:
        print('CANLI FİYAT KONTROL HATA',e); return False
'''

new = '''    try:
        live_current, live_old = marketplace_price_check(s,u,c)
        if live_current is None:
            # Trendyol/Hepsiburada/Amazon frequently block simple HTTP price reads.
            # A source deal must not disappear merely because the marketplace page
            # cannot be scraped. Keep the source price and continue as a source-led deal.
            print(f'CANLI FİYAT OKUNAMADI | {source}:{post_id} | kaynak fiyat korunuyor={c:.2f}')
            p = None
            disc = None
        elif special:
            if c > live_current*1.05:
                print(f'ATLANDI | {source}:{post_id} | kampanya fiyatı canlı fiyattan yüksek | kaynak={c:.2f} | canlı={live_current:.2f}'); remember(key); return False
            if live_current > c*1.02:
                p=live_current; disc=(p-c)/p*100
            else:
                c=live_current
        else:
            drift=abs(live_current-c)/max(c,1)
            if drift>0.05:
                print(f'ATLANDI | {source}:{post_id} | fiyat değişti | kaynak={c:.2f} | canlı={live_current:.2f} | fark=%{drift*100:.1f}'); remember(key); return False
            c=live_current
    except Exception as e:
        print(f'CANLI FİYAT KONTROL HATA | {source}:{post_id} | kaynak fiyat korunuyor={c:.2f} | {type(e).__name__}: {e}')
        p = None
        disc = None
'''

if old not in s:
    raise SystemExit('Telegram canlı fiyat kontrol bloğu bulunamadı; güvenli patch uygulanmadı')
s = s.replace(old, new, 1)
P.write_text(s, encoding='utf-8')
print('Telegram safety patch uygulandı: canlı fiyat okunamazsa kaynak fırsat artık kaybolmayacak.')
