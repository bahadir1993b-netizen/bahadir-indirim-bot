import os,re,time,requests
from datetime import datetime,timezone
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
# Telegram public web bazen birkaç dakika cache'li geliyor. 180 dk pencere kullanıp post-id ile tekrarları engelliyoruz.
ts.MAX_AGE=max(30,int(os.environ.get('TELEGRAM_MAX_AGE','180')))
ts.AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or '').strip()

_original_send=ts.send

# Kaynak kanalın reklam / kullanıcı adı / sohbet grubu metnini başlığa asla taşıma.
def clean_title(raw):
    text=(raw or '').replace('\r','\n')
    # Fiyat ve kampanya kısmından sonrasını başlığa alma.
    cut_patterns=[r'\s[💰🏷️]\s*',r'\s(?:\d[\d.,]*)\s*(?:TL|₺)\b',r'\s(?:sepette|kampanya|kupon|kod(?:u)?|fırsata git|firsata git)\b']
    pos=len(text)
    for pat in cut_patterns:
        m=re.search(pat,text,re.I)
        if m: pos=min(pos,m.start())
    text=text[:pos]
    text=re.sub(r'@[A-Za-z0-9_]+',' ',text)
    text=re.sub(r'#(?:tanıtım|tanitim|reklam)\b',' ',text,flags=re.I)
    text=re.sub(r'(?i)\b(?:sohbet grubumuz(?: için)?|kanalımıza katıl|kanalımıza katilin|takip et|reklam)\b.*$',' ',text)
    text=re.sub(r'[🔥✅🔻🔗👉👇📣🛍️🎯🎁]+',' ',text)
    text=re.sub(r'\s+',' ',text).strip(' -|•')
    return (text[:180] if len(text)>=4 else 'Fırsat Ürünü')

ts.extract_title=clean_title

def strict_send(s,u,t,c,p,source,post_id,signal,coupon=None):
    # Son güvenlik temizliği: dış kanal adı / reklam ifadesi hiçbir koşulda kullanıcıya gitmesin.
    t=clean_title(t)
    signal_clean=re.sub(r'@[A-Za-z0-9_]+',' ',signal or '')
    signal_clean=re.sub(r'(?i)\bsohbet grubumuz(?: için)?\b.*',' ',signal_clean)

    if p and p>c:
        disc=(p-c)/p*100
        if disc>=ts.MIN_DISCOUNT:
            return _original_send(s,u,t,c,p,source,post_id,signal_clean,coupon)
        try: ts.remember(f'{source}:{post_id}')
        except Exception: pass
        print(f'ATLANDI | {source}:{post_id} | kesin indirim %{disc:.1f} < %{ts.MIN_DISCOUNT}')
        return False

    # 3 al 2 öde / 2 al 1 öde: oran matematiksel olarak kesin.
    m=re.search(r'\b(\d+)\s*al\s*(\d+)\s*(?:öde|ode)\b',signal_clean,re.I)
    if m:
        buy,paid=int(m.group(1)),int(m.group(2))
        if buy>paid>0:
            disc=(buy-paid)/buy*100
            if disc>=ts.MIN_DISCOUNT:
                effective=c*(paid/buy)
                return _original_send(s,u,t,effective,c,source,post_id,signal_clean,coupon)

    # "4 adet alımda geçerli" gibi güvenilir kaynaklı çoklu-alım fırsatlarını yüzde uydurmadan FIRSAT olarak paylaş.
    # Böylece Copier Bond örneği gibi fırsatlar kaçmaz; mesajda sahte "önceki fiyat" gösterilmez.
    if re.search(r'\b\d+\s*adet\s*(?:alımda|alimda)\s*geçerli\b',signal_clean,re.I):
        return _original_send(s,u,t,c,None,source,post_id,signal_clean+' kampanya',coupon)

    try: ts.remember(f'{source}:{post_id}')
    except Exception: pass
    print(f'ATLANDI | {source}:{post_id} | %15+ doğrulanabilir indirim yok')
    return False

ts.send=strict_send

HEAD=dict(ts.HEAD)
HEAD.update({'Cache-Control':'no-cache, no-store, max-age=0','Pragma':'no-cache'})

def fetch_source(item):
    source,channel=item
    # Cache-busting query: Frankfurt VPS'in eski Telegram HTML'i görme ihtimalini azaltır.
    url=f'https://t.me/s/{channel}?v={int(time.time()//30)}'
    try:
        r=requests.get(url,headers=HEAD,timeout=10)
        return source,r
    except Exception as e:
        print(f'Telegram kaynak hata {source}: {type(e).__name__}: {e}')
        return source,None

def realtime_main():
    print(f'=== Telegram gerçek-zamanlı tarama | eşik=%15 | yaş={ts.MAX_AGE} dk ===')
    fetched=[]
    with ThreadPoolExecutor(max_workers=len(ts.SOURCES)) as ex:
        for f in as_completed([ex.submit(fetch_source,x) for x in ts.SOURCES.items()]):
            source,r=f.result(); blocks=[]; newest_age=None
            if r and r.status_code<400:
                now=datetime.now(timezone.utc)
                all_blocks=BeautifulSoup(r.text,'html.parser').select('.tgme_widget_message')
                # Son 12 gönderiyi incelemek timestamp/cache sorunlarında da yeni postu kaçırmamamızı sağlar.
                for b in all_blocks[-12:]:
                    tm=b.select_one('time[datetime]'); tx=b.select_one('.tgme_widget_message_text')
                    if not tm or not tx: continue
                    try: dt=datetime.fromisoformat(tm['datetime'].replace('Z','+00:00'))
                    except Exception: continue
                    age=(now-dt).total_seconds()/60
                    newest_age=age if newest_age is None else min(newest_age,age)
                    if -5<=age<=ts.MAX_AGE: blocks.append(b)
            print(f'Telegram kaynak {source}: HTTP {r.status_code if r else "HATA"} aday={len(blocks)} en_yeni_yas={newest_age if newest_age is not None else "?"}')
            fetched.append((source,blocks))

    total=sum(len(x[1]) for x in fetched); sent=0
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True); page=browser.new_page()
        for source,blocks in fetched:
            for b in blocks:
                post_id=b.get('data-post','').split('/')[-1]
                if not post_id: continue
                try:
                    if ts.seen(f'{source}:{post_id}'):
                        continue
                except Exception:
                    pass
                try:
                    if ts.process(source,b,page): sent+=1
                except Exception as e:
                    print(f'ADAY HATA | {source}:{post_id}: {type(e).__name__}: {e}')
        browser.close()
    print(f'=== Bitti. Aday={total} Gönderilen={sent} Eşik=%{ts.MIN_DISCOUNT} ===')

realtime_main()
