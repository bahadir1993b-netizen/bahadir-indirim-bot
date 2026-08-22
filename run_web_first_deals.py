import os,re,html,json,time,requests
from datetime import datetime,timezone,timedelta
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import telegram_sources as ts
import run_direct_watch_v2 as v2
import local_store as ls
import archive_store as ar

MIN_DISC=max(8.0,float(os.environ.get('WEB_FIRST_MIN_DISCOUNT','15')))
MAX_CHECK=max(40,int(os.environ.get('WEB_FIRST_MAX_CHECK','180')))
BROWSER_LIMIT=max(0,int(os.environ.get('WEB_FIRST_BROWSER_LIMIT','35')))
HEAD=dict(ts.HEAD)
LANDINGS=[
 ('Amazon','https://www.amazon.com.tr/deals'),
 ('Hepsiburada','https://www.hepsiburada.com/kampanyalar'),
 ('Trendyol','https://www.trendyol.com/sr?fl=encokavantajliurunler'),
 ('N11','https://www.n11.com/kampanyalar'),
]

def canonical(u):return v2.canonical(u)
def fmt(x):return v2.fmt(x)
def clean_title(s):
    s=re.sub(r'\s+',' ',str(s or '')).strip(' -|')
    return s[:170] if s else 'Fırsat Ürünü'

def product_links(body,base):
    soup=BeautifulSoup(body,'html.parser');out=[]
    for a in soup.select('a[href]'):
        u=urljoin(base,a.get('href') or '')
        s=ts.site(u)
        if s and ts.valid(s,u):
            n=ts.normalize(s,u)
            if n and n not in out:out.append(n)
    return out

def discover(page):
    out=[]
    for site,url in LANDINGS:
        links=[]
        try:
            r=requests.get(url,headers=HEAD,timeout=10,allow_redirects=True)
            if r.ok:links=product_links(r.text,r.url)
        except Exception:pass
        if len(links)<5:
            try:
                page.goto(url,wait_until='domcontentloaded',timeout=14000);page.wait_for_timeout(1200)
                links=product_links(page.content(),page.url)
            except Exception:pass
        for u in links[:60]:
            if u not in out:out.append(u)
        print(f'WEB KEŞİF | {site} | ürün_linki={len(links)}')
    return out

def recent_row(url):
    try:
        rows=ts.sb('GET','products',params={'select':'*','product_url':f'eq.{url}','limit':'1'})
        return rows[0] if rows else None
    except:return None

def duplicate(row,current):
    if not row:return False
    last=row.get('last_posted_at')
    if not last:return False
    try:
        age=datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))
        old=float(row.get('last_posted_price') or row.get('current_price') or 0)
        return age<timedelta(days=30) and (not old or current>=old*.95)
    except:return False

def send(row,url,site,title,current,ref,image,campaign=None):
    disc=(ref-current)/ref*100
    lines=[f'🔥 %{disc:.0f} İNDİRİM','',f'🛍️ {html.escape(clean_title(title))}']
    if campaign:
        lines += [f'💰 Efektif birim fiyat: {fmt(current)} TL',f'🎯 Kampanya: {html.escape(campaign.get("label") or "Kampanyalı alım")}']
        if campaign.get('qty'):lines.append(f'📦 {campaign["qty"]} adet alımda geçerli')
    else:lines.append(f'💰 {fmt(current)} TL')
    lines += [f'🏷️ Referans fiyat: {fmt(ref)} TL',f'🛍️ {site}','','👇 Fırsata git']
    text='\n'.join(lines);kb={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':url}]]};rr=None
    if image:
        try:
            im=requests.get(image,headers=HEAD,timeout=10,allow_redirects=True);ct=(im.headers.get('content-type') or 'image/jpeg').split(';')[0]
            if im.ok and len(im.content)>4000:
                rr=requests.post('https://api.telegram.org/bot'+ts.TOKEN+'/sendPhoto',data={'chat_id':ts.CHAT,'caption':text[:1024],'parse_mode':'HTML','reply_markup':json.dumps(kb,ensure_ascii=False)},files={'photo':('product.jpg',im.content,ct)},timeout=22)
        except Exception:rr=None
    if not rr or not rr.ok:
        rr=requests.post('https://api.telegram.org/bot'+ts.TOKEN+'/sendMessage',json={'chat_id':ts.CHAT,'text':text,'parse_mode':'HTML','disable_web_page_preview':True,'link_preview_options':{'is_disabled':True},'reply_markup':kb},timeout=18)
    rr.raise_for_status()
    if row and row.get('id'):
        ts.sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat(),'last_posted_price':current})
    try:ts.sb('POST','price_history',json={'price':current,'product_url':url,'site':site,'recorded_at':datetime.now(timezone.utc).isoformat()})
    except Exception:pass
    print(f'WEB-FIRST GÖNDERİLDİ | {site} | {current:.2f}->{ref:.2f} | %{disc:.1f}')

def reference_for(url,title,live,old):
    hist=ls.history(url,days=180,limit=300)
    ref,src=ar.smart_reference(title,live,hist,old,None)
    return ref,src

def main():
    checked=sent=browser_used=0
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled']);page=browser.new_page()
        discovered=discover(page)
        catalog=[r.get('url') for r in ls.list_products(1200) if r.get('url')]
        urls=[]
        for u in discovered+catalog:
            c=canonical(u)
            if c and c not in urls:urls.append(c)
            if len(urls)>=MAX_CHECK:break
        for url in urls:
            site=v2.site_of(url,'')
            if site not in {'Amazon','Hepsiburada','Trendyol','N11'}:continue
            try:
                info=v2.http_check(url,None)
                if (not info or not info.get('live')) and browser_used<BROWSER_LIMIT:
                    browser_used+=1;info=v2.browser_check(page,url,None) or info
                if not info or info.get('oos') or not info.get('live'):continue
                checked+=1;live=float(info['live']);campaign=info.get('campaign');current=live;ref=None;rsrc=''
                if campaign and campaign.get('effective') and float(campaign['effective'])<live*.99:
                    current=float(campaign['effective']);ref=live;rsrc='page-campaign'
                else:
                    ref,rsrc=reference_for(url,info.get('title') or '',live,info.get('old'))
                title=clean_title(info.get('title') or 'Ürün')
                ls.upsert_product(url,site,title,live,info.get('old'),'web-first','',info.get('image') or '')
                ls.add_price(url,site,live,info.get('old'),'web-first','')
                ar.add(title,live,site,info.get('old'),'WebFirst','market-normal',url)
                if not ref or ref<=current:continue
                disc=(ref-current)/ref*100
                if disc<MIN_DISC:continue
                row=recent_row(url)
                if not row:
                    row=ts.save(site,url,title,live,info.get('old') or ref)
                if duplicate(row,current):continue
                send(row,url,site,title,current,float(ref),info.get('image'),campaign if current<live else None);sent+=1
            except Exception as e:print(f'WEB-FIRST HATA | {type(e).__name__}: {e}')
        browser.close()
    print(f'=== WEB-FIRST BİTTİ | kontrol={checked} | gönderilen={sent} | browser={browser_used} ===')

if __name__=='__main__':main()
