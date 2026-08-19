from pathlib import Path
import re

p = Path('bot.py')
s = p.read_text(encoding='utf-8')

# 1) Remove the old runtime Akakce function if a previous run injected it.
start = s.find('\ndef akakce_check(')
if start != -1:
    end = s.find('\ndef process(p):', start)
    if end == -1:
        raise SystemExit('Akakce block end not found')
    s = s[:start] + s[end:]

# 2) Remove the unsafe fallback that treated any unrelated higher number on a page as an old price.
s = s.replace(
'''    if not previous and ps:\n        higher=[x for x in ps if x>current*1.05]\n        if higher:previous=min(higher)''',
'''    # Never infer an old price from an arbitrary higher number on the page.''',
1)
s = s.replace(
'''        higher=[x for x in ps if current and x>current*1.05];previous=min(higher) if higher else None''',
'''        previous=None''',
1)

# 3) Harden Trendyol discovery by explicitly extracting href/url values containing -p-.
needle = '''    # Search-engine RSS is XML. Read <link> values explicitly before generic HTML parsing.\n'''
insert = '''    if site == "Trendyol":\n        for m in re.finditer(r''' + repr(r'''(?:href|url|link)\s*[:=]\s*["']([^"']+-p-\d+(?:[/?#][^"']*)?)''') + r''', html2, re.I):\n            add(m.group(1))\n            if len(out) >= MAX_PRODUCTS_PER_SITE:\n                return out\n        for m in re.finditer(r''' + repr(r'''https?://(?:www\.)?trendyol\.com/[^"'<>\s]+-p-\d+(?:[/?#][^"'<>\s]*)?''') + r''', html2, re.I):\n            add(m.group(0))\n            if len(out) >= MAX_PRODUCTS_PER_SITE:\n                return out\n'''
if needle in s and 'if site == "Trendyol":\n        for m in re.finditer' not in s:
    s = s.replace(needle, insert + needle, 1)

# 4) Replace page_product completely. This avoids regex replacement strings and supports Amazon meta/JSON prices.
ps = s.find('\ndef page_product(')
pe = s.find('\ndef direct_discover(', ps)
if ps == -1 or pe == -1:
    raise SystemExit('page_product boundaries not found')
page_func = r'''
def page_product(site,url,title,browser):
    ctx=browser.new_context(locale="tr-TR",timezone_id="Europe/Istanbul",user_agent=HEADERS["User-Agent"],viewport={"width":1440,"height":1000},extra_http_headers=HEADERS);page=ctx.new_page()
    try:
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        r=page.goto(url,wait_until="domcontentloaded",timeout=30000)
        if not r or r.status>=400:return None
        page.wait_for_timeout(1800)
        html=page.content();text=page.locator("body").inner_text(timeout=10000);jd=parse_jsonld(html)
        current=jd.get("price") or labeled(text,["Sepetteki Fiyat","Sepette","İndirimli Fiyat","Satış Fiyatı","Güncel Fiyat","Fiyat"])
        if current is None:
            for sel in ['meta[itemprop="price"]','meta[property="product:price:amount"]','meta[name="price"]']:
                try:
                    v=page.locator(sel).first.get_attribute("content")
                    current=price(v)
                    if current:break
                except Exception:pass
        if current is None and site=="Amazon":
            for pat in [r'"priceAmount"\s*:\s*"?([0-9.,]+)',r'"price"\s*:\s*"?([0-9.,]+)',r'"displayPrice"\s*:\s*"[^0-9]*([0-9.,]+)']:
                m=re.search(pat,html,re.I)
                if m:
                    current=price(m.group(1))
                    if current:break
        ps=prices(text)
        if current is None and ps:current=ps[0]
        previous=None
        return make_product(site,jd.get("name") or title,url,text,current,previous) or make_product(site,title,url,text,current,previous)
    except Exception as e:print(f"{site} ürün sayfası hata: {type(e).__name__}: {e}");return None
    finally:ctx.close()
'''
s = s[:ps] + page_func + s[pe:]

# 5) Inject Akakce current-market comparison. It is NOT treated as historical price.
ak = r'''
def akakce_check(name,current,browser):
    try:
        q=re.sub(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşü+ -]"," ",name or "").strip();q=re.sub(r"\s+"," ",q)[:180]
        if not q:return None
        ctx=browser.new_context(locale="tr-TR",timezone_id="Europe/Istanbul",user_agent=HEADERS["User-Agent"],viewport={"width":1440,"height":1000},extra_http_headers=HEADERS);page=ctx.new_page()
        try:
            u="https://www.akakce.com/arama/?q="+quote(q,safe="")
            r=page.goto(u,wait_until="domcontentloaded",timeout=30000)
            if not r or r.status>=400:return None
            page.wait_for_timeout(1200)
            candidates=[]
            for a in page.locator('a[href]').all():
                try:
                    h=a.get_attribute('href') or '';t=(a.inner_text() or '').strip()
                    if re.search(r',\d+\.html(?:$|[?#])',h) and t:candidates.append((h,t))
                except Exception:pass
            if not candidates:return None
            h,t=candidates[0]
            if not h.startswith('http'):h=urljoin('https://www.akakce.com',h)
            r=page.goto(h,wait_until="domcontentloaded",timeout=30000)
            if not r or r.status>=400:return None
            page.wait_for_timeout(1000)
            text=re.sub(r"\s+"," ",page.locator('body').inner_text(timeout=10000) or '')
            vals=[x for x in prices(text) if x>50]
            if not vals:return None
            low=min(vals)
            print(f"Akakce: {t[:80]} | en düşük={low:.2f} TL | bot={float(current):.2f} TL")
            return {"url":canonical(h),"low":low,"name":t[:300]}
        finally:ctx.close()
    except Exception as e:print(f"Akakce hata: {type(e).__name__}: {e}");return None
'''
proc = s.find('\ndef process(p):')
if proc == -1: raise SystemExit('process function not found')
s = s[:proc] + ak + s[proc:]

# 6) Add Akakce as a market-reference baseline only when it is higher than the bot price.
marker = '''    if len(hp)>=MIN_HISTORY_SAMPLES:\n        hm=median(hp)\n        if hm and hm>current:baseline=max(baseline or 0,hm)\n'''
addition = marker + '''    ak=akakce_check(p.get("name"),current,process.browser)\n    if ak and ak.get("low") and ak["low"]>current:\n        baseline=max(baseline or 0,float(ak["low"]))\n'''
if marker in s and 'ak=akakce_check(p.get("name")' not in s:
    s=s.replace(marker,addition,1)

# 7) Give process() access to the browser created by main().
s=s.replace(
'''browser=pw.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled","--no-sandbox"]);total=0''',
'''browser=pw.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled","--no-sandbox"]);process.browser=browser;total=0''',1)

p.write_text(s,encoding='utf-8')
print('runtime patch applied: robust product extraction')
