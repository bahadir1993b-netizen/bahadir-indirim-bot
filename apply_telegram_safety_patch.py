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

# Aynı ürün farklı kaynaklardan veya farklı kısaltılmış linklerden geldiğinde
# URL birebir aynı olmasa bile yakın fiyat + güçlü başlık benzerliği ile tekrar paylaşma.
dedupe = '''

# Kaynaklar aynı ürünü farklı fiyat yuvarlaması/linkiyle gönderebilir.
# Son 12 saatte aynı mağaza + çok benzer ürün başlığı + %1'den küçük fiyat farkı
# varsa ikinci paylaşımı engelle.
if 'def _bahadir_duplicate_recent' not in s:
    marker = "def send(s,u,t,c,p,source,post_id,signal,coupon=None):"
    helper = '''def _bahadir_norm_title(text):
    return set(re.findall(r'[a-zçğıöşü0-9]{3,}', (text or '').lower()))

def _bahadir_duplicate_recent(site_name, title, current):
    try:
        rows = sb('GET','products',params={'select':'product_name,current_price,last_posted_at,site','site':f'eq.{site_name}','order':'updated_at.desc','limit':'100'})
        now = datetime.now(timezone.utc)
        cur_tokens = _bahadir_norm_title(title)
        if len(cur_tokens) < 3: return False
        for row in rows:
            last = row.get('last_posted_at')
            if not last: continue
            try: age = now - datetime.fromisoformat(last.replace('Z','+00:00'))
            except: continue
            if age >= timedelta(hours=COOLDOWN): continue
            old_price = float(row.get('current_price') or 0)
            if not old_price or abs(old_price-current)/max(old_price,current,1) > 0.01: continue
            old_tokens = _bahadir_norm_title(row.get('product_name'))
            overlap = len(cur_tokens & old_tokens) / max(1, min(len(cur_tokens), len(old_tokens)))
            if overlap >= 0.72:
                print(f'ATLANDI | {site_name} | aynı ürün zaten paylaşıldı | %{overlap*100:.0f} başlık benzerliği | {current:.2f} TL')
                return True
    except Exception as e:
        print('TEKRAR KONTROL HATA', e)
    return False

'''
    s = s.replace(marker, helper + marker, 1)
    needle = "    row=save(s,u,title,c,p); last=row.get('last_posted_at') if isinstance(row,dict) else None"
    replacement = "    if _bahadir_duplicate_recent(s,title,c): remember(key); return False\n" + needle
    if needle not in s:
        raise SystemExit('Telegram send kayıt satırı bulunamadı; tekrar koruması uygulanmadı')
    s = s.replace(needle, replacement, 1)

P.write_text(s, encoding='utf-8')
print('Telegram safety patch uygulandı: canlı fiyat fallback + aynı üründe 12 saat tekrar koruması.')