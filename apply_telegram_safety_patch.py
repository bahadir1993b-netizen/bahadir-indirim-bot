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

if old in s:
    s = s.replace(old, new, 1)
else:
    print('Canlı fiyat bloğu zaten patchlenmiş; atlandı.')

# Aynı mağaza + çok benzer ürün + %1'den küçük fiyat farkı ile 12 saatlik tekrar engeli.
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
    if marker in s:
        s = s.replace(marker, helper + marker, 1)
        needle = "    row=save(s,u,title,c,p); last=row.get('last_posted_at') if isinstance(row,dict) else None"
        replacement = "    if _bahadir_duplicate_recent(s,title,c): remember(key); return False\n" + needle
        if needle in s:
            s = s.replace(needle, replacement, 1)
        else:
            print('send kayıt satırı bulunamadı; tekrar koruması atlandı.')
    else:
        print('send marker bulunamadı; tekrar koruması atlandı.')

# Telegram kaynak mesajındaki fotoğrafı, mevcut send() fonksiyonunun sendMessage çağrısını
# sendPhoto'ya dönüştürerek taşır. Böylece mevcut fiyat/filtre mantığı bozulmaz.
if 'def _bahadir_photo_process' not in s:
    photo_patch = r'''

_BAHADIR_SOURCE_IMAGE = None
_BAHADIR_ORIGINAL_SEND = send
_BAHADIR_ORIGINAL_PROCESS = process

def _bahadir_extract_source_image(block):
    wrap = block.select_one('.tgme_widget_message_photo_wrap')
    if wrap:
        st = wrap.get('style','')
        m = re.search(r"url\(['\"]?([^'\")]+)", st)
        if m: return clean(m.group(1))
    img = block.select_one('.tgme_widget_message_photo img, .tgme_widget_message_photo_wrap img')
    if img:
        return clean(img.get('src') or img.get('data-src') or '')
    return None

def send(s,u,t,c,p,source,post_id,signal,coupon=None):
    image_url = _BAHADIR_SOURCE_IMAGE
    if not image_url:
        return _BAHADIR_ORIGINAL_SEND(s,u,t,c,p,source,post_id,signal,coupon)
    original_post = requests.post
    def post_intercept(url, **kwargs):
        if '/sendMessage' not in url:
            return original_post(url, **kwargs)
        try:
            payload = kwargs.get('json') or {}
            text = payload.get('text','')
            markup = payload.get('reply_markup')
            ir = requests.get(image_url, headers=HEAD, timeout=10)
            if not ir.ok or len(ir.content) < 1000:
                print(f'GÖRSEL ALINAMADI | {source}:{post_id} | HTTP={ir.status_code}')
                return original_post(url, **kwargs)
            import json
            return original_post('https://api.telegram.org/bot'+TOKEN+'/sendPhoto',
                data={'chat_id':CHAT,'caption':text[:1024],
                      'reply_markup':json.dumps(markup,ensure_ascii=False) if markup else None},
                files={'photo':('source.jpg',ir.content,'image/jpeg')}, timeout=20)
        except Exception as e:
            print(f'GÖRSEL TAŞIMA HATA | {source}:{post_id} | {type(e).__name__}: {e}')
            return original_post(url, **kwargs)
    requests.post = post_intercept
    try:
        return _BAHADIR_ORIGINAL_SEND(s,u,t,c,p,source,post_id,signal,coupon)
    finally:
        requests.post = original_post

def process(source,b,page):
    global _BAHADIR_SOURCE_IMAGE
    _BAHADIR_SOURCE_IMAGE = _bahadir_extract_source_image(b)
    try:
        return _BAHADIR_ORIGINAL_PROCESS(source,b,page)
    finally:
        _BAHADIR_SOURCE_IMAGE = None

'''
    main_marker = "if __name__ == '__main__':"
    if main_marker in s:
        s = s.replace(main_marker, photo_patch + main_marker, 1)
    else:
        s += photo_patch

P.write_text(s, encoding='utf-8')
print('Telegram safety patch uygulandı: canlı fiyat fallback + ürün tekrar koruması + kaynak fotoğraf taşıma.')
'''

