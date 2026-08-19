from pathlib import Path
import re

p = Path('bot.py')
s = p.read_text(encoding='utf-8')

start = s.find('\ndef akakce_check(')
if start != -1:
    end = s.find('\ndef process(p):', start)
    if end == -1:
        raise SystemExit('Akakce block end not found')
    s = s[:start] + s[end:]

func = r'''
def akakce_check(name, current, browser):
    try:
        q = re.sub(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşü+ -]", " ", name or "").strip()
        q = re.sub(r"\s+", " ", q)[:180]
        if not q:
            return None
        ctx = browser.new_context(locale="tr-TR", timezone_id="Europe/Istanbul", user_agent=HEADERS["User-Agent"], viewport={"width":1440,"height":1000}, extra_http_headers=HEADERS)
        page = ctx.new_page()
        try:
            search_url = "https://www.akakce.com/arama/?q=" + quote(q, safe="")
            r = page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            if not r or r.status >= 400:
                print(f"Akakce arama HTTP: {r.status if r else 0}")
                return None
            page.wait_for_timeout(1200)
            candidates = []
            for a in page.locator('a[href]').all():
                try:
                    href = a.get_attribute('href') or ''
                    txt = (a.inner_text() or '').strip()
                    if re.search(r',\d+\.html(?:$|[?#])', href) and txt:
                        candidates.append((href, txt))
                except Exception:
                    pass
            if not candidates:
                print('Akakce: ürün sonucu bulunamadı')
                return None
            href, title = candidates[0]
            if not href.startswith('http'):
                href = urljoin('https://www.akakce.com', href)
            rp = page.goto(href, wait_until="domcontentloaded", timeout=30000)
            if not rp or rp.status >= 400:
                print(f"Akakce ürün HTTP: {rp.status if rp else 0}")
                return None
            page.wait_for_timeout(1000)
            text = re.sub(r"\s+", " ", page.locator('body').inner_text(timeout=10000) or '')
            vals = [x for x in prices(text) if x > 50]
            if not vals:
                return None
            low = min(vals)
            print(f"Akakce: {title[:80]} | en düşük={low:.2f} TL | bot={float(current):.2f} TL")
            return {"url": canonical(href), "low": low, "name": title[:300]}
        finally:
            ctx.close()
    except Exception as e:
        print(f"Akakce hata: {type(e).__name__}: {e}")
        return None
'''

marker = '\ndef process(p):'
pos = s.find(marker)
if pos == -1:
    raise SystemExit('process function not found')
s = s[:pos] + func + s[pos:]

# Never use arbitrary unrelated prices on a page as the old price.
s = re.sub(r'\n    if not previous and ps:\n        higher=\[x for x in ps if x>current\*1\.05\]\n        if higher:previous=min\(higher\)', '', s, count=1)
s = re.sub(r'\n        higher=\[x for x in ps if current and x>current\*1\.05\];previous=min\(higher\) if higher else None', '\n        previous=None', s, count=1)

old = 'def process(p):\n    now=datetime.now(timezone.utc);url=p["url"];current=float(p["price"]);hist=history(url);hp=[float(x["price"]) for x in hist if x.get("price") is not None]\n    baseline=float(p["previous_display_price"]) if p.get("previous_display_price") and float(p["previous_display_price"])>current else None'
new = 'def process(p):\n    now=datetime.now(timezone.utc);url=p["url"];current=float(p["price"]);hist=history(url);hp=[float(x["price"]) for x in hist if x.get("price") is not None]\n    baseline=float(p["previous_display_price"]) if p.get("previous_display_price") and float(p["previous_display_price"])>current else None\n    ak=akakce_check(p.get("name"),current,process.browser)\n    if ak and ak.get("low") and ak["low"]>current:\n        baseline=max(baseline or 0,float(ak["low"]))'
if old not in s:
    raise SystemExit('process insertion point not found')
s = s.replace(old, new, 1)

needle = 'browser=pw.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled","--no-sandbox"]);total=0'
s = s.replace(needle, needle.replace(';total=0', ';process.browser=browser;total=0'), 1)

p.write_text(s, encoding='utf-8')
print('runtime patch applied: Akakce integration fixed')
