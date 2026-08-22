import os,re,html,json,time,requests
from datetime import datetime,timezone,timedelta
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import telegram_sources as ts
from deal_validation import inspect_page

TRUSTED={'OzelFirsatlar','AmazonOzel','FirsatMerkezi'}
MAX_AGE=max(3,int(os.environ.get('FAST_LANE_MAX_AGE','60')))
MIN_DISC=max(3.0,float(os.environ.get('FAST_LANE_MIN_DISCOUNT','5')))
HEAD=dict(ts.HEAD);HEAD.update({'Cache-Control':'no-cache, no-store, max-age=0','Pragma':'no-cache'})

def fmt(x):return f'{x:,.2f}'.replace(',','X').replace('.',',').replace('X','.')
def fast_key(source,post_id):return f'fast:{source}:{post_id}'
def clean_title(raw):
    s=re.sub(r'@[A-Za-z0-9_]+|#(?:tanıtım|tanitim|reklam)',' ',raw or '',flags=re.I)
    s=re.sub(r'(?i)\b(?:sohbet grubumuz|kanalımıza katıl|takip et|reklam)\b.*$',' ',s)
    s=re.sub(r'[🔥✅🔻🔗👉👇📣🎯🎁]+',' ',s)
    s=re.sub(r'\s+',' ',s).strip(' -|•')
    for pat in [r'\s\d[\d.,]*\s*(?:TL|₺)\b',r'\s(?:fırsata git|firsata git)\b']:
        m=re.search(pat,s,re.I)
        if m:s=s[:m.start()]
    return s[:170] if len(s)>=5 else 'Fırsat Ürünü'

def source_photo(block):
    for el in block.select('.tgme_widget_message_photo_wrap,.tgme_widget_message_photo'):
        m=re.search(r"background-image\s*:\s*url\(['\"]?([^'\")]+)",html.unescape(el.get('style') or ''),re.I)
        if m:return m.group(1)
    return None

def qty_hint(text):
    for pat in [r'\b(\d+)\s*adet\s*alımda',r'\b(\d+)\s*adet\s*alimda',r'\b(\d+)\s*adet\b']:
        m=re.search(pat,text or '',re.I)
        if m:
            q=int(m.group(1))
            if 2<=q<=10:return q
    return None

def explicit_unit(text):
    return bool(re.search(r'(?i)(?:tanesi|birim|adet(?:\s+başına)?|\d+\s*adet\s*alımda|\d+\s*adet\s*alimda)',text or ''))

def payment_hint(text):
    return bool(re.search(r'(?i)\b(?:ödeme|odeme)\s+(?:adımında|adiminda|anında|aninda)|\bsepette\b|\bkasada\b',text or ''))

def recently_posted(row,current):
    last=row.get('last_posted_at') if isinstance(row,dict) else None
    if not last:return False
    try:
        age=datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))
        old=float(row.get('last_posted_price') or row.get('current_price') or 0)
        return age<timedelta(days=30) and (not old or current>=old*.95)
    except:return False

def send(site,url,title,current,ref,campaign,image,row):
    disc=(ref-current)/ref*100 if ref and ref>current else None
    lines=[f'🔥 %{disc:.0f} İNDİRİM' if disc is not None else '🔥 FIRSAT','',f'🛍️ {html.escape(title)}']
    if campaign:
        lines.append(f'💰 Efektif birim fiyat: {fmt(current)} TL')
        lines.append(f'🎯 Kampanya: {html.escape(campaign)}')
    else:lines.append(f'💰 {fmt(current)} TL')
    if ref and ref>current:lines.append(f'🏷️ Referans fiyat: {fmt(ref)} TL')
    lines += [f'🛍️ {site}','','👇 Fırsata git']
    text='\n'.join(lines);kb={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':url}]]};rr=None
    if image:
        try:
            im=requests.get(image,headers=HEAD,timeout=10,allow_redirects=True)
            ct=(im.headers.get('content-type') or 'image/jpeg').split(';')[0]
            if im.ok and len(im.content)>4000:
                rr=requests.post('https://api.telegram.org/bot'+ts.TOKEN+'/sendPhoto',data={'chat_id':ts.CHAT,'caption':text[:1024],'parse_mode':'HTML','reply_markup':json.dumps(kb,ensure_ascii=False)},files={'photo':('product.jpg',im.content,ct)},timeout=22)
        except Exception:rr=None
    if not rr or not rr.ok:
        rr=requests.post('https://api.telegram.org/bot'+ts.TOKEN+'/sendMessage',json={'chat_id':ts.CHAT,'text':text,'parse_mode':'HTML','disable_web_page_preview':True,'link_preview_options':{'is_disabled':True},'reply_markup':kb},timeout=18)
    rr.raise_for_status()
    if isinstance(row,dict) and row.get('id'):
        ts.sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat(),'last_posted_price':current})
    try:ts.sb('POST','price_history',json={'price':current,'product_url':url,'site':site,'recorded_at':datetime.now(timezone.utc).isoformat()})
    except Exception:pass
    if disc is not None:print(f'FAST GÖNDERİLDİ | {site} | {current:.2f}->{ref:.2f} | %{disc:.1f} | {campaign or "normal"}')
    else:print(f'FAST GÖNDERİLDİ | {site} | {current:.2f} | referans_yok-güvenilir-kampanya | {campaign or "fırsat"}')
    return True

def process(source,b,page):
    post_id=b.get('data-post','').split('/')[-1]
    key=fast_key(source,post_id) if post_id else ''
    if not post_id or ts.seen(key):return False
    tx=b.select_one('.tgme_widget_message_text')
    if not tx:return False
    raw=tx.get_text(' ',strip=True);links=[ts.clean(a.get('href') or '') for a in b.select('a[href]')]
    site=next((ts.site(x) for x in links if ts.site(x)),None) or next((x for x in set(ts.MARKET.values()) if re.search(r'\b'+re.escape(x)+r'\b',raw,re.I)),None)
    cur,old=ts.source_pair(raw)
    if not site or not cur:
        print(f'FAST ATLANDI | {source}:{post_id} | site/fiyat_yok | site={site} fiyat={cur}')
        return False
    title=clean_title(raw)
    u=next((ts.normalize(site,x) for x in links if ts.valid(site,x)),None)
    if not u:u=ts.resolve(page,links[0] if links else '',site,title)
    if not u:
        print(f'FAST ATLANDI | {source}:{post_id} | link_yok');return False
    pg=inspect_page(u,cur)
    if pg.get('available') is False:
        ts.remember(key);print(f'FAST ATLANDI | {source}:{post_id} | stok_yok');return False
    if pg.get('title'):title=clean_title(pg['title'])
    live=pg.get('live');campaign=pg.get('campaign');effective=None;label=None
    if campaign and campaign.get('effective') and live:
        effective=float(campaign['effective']);label=campaign.get('label')
    q=qty_hint(raw)
    if not effective and live and q and explicit_unit(raw) and cur<live*.97 and cur>=live/max(q,2)*.80:
        effective=float(cur);label=f'{q} adet alımda geçerli'
    if not effective:effective=float(cur)
    if payment_hint(raw) and not label:label='Ödeme adımında geçerli'
    refs=[x for x in [live,pg.get('old'),old] if x and float(x)>effective*1.03 and float(x)<=effective*2.2]
    ref=min(map(float,refs)) if refs else None
    if not ref and live and effective<live*.95:ref=float(live)
    # Öncelikli, güvenilir kaynaklardaki ödeme-adımı/sepette fırsatlarını sırf
    # referans fiyat çekilemedi diye kaçırma. Bu durumda yüzde iddiası yapmadan
    # temiz 'FIRSAT' formatında yayınla.
    trusted_payment_rescue = source in TRUSTED and payment_hint(raw)
    if not ref and not trusted_payment_rescue:
        print(f'FAST ATLANDI | {source}:{post_id} | referans_yok | kaynak={cur:.2f} canlı={live or 0:.2f}');return False
    if ref:
        disc=(ref-effective)/ref*100
        if disc<MIN_DISC:
            ts.remember(key);print(f'FAST ATLANDI | {source}:{post_id} | %{disc:.1f}');return False
    row=ts.save(site,u,title,effective,ref)
    if recently_posted(row,effective):
        ts.remember(key);print(f'FAST ATLANDI | {source}:{post_id} | duplicate-30d');return False
    photo=source_photo(b) or pg.get('image')
    ok=send(site,u,title,effective,ref,label,photo,row)
    ts.remember(key)
    return ok

def main():
    fetched=[];now=datetime.now(timezone.utc)
    for source in TRUSTED:
        channel=ts.SOURCES.get(source)
        if not channel:continue
        try:r=requests.get(f'https://t.me/s/{channel}?v={int(time.time()//15)}',headers=HEAD,timeout=8)
        except Exception as e:print(f'FAST kaynak hata {source}: {e}');continue
        blocks=[]
        if r.ok:
            for b in BeautifulSoup(r.text,'html.parser').select('.tgme_widget_message')[-25:]:
                tm=b.select_one('time[datetime]')
                if not tm:continue
                try:dt=datetime.fromisoformat(tm['datetime'].replace('Z','+00:00'))
                except:continue
                if -3<=(now-dt).total_seconds()/60<=MAX_AGE:blocks.append(b)
        fetched.append((source,blocks));print(f'FAST kaynak {source}: aday={len(blocks)}')
    sent=0
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True);page=browser.new_page()
        for source,blocks in fetched:
            for b in blocks:
                try:
                    if process(source,b,page):sent+=1
                except Exception as e:print(f'FAST HATA | {source} | {type(e).__name__}: {e}')
        browser.close()
    print(f'=== FAST LANE BİTTİ | gönderilen={sent} ===')

if __name__=='__main__':main()
