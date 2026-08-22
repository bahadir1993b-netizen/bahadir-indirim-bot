import os,re,html,json,requests
from datetime import datetime,timezone,timedelta
from urllib.parse import urljoin,urlsplit,urlunsplit,parse_qsl,urlencode
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import telegram_sources as ts
import run_direct_watch_v2 as v2
import local_store as ls
import archive_store as ar

MIN_DISC=max(8.0,float(os.environ.get('WEB_FIRST_MIN_DISCOUNT','15')))
MAX_CHECK=max(60,int(os.environ.get('WEB_FIRST_MAX_CHECK','260')))
BROWSER_LIMIT=max(0,int(os.environ.get('WEB_FIRST_BROWSER_LIMIT','55')))
AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or 'ozelfirsat09-21').strip() or 'ozelfirsat09-21'
HEAD=dict(ts.HEAD)
LANDINGS=[
 ('Amazon','https://www.amazon.com.tr/deals'),('Amazon','https://www.amazon.com.tr/gp/goldbox'),('Amazon','https://www.amazon.com.tr/s?k=indirim'),('Amazon','https://www.amazon.com.tr/s?k=firsat'),
 ('Hepsiburada','https://www.hepsiburada.com/kampanyalar'),('Hepsiburada','https://www.hepsiburada.com/ara?q=indirim'),('Hepsiburada','https://www.hepsiburada.com/ara?q=firsat'),
 ('Trendyol','https://www.trendyol.com/sr?fl=encokavantajliurunler'),('Trendyol','https://www.trendyol.com/sr?q=indirim'),('Trendyol','https://www.trendyol.com/sr?q=firsat'),
 ('N11','https://www.n11.com/kampanyalar'),('N11','https://www.n11.com/arama?q=indirim'),('N11','https://www.n11.com/arama?q=firsat'),
]
DISCOVERY_TERMS=['şampuan','kişisel bakım','deterjan','temizlik','bebek bezi','ıslak mendil','kahve','gıda','mutfak','küçük ev aletleri','kulaklık','akıllı saat','tablet','televizyon','telefon aksesuar','oyuncak','kırtasiye','ev yaşam','spor ayakkabı','pet ürünleri']
BAD_TITLES={'ürün','fırsat ürünü','sepete ekleniyor','sepete ekle','hemen al','amazon'}

def canonical(u):return ls.canonical(u)
def fmt(x):return v2.fmt(x)
def clean_title(s):
    s=re.sub(r'\s*[:|\-]?\s*Amazon\.com\.tr\s*:\s*.*$',' ',str(s or ''),flags=re.I)
    s=re.sub(r'(?i)^\s*(?:sepete ekleniyor|sepete ekle|hemen al)\s*[.!…-]*\s*',' ',s)
    s=re.sub(r'\s+',' ',s).strip(' -|:')
    return s[:170] if len(s)>=5 and s.lower() not in BAD_TITLES else 'Fırsat Ürünü'
def good_title(s):return clean_title(s)!='Fırsat Ürünü'

def affiliate_url(url,site):
    if site!='Amazon':return url
    try:
        p=urlsplit(url);q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower()!='tag'];q.append(('tag',AMAZON_TAG))
        return urlunsplit((p.scheme,p.netloc,p.path,urlencode(q,doseq=True),p.fragment))
    except Exception:return url+('&' if '?' in url else '?')+'tag='+AMAZON_TAG

def _jsonld_meta(soup):
    for el in soup.select('script[type="application/ld+json"]'):
        try:data=json.loads(el.string or el.get_text() or '{}')
        except:continue
        objs=data if isinstance(data,list) else [data]
        for obj in objs:
            if not isinstance(obj,dict):continue
            if isinstance(obj.get('@graph'),list):objs.extend(x for x in obj['@graph'] if isinstance(x,dict))
            title=obj.get('name') if isinstance(obj.get('name'),str) else ''
            image=obj.get('image');image=image[0] if isinstance(image,list) and image else image
            if title or (isinstance(image,str) and image.startswith('http')):return title,image
    return '',None

def sale_meta(url,site,hint=''):
    urls=[url]
    if site=='Amazon':
        m=re.search(r'/(?:dp|gp/product)/([A-Z0-9]{8,12})',url,re.I)
        if m:
            asin=m.group(1).upper();urls += [f'https://www.amazon.com.tr/dp/{asin}?th=1&psc=1',f'https://www.amazon.com.tr/gp/aw/d/{asin}']
    best_title=clean_title(hint);best_image=None
    for u in urls:
        try:r=requests.get(u,headers=HEAD,timeout=7,allow_redirects=True)
        except:continue
        if not r.ok:continue
        soup=BeautifulSoup(r.text,'html.parser');title='';image=None
        for sel,attr in [('#productTitle',None),('h1',None),('meta[property="og:title"]','content'),('meta[name="twitter:title"]','content')]:
            e=soup.select_one(sel)
            if e:
                title=(e.get(attr) if attr else e.get_text(' ',strip=True)) or ''
                title=clean_title(title)
                if title!='Fırsat Ürünü':break
        for sel,attr in [('#landingImage','data-old-hires'),('#landingImage','src'),('img#imgBlkFront','src'),('meta[property="og:image"]','content'),('meta[name="twitter:image"]','content'),('[itemprop="image"]','content'),('[itemprop="image"]','src')]:
            e=soup.select_one(sel)
            if e:
                v=e.get(attr) or ''
                if isinstance(v,str) and v.startswith('http'):image=v;break
        if not title or not image:
            jt,ji=_jsonld_meta(soup)
            if title in ('','Fırsat Ürünü') and jt:title=clean_title(jt)
            if not image and isinstance(ji,str) and ji.startswith('http'):image=ji
        if title!='Fırsat Ürünü':best_title=title
        if image:best_image=image
        if best_title!='Fırsat Ürünü' and best_image:break
    if not best_image and best_title!='Fırsat Ürünü':
        base={'Amazon':'https://www.amazon.com.tr/s?k=','Hepsiburada':'https://www.hepsiburada.com/ara?q=','Trendyol':'https://www.trendyol.com/sr?q='}.get(site)
        if base:
            try:
                r=requests.get(base+requests.utils.quote(best_title[:120]),headers=HEAD,timeout=6)
                soup=BeautifulSoup(r.text,'html.parser');want=ts.tokens(best_title);best=(0,None)
                for card in soup.select('div[data-asin],li,div[class*="product"],div[class*="p-card"]')[:80]:
                    txt=card.get_text(' ',strip=True);got=ts.tokens(txt);score=len(want&got)/max(1,len(want))
                    img=card.select_one('img[src]');src=img.get('src') if img else None
                    if src and src.startswith('http') and score>best[0]:best=(score,src)
                if best[0]>=.50:best_image=best[1]
            except:pass
    return best_title,best_image

def product_links(body,base,expected_site=None):
    soup=BeautifulSoup(body,'html.parser');out=[]
    for a in soup.select('a[href]'):
        u=urljoin(base,a.get('href') or '');s=ts.site(u) or expected_site
        if s and ts.valid(s,u):
            n=ts.normalize(s,u) or u
            if n and n not in out:out.append(n)
    return out

def _dynamic_targets():
    idx=int(datetime.now(timezone.utc).timestamp()//120)%len(DISCOVERY_TERMS);targets=list(LANDINGS)
    terms=[DISCOVERY_TERMS[(idx+i)%len(DISCOVERY_TERMS)] for i in range(3)]
    for term in terms:
        q=requests.utils.quote(term+' indirim')
        targets.extend([
            ('Amazon','https://www.amazon.com.tr/s?k='+q),
            ('Hepsiburada','https://www.hepsiburada.com/ara?q='+q),
            ('Trendyol','https://www.trendyol.com/sr?q='+q),
            ('N11','https://www.n11.com/arama?q='+q),
        ])
    print('WEB KEŞİF KONULARI | '+', '.join(terms));return targets

def discover(page):
    out=[];per_site={}
    for site,url in _dynamic_targets():
        links=[]
        try:
            r=requests.get(url,headers=HEAD,timeout=10,allow_redirects=True)
            if r.ok:links=product_links(r.text,r.url,site)
        except Exception:pass
        if len(links)<5:
            try:page.goto(url,wait_until='domcontentloaded',timeout=14000);page.wait_for_timeout(800);links=product_links(page.content(),page.url,site)
            except Exception:pass
        per_site[site]=per_site.get(site,0)+len(links)
        for u in links[:80]:
            c=canonical(u)
            if c and c not in out:out.append(c)
    for site,count in per_site.items():print(f'WEB KEŞİF | {site} | ürün_linki={count}')
    return out

def recent_row(url):
    try:
        rows=ts.sb('GET','products',params={'select':'*','product_url':f'eq.{url}','limit':'1'});return rows[0] if rows else None
    except:return None

def duplicate_db(row,current):
    if not row:return False
    last=row.get('last_posted_at')
    if not last:return False
    try:
        age=datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'));old=float(row.get('last_posted_price') or row.get('current_price') or 0)
        return age<timedelta(days=30) and (not old or current>=old*.95)
    except:return False

def send(row,url,site,title,current,ref,image,campaign=None):
    title,image2=sale_meta(url,site,title);image=image or image2
    if title=='Fırsat Ürünü':print(f'WEB-FIRST YAYIN ENGELLENDİ | ürün_adı_yok | {site} | {url}');return False
    out_url=affiliate_url(ts.normalize(site,url) or url,site);disc=(ref-current)/ref*100
    if site=='Amazon' and ('tag='+AMAZON_TAG) not in out_url:raise RuntimeError('Amazon affiliate tag missing at publish boundary')
    lines=[f'🔥 %{disc:.0f} İNDİRİM','',f'🛍️ {html.escape(title)}']
    if campaign:
        lines += [f'💰 Efektif birim fiyat: {fmt(current)} TL',f'🎯 Kampanya: {html.escape(campaign.get("label") or "Kampanyalı alım")}']
        if campaign.get('qty'):lines.append(f'📦 {campaign["qty"]} adet alımda geçerli')
    else:lines.append(f'💰 {fmt(current)} TL')
    lines += [f'🏷️ Referans fiyat: {fmt(ref)} TL',f'🛍️ {site}','','👇 Fırsata git']
    text='\n'.join(lines);kb={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':out_url}]]};rr=None
    if image:
        try:
            im=requests.get(image,headers=HEAD,timeout=10,allow_redirects=True);ct=(im.headers.get('content-type') or 'image/jpeg').split(';')[0]
            if im.ok and len(im.content)>4000:rr=requests.post('https://api.telegram.org/bot'+ts.TOKEN+'/sendPhoto',data={'chat_id':ts.CHAT,'caption':text[:1024],'parse_mode':'HTML','reply_markup':json.dumps(kb,ensure_ascii=False)},files={'photo':('product.jpg',im.content,ct)},timeout=22)
        except Exception:rr=None
    if not rr or not rr.ok:rr=requests.post('https://api.telegram.org/bot'+ts.TOKEN+'/sendMessage',json={'chat_id':ts.CHAT,'text':text,'parse_mode':'HTML','disable_web_page_preview':True,'link_preview_options':{'is_disabled':True},'reply_markup':kb},timeout=18)
    rr.raise_for_status();ls.mark_published(url,current,'web-first')
    if row and row.get('id'):ts.sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat(),'last_posted_price':current})
    try:ts.sb('POST','price_history',json={'price':current,'product_url':canonical(url),'site':site,'recorded_at':datetime.now(timezone.utc).isoformat()})
    except Exception:pass
    print(f'WEB-FIRST GÖNDERİLDİ | {site} | {current:.2f}->{ref:.2f} | %{disc:.1f} | foto={"var" if image else "yok"} | affiliate={"ok" if site!="Amazon" or "tag="+AMAZON_TAG in out_url else "HATA"}');return True

def reference_for(url,title,live,old):
    hist=ls.history(url,days=180,limit=300);return ar.smart_reference(title,live,hist,old,None)

def main():
    ls.runtime_start('web-first');checked=sent=browser_used=errors=0;discovered=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled']);page=browser.new_page();discovered=discover(page)
            catalog=[r.get('url') for r in ls.list_products(2000) if r.get('url')];urls=[]
            for u in discovered+catalog:
                c=canonical(u)
                if c and c not in urls:urls.append(c)
                if len(urls)>=MAX_CHECK:break
            for url in urls:
                site=v2.site_of(url,'')
                if site not in {'Amazon','Hepsiburada','Trendyol','N11'}:continue
                try:
                    info=v2.http_check(url,None)
                    if (not info or not info.get('live') or not info.get('image') or not good_title(info.get('title'))) and browser_used<BROWSER_LIMIT:
                        browser_used+=1;bi=v2.browser_check(page,url,None);info=bi or info
                    if not info or info.get('oos') or not info.get('live'):continue
                    checked+=1;live=float(info['live']);campaign=info.get('campaign');current=live;ref=None
                    if campaign and campaign.get('effective') and float(campaign['effective'])<live*.99:current=float(campaign['effective']);ref=live
                    else:ref,_=reference_for(url,info.get('title') or '',live,info.get('old'))
                    title,image=sale_meta(url,site,info.get('title') or '')
                    if title=='Fırsat Ürünü':print(f'WEB-FIRST ATLANDI | ürün_adı_yok | {site} | {url}');continue
                    image=info.get('image') or image
                    ls.upsert_product(url,site,title,live,info.get('old'),'web-first','',image or '');ls.add_price(url,site,live,info.get('old'),'web-first','');ar.add(title,live,site,info.get('old'),'WebFirst','market-normal',url)
                    if not ref or ref<=current:continue
                    disc=(ref-current)/ref*100
                    if disc<MIN_DISC:continue
                    if ls.recently_published(url,current,days=30,min_drop=.05):continue
                    row=recent_row(url) or ts.save(site,canonical(url),title,live,info.get('old') or ref)
                    if duplicate_db(row,current):continue
                    if send(row,url,site,title,current,float(ref),image,campaign if current<live else None):sent+=1
                except Exception as e:errors+=1;print(f'WEB-FIRST HATA | {type(e).__name__}: {e}')
            browser.close()
        ls.runtime_finish('web-first','ok' if errors==0 else 'warning',candidates=len(discovered),checked=checked,sent=sent,errors=errors,details={'browser':browser_used})
        print(f'=== WEB-FIRST BİTTİ | keşif={len(discovered)} | kontrol={checked} | gönderilen={sent} | browser={browser_used} | hata={errors} ===')
    except Exception as e:
        ls.runtime_finish('web-first','error',candidates=len(discovered),checked=checked,sent=sent,errors=errors+1,details={'error':type(e).__name__});raise

if __name__=='__main__':main()
