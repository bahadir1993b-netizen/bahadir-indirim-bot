import os,re,time,requests,html,json,sqlite3
from datetime import datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor,as_completed
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from urllib.parse import urlparse

sb=os.environ.get('SUPABASE_URL','').rstrip('/')
if sb.endswith('/rest/v1'):
    sb=sb[:-8].rstrip('/'); os.environ['SUPABASE_URL']=sb

import telegram_sources as ts
from price_reference import market_snapshot
from deal_validation import inspect_page,choose_reference

ts.MIN_DISCOUNT=float(os.environ.get('MIN_DISCOUNT','15'))
ts.MAX_AGE=max(30,int(os.environ.get('TELEGRAM_MAX_AGE','180')))
ts.AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or '').strip()
API_FREE=str(os.environ.get('SERPER_DISABLED','0')).strip().lower() in {'1','true','yes','on'}
LOCAL_DB=os.environ.get('LOCAL_PRICE_DB','/app/data/price_memory.db')


def clean_title(raw):
    text=(raw or '').replace('\r','\n')
    text=re.sub(r'(?i)^.*?(?:30|90|365)\s*günün en düşük fiyatı\s*','',text)
    text=re.sub(r'(?i)^.*?fırsatın fiyatı\s*:\s*[\d.,]+\s*(?:TL|₺)\s*','',text)
    pos=len(text)
    for pat in [r'\s[💰🏷️]\s*',r'\s(?:\d[\d.,]*)\s*(?:TL|₺)\b',r'\s(?:sepette|kampanya|kupon|kod(?:u)?|fırsata git|firsata git)\b']:
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


def _photo_from_html(body):
    soup=BeautifulSoup(body,'html.parser')
    for el in soup.select('.tgme_widget_message_photo_wrap,.tgme_widget_message_photo'):
        m=re.search(r"background-image\s*:\s*url\(['\"]?([^'\")]+)",html.unescape(el.get('style') or ''),re.I)
        if m and m.group(1).startswith('http'):return m.group(1)
    for sel,attr in [('meta[property="og:image"]','content'),('meta[name="twitter:image"]','content'),('img[itemprop="image"]','src'),('.tgme_widget_message_photo img','src')]:
        el=soup.select_one(sel)
        if el:
            u=html.unescape(el.get(attr) or '')
            if u.startswith('http'):return u
    return None


def source_photo(source,post_id,product_url=None,page_image=None):
    if page_image:return page_image
    channel=ts.SOURCES.get(source);urls=[]
    if channel and post_id:urls += [f'https://t.me/{channel}/{post_id}?embed=1&mode=tme',f'https://t.me/s/{channel}/{post_id}']
    if product_url:urls.append(product_url)
    for url in urls:
        try:
            r=requests.get(url,headers=ts.HEAD,timeout=9,allow_redirects=True)
            if r.ok:
                p=_photo_from_html(r.text)
                if p:return p
        except Exception:pass
    return None


def fmt(x):return f'{x:,.2f}'.replace(',','X').replace('.',',').replace('X','.')


def own_history(url,current):
    try:
        since=(datetime.now(timezone.utc)-timedelta(days=90)).isoformat()
        rows=ts.sb('GET','price_history',params={'select':'price,recorded_at','product_url':f'eq.{url}','recorded_at':f'gte.{since}','order':'recorded_at.desc','limit':'100'})
        vals=[]
        for x in rows:
            try:p=float(x.get('price'))
            except:p=0
            if p and current*.75<=p<=current*1.55:vals.append(p)
        return vals
    except Exception:return []


def _canonical_product_key(url,title=''):
    u=(url or '').strip()
    try:
        if 'amzn.to/' in u or 'link.amazon/' in u:
            r=requests.get(u,headers=ts.HEAD,timeout=6,allow_redirects=True,stream=True);u=r.url or u;r.close()
    except Exception:pass
    m=re.search(r'(?i)/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)',u)
    if m:return 'amazon:'+m.group(1).upper()
    try:
        p=urlparse(u);path=re.sub(r'/+$','',p.path or '/')
        if p.netloc and path:return p.netloc.lower()+path.lower()
    except Exception:pass
    return 'title:'+re.sub(r'[^a-z0-9çğıöşü]+',' ',(title or '').lower()).strip()[:120]


def _dedupe_conn():
    c=sqlite3.connect(LOCAL_DB,timeout=20)
    c.execute('CREATE TABLE IF NOT EXISTS realtime_product_alerts(product_key TEXT PRIMARY KEY, price REAL, alerted_at TEXT)')
    return c


def recently_sent_product(url,title,price):
    k=_canonical_product_key(url,title);c=_dedupe_conn();r=c.execute('SELECT price,alerted_at FROM realtime_product_alerts WHERE product_key=?',(k,)).fetchone();c.close()
    if not r:return False
    try:age=datetime.now(timezone.utc)-datetime.fromisoformat(str(r[1]).replace('Z','+00:00'));old=float(r[0])
    except Exception:return False
    return age<timedelta(days=30) and float(price)>=old*.95


def mark_product_sent(url,title,price):
    k=_canonical_product_key(url,title);c=_dedupe_conn();c.execute('INSERT INTO realtime_product_alerts(product_key,price,alerted_at) VALUES(?,?,?) ON CONFLICT(product_key) DO UPDATE SET price=excluded.price,alerted_at=excluded.alerted_at',(k,float(price),datetime.now(timezone.utc).isoformat()));c.commit();c.close()


def _send_photo(photo,text,keyboard):
    if not photo:return None
    endpoint='https://api.telegram.org/bot'+ts.TOKEN+'/sendPhoto'
    data={'chat_id':ts.CHAT,'caption':text[:1024],'parse_mode':'HTML','reply_markup':json.dumps(keyboard,ensure_ascii=False)}
    try:
        img=requests.get(photo,headers=ts.HEAD,timeout=12,allow_redirects=True)
        if img.ok and img.content and len(img.content)>1000:
            ct=(img.headers.get('content-type') or 'image/jpeg').split(';')[0]
            ext='png' if 'png' in ct else 'webp' if 'webp' in ct else 'jpg'
            rr=requests.post(endpoint,data=data,files={'photo':(f'product.{ext}',img.content,ct)},timeout=25)
            if rr.ok:return rr
    except Exception:pass
    try:
        rr=requests.post(endpoint,data={**data,'photo':photo},timeout=20)
        if rr.ok:return rr
    except Exception:pass
    return None


def send_clean(s,u,t,c,p,source,post_id,signal,coupon=None,campaign=None,page_image=None,ref_source=''):
    if not ts.valid(s,u):print(f'ATLANDI | {source}:{post_id} | geçersiz link');return False
    key=f'{source}:{post_id}'
    if ts.seen(key):return False
    title=clean_title(t)
    if recently_sent_product(u,title,c):
        print(f'ATLANDI | {source}:{post_id} | aynı ürün 30 günlük tekrar koruması');ts.remember(key);return False
    disc=(p-c)/p*100 if p and p>c else None
    if disc is not None and disc<ts.MIN_DISCOUNT:
        print(f'ATLANDI | {source}:{post_id} | %{disc:.1f} < %{ts.MIN_DISCOUNT}');ts.remember(key);return False
    row=ts.save(s,u,title,c,p);last=row.get('last_posted_at') if isinstance(row,dict) else None
    if last:
        try:
            if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=ts.COOLDOWN):
                print(f'ATLANDI | {source}:{post_id} | cooldown');ts.remember(key);return False
        except Exception:pass
    lines=[f'🔥 %{disc:.0f} İNDİRİM' if disc is not None else ('🎟️ KUPONLU FIRSAT' if coupon else '🔥 FIRSAT'),' ',f'🛍️ {html.escape(title)}',f'💰 {fmt(c)} TL']
    if p and p>c:
        lines.append(f'🏷️ {"Normal fiyat" if campaign else "Referans fiyat"}: {fmt(p)} TL')
    if campaign:
        lines.append(f'🎯 Kampanya: {html.escape(campaign.get("label") or "Kampanyalı alım")}')
        if campaign.get('qty'):lines.append(f'📦 {campaign["qty"]} adet alımda geçerli')
    if coupon:lines.append(f'🎟️ Kupon: {html.escape(coupon)}')
    lines += ['',f'👇 <a href="{html.escape(u,quote=True)}"><b>Fırsata git</b></a>']
    text='\n'.join(lines);keyboard={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}
    photo=source_photo(source,post_id,u,page_image)
    rr=_send_photo(photo,text,keyboard)
    if not rr:
        rr=requests.post('https://api.telegram.org/bot'+ts.TOKEN+'/sendMessage',json={'chat_id':ts.CHAT,'text':text,'parse_mode':'HTML','disable_web_page_preview':True,'link_preview_options':{'is_disabled':True},'reply_markup':keyboard},timeout=18)
    rr.raise_for_status();mark_product_sent(u,title,c)
    if isinstance(row,dict) and row.get('id'):ts.sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat()})
    ts.remember(key);print(f'GÖNDERİLDİ | {s} | {c:.2f} TL | ref={p or 0:.2f} | kaynak={ref_source} | kampanya={campaign.get("label") if campaign else "yok"} | foto={"var" if photo else "yok"}');return True


def _infer_quantity_campaign(signal,source_price,live):
    if not live or not source_price or source_price>=live*.97:return None
    m=re.search(r'\b(\d+)\s*(?:adet\s*)?(?:alımda|alimda)(?:\s+geçerli|\s+gecerli)?\b',signal or '',re.I) or re.search(r'\b(\d+)\s*adet\b',signal or '',re.I)
    if not m:return None
    qty=int(m.group(1))
    if qty<2 or qty>10:return None
    best=None
    for paid in range(1,qty):
        eff=live*paid/qty;err=abs(source_price-eff)/max(eff,1)
        if err<=.10 and (best is None or err<best[0]):best=(err,paid,eff)
    if not best:return None
    _,paid,eff=best
    return {'label':f'{qty} al {paid} öde','qty':qty,'effective':source_price,'verified_effective':eff}


def strict_send(s,u,t,c,p,source,post_id,signal,coupon=None):
    key=f'{source}:{post_id}'
    if ts.seen(key):return False
    t=clean_title(t);signal_clean=re.sub(r'@[A-Za-z0-9_]+',' ',signal or '')
    signal_clean=re.sub(r'(?i)\bsohbet grubumuz(?: için)?\b.*',' ',signal_clean)
    signal_clean=re.sub(r'(?i)\b(?:kanalımıza katıl|takip et|reklam)\b.*',' ',signal_clean)
    pg=inspect_page(u,c)
    if pg.get('available') is False:
        print(f'ATLANDI | {source}:{post_id} | stokta yok');ts.remember(key);return False
    page_title=clean_title(pg.get('title') or '')
    if page_title!='Fırsat Ürünü' and len(page_title)>=8:t=page_title
    campaign=pg.get('campaign');live=pg.get('live')
    if not live:
        try:
            pc,po=ts.marketplace_price_check(s,u,c)
            if pc:live=pc
            if po and not pg.get('old'):pg['old']=po
        except Exception:pass
    inferred=_infer_quantity_campaign(signal_clean,c,live)
    if inferred:
        current=c;campaign=inferred;page_ref=live;ref_source='source-qty-verified'
    else:
        if campaign and campaign.get('effective'):
            eff=float(campaign['effective']);current=eff if abs(c-eff)/max(eff,1)<=0.08 else (live or c)
        elif live:current=live if abs(live-c)/max(live,1)>0.03 else c
        else:current=c
        page_ref=(live if campaign and live and live>current else pg.get('old'));ref_source=None
    hist=own_history(u,current)
    if API_FREE:floor=med=None;n=0;msrc='api-free'
    else:
        try:floor,med,n,msrc=market_snapshot(t)
        except Exception:floor=med=None;n=0;msrc='market-error'
    ref,chosen_source=choose_reference(current,history=hist,source=p,page=page_ref,market_median=med,market_floor=floor)
    if ref_source is None:ref_source=chosen_source
    print(f'DOĞRULAMA | {source}:{post_id} | kaynak_fiyat={c:.2f} canlı={live or 0:.2f} efektif={campaign.get("effective") if campaign else 0} geçmiş={len(hist)} piyasa={med or 0:.2f} kaynak_ref={p or 0:.2f} seçilen_ref={ref or 0:.2f} | {ref_source}/{msrc}')
    if not ref or ref<=current:
        m=re.search(r'\b(\d+)\s*al\s*(\d+)\s*(?:öde|ode)\b',signal_clean,re.I)
        if m and live:
            buy,paid=int(m.group(1)),int(m.group(2))
            if buy>paid>0:current=live*paid/buy;ref=live;campaign={'label':f'{buy} al {paid} öde','qty':buy,'effective':current};ref_source='page-campaign'
    if inferred and live and (not ref or ref<=current):ref=live;ref_source='source-qty-verified'
    if not ref or ref<=current:
        print(f'ATLANDI | {source}:{post_id} | güvenilir referans yok');ts.remember(key);return False
    disc=(ref-current)/ref*100
    if disc<ts.MIN_DISCOUNT:
        print(f'ATLANDI | {source}:{post_id} | doğrulanmış indirim %{disc:.1f}');ts.remember(key);return False
    return send_clean(s,u,t,current,ref,source,post_id,signal_clean,coupon,campaign,pg.get('image'),ref_source)
ts.send=strict_send

HEAD=dict(ts.HEAD);HEAD.update({'Cache-Control':'no-cache, no-store, max-age=0','Pragma':'no-cache'})
def fetch_source(item):
    source,channel=item;url=f'https://t.me/s/{channel}?v={int(time.time()//30)}'
    try:return source,requests.get(url,headers=HEAD,timeout=10)
    except Exception as e:print(f'Telegram kaynak hata {source}: {type(e).__name__}: {e}');return source,None

def realtime_main():
    print(f'=== Telegram gerçek-zamanlı tarama | eşik=%{ts.MIN_DISCOUNT:g} | yaş={ts.MAX_AGE} dk | API={"YOK" if API_FREE else "VAR"} ===');fetched=[]
    with ThreadPoolExecutor(max_workers=len(ts.SOURCES)) as ex:
        for f in as_completed([ex.submit(fetch_source,x) for x in ts.SOURCES.items()]):
            source,r=f.result();blocks=[];newest_age=None
            if r and r.status_code<400:
                now=datetime.now(timezone.utc);all_blocks=BeautifulSoup(r.text,'html.parser').select('.tgme_widget_message')
                for b in all_blocks[-15:]:
                    tm=b.select_one('time[datetime]');tx=b.select_one('.tgme_widget_message_text')
                    if not tm or not tx:continue
                    try:dt=datetime.fromisoformat(tm['datetime'].replace('Z','+00:00'))
                    except Exception:continue
                    age=(now-dt).total_seconds()/60;newest_age=age if newest_age is None else min(newest_age,age)
                    if -5<=age<=ts.MAX_AGE:blocks.append(b)
            print(f'Telegram kaynak {source}: HTTP {r.status_code if r else "HATA"} aday={len(blocks)} en_yeni_yas={newest_age if newest_age is not None else "?"}');fetched.append((source,blocks))
    total=sum(len(x[1]) for x in fetched);sent=0
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True);page=browser.new_page()
        for source,blocks in fetched:
            for b in blocks:
                post_id=b.get('data-post','').split('/')[-1]
                if not post_id:continue
                try:
                    if ts.seen(f'{source}:{post_id}'):continue
                    if ts.process(source,b,page):sent+=1
                except Exception as e:print(f'ADAY HATA | {source}:{post_id}: {type(e).__name__}: {e}')
        browser.close()
    print(f'=== Bitti. Aday={total} Gönderilen={sent} Eşik=%{ts.MIN_DISCOUNT} ===')
realtime_main()
