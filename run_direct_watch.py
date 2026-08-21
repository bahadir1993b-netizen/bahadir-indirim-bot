import os,re,json,statistics,time,html
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse,urlunparse,urlencode
import requests
from playwright.sync_api import sync_playwright

import bot
from deal_validation import inspect_page

MIN_DISCOUNT=float(os.environ.get('MIN_DISCOUNT','15'))
MAX_PRODUCTS=max(20,int(os.environ.get('DIRECT_MAX_PRODUCTS','70')))
HISTORY_DAYS=max(30,int(os.environ.get('DIRECT_HISTORY_DAYS','90')))
HISTORY_WRITE_HOURS=max(2,int(os.environ.get('DIRECT_HISTORY_WRITE_HOURS','6')))
CHANNEL_ID=os.environ.get('TELEGRAM_CHANNEL_ID','-1004424116637')
AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or '').strip()
HEAD={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}

STATS={'seen':0,'checked':0,'live':0,'browser':0,'no_price':0,'no_ref':0,'below':0,'stock':0,'sent':0,'errors':0,'cooldown':0,'campaign':0}

def fmt(x):return f'{x:,.2f}'.replace(',','X').replace('.',',').replace('X','.')

def canonical(u):
    try:
        p=urlparse(u or '')
        return urlunparse(('https',p.netloc.lower(),p.path.rstrip('/'),'','',''))
    except:return u

def outlink(u,site):
    base=canonical(u)
    if site=='Amazon' and AMAZON_TAG:return base+'?'+urlencode({'tag':AMAZON_TAG})
    return base

def pprice(v):
    try:
        if v is None:return None
        s=re.sub(r'[^0-9,.]','',str(v))
        if not s:return None
        if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
        elif ',' in s:
            a,b=s.rsplit(',',1);s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
        elif '.' in s:
            a,b=s.rsplit('.',1);s=s.replace('.','') if len(b)>2 else s
        x=float(s);return x if 1<x<10000000 else None
    except:return None

def history_rows(url):
    try:
        since=(datetime.now(timezone.utc)-timedelta(days=HISTORY_DAYS)).isoformat()
        return bot.sb('GET','price_history',params={'select':'price,recorded_at','product_url':f'eq.{url}','recorded_at':f'gte.{since}','order':'recorded_at.desc','limit':'120'})
    except Exception as e:
        print(f'GEÇMİŞ HATA | {type(e).__name__}: {e}');return []

def robust_history_ref(rows,current):
    vals=[]
    for r in rows:
        p=pprice(r.get('price'))
        if p and current*.75<=p<=current*1.60:vals.append(p)
    if len(vals)<3:return None
    higher=[p for p in vals if current*1.08<p<=current*1.50]
    if len(higher)<2:return None
    # Require the higher level to be a repeated state, not one bad scrape.
    if len(higher)/max(1,len(vals))<0.25:return None
    ref=float(statistics.median(higher))
    return ref if current*1.08<ref<=current*1.50 else None

def page_ref(pg,current):
    old=pprice(pg.get('old'))
    if old and current*1.08<old<=current*1.45:return old
    return None

def browser_inspect(page,url,expected=None):
    out={'ok':False,'available':None,'live':None,'old':None,'title':'','image':'','campaign':None}
    try:
        page.goto(url,wait_until='domcontentloaded',timeout=18000);page.wait_for_timeout(1300)
        body=re.sub(r'\s+',' ',page.locator('body').inner_text(timeout=6500));low=body.lower();out['ok']=True
        bad=['stokta yok','stokta bulunmuyor','stokta bulunmamaktadır','ürün tükendi','urun tukendi','currently unavailable','out of stock','sold out','satışa kapalı','satisa kapali']
        if any(x in low for x in bad):out['available']=False
        elif any(x in low for x in ['sepete ekle','hemen al','şimdi al','simdi al','add to cart','buy now','satın al','satin al']):out['available']=True
        vals=[];olds=[]
        selectors=['meta[property="product:price:amount"]','meta[itemprop="price"]','.a-price .a-offscreen','.apexPriceToPay .a-offscreen','[data-test-id="price-current-price"]','[class*="currentPrice"]','[class*="salePrice"]','.prc-dsc','.prc-slg','[itemprop="price"]']
        for sel in selectors:
            try:
                loc=page.locator(sel)
                for i in range(min(loc.count(),10)):
                    e=loc.nth(i);raw=e.get_attribute('content') or e.inner_text(timeout=350);x=pprice(raw)
                    if x and (not expected or expected*.45<=x<=expected*1.8):vals.append(x)
            except:pass
        for sel in ['del','s','.a-text-price .a-offscreen','.basisPrice .a-offscreen','[class*="oldPrice"]','[class*="listPrice"]']:
            try:
                loc=page.locator(sel)
                for i in range(min(loc.count(),8)):
                    x=pprice(loc.nth(i).inner_text(timeout=300))
                    if x:olds.append(x)
            except:pass
        if vals:
            vals=sorted(set(vals));out['live']=min(vals,key=lambda x:abs(x-expected)) if expected else min(vals)
        if out['live']:
            cand=[x for x in olds if out['live']*1.08<x<=out['live']*1.45]
            if cand:out['old']=float(statistics.median(cand))
        try:out['title']=re.sub(r'\s+',' ',page.locator('h1').first.inner_text(timeout=900)).strip()[:300]
        except:pass
        try:
            img=page.locator('meta[property="og:image"]').get_attribute('content');out['image']=img or ''
        except:pass
        return out
    except Exception as e:
        print(f'BROWSER HATA | {type(e).__name__} | {url[:100]}');return out

def maybe_record(url,site,price,rows):
    now=datetime.now(timezone.utc)
    if rows:
        try:last_dt=datetime.fromisoformat((rows[0].get('recorded_at') or '').replace('Z','+00:00'))
        except:last_dt=now-timedelta(days=1)
        last=pprice(rows[0].get('price'))
        if last and abs(last-price)/max(price,1)<.002 and now-last_dt<timedelta(hours=HISTORY_WRITE_HOURS):return
    try:bot.sb('POST','price_history',json={'price':price,'product_url':url,'site':site,'recorded_at':now.isoformat()})
    except Exception as e:print(f'GEÇMİŞ YAZ HATA | {type(e).__name__}')

def send_deal(row,title,current,ref,pg,campaign=None):
    site=row.get('site') or '';url=row.get('product_url') or '';disc=(ref-current)/ref*100
    last=row.get('last_posted_at')
    if last:
        try:
            if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=12):
                STATS['cooldown']+=1;return False
        except:pass
    lines=[f'⭐️⭐️⭐️ 🔥 %{disc:.0f} İNDİRİM','',html.escape(title),'',f'💰 {fmt(current)} TL',f'🏷️ Referans fiyat: {fmt(ref)} TL',f'🛍️ {html.escape(site)}']
    if campaign:
        lines.append(f'🎯 Kampanya: {html.escape(campaign.get("label") or "Kampanyalı alım")}')
        if campaign.get('qty'):lines.append(f'📦 {campaign["qty"]} adet alımda geçerli')
    u=outlink(url,site);lines += ['',f'👇 <a href="{html.escape(u,quote=True)}"><b>Fırsata git</b></a>']
    text='\n'.join(lines);kb={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}
    photo=pg.get('image') or ''
    try:
        if photo:
            rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendPhoto',data={'chat_id':CHANNEL_ID,'photo':photo,'caption':text[:1024],'parse_mode':'HTML','reply_markup':json.dumps(kb,ensure_ascii=False)},timeout=18)
        else:rr=None
        if not rr or not rr.ok:
            rr=requests.post(f'https://api.telegram.org/bot{bot.TOKEN}/sendMessage',json={'chat_id':CHANNEL_ID,'text':text,'parse_mode':'HTML','disable_web_page_preview':False,'reply_markup':kb},timeout=18)
        rr.raise_for_status()
        if row.get('id'):bot.sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat(),'last_posted_price':current})
        STATS['sent']+=1;print(f'Telegram HTTP: {rr.status_code} | foto={"var" if photo else "yok"}');return True
    except Exception as e:
        STATS['errors']+=1;print(f'GÖNDERİM HATA | {type(e).__name__}: {e}');return False

def main():
    print(f'=== API-SİZ fiyat takip motoru | limit={MAX_PRODUCTS} | geçmiş={HISTORY_DAYS} gün ===')
    try:
        rows=bot.sb('GET','products',params={'select':'*','order':'updated_at.asc.nullsfirst','limit':str(MAX_PRODUCTS*3)})
    except Exception as e:
        print(f'ÜRÜN LİSTESİ HATA: {type(e).__name__}: {e}');return
    valid=[]
    for r in rows:
        u=r.get('product_url') or '';s=r.get('site') or ''
        if u.startswith('http') and s in ('Amazon','Hepsiburada','Trendyol'):valid.append(r)
        if len(valid)>=MAX_PRODUCTS:break
    STATS['seen']=len(valid)
    browser=None;page=None;pw=None
    try:
        for row in valid:
            u=row.get('product_url') or '';site=row.get('site') or '';expected=pprice(row.get('current_price'));title=row.get('product_name') or 'Ürün';STATS['checked']+=1
            try:
                pg=inspect_page(u,expected)
                if pg.get('available') is False:
                    STATS['stock']+=1;print(f'STOK ENGELİ: {site} | stokta yok | {title[:100]} | {u}');continue
                if not pg.get('live'):
                    if pw is None:
                        pw=sync_playwright().start();browser=pw.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled']);page=browser.new_page(user_agent=HEAD['User-Agent'],locale='tr-TR')
                    pg2=browser_inspect(page,u,expected)
                    STATS['browser']+=1
                    if pg2.get('available') is False:
                        STATS['stock']+=1;print(f'STOK ENGELİ: {site} | stokta yok | {title[:100]} | {u}');continue
                    for k,v in pg2.items():
                        if v not in (None,'',False):pg[k]=v
                live=pprice(pg.get('live'))
                if not live:
                    STATS['no_price']+=1;print(f'FİYAT YOK: {site} | {title[:110]}');continue
                STATS['live']+=1
                if pg.get('title') and len(pg['title'])>8:title=pg['title']
                hrows=history_rows(u);href=robust_history_ref(hrows,live);pref=page_ref(pg,live)
                campaign=pg.get('campaign');effective=None
                if campaign and pprice(campaign.get('effective')):
                    e=pprice(campaign.get('effective'))
                    if e and e<live and e>=live*.45:effective=e
                current=effective or live
                if effective:
                    ref=live;STATS['campaign']+=1;refsrc='page-campaign'
                else:
                    refs=[x for x in (href,pref) if x and x>current]
                    ref=min(refs) if refs else None;refsrc='history' if href and ref==href else ('page-old' if pref and ref==pref else 'none')
                maybe_record(u,site,live,hrows)
                try:
                    if row.get('id'):bot.sb('PATCH',f'products?id=eq.{row["id"]}',json={'product_name':title,'current_price':live,'previous_price':ref,'updated_at':datetime.now(timezone.utc).isoformat()})
                except:pass
                print(f'Kontrol: {site} | {current:.2f} TL | referans={ref or 0:.2f} | geçmiş={len(hrows)} | kaynak={refsrc} | {title[:105]}')
                if not ref or ref<=current:
                    STATS['no_ref']+=1;continue
                disc=(ref-current)/ref*100
                if disc<MIN_DISCOUNT:
                    STATS['below']+=1;continue
                send_deal(row,title,current,ref,pg,campaign if effective else None)
            except Exception as e:
                STATS['errors']+=1;print(f'ÜRÜN HATA | {site} | {type(e).__name__}: {e}')
    finally:
        try:
            if browser:browser.close()
            if pw:pw.stop()
        except:pass
    print(f'=== Bitti. Hedef={STATS["seen"]} | kontrol={STATS["checked"]} canlı_fiyat={STATS["live"]} browser={STATS["browser"]} fiyat_yok={STATS["no_price"]} referans_yok={STATS["no_ref"]} esik_alti={STATS["below"]} stok_yok={STATS["stock"]} kampanya={STATS["campaign"]} cooldown={STATS["cooldown"]} hata={STATS["errors"]} | Gönderilen={STATS["sent"]} ===')

if __name__=='__main__':main()
