from pathlib import Path
import re
P=Path('telegram_sources.py'); s=P.read_text(encoding='utf-8')
new_extract=r'''def extract_title(raw):
    text=re.sub(r'(?i)\s*👉?\s*FIRSATA\s*G[İI]T.*$','',raw or '')
    text=re.sub(r'(?i)\s*(?:GOOGLE\s*🔍?|#\w+|@\w+)\b',' ',text)
    text=re.sub(r'\s+',' ',text).strip(' -•👉')
    text=re.split(r'\s*(?:🏷️|💰|\b\d[\d.,]*\s*(?:TL|₺))\b',text,maxsplit=1,flags=re.I)[0]
    return re.sub(r'\s+',' ',text).strip(' -•👉')[:180]
'''
s,n=re.subn(r'def extract_title\(raw\):.*?(?=\ndef process\()',lambda m:new_extract.rstrip()+'\n',s,count=1,flags=re.S)
if n!=1:raise SystemExit('extract_title bulunamadı')
new_send=r'''def send(s,u,t,c,p,source,post_id,signal,coupon=None):
    if not valid(s,u): print(f'ATLANDI | {source}:{post_id} | geçersiz link'); return False
    key=f'{source}:{post_id}'
    if seen(key): return False
    title=extract_title(t)
    if re.search(r'\b(kitap|kitapları|roman|dergi|magazin|e-kitap|ebook|yayınevi|yayıncılık)\b',title,re.I):
        print(f'ATLANDI | {source}:{post_id} | kitap kategorisi'); remember(key); return False
    disc=(p-c)/p*100 if p and p>c else None
    if disc is not None and disc<MIN_DISCOUNT: print(f'ATLANDI | {source}:{post_id} | %{disc:.1f} < %{MIN_DISCOUNT}'); remember(key); return False
    if disc is None and not coupon and not DEAL_WORDS.search(signal or ''): print(f'ATLANDI | {source}:{post_id} | kampanya sinyali yok'); remember(key); return False
    market_ref=None
    if s=='Amazon':
        try:
            q=' '.join(re.sub(r'Amazon\.com\.tr.*$','',title,flags=re.I).split()); words=q.split()
            # Uzun başlıkları kademeli kısaltarak Akakçe'de gerçek piyasa fiyatını ara.
            queries=[' '.join(words[:24]),' '.join(words[:14]),' '.join(words[:8])]; best=[]
            for qq in queries:
                if not qq: continue
                r=requests.get('https://www.akakce.com/arama/?q='+requests.utils.quote(qq),headers=HEAD,timeout=7)
                if not r.ok: continue
                soup=BeautifulSoup(r.text,'html.parser'); vals=[]
                for el in soup.select('.pt_v8,.price,.fiyat,[class*="price"]'):
                    x=money(el.get_text(' ',strip=True))
                    if x and x>1: vals.append(x)
                if vals: best.append(min(vals))
            if best:
                market_ref=min(best)
                market_disc=(market_ref-c)/market_ref*100 if market_ref>c else 0
                # Kaynağın "önceki" fiyatı piyasa fiyatından kopuksa onu indirim hesabında kullanma.
                if market_disc < MIN_DISCOUNT:
                    print(f'ATLANDI | {source}:{post_id} | piyasa indirimi yetersiz | mevcut={c:.2f} | Akakçe={market_ref:.2f} | %{market_disc:.1f}')
                    remember(key); return False
                # Güvenilir piyasa referansı varsa sahte/list fiyatını tamamen bırak.
                p=market_ref; disc=market_disc
                print(f'AKAKÇE REFERANS | mevcut={c:.2f} | piyasa={market_ref:.2f} | gerçek indirim=%{disc:.1f}')
        except Exception as e: print('AKAKCE KONTROL HATA',e)
    row=save(s,u,title,c,p); last=row.get('last_posted_at') if isinstance(row,dict) else None
    if last:
        try:
            if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN): print(f'ATLANDI | {source}:{post_id} | cooldown'); remember(key); return False
        except: pass
    lines=[f'🔥 %{disc:.0f} İNDİRİM' if disc is not None else ('🎟️ KUPONLU FIRSAT' if coupon else '🔥 FIRSAT'),' ',f'🛍️ {title}',f'💰 {c:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.')]
    if p and p>c: lines.append(f'🏷️ Piyasa/Önceki: {p:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.'))
    if coupon: lines.append(f'🎟️ Kupon: {coupon}')
    lines+=['','👇 Fırsata git']; caption='\n'.join(lines); markup={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}; image=None
    try:
        r=requests.get(u,headers=HEAD,timeout=7)
        if r.ok:
            soup=BeautifulSoup(r.text,'html.parser')
            for sel in ['meta[property="og:image"]','meta[name="twitter:image"]','meta[property="twitter:image"]']:
                el=soup.select_one(sel)
                if el and el.get('content'): image=clean(el.get('content')); break
    except: pass
    resp=None
    if image:
        try:
            ir=requests.get(image,headers=HEAD,timeout=10)
            if ir.ok and len(ir.content)>1000:
                resp=requests.post('https://api.telegram.org/bot'+TOKEN+'/sendPhoto',data={'chat_id':CHAT,'caption':caption[:1024],'reply_markup':__import__('json').dumps(markup,ensure_ascii=False)},files={'photo':('product.jpg',ir.content,'image/jpeg')},timeout=20)
        except Exception as e: print('GÖRSEL HATA',e)
    if not resp or not resp.ok:
        resp=requests.post('https://api.telegram.org/bot'+TOKEN+'/sendMessage',json={'chat_id':CHAT,'text':caption,'disable_web_page_preview':False,'reply_markup':markup},timeout=15)
    resp.raise_for_status()
    if isinstance(row,dict) and row.get('id'): sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat()})
    remember(key); print(f'GÖNDERİLDİ | {s} | {c:.2f} TL'+(f' | %{disc:.1f}' if disc is not None else '')); return True
'''
s,n=re.subn(r'def send\(s,u,t,c,p,source,post_id,signal,coupon=None\):.*?(?=\ndef extract_title\()',lambda m:new_send.rstrip()+'\n',s,count=1,flags=re.S)
if n!=1:raise SystemExit('send bulunamadı')
P.write_text(s,encoding='utf-8'); print('Telegram patch: piyasa fiyatı doğrulaması + gerçek görsel yükleme')
