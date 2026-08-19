from pathlib import Path
import re

p=Path('bot.py')
s=p.read_text(encoding='utf-8')

# Rastgele yuksek sayilari eski fiyat kabul etme.
s=s.replace('    if not previous and ps:\n        higher=[x for x in ps if x>current*1.05]\n        if higher:previous=min(higher)\n', '')
s=s.replace('        higher=[x for x in ps if current and x>current*1.05];previous=min(higher) if higher else None\n', '        previous=None\n')

# Sayfanin ilan ettigi indirim oranini yakala.
old='    return {"name":re.sub(r"\\s+"," ",name or "Ürün").strip()[:300],"price":current,"previous_display_price":previous if previous and previous>current else None,"campaign_price":campaign if campaign and campaign<current else None,"coupon_code":coupon,"campaign_note":None,"url":url,"site":site}\n'
new='    declared_discount=None\n    dm=re.search(r"(\\d+(?:[.,]\\d+)?)\\s*%\\s*(?:indirim|tasarruf|discount)",text,re.I)\n    if dm:\n        try: declared_discount=float(dm.group(1).replace(",","."))\n        except: pass\n    return {"name":re.sub(r"\\s+"," ",name or "Ürün").strip()[:300],"price":current,"previous_display_price":previous if previous and previous>current else None,"campaign_price":campaign if campaign and campaign<current else None,"coupon_code":coupon,"campaign_note":None,"declared_discount":declared_discount,"url":url,"site":site}\n'
if old in s:s=s.replace(old,new)

# JSON-LD ismi yoksa sayfa basligini kullan.
old='        return make_product(site,jd.get("name") or title,url,text,current,previous) or make_product(site,title,url,text,current,previous)\n'
new='        page_title=page.title() or title or "Ürün"\n        return make_product(site,jd.get("name") or page_title,url,text,current,previous) or make_product(site,page_title,url,text,current,previous)\n'
if old in s:s=s.replace(old,new)

# Akakce kontrolu: kendi buldugumuz urunu Akakce'de arar ve mevcut piyasa fiyatlarini/grafik verisini kontrol eder.
akakce_code=r'''\n\ndef akakce_check(name,current,browser):\n    try:\n        q=re.sub(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşü+ -]"," ",name or "").strip()\n        q=re.sub(r"\\s+"," ",q)[:180]\n        if not q:return None\n        ctx=browser.new_context(locale="tr-TR",timezone_id="Europe/Istanbul",user_agent=HEADERS["User-Agent"],viewport={"width":1440,"height":1000},extra_http_headers=HEADERS);page=ctx.new_page()\n        try:\n            url="https://www.akakce.com/arama/?q="+quote(q,safe="")\n            r=page.goto(url,wait_until="domcontentloaded",timeout=30000)\n            if not r or r.status>=400:\n                print(f"Akakce arama HTTP: {r.status if r else 0}");return None\n            page.wait_for_timeout(1500)\n            links=[]\n            for a in page.locator('a[href]').all():\n                try:\n                    href=a.get_attribute("href") or "";txt=(a.inner_text() or "").strip()\n                    if re.search(r"(?:https?://)?(?:www\\.)?akakce\\.com/[^\"'<> ]+?,\\d+\\.html",href,re.I) and href not in [x[0] for x in links]:links.append((href,txt))\n                except:pass\n            if not links:\n                print("Akakce: eslesen urun bulunamadi");return None\n            ak_url=canonical(urljoin("https://www.akakce.com",links[0][0]))\n            print(f"Akakce urun: {ak_url}")\n            rr=page.goto(ak_url,wait_until="domcontentloaded",timeout=30000)\n            if not rr or rr.status>=400:\n                print(f"Akakce urun HTTP: {rr.status if rr else 0}");return None\n            page.wait_for_timeout(1200)\n            html=page.content();text=re.sub(r"\\s+"," ",page.locator("body").inner_text(timeout=10000) or "").strip()\n            vals=prices(text)\n            market=[x for x in vals if 1<x<current*3]\n            ak_low=min(market) if market else None\n            if ak_low:print(f"Akakce piyasa en dusuk: {ak_low:.2f} TL | bot fiyat: {current:.2f} TL")\n            # JS icindeki fiyat serilerinden birden fazla yuksek nokta yakalanirsa gecmis referansi olarak kullan.\n            chart_prices=[]\n            for pat in [r"(?:price|fiyat)[^\\n]{0,120}(\\d{2,3}(?:[.,]\\d{3})+(?:[.,]\\d{1,2})?)"]:\n                for m in re.finditer(pat,html,re.I):\n                    v=price(m.group(1))\n                    if v and v>current*1.05 and v<current*3:chart_prices.append(v)\n            hist_max=max(chart_prices) if len(set(chart_prices))>=2 else None\n            return {"url":ak_url,"low":ak_low,"history_max":hist_max,"name":links[0][1]}\n        finally:ctx.close()\n    except Exception as e:\n        print(f"Akakce kontrol hata: {type(e).__name__}: {e}");return None\n'''
marker='\ndef process(p):\n'
if 'def akakce_check(' not in s:s=s.replace(marker,akakce_code+marker)

# Process basinda Akakce kontrolu yap.
old='def process(p):\n    now=datetime.now(timezone.utc);url=p["url"];current=float(p["price"]);hist=history(url);hp=[float(x["price"]) for x in hist if x.get("price") is not None]\n'
new='def process(p):\n    now=datetime.now(timezone.utc);url=p["url"];current=float(p["price"]);hist=history(url);hp=[float(x["price"]) for x in hist if x.get("price") is not None]\n    ak=akakce_check(p.get("name"),current,process.browser)\n    if ak: p["akakce_url"]=ak.get("url")\n'
if old in s:s=s.replace(old,new)
else: raise SystemExit("process header not found")

# Kendi Supabase gecmisimize ek olarak Akakce grafik verisini kullan.
old='    if len(hp)>=MIN_HISTORY_SAMPLES:\n        hm=median(hp)\n        if hm and hm>current:baseline=max(baseline or 0,hm)\n'
new='    if len(hp)>=MIN_HISTORY_SAMPLES:\n        hm=median(hp)\n        if hm and hm>current:baseline=max(baseline or 0,hm)\n    if ak and ak.get("history_max") and ak["history_max"]>current:\n        baseline=max(baseline or 0,float(ak["history_max"]))\n'
if old in s:s=s.replace(old,new)

# Site ilan ediyorsa onun indirim oranini ust sinir yap.
old='    discount=((baseline-current)/baseline*100) if baseline and baseline>current else 0\n    print(f"DEĞERLENDİR: {p[\'site\']} | {current:.2f} TL | baz={baseline} | indirim=%{discount:.1f}")\n'
new='    discount=((baseline-current)/baseline*100) if baseline and baseline>current else 0\n    declared=p.get("declared_discount")\n    if declared is not None: discount=min(discount,float(declared)) if discount else float(declared)\n    print(f"DEĞERLENDİR: {p[\'site\']} | {current:.2f} TL | baz={baseline} | indirim=%{discount:.1f} | Akakce={ak.get(\"low\") if ak else None}")\n'
if old in s:s=s.replace(old,new)

# Browser'i process'e aktar.
old='        browser=pw.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled","--no-sandbox"]);total=0\n'
new='        browser=pw.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled","--no-sandbox"]);process.browser=browser;total=0\n'
if old in s:s=s.replace(old,new)

p.write_text(s,encoding="utf-8")
print("runtime patch applied: Akakce kontrolu eklendi")
