from pathlib import Path
import re

P = Path('telegram_sources.py')
s = P.read_text(encoding='utf-8')

new_extract = r'''def extract_title(raw):
    """Kaynak mesajından sadece gerçek ürün adını çıkar; yönlendirme/etiket gürültüsünü at."""
    text = re.sub(r'(?i)\s*👉?\s*FIRSATA\s*G[İI]T.*$', '', raw or '')
    text = re.sub(r'(?i)\s*(?:GOOGLE\s*🔍?|#\w+|@\w+)\b', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip(' -•👉')
    # Fiyat/kampanya bölümünden sonrasını ürün adından çıkar.
    text = re.split(r'\s*(?:🏷️|💰|\b\d[\d.,]*\s*(?:TL|₺))\b', text, maxsplit=1, flags=re.I)[0]
    text = re.sub(r'\s+', ' ', text).strip(' -•👉')
    return text[:180] if text else (raw or '')[:180]
'''

s2, n1 = re.subn(r'def extract_title\(raw\):.*?(?=\ndef process\()', new_extract.rstrip() + '\n', s, count=1, flags=re.S)
if n1 != 1:
    raise SystemExit('extract_title fonksiyonu bulunamadı')

new_send = r'''def send(s,u,t,c,p,source,post_id,signal,coupon=None):
    if not valid(s,u):
        print(f'ATLANDI | {source}:{post_id} | geçersiz link'); return False
    key=f'{source}:{post_id}'
    if seen(key): return False
    disc=(p-c)/p*100 if p and p>c else None
    if disc is not None and disc<MIN_DISCOUNT:
        print(f'ATLANDI | {source}:{post_id} | %{disc:.1f} < %{MIN_DISCOUNT}'); remember(key); return False
    if disc is None and not coupon and not DEAL_WORDS.search(signal or ''):
        print(f'ATLANDI | {source}:{post_id} | kampanya sinyali yok'); remember(key); return False

    row=save(s,u,t,c,p); last=row.get('last_posted_at') if isinstance(row,dict) else None
    if last:
        try:
            if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN):
                print(f'ATLANDI | {source}:{post_id} | cooldown'); remember(key); return False
        except: pass

    title=extract_title(t)
    lines=[f'🔥 %{disc:.0f} İNDİRİM' if disc is not None else ('🎟️ KUPONLU FIRSAT' if coupon else '🔥 FIRSAT'), '', f'🛍️ {title}', f'💰 {c:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.')]
    if p and p>c: lines.append(f'🏷️ Önceki: {p:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.'))
    if coupon: lines.append(f'🎟️ Kupon: {coupon}')
    lines += ['', '👇 Fırsata git']
    caption='\n'.join(lines)

    # Mümkünse ürün sayfasındaki gerçek ürün görselini kullan.
    image=None
    try:
        r=requests.get(u,headers=HEAD,timeout=6)
        if r.ok:
            soup=BeautifulSoup(r.text,'html.parser')
            for sel,attr in [('meta[property="og:image"]','content'),('meta[name="twitter:image"]','content'),('meta[property="twitter:image"]','content')]:
                el=soup.select_one(sel)
                if el and el.get(attr):
                    image=clean(el.get(attr)); break
            if not image:
                for el in soup.select('img[src]')[:30]:
                    src=clean(el.get('src') or '')
                    if src.startswith('http') and not any(x in src.lower() for x in ('logo','icon','sprite','avatar','banner')):
                        image=src; break
    except: pass

    markup={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}
    if image:
        resp=requests.post('https://api.telegram.org/bot'+TOKEN+'/sendPhoto',json={'chat_id':CHAT,'photo':image,'caption':caption[:1024],'reply_markup':markup},timeout=15)
        if not resp.ok:
            print(f'GÖRSEL YÜKLENEMEDİ | {resp.text[:300]}')
            resp=requests.post('https://api.telegram.org/bot'+TOKEN+'/sendMessage',json={'chat_id':CHAT,'text':caption,'disable_web_page_preview':False,'reply_markup':markup},timeout=15)
    else:
        resp=requests.post('https://api.telegram.org/bot'+TOKEN+'/sendMessage',json={'chat_id':CHAT,'text':caption,'disable_web_page_preview':False,'reply_markup':markup},timeout=15)
    resp.raise_for_status()

    if isinstance(row,dict) and row.get('id'):
        sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat()})
    remember(key)
    print(f'GÖNDERİLDİ | {s} | {c:.2f} TL'+(f' | %{disc:.1f}' if disc is not None else '')+(f' | görsel={bool(image)}'))
    return True
'''

s3, n2 = re.subn(r'def send\(s,u,t,c,p,source,post_id,signal,coupon=None\):.*?(?=\ndef extract_title\()', new_send.rstrip() + '\n', s2, count=1, flags=re.S)
if n2 != 1:
    raise SystemExit('send fonksiyonu bulunamadı')

P.write_text(s3, encoding='utf-8')
print('Telegram runtime patch uygulandı: başlık temizleme + ürün görseli + %6 korunuyor')
