import os,re,json,html,requests
from datetime import datetime,timezone,timedelta
from urllib.parse import urlencode,urlsplit,urlunsplit,parse_qsl
from playwright.sync_api import sync_playwright
import local_store as ls
import archive_store as ar
import run_direct_watch_v2 as v2

TOKEN=os.environ['TELEGRAM_BOT_TOKEN']; CHANNEL_ID=os.environ.get('TELEGRAM_CHANNEL_ID','-1004424116637')
MIN_DISCOUNT=float(os.environ.get('MIN_DISCOUNT','15'))
MAX_PRODUCTS=max(20,int(os.environ.get('DIRECT_MAX_PRODUCTS','120')))
BROWSER_LIMIT=max(5,int(os.environ.get('DIRECT_BROWSER_LIMIT','24')))
FRESH_HOURS=max(1,int(os.environ.get('DIRECT_FRESH_SOURCE_HOURS','4')))
AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or 'ozelfirsat09-21').strip() or 'ozelfirsat09-21'
DIRECT_ALERTS_ENABLED=str(os.environ.get('DIRECT_ALERTS_ENABLED','0')).strip().lower() in {'1','true','yes','on'}
STATS={'catalog':0,'checked':0,'http_live':0,'browser':0,'local_fresh':0,'no_price':0,'no_ref':0,'below':0,'oos':0,'sent':0,'duplicates':0,'errors':0,'history_writes':0,'archive_ref':0,'campaign_deals':0}

def num(x):return v2.num(x)
def fmt(x):return v2.fmt(x)
def canonical(u):return ls.canonical(u)
def site_of(u,s=''):return v2.site_of(u,s)
def clean_title(t):
    t=re.sub(r'\s*[:|\-]?\s*Amazon\.com\.tr\s*:\s*.*$',' ',t or '',flags=re.I)
    return re.sub(r'\s+',' ',t).strip(' -|:')[:220] or 'Ürün'
def parse_dt(s):
    try:return datetime.fromisoformat(str(s).replace('Z','+00:00'))
    except:return None

def local_hist(url):return ls.history(url,days=365,limit=700)
def fresh_local_price(url):
    rows=local_hist(url)
    if not rows:return None
    r=rows[0];d=parse_dt(r.get('recorded_at'))
    if not d or datetime.now(timezone.utc)-d>timedelta(hours=FRESH_HOURS):return None
    return {'live':float(r['price']),'old':float(r['old_price']) if r.get('old_price') else None,'title':'','image':'','oos':False,'source':r.get('source') or 'local','recorded_at':r.get('recorded_at'),'campaign':None}

def merge_catalog():
    merged={}
    for r in ls.list_products(MAX_PRODUCTS*4):
        u=canonical(r.get('url') or '');s=site_of(u,r.get('site') or '')
        if s in {'Amazon','Hepsiburada','Trendyol','N11'} and u.startswith('http'):
            merged[u]={'id':None,'product_url':u,'site':s,'product_name':r.get('title') or 'Ürün','current_price':r.get('last_price'),'previous_price':r.get('last_old_price'),'last_posted_at':None,'local_last_seen':r.get('last_seen')}
    try:
        for r in v2.load_catalog():
            u=canonical(r.get('product_url') or '');s=site_of(u,r.get('site') or '')
            if not u or s not in {'Amazon','Hepsiburada','Trendyol','N11'}:continue
            if u in merged:merged[u].update({k:v for k,v in r.items() if v not in (None,'')})
            else:
                r=dict(r);r['product_url']=u;merged[u]=r
    except Exception as e:print(f'SUPABASE KATALOG UYARI: {type(e).__name__}: {e}')
    rows=list(merged.values());rows.sort(key=lambda x:x.get('updated_at') or x.get('local_last_seen') or '')
    return rows[:MAX_PRODUCTS]

def outlink(url,site):
    u=canonical(url)
    if site!='Amazon':return u
    try:
        p=urlsplit(u);q=dict(parse_qsl(p.query,keep_blank_values=True));q['tag']=AMAZON_TAG
        return urlunsplit((p.scheme,p.netloc,p.path,urlencode(q),p.fragment))
    except Exception:
        return u+('&' if '?' in u else '?')+urlencode({'tag':AMAZON_TAG})

def send(row,current,ref,title,image,site,refsrc,campaign=None):
    if not DIRECT_ALERTS_ENABLED:
        print(f'YAYIN KAPALI (direct) | {site} | {current:.2f}->{ref:.2f} | kampanya={campaign.get("label") if campaign else "yok"} | {title[:70]}')
        return False
    url=outlink(row['product_url'],site)
    if ls.recently_published(url,current,days=30,min_drop=.05):
        STATS['duplicates']+=1;print(f'TEKRAR ENGELLENDİ: {site} | {current:.2f} | {title[:70]}');return False
    disc=(ref-current)/ref*100
    lines=[f'🔥 %{disc:.0f} İNDİRİM','',f'🛍️ {html.escape(title)}',f'💰 Efektif birim fiyat: {fmt(current)} TL' if campaign else f'💰 {fmt(current)} TL']
    if campaign:
        lines.append(f'🎯 Kampanya: {html.escape(campaign.get("label") or "Kampanyalı alım")}')
        if campaign.get('qty'):
            total=current*float(campaign['qty'])
            lines.append(f'📦 {campaign["qty"]} adet toplam yaklaşık: {fmt(total)} TL')
    lines += [f'🏷️ Normal fiyat: {fmt(ref)} TL',f'🛍️ {site}','','👇 Fırsata git']
    kb={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':url}]]};text='\n'.join(lines);resp=None
    if image:
        try:
            img=requests.get(image,headers=v2.HEAD,timeout=12,allow_redirects=True)
            if img.ok and len(img.content)>4000:
                resp=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendPhoto',data={'chat_id':CHANNEL_ID,'caption':text[:1024],'parse_mode':'HTML','reply_markup':json.dumps(kb,ensure_ascii=False)},files={'photo':('product.jpg',img.content,img.headers.get('content-type','image/jpeg'))},timeout=22)
        except:resp=None
    if not resp or not resp.ok:resp=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHANNEL_ID,'text':text,'parse_mode':'HTML','disable_web_page_preview':True,'link_preview_options':{'is_disabled':True},'reply_markup':kb},timeout=18)
    if not resp.ok:raise RuntimeError(f'Telegram {resp.status_code}: {resp.text[:160]}')
    ls.mark_published(url,current,'direct')
    STATS['sent']+=1;print(f'GÖNDERİLDİ: {site} | {current:.2f}->{ref:.2f} | %{disc:.1f} | ref={refsrc} | kampanya={campaign.get("label") if campaign else "yok"} | {title[:70]}')
    return True

def main():
    rows=merge_catalog();STATS['catalog']=len(rows)
    print(f'=== API-SİZ fiyat takip V5 | kampanya-efektif + VPS hafıza | katalog={len(rows)} | browser_limit={BROWSER_LIMIT} | yayin={"ACIK" if DIRECT_ALERTS_ENABLED else "KAPALI"} ===')
    browser_used=0
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled']);page=browser.new_page()
        for row in rows:
            try:
                STATS['checked']+=1;url=canonical(row.get('product_url'));site=site_of(url,row.get('site') or '');expected=num(row.get('current_price'))
                info=v2.http_check(url,expected)
                if info and info.get('oos'):STATS['oos']+=1;continue
                if info and info.get('live'):STATS['http_live']+=1
                if (not info or not info.get('live')) and browser_used<BROWSER_LIMIT:
                    browser_used+=1;STATS['browser']+=1;info=v2.browser_check(page,url,expected) or info
                if not info or not info.get('live'):
                    info=fresh_local_price(url)
                    if info:STATS['local_fresh']+=1
                if not info or not info.get('live'):
                    STATS['no_price']+=1;print(f'FİYAT YOK: {site} | {row.get("product_name","")[:80]}');continue
                normal=float(info['live']);campaign=info.get('campaign');current=float(campaign['effective']) if campaign and campaign.get('effective') else normal
                title=clean_title(info.get('title') or row.get('product_name') or 'Ürün');hist=local_hist(url)
                ref,refsrc=ar.smart_reference(title,current,hist,info.get('old'),num(row.get('previous_price')))
                if campaign and normal>current*1.03:
                    if not ref or ref>normal:ref=normal
                    refsrc='page-campaign';STATS['campaign_deals']+=1
                if refsrc.startswith('weighted') or refsrc=='deal-history-not-low':STATS['archive_ref']+=1
                ls.upsert_product(url,site=site,title=title,price=normal,old_price=info.get('old') or num(row.get('previous_price')),source=info.get('source') or 'direct',post_id='',image=info.get('image') or '')
                ls.add_price(url,site,normal,info.get('old'),'direct-check','');ar.add(title,normal,site,info.get('old'),'DirectCheck','direct-current',url);STATS['history_writes']+=1
                if campaign and current<normal*.99:ar.add(title,current,site,normal,'DirectCheck','campaign-effective',url)
                if not ref:
                    STATS['no_ref']+=1;print(f'REFERANS YOK: {site} | {current:.2f} | url_geçmiş={len(hist)} | başlık_geçmiş={len(ar.history_by_title(title,365,50))} | neden={refsrc} | {title[:65]}');continue
                disc=(ref-current)/ref*100
                print(f'KONTROL: {site} | {current:.2f} -> ref {ref:.2f} | %{disc:.1f} | kaynak={info.get("source","web")} | ref={refsrc} | kampanya={campaign.get("label") if campaign else "yok"} | {title[:60]}')
                if disc<MIN_DISCOUNT:STATS['below']+=1;continue
                send(row,current,ref,title,info.get('image'),site,refsrc,campaign)
            except Exception as e:STATS['errors']+=1;print(f'ÜRÜN HATA: {type(e).__name__}: {e}')
        browser.close()
    print('=== API-SİZ V5 BİTTİ | '+' | '.join(f'{k}={v}' for k,v in STATS.items())+' | lokal='+str(ls.stats())+' | arsiv='+str(ar.stats())+' ===')

if __name__=='__main__':main()