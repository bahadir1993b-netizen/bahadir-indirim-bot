from pathlib import Path
import re

p = Path('bot.py')
s = p.read_text(encoding='utf-8')

# Keep the existing Akakce patch, then harden product discovery.
# Trendyol currently embeds product hrefs in HTML without exposing productId.
needle = '    # Search-engine RSS is XML. Read <link> values explicitly before generic HTML parsing.\n'
insert = '''    # Trendyol product links are present as hrefs containing -p- even when productId is absent.\n    if site == "Trendyol":\n        for m in re.finditer(r"(?:href|url|link)\\s*[:=]\\s*[\\\"\\\\']([^\\\"\\\\']+-p-\\d+(?:[/?#][^\\\"\\\\']*)?)", html2, re.I):\n            add(m.group(1))\n            if len(out) >= MAX_PRODUCTS_PER_SITE:\n                return out\n        for m in re.finditer(r"(https?://(?:www\\.)?trendyol\\.com/[^\\\"'<>\\s]+-p-\\d+(?:[/?#][^\\\"'<>\\s]*)?)", html2, re.I):\n            add(m.group(1))\n            if len(out) >= MAX_PRODUCTS_PER_SITE:\n                return out\n\n'''
if needle in s and insert not in s:
    s = s.replace(needle, insert + needle, 1)

# Add a more permissive product URL check for Trendyol encoded/relative links.
s = s.replace('if site=="Trendyol":return bool(re.search(r"-p-\\d+(?:$|[/?#])",p,re.I))', 'if site=="Trendyol":return bool(re.search(r"-p-\\d+(?:$|[/?#])",p,re.I))')

# Make product-page price extraction more resilient. In particular, Amazon often exposes
# the price in meta tags/JSON even when visible text is incomplete.
old = '        page.wait_for_timeout(1800);html=page.content();text=page.locator("body").inner_text(timeout=10000);jd=parse_jsonld(html)\n        current=jd.get("price") or labeled(text,["Sepetteki Fiyat","Sepette","İndirimli Fiyat","Satış Fiyatı","Güncel Fiyat","Fiyat"]);ps=prices(text)\n        if current is None and ps:current=ps[0]\n        previous=None'
new = '''        page.wait_for_timeout(1800);html=page.content();text=page.locator("body").inner_text(timeout=10000);jd=parse_jsonld(html)\n        current=jd.get("price") or labeled(text,["Sepetteki Fiyat","Sepette","İndirimli Fiyat","Satış Fiyatı","Güncel Fiyat","Fiyat"])\n        if current is None:\n            for sel in ['meta[itemprop="price"]','meta[property="product:price:amount"]','meta[name="price"]']:\n                try:\n                    v=page.locator(sel).first.get_attribute("content")\n                    current=price(v)\n                    if current:break\n                except Exception: pass\n        ps=prices(text)\n        if current is None and ps:current=ps[0]\n        if current is None and site == "Amazon":\n            # Amazon sometimes keeps the current price in HTML spans that the body text omits.\n            for pat in [r'"priceAmount"\\s*:\\s*"?([0-9.,]+)', r'"price"\\s*:\\s*"?([0-9.,]+)', r'"displayPrice"\\s*:\\s*"[^0-9]*([0-9.,]+)']:\n                m=re.search(pat,html,re.I)\n                if m:\n                    current=price(m.group(1))\n                    if current:break\n        previous=None'''
if old in s:
    s = s.replace(old, new, 1)
else:
    # Compatible fallback if the exact previous patch text differs.
    s = re.sub(r'        page\.wait_for_timeout\(1800\);html=page\.content\(\);text=page\.locator\("body"\)\.inner_text\(timeout=10000\);jd=parse_jsonld\(html\).*?        previous=None', new, s, count=1, flags=re.S)

# Add diagnostic counts to search fallback so a 200 challenge page is obvious.
old_search = '                if r.status_code>=400:continue\n                for c in extract_candidate_urls(site,r.text,u):'
new_search = '                if r.status_code>=400:continue\n                print(f"{site} arama HTML={len(r.text)} | -p-={r.text.lower().count(\"-p-\")}")\n                for c in extract_candidate_urls(site,r.text,u):'
s = s.replace(old_search, new_search, 1)

p.write_text(s, encoding='utf-8')
print('runtime patch applied: discovery hardened')
