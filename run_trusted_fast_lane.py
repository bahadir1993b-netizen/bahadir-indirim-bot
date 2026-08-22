import os,re,html,json,time,requests
from datetime import datetime,timezone,timedelta
from urllib.parse import urljoin,urlparse
from bs4 import BeautifulSoup
import telegram_sources as ts
import local_store as ls
from deal_validation import inspect_page

TRUSTED={'OzelFirsatlar','AmazonOzel','FirsatMerkezi'}
MAX_AGE=max(3,int(os.environ.get('FAST_LANE_MAX_AGE','20')))
MIN_DISC=max(3.0,float(os.environ.get('FAST_LANE_MIN_DISCOUNT','5')))
HEAD=dict(ts.HEAD);HEAD.update({'Cache-Control':'no-cache, no-store, max-age=0','Pragma':'no-cache'})
SHORT_HOSTS={'app.hb.biz':'Hepsiburada','hps.im':'Hepsiburada','ty.gl':'Trendyol','trendyol.app.link':'Trendyol','n11.mn':'N11','amzn.to':'Amazon'}

def fmt(x):return f'{x:,.2f}'.replace(',','X').replace('.',',').replace('X','.')
def fast_key(source,post_id):return f'fast:{source}:{post_id}'
def clean_title(raw):
    s=raw or ''
    s=re.sub(r'(?i)^\s*(?:ÖZEL\s+FIRSATLAR\s*-\s*Güncel\s+İndirimler|Amazon\s+İndirimleri\s*-\s*Özel\s+Fırsatlar|Fırsat\s+Merkezi)\s*',' ',s)
    s=re.sub(r'@[A-Za-z0-9_]+|#(?:tanıtım|tanitim|reklam)',' ',s,flags=re.I)
    s=re.sub(r'(?i)\b(?:sohbet grubumuz|kanalımıza katıl|takip et|reklam)\b.*$',' ',s)
    s=re.sub(r'[🔥✅🔻🔗👉👇📣🎯🎁]+',' ',s);s=re.sub(r'\s+',' ',s).strip(' -|•:')
    for pat in [r'\s\d[\d.,]*\s*(?:TL|₺)\b',r'\s(?:fırsata git|firsata git)\b']:
        m=re.search(pat,s,re.I)
        if m:s=s[:m.start()]
    bad={'fırsat ürünü','sepete ekleniyor','sepete ekle','hemen al','amazon','ürün'}
    return s[:170] if len(s)>=5 and s.lower() not in bad else 'Fırsat Ürünü'
def qty_hint(text):
    m=re.search(r'\b(\d+)\s*(?:adet\s*)?(?:alımda|alimda|adet)',text or '',re.I)
    return int(m.group(1)) if m and 2<=int(m.group(1))<=10 else None
def payment_hint(text):return bool(re.search(r'(?i)\b(?:ödeme|odeme)\s+(?:adımında|adiminda|anında|aninda)|\bsepette\b|\bkasada\b',text or ''))
def strong_hint(text):return bool(re.search(r'(?i)%\s*\d{1,2}\s*indirim|en\s+düşük|fırsat(?:ın)?\s+fiyatı|çok\s+iyi\s+fiyat|kaçmaz',text or ''))
def local_meta(url):
    cu=ls.canonical(url)
    try:
        for r in ls.list_products(2500):
            if ls.canonical(r.get('url') or '')==cu:return clean_title(r.get('title') or ''),r.get('image') or ''
    except:pass
    return 'Fırsat Ürünü',''

def _short_site(u):
    try:return SHORT_HOSTS.get(urlparse(u or '').netloc.lower().split(':')[0])
    except:return None

def _direct_from_response(resp,site):
    try:
        final=ts.clean(resp.url)
        if ts.site(final)==site and ts.valid(site,final):return ts.normalize(site,final)
        body=html.unescape((resp.text or '').replace('\\/','/'))
        soup=BeautifulSoup(body,'html.parser')
        for sel,attr in [('link[rel="canonical"]','href'),('meta[property="og:url"]','content')]:
            el=soup.select_one(sel)
            if el:
                u=urljoin(final,el.get(attr) or '')
                if ts.site(u)==site and ts.valid(site,u):return ts.normalize(site,u)
        for a in soup.select('a[href]'):
            u=urljoin(final,a.get('href') or '')
            if ts.site(u)==site and ts.valid(site,u):return ts.normalize(site,u)
        meta=soup.select_one('meta[http-equiv]')
        if meta and (meta.get('http-equiv') or '').lower()=='refresh':
            m=re.search(r'url\s*=\s*([^;]+)$',meta.get('content') or '',re.I)
            if m:
                u=urljoin(final,m.group(1).strip(' \"\''))
                if ts.site(u)==site and ts.valid(site,u):return ts.normalize(site,u)
        # JS/deeplink sayfalarında gömülü gerçek mağaza URL'sini tara.
        for m in re.finditer(r'https?://[^\s\"\'<>]+',body,re.I):
            u=ts.clean(m.group(0).rstrip('),;'))
            if ts.site(u)==site and ts.valid(site,u):return ts.normalize(site,u)
    except Exception:pass
    return None

def _resolve_short(site,x):
    try:
        u=ts.http_resolve(x,site)
        if u:return u
    except Exception:pass
    headers_list=[HEAD,dict(HEAD,**{'User-Agent':'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1'})]
    for headers in headers_list:
        for method in ('get','head'):
            try:
                r=getattr(requests,method)(x,headers=headers,timeout=6,allow_redirects=True)
                u=_direct_from_response(r,site)
                if u:return u
            except Exception:pass
    return None

def _search_store(site,title):
    try:
        u=ts.search_marketplace_http(site,title)
        if u:return u
    except Exception:pass
    if site=='Hepsiburada' and title and title!='Fırsat Ürünü':
        try:
            q=requests.utils.quote(title[:140]);base='https://www.hepsiburada.com/ara?q='+q
            r=requests.get(base,headers=HEAD,timeout=6,allow_redirects=True)
            if r.ok:
                soup=BeautifulSoup(r.text,'html.parser');best=[]
                toks={z for z in re.findall(r'[a-zçğıöşü0-9]{3,}',title.lower())}
                for a in soup.select('a[href]'):
                    u=urljoin(r.url,a.get('href') or '')
                    if not ts.valid('Hepsiburada',u):continue
                    txt=(a.get_text(' ',strip=True) or '').lower();score=sum(1 for z in toks if z in txt)
                    best.append((score,u))
                if best:
                    _,u=max(best,key=lambda z:z[0]);return ts.normalize('Hepsiburada',u)
        except Exception:pass
    return None

def resolve_product_url(site,links,title):
    for x in links:
        try:
            if ts.site(x)==site and ts.valid(site,x):
                u=ts.normalize(site,x)
                if u:return u
        except Exception:pass
    short_fallback=None
    for x in links:
        try:
            xs=_short_site(x) or ts.site(x)
            if xs==site:
                if _short_site(x):short_fallback=x
                u=_resolve_short(site,x)
                if u:return u
        except Exception:pass
    u=_search_store(site,title)
    if u:return u
    # Güvenilir Telegram fırsatını yalnızca canonical URL çözülemedi diye kaçırma.
    # Amazon'da affiliate zorunlu olduğu için kısa link fallback kullanılmaz.
    if short_fallback and site!='Amazon':
        print(f'FAST LINK FALLBACK | {site} | {short_fallback}')
        return short_fallback
    return None

def _meta_from_url(url):
    try:
        r=requests.get(url,headers=HEAD,timeout=6,allow_redirects=True)
        body=r.text or '';soup=BeautifulSoup(body,'html.parser');title='';image=''
        e=soup.select_one('meta[property="og:title"]') or soup.select_one('h1')
        if e:title=clean_title(e.get('content') if e.name=='meta' else e.get_text(' ',strip=True))
        e=soup.select_one('meta[property="og:image"]') or soup.select_one('meta[name="twitter:image"]')
        if e:image=e.get('content') or ''
        return title,image
    except:return 'Fırsat Ürünü',''

def send(site,url,title,current,ref,campaign,image,row):
    disc=(ref-current)/ref*100 if ref and ref>current else None
    lines=[f'🔥 %{disc:.0f} İNDİRİM' if disc is not None else '🔥 FIRSAT','',f'🛍️ {html.escape(title)}',f'💰 Efektif birim fiyat: {fmt(current)} TL' if campaign else f'💰 {fmt(current)} TL']
    if ref and ref>current:lines.append(f'🏷️ Referans fiyat: {fmt(ref)} TL')
    if campaign:lines.append(f'🎯 Kampanya: {html.escape(campaign)}')
    lines += [f'🛍️ {site}','','👇 Fırsata git'];text='\n'.join(lines);kb={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':url}]]};rr=None
    if image:
        try:
            im=requests.get(image,headers=HEAD,timeout=4,allow_redirects=True);ct=(im.headers.get('content-type') or 'image/jpeg').split(';')[0]
            if im.ok and len(im.content)>4000:rr=requests.post('https://api.telegram.org/bot'+ts.TOKEN+'/sendPhoto',data={'chat_id':ts.CHAT,'caption':text[:1024],'parse_mode':'HTML','reply_markup':json.dumps(kb,ensure_ascii=False)},files={'photo':('product.jpg',im.content,ct)},timeout=8)
        except:pass
    if not rr or not rr.ok:rr=requests.post('https://api.telegram.org/bot'+ts.TOKEN+'/sendMessage',json={'chat_id':ts.CHAT,'text':text,'parse_mode':'HTML','disable_web_page_preview':True,'reply_markup':kb},timeout=8)
    rr.raise_for_status();ls.mark_published(url,current,'trusted-fast-lane')
    if isinstance(row,dict) and row.get('id'):ts.sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat(),'last_posted_price':current})
    print(f'FAST GÖNDERİLDİ | {site} | {current:.2f} | foto={"var" if image else "yok"} | {title[:80]}');return True

def process(source,b):
    post_id=b.get('data-post','').split('/')[-1];key=fast_key(source,post_id) if post_id else ''
    if not post_id or ts.seen(key):return False
    tx=b.select_one('.tgme_widget_message_text')
    if not tx:return False
    raw=tx.get_text(' ',strip=True);links=[ts.clean(a.get('href') or '') for a in b.select('a[href]')];title=clean_title(raw);cur,old=ts.source_pair(raw)
    site=next((ts.site(x) or _short_site(x) for x in links if (ts.site(x) or _short_site(x))),None)
    u=resolve_product_url(site,links,title) if site else None
    if not site or not cur or not u:
        print(f'FAST ATLANDI | {source}:{post_id} | eksik_temel | site={site} fiyat={cur} link={bool(u)} | başlık={title[:70]}');return False
    pg=inspect_page(u,cur) or {}
    if pg.get('available') is False:ts.remember(key);print(f'FAST ATLANDI | {source}:{post_id} | stok_yok');return False
    local_title,local_image=local_meta(u);page_title=clean_title(pg.get('title') or '')
    short_title,short_image=_meta_from_url(u) if (_short_site(u) and (page_title=='Fırsat Ürünü' or not pg.get('image'))) else ('Fırsat Ürünü','')
    if page_title!='Fırsat Ürünü':title=page_title
    elif short_title!='Fırsat Ürünü':title=short_title
    elif title=='Fırsat Ürünü' and local_title!='Fırsat Ürünü':title=local_title
    image=pg.get('image') or short_image or local_image
    if title=='Fırsat Ürünü':print(f'FAST BEKLET | {source}:{post_id} | ürün_adı_alınamadı | foto={"var" if image else "yok"}');return False
    live=pg.get('live');campaign=pg.get('campaign');effective=float(cur);label=None
    if campaign and campaign.get('effective'):effective=float(campaign['effective']);label=campaign.get('label')
    q=qty_hint(raw)
    if live and q and cur<live*.97:effective=float(cur);label=label or f'{q} adet alımda geçerli'
    if payment_hint(raw) and not label:label='Ödeme adımında geçerli'
    refs=[float(x) for x in [live,pg.get('old'),old] if x and float(x)>effective*1.03 and float(x)<=effective*1.8];ref=min(refs) if refs else None
    rescue=source in TRUSTED and (payment_hint(raw) or strong_hint(raw) or (old and old>effective*1.05))
    if not ref and not rescue:print(f'FAST ATLANDI | {source}:{post_id} | referans_yok | fiyat={effective:.2f} canlı={live or 0:.2f}');return False
    if ref and (ref-effective)/ref*100<MIN_DISC:
        disc=(ref-effective)/ref*100;print(f'FAST ATLANDI | {source}:{post_id} | indirim_yetersiz | fiyat={effective:.2f} ref={ref:.2f} indirim=%{disc:.1f} min=%{MIN_DISC}');ts.remember(key);return False
    if ls.recently_published(u,effective,days=30,min_drop=.05):
        print(f'FAST ATLANDI | {source}:{post_id} | tekrar_30gun_fiyat_dusmedi | fiyat={effective:.2f}');ts.remember(key);return False
    row=ts.save(site,u,title,effective,ref);ok=send(site,u,title,effective,ref,label,image,row);ts.remember(key);return ok

def main():
    ls.runtime_start('trusted-fast-lane');now=datetime.now(timezone.utc);jobs=[];sent=errors=0
    for source in TRUSTED:
        channel=ts.SOURCES.get(source)
        if not channel:continue
        try:r=requests.get(f'https://t.me/s/{channel}?v={time.time_ns()}',headers=HEAD,timeout=4)
        except Exception as e:errors+=1;print(f'FAST KAYNAK HATA | {source} | {type(e).__name__}: {e}');continue
        if not r.ok:print(f'FAST KAYNAK HATA | {source} | HTTP {r.status_code}');continue
        blocks=[]
        for b in BeautifulSoup(r.text,'html.parser').select('.tgme_widget_message')[-12:]:
            tm=b.select_one('time[datetime]')
            if not tm:continue
            try:dt=datetime.fromisoformat(tm['datetime'].replace('Z','+00:00'));age=(now-dt).total_seconds()/60
            except:continue
            if -2<=age<=MAX_AGE:blocks.append((dt,source,b))
        jobs.extend(blocks);print(f'FAST kaynak {source}: aday={len(blocks)}')
    jobs.sort(key=lambda x:x[0],reverse=True)
    for dt,source,b in jobs:
        try:
            if process(source,b):sent+=1
        except Exception as e:errors+=1;print(f'FAST HATA | {source} | {type(e).__name__}: {e}')
    ls.runtime_finish('trusted-fast-lane','ok' if errors==0 else 'warning',candidates=len(jobs),checked=len(jobs),sent=sent,errors=errors)
    print(f'=== FAST LANE BİTTİ | aday={len(jobs)} | gönderilen={sent} | hata={errors} ===')

if __name__=='__main__':main()
