import os,re,time,requests,html
from datetime import datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor,as_completed
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Supabase proje URL'sini normalize et.
sb=os.environ.get('SUPABASE_URL','').rstrip('/')
if sb.endswith('/rest/v1'):
    sb=sb[:-8].rstrip('/')
    os.environ['SUPABASE_URL']=sb

import telegram_sources as ts

ts.MIN_DISCOUNT=15.0
# Telegram public web bazen birkaç dakika cache'li geliyor. Geniş pencere + post-id tekrar engeli kullanıyoruz.
ts.MAX_AGE=max(30,int(os.environ.get('TELEGRAM_MAX_AGE','180')))
ts.AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or '').strip()

# Kaynak kanalın reklam / kullanıcı adı / sohbet grubu metnini başlığa asla taşıma.
def clean_title(raw):
    text=(raw or '').replace('\r','\n')
    cut_patterns=[r'\s[💰🏷️]\s*',r'\s(?:\d[\d.,]*)\s*(?:TL|₺)\b',r'\s(?:sepette|kampanya|kupon|kod(?:u)?|fırsata git|firsata git)\b']
    pos=len(text)
    for pat in cut_patterns:
        m=re.search(pat,text,re.I)
        if m:pos=min(pos,m.start())
    text=text[:pos]
    text=re.sub(r'@[A-Za-z0-9_]+',' ',text)
    text=re.sub(r'#(?:tanıtım|tanitim|reklam)\b',' ',text,flags=re.I)
    text=re.sub(r'(?i)\b(?:sohbet grubumuz(?: için)?|kanalımıza katıl|kanalımıza katilin|takip et|reklam)\b.*$',' ',text)
    text=re.sub(r'[🔥✅🔻🔗👉👇📣🛍️🎯🎁]+',' ',text)
    text=re.sub(r'\s+',' ',text).strip(' -|•')
    return text[:180] if len(text)>=4 else 'Fırsat Ürünü'

ts.extract_title=clean_title


def source_photo(source,post_id):
    """Kaynak Telegram gönderisinin görselini al. Yalnızca bizim kanala fotoğraf olarak yeniden gönderilir."""
    channel=ts.SOURCES.get(source)
    if not channel or not post_id:return None
    urls=[
        f'https://t.me/{channel}/{post_id}?embed=1&mode=tme',
        f'https://t.me/s/{channel}/{post_id}',
    ]
    for url in urls:
        try:
            r=requests.get(url,headers=ts.HEAD,timeout=8)
            if not r.ok:continue
            soup=BeautifulSoup(r.text,'html.parser')
            # Telegram fotoğrafları çoğu zaman background-image olarak geliyor.
            for el in soup.select('.tgme_widget_message_photo_wrap, .tgme_widget_message_photo'):
                style=html.unescape(el.get('style') or '')
                m=re.search(r"background-image\s*:\s*url\(['\"]?([^'\")]+)",style,re.I)
                if m and m.group(1).startswith('http'):return m.group(1)
            # Bazı gönderilerde img veya og:image bulunuyor.
            for sel,attr in [('.tgme_widget_message_photo img','src'),('meta[property="og:image"]','content')]:
                el=soup.select_one(sel)
                if el:
                    u=html.unescape(el.get(attr) or '')
                    if u.startswith('http') and 'telegram' not in u.lower().split('/')[-1]:return u
        except Exception:
            pass
    return None


def send_clean(s,u,t,c,p,source,post_id,signal,coupon=None):
    if not ts.valid(s,u):
        print(f'ATLANDI | {source}:{post_id} | geçersiz link');return False
    key=f'{source}:{post_id}'
    if ts.seen(key):return False

    disc=(p-c)/p*100 if p and p>c else None
    if disc is not None and disc<ts.MIN_DISCOUNT:
        print(f'ATLANDI | {source}:{post_id} | %{disc:.1f} < %{ts.MIN_DISCOUNT}')
        ts.remember(key);return False
    if disc is None and not coupon and not ts.DEAL_WORDS.search(signal or ''):
        print(f'ATLANDI | {source}:{post_id} | kampanya sinyali yok')
        ts.remember(key);return False

    row=ts.save(s,u,t,c,p)
    last=row.get('last_posted_at') if isinstance(row,dict) else None
    if last:
        try:
            if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=ts.COOLDOWN):
                print(f'ATLANDI | {source}:{post_id} | cooldown');ts.remember(key);return False
        except Exception:pass

    fmt=lambda x:f'{x:,.2f}'.replace(',','X').replace('.',',').replace('X','.')
    lines=[f'🔥 %{disc:.0f} İNDİRİM' if disc is not None else ('🎟️ KUPONLU FIRSAT' if coupon else '🔥 FIRSAT'),' ',f'🛍️ {clean_title(t)}',f'💰 {fmt(c)} TL']
    if p and p>c:lines.append(f'🏷️ Önceki: {fmt(p)} TL')
    if coupon:lines.append(f'🎟️ Kupon: {coupon}')
    lines+=['','👇 Fırsata git']
    text='\n'.join(lines)
    keyboard={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}

    photo=source_photo(source,post_id)
    rr=None
    if photo:
        rr=requests.post('https://api.telegram.org/bot'+ts.TOKEN+'/sendPhoto',data={
            'chat_id':ts.CHAT,
            'photo':photo,
            'caption':text[:1024],
            'reply_markup':__import__('json').dumps(keyboard,ensure_ascii=False),
        },timeout=15)
        if rr.ok:
            print(f'GÖRSELLİ GÖNDERİLDİ | {s} | {c:.2f} TL | foto=var')
    if not rr or not rr.ok:
        if photo:print(f'Fotoğraf gönderilemedi, metne düşüldü | HTTP={rr.status_code if rr else "?"}')
        rr=requests.post('https://api.telegram.org/bot'+ts.TOKEN+'/sendMessage',json={
            'chat_id':ts.CHAT,'text':text,'disable_web_page_preview':False,'reply_markup':keyboard
        },timeout=15)
    rr.raise_for_status()

    if isinstance(row,dict) and row.get('id'):
        ts.sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat()})
    ts.remember(key)
    print(f'GÖNDERİLDİ | {s} | {c:.2f} TL'+(f' | %{disc:.1f}' if disc is not None else '')+f' | foto={"var" if photo else "yok"}')
    return True


def strict_send(s,u,t,c,p,source,post_id,signal,coupon=None):
    t=clean_title(t)
    signal_clean=re.sub(r'@[A-Za-z0-9_]+',' ',signal or '')
    signal_clean=re.sub(r'(?i)\bsohbet grubumuz(?: için)?\b.*',' ',signal_clean)
    signal_clean=re.sub(r'(?i)\b(?:kanalımıza katıl|takip et|reklam)\b.*',' ',signal_clean)

    if p and p>c:
        disc=(p-c)/p*100
        if disc>=ts.MIN_DISCOUNT:return send_clean(s,u,t,c,p,source,post_id,signal_clean,coupon)
        try:ts.remember(f'{source}:{post_id}')
        except Exception:pass
        print(f'ATLANDI | {source}:{post_id} | kesin indirim %{disc:.1f} < %{ts.MIN_DISCOUNT}')
        return False

    m=re.search(r'\b(\d+)\s*al\s*(\d+)\s*(?:öde|ode)\b',signal_clean,re.I)
    if m:
        buy,paid=int(m.group(1)),int(m.group(2))
        if buy>paid>0:
            disc=(buy-paid)/buy*100
            if disc>=ts.MIN_DISCOUNT:
                effective=c*(paid/buy)
                return send_clean(s,u,t,effective,c,source,post_id,signal_clean,coupon)

    # "4 adet alımda geçerli" gibi kaynakta açıkça belirtilmiş çoklu-alım fırsatlarını yüzdesiz FIRSAT olarak paylaş.
    if re.search(r'\b\d+\s*adet\s*(?:alımda|alimda)\s*geçerli\b',signal_clean,re.I):
        return send_clean(s,u,t,c,None,source,post_id,signal_clean+' kampanya',coupon)

    try:ts.remember(f'{source}:{post_id}')
    except Exception:pass
    print(f'ATLANDI | {source}:{post_id} | %15+ doğrulanabilir indirim yok')
    return False

ts.send=strict_send

HEAD=dict(ts.HEAD)
HEAD.update({'Cache-Control':'no-cache, no-store, max-age=0','Pragma':'no-cache'})

def fetch_source(item):
    source,channel=item
    url=f'https://t.me/s/{channel}?v={int(time.time()//30)}'
    try:return source,requests.get(url,headers=HEAD,timeout=10)
    except Exception as e:
        print(f'Telegram kaynak hata {source}: {type(e).__name__}: {e}');return source,None

def realtime_main():
    print(f'=== Telegram gerçek-zamanlı tarama | eşik=%15 | yaş={ts.MAX_AGE} dk ===')
    fetched=[]
    with ThreadPoolExecutor(max_workers=len(ts.SOURCES)) as ex:
        for f in as_completed([ex.submit(fetch_source,x) for x in ts.SOURCES.items()]):
            source,r=f.result();blocks=[];newest_age=None
            if r and r.status_code<400:
                now=datetime.now(timezone.utc)
                all_blocks=BeautifulSoup(r.text,'html.parser').select('.tgme_widget_message')
                for b in all_blocks[-12:]:
                    tm=b.select_one('time[datetime]');tx=b.select_one('.tgme_widget_message_text')
                    if not tm or not tx:continue
                    try:dt=datetime.fromisoformat(tm['datetime'].replace('Z','+00:00'))
                    except Exception:continue
                    age=(now-dt).total_seconds()/60
                    newest_age=age if newest_age is None else min(newest_age,age)
                    if -5<=age<=ts.MAX_AGE:blocks.append(b)
            print(f'Telegram kaynak {source}: HTTP {r.status_code if r else "HATA"} aday={len(blocks)} en_yeni_yas={newest_age if newest_age is not None else "?"}')
            fetched.append((source,blocks))

    total=sum(len(x[1]) for x in fetched);sent=0
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True);page=browser.new_page()
        for source,blocks in fetched:
            for b in blocks:
                post_id=b.get('data-post','').split('/')[-1]
                if not post_id:continue
                try:
                    if ts.seen(f'{source}:{post_id}'):continue
                except Exception:pass
                try:
                    if ts.process(source,b,page):sent+=1
                except Exception as e:print(f'ADAY HATA | {source}:{post_id}: {type(e).__name__}: {e}')
        browser.close()
    print(f'=== Bitti. Aday={total} Gönderilen={sent} Eşik=%{ts.MIN_DISCOUNT} ===')

realtime_main()
