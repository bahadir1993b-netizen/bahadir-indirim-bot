import os,re,time,requests,html,json
from datetime import datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor,as_completed
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

sb=os.environ.get('SUPABASE_URL','').rstrip('/')
if sb.endswith('/rest/v1'):
    sb=sb[:-8].rstrip('/'); os.environ['SUPABASE_URL']=sb

import telegram_sources as ts
import local_store as ls
import publish_core as pc
from price_reference import market_snapshot
from deal_validation import inspect_page,choose_reference

ts.MIN_DISCOUNT=float(os.environ.get('MIN_DISCOUNT','15'))
ts.MAX_AGE=max(30,int(os.environ.get('TELEGRAM_MAX_AGE','180')))
ts.AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or 'ozelfirsat09-21').strip() or 'ozelfirsat09-21'
API_FREE=str(os.environ.get('SERPER_DISABLED','0')).strip().lower() in {'1','true','yes','on'}
TRUSTED_SOURCES={'OzelFirsatlar','AmazonOzel','FirsatMerkezi'}
TRUSTED_MIN=float(os.environ.get('TRUSTED_TELEGRAM_MIN_DISCOUNT','5'))

def source_threshold(source):return min(ts.MIN_DISCOUNT,TRUSTED_MIN) if source in TRUSTED_SOURCES else ts.MIN_DISCOUNT

def clean_title(raw):
    text=(raw or '').replace('\r','\n');pos=len(text)
    for pat in [r'\s[💰🏷️]\s*',r'\s(?:\d[\d.,]*)\s*(?:TL|₺)\b',r'\s(?:sepette|kampanya|kupon|kod(?:u)?|fırsata git|firsata git)\b']:
        m=re.search(pat,text,re.I)
        if m:pos=min(pos,m.start())
    text=text[:pos]
    return pc.clean_title(text)
ts.extract_title=clean_title

def source_photo(source,post_id,product_url=None,page_image=None):
    """Never copy the source Telegram channel image; use the sales page only."""
    if page_image and str(page_image).startswith('http'):return page_image
    if product_url:
        meta=pc.resolve_meta(product_url,ts.site(product_url) or '', '',timeout=7)
        if meta.get('image'):return meta['image']
    return None

def fmt(x):return f'{x:,.2f}'.replace(',','X').replace('.',',').replace('X','.')

def own_history(url,current):
    try:
        since=(datetime.now(timezone.utc)-timedelta(days=90)).isoformat();rows=ts.sb('GET','price_history',params={'select':'price,recorded_at','product_url':f'eq.{url}','recorded_at':f'gte.{since}','order':'recorded_at.desc','limit':'100'});vals=[]
        for x in rows:
            try:p=float(x.get('price'))
            except:p=0
            if p and current*.75<=p<=current*1.55:vals.append(p)
        return vals
    except Exception as e:print(f'HISTORY UYARI | {type(e).__name__}');return []

def product_recently_posted(url,current):
    if ls.recently_published(url,current,days=30,min_drop=.05):return True
    key=ls.publication_key(url)
    try:
        since=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat();rows=ts.sb('GET','price_history',params={'select':'price,product_url,recorded_at','recorded_at':f'gte.{since}','order':'recorded_at.desc','limit':'500'})
        for r in rows:
            ru=r.get('product_url') or ''
            if ru.startswith('telegram://') or ls.publication_key(ru)!=key:continue
            try:old=float(r.get('price') or 0)
            except:old=0
            if old and current>=old*.95:return True
    except Exception as e:print(f'DUPLICATE UYARI | {type(e).__name__}')
    return False

def send_clean(s,u,t,c,p,source,post_id,signal,coupon=None,campaign=None,page_image=None,ref_source=''):
    if not ts.valid(s,u):print(f'ATLANDI | {source}:{post_id} | geçersiz link');return False
    key=f'{source}:{post_id}'
    if ts.seen(key):return False
    disc=(p-c)/p*100 if p and p>c else None;threshold=source_threshold(source)
    if disc is not None and disc<threshold:print(f'ATLANDI | {source}:{post_id} | %{disc:.1f} < %{threshold}');ts.remember(key);return False
    if product_recently_posted(u,c):print(f'ATLANDI | {source}:{post_id} | duplicate-product-30d');ts.remember(key);return False

    meta=pc.resolve_meta(u,s,t,timeout=7);title=pc.clean_title(meta.get('title') or t);image=page_image or meta.get('image')
    if not title:
        print(f'ATLANDI | {source}:{post_id} | ürün_adı_alınamadı');return False
    out_url=pc.affiliate_url(meta.get('resolved_url') or u)
    if s=='Amazon' and not pc.affiliate_ok(out_url):raise RuntimeError('Amazon affiliate tag missing at publish boundary')

    row=ts.save(s,u,title,c,p);last=row.get('last_posted_at') if isinstance(row,dict) else None
    if last:
        try:
            if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=ts.COOLDOWN):print(f'ATLANDI | {source}:{post_id} | cooldown');ts.remember(key);return False
        except Exception:pass
    lines=[f'🔥 %{disc:.0f} İNDİRİM' if disc is not None else ('🎟️ KUPONLU FIRSAT' if coupon else '🔥 FIRSAT'),' ',f'🛍️ {html.escape(title)}',f'💰 {fmt(c)} TL']
    if p and p>c:lines.append(f'🏷️ {"Normal fiyat" if campaign else "Referans fiyat"}: {fmt(p)} TL')
    if campaign:
        lines.append(f'🎯 Kampanya: {html.escape(campaign.get("label") or "Kampanyalı alım")}')
        if campaign.get('qty'):lines.append(f'📦 {campaign["qty"]} adet alımda geçerli')
    if coupon:lines.append(f'🎟️ Kupon: {html.escape(coupon)}')
    lines += ['','👇 Fırsata git'];text='\n'.join(lines);keyboard={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':out_url}]]};rr=None;photo_state='yok'
    photo=source_photo(source,post_id,u,image)
    if photo:
        try:
            img=requests.get(photo,headers=ts.HEAD,timeout=12,allow_redirects=True);ctype=(img.headers.get('content-type') or '').lower()
            if img.ok and img.content and ('image/' in ctype or len(img.content)>5000):
                ext='png' if 'png' in ctype else 'webp' if 'webp' in ctype else 'jpg';rr=requests.post('https://api.telegram.org/bot'+ts.TOKEN+'/sendPhoto',data={'chat_id':ts.CHAT,'caption':text[:1024],'parse_mode':'HTML','reply_markup':json.dumps(keyboard,ensure_ascii=False)},files={'photo':(f'product.{ext}',img.content,ctype or 'image/jpeg')},timeout=25);photo_state='gönderildi' if rr.ok else f'telegram-hata-{rr.status_code}'
        except Exception as e:photo_state=f'indirme-hata-{type(e).__name__}';rr=None
    if not rr or not rr.ok:rr=requests.post('https://api.telegram.org/bot'+ts.TOKEN+'/sendMessage',json={'chat_id':ts.CHAT,'text':text,'parse_mode':'HTML','disable_web_page_preview':True,'link_preview_options':{'is_disabled':True},'reply_markup':keyboard},timeout=18)
    rr.raise_for_status();ls.mark_published(u,c,'telegram-realtime')
    if isinstance(row,dict) and row.get('id'):ts.sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat(),'last_posted_price':c})
    try:ts.sb('POST','price_history',json={'price':c,'product_url':ls.canonical(u),'site':s,'recorded_at':datetime.now(timezone.utc).isoformat()})
    except Exception as e:print(f'PRICE HISTORY UYARI | {type(e).__name__}')
    ts.remember(key);print(f'GÖNDERİLDİ | {s} | {c:.2f} TL | ref={p or 0:.2f} | kaynak={ref_source} | kampanya={campaign.get("label") if campaign else "yok"} | foto={photo_state} | affiliate={"ok" if s!="Amazon" or pc.affiliate_ok(out_url) else "HATA"}');return True

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
    _,paid,eff=best;return {'label':f'{qty} al {paid} öde','qty':qty,'effective':source_price,'verified_effective':eff}

def strict_send(s,u,t,c,p,source,post_id,signal,coupon=None):
    key=f'{source}:{post_id}'
    if ts.seen(key):return False
    t=clean_title(t);signal_clean=re.sub(r'@[A-Za-z0-9_]+',' ',signal or '');signal_clean=re.sub(r'(?i)\bsohbet grubumuz(?: için)?\b.*',' ',signal_clean);signal_clean=re.sub(r'(?i)\b(?:kanalımıza katıl|takip et|reklam)\b.*',' ',signal_clean)
    pg=inspect_page(u,c)
    if pg.get('available') is False:print(f'ATLANDI | {source}:{post_id} | stokta yok');ts.remember(key);return False
    if pg.get('title'):t=clean_title(pg['title'])
    campaign=pg.get('campaign');live=pg.get('live')
    if not live:
        try:
            price_now,price_old=ts.marketplace_price_check(s,u,c)
            if price_now:live=price_now
            if price_old and not pg.get('old'):pg['old']=price_old
        except Exception as e:print(f'PRICE CHECK UYARI | {type(e).__name__}')
    inferred=_infer_quantity_campaign(signal_clean,c,live)
    if inferred:current=c;campaign=inferred;page_ref=live;ref_source='source-qty-verified'
    else:
        if campaign and campaign.get('effective'):
            eff=float(campaign['effective']);current=eff if abs(c-eff)/max(eff,1)<=0.08 else (live or c)
        elif live:current=live if abs(live-c)/max(live,1)>0.03 else c
        else:current=c
        page_ref=(live if campaign and live and live>current else pg.get('old'));ref_source=None
    hist=own_history(u,current)
    if API_FREE:floor=med=None;msrc='api-free'
    else:
        try:floor,med,_,msrc=market_snapshot(t)
        except Exception:floor=med=None;msrc='market-error'
    ref,chosen_source=choose_reference(current,history=hist,source=p,page=page_ref,market_median=med,market_floor=floor)
    if ref_source is None:ref_source=chosen_source
    if source in TRUSTED_SOURCES and (not ref or ref<=current) and p and current*1.05<p<=current*1.45:ref=float(p);ref_source='trusted-source-ref'
    print(f'DOĞRULAMA | {source}:{post_id} | kaynak_fiyat={c:.2f} canlı={live or 0:.2f} efektif={campaign.get("effective") if campaign else 0} geçmiş={len(hist)} piyasa={med or 0:.2f} kaynak_ref={p or 0:.2f} seçilen_ref={ref or 0:.2f} | {ref_source}/{msrc}')
    if not ref or ref<=current:
        m=re.search(r'\b(\d+)\s*al\s*(\d+)\s*(?:öde|ode)\b',signal_clean,re.I)
        if m and live:
            buy,paid=int(m.group(1)),int(m.group(2))
            if buy>paid>0:current=live*paid/buy;ref=live;campaign={'label':f'{buy} al {paid} öde','qty':buy,'effective':current};ref_source='page-campaign'
    if inferred and live and (not ref or ref<=current):ref=live;ref_source='source-qty-verified'
    if not ref or ref<=current:print(f'ATLANDI | {source}:{post_id} | güvenilir referans yok');ts.remember(key);return False
    disc=(ref-current)/ref*100;threshold=source_threshold(source)
    if disc<threshold:print(f'ATLANDI | {source}:{post_id} | doğrulanmış indirim %{disc:.1f} < %{threshold}');ts.remember(key);return False
    return send_clean(s,u,t,current,ref,source,post_id,signal_clean,coupon,campaign,pg.get('image'),ref_source)
ts.send=strict_send

HEAD=dict(ts.HEAD);HEAD.update({'Cache-Control':'no-cache, no-store, max-age=0','Pragma':'no-cache'})
def fetch_source(item):
    source,channel=item;url=f'https://t.me/s/{channel}?v={int(time.time()//30)}'
    for attempt in range(3):
        try:
            r=requests.get(url,headers=HEAD,timeout=(4,10))
            if attempt:print(f'TELEGRAM KAYNAK KURTARILDI | {source} | deneme={attempt+1}')
            return source,r
        except Exception as e:
            if attempt==2:print(f'Telegram kaynak hata {source}: {type(e).__name__}: {e}')
            else:time.sleep(.4*(attempt+1))
    return source,None

def realtime_main():
    ls.runtime_start('telegram-realtime');errors=0;fetched=[];sent=0
    try:
        print(f'=== Telegram gerçek-zamanlı tarama | eşik=%{ts.MIN_DISCOUNT:g} | öncelikli={TRUSTED_SOURCES}:%{TRUSTED_MIN:g} | yaş={ts.MAX_AGE} dk | API={"YOK" if API_FREE else "VAR"} ===')
        with ThreadPoolExecutor(max_workers=len(ts.SOURCES)) as ex:
            for f in as_completed([ex.submit(fetch_source,x) for x in ts.SOURCES.items()]):
                source,r=f.result();blocks=[];newest_age=None
                if r and r.status_code<400:
                    now=datetime.now(timezone.utc);all_blocks=BeautifulSoup(r.text,'html.parser').select('.tgme_widget_message')
                    for b in all_blocks[-20:]:
                        tm=b.select_one('time[datetime]');tx=b.select_one('.tgme_widget_message_text')
                        if not tm or not tx:continue
                        try:dt=datetime.fromisoformat(tm['datetime'].replace('Z','+00:00'))
                        except Exception:continue
                        age=(now-dt).total_seconds()/60;newest_age=age if newest_age is None else min(newest_age,age)
                        if -5<=age<=ts.MAX_AGE:blocks.append(b)
                else:errors+=1
                print(f'Telegram kaynak {source}: HTTP {r.status_code if r else "HATA"} aday={len(blocks)} en_yeni_yas={newest_age if newest_age is not None else "?"}');fetched.append((source,blocks))
        fetched.sort(key=lambda x:(0 if x[0] in TRUSTED_SOURCES else 1,x[0]));total=sum(len(x[1]) for x in fetched)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True);page=browser.new_page()
            for source,blocks in fetched:
                for b in blocks:
                    post_id=b.get('data-post','').split('/')[-1]
                    if not post_id:continue
                    try:
                        if ts.seen(f'{source}:{post_id}'):continue
                        if ts.process(source,b,page):sent+=1
                    except Exception as e:errors+=1;print(f'ADAY HATA | {source}:{post_id}: {type(e).__name__}: {e}')
            browser.close()
        ls.runtime_finish('telegram-realtime','ok' if errors==0 else 'warning',candidates=total,checked=total,sent=sent,errors=errors,details={'trusted':list(TRUSTED_SOURCES),'affiliate':pc.AMAZON_TAG})
        print(f'=== Bitti. Aday={total} Gönderilen={sent} Hata={errors} Eşik=%{ts.MIN_DISCOUNT} Öncelikli=%{TRUSTED_MIN} ===')
    except Exception as e:
        ls.runtime_finish('telegram-realtime','error',candidates=sum(len(x[1]) for x in fetched),checked=0,sent=sent,errors=errors+1,details={'error':type(e).__name__});raise
realtime_main()
