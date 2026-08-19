from pathlib import Path

p = Path("bot.py")
s = p.read_text(encoding="utf-8")

# Replace URL discovery with a simple, syntax-safe implementation.
start = s.find("def extract_candidate_urls(")
end = s.find("def parse_jsonld(", start)
if start < 0 or end < 0:
    raise SystemExit("extract_candidate_urls boundaries not found")

extract_func = r'''def extract_candidate_urls(site, html, base):
    html = html or ""
    html2 = html.replace("\\/", "/").replace("\\u002F", "/").replace("\\u003A", ":")
    soup = BeautifulSoup(html, "html.parser")
    out = []
    seen = set()

    def add(raw, title=""):
        raw = unwrap(raw).replace("\\/", "/")
        if not raw:
            return
        u = canonical(urljoin(base, raw))
        if product_url(site, u) and u not in seen:
            seen.add(u)
            out.append((u, (title or "Ürün").strip()[:300]))

    for a in soup.find_all("a", href=True):
        add(a.get("href"), a.get("title") or a.get("aria-label") or a.get_text(" ", strip=True))
        if len(out) >= MAX_PRODUCTS_PER_SITE:
            return out

    if site == "Trendyol":
        for m in re.finditer(r'''href=["']([^"']+-p-\d+(?:[/?#][^"']*)?)["']''', html2, re.I):
            add(m.group(1))
            if len(out) >= MAX_PRODUCTS_PER_SITE:
                return out
        for m in re.finditer(r'''https?://(?:www\.)?trendyol\.com/[^"'<>\s]+-p-\d+(?:[/?#][^"'<>\s]*)?''', html2, re.I):
            add(m.group(0))
            if len(out) >= MAX_PRODUCTS_PER_SITE:
                return out
        for m in re.finditer(r'''/[^"'<>\s]+-p-\d+(?:[/?#][^"'<>\s]*)?''', html2, re.I):
            add(m.group(0))
            if len(out) >= MAX_PRODUCTS_PER_SITE:
                return out

    domain = {"Amazon": "amazon.com.tr", "Hepsiburada": "hepsiburada.com", "Trendyol": "trendyol.com"}[site]
    patterns = [
        rf"https?://(?:www\.)?{re.escape(domain)}[^\"'<>\s]+",
        rf"(?:https?:)?//(?:www\.)?{re.escape(domain)}[^\"'<>\s]+",
        rf"/[^\"'<>\s]+-p-[A-Za-z0-9]+(?:[/?#][^\"'<>\s]*)?",
        r"/(?:dp|gp/product|gp/aw/d)/[A-Za-z0-9]{8,}(?:[/?#][^\"'<>\s]*)?",
    ]
    for pat in patterns:
        for m in re.finditer(pat, html2, re.I):
            add(m.group(0))
            if len(out) >= MAX_PRODUCTS_PER_SITE:
                return out
    return out

'''
s = s[:start] + extract_func + s[end:]

# Replace page parsing. Do not invent an old price from unrelated numbers.
start = s.find("def page_product(")
end = s.find("def direct_discover(", start)
if start < 0 or end < 0:
    raise SystemExit("page_product boundaries not found")

page_func = r'''def page_product(site, url, title, browser):
    ctx = browser.new_context(locale="tr-TR", timezone_id="Europe/Istanbul", user_agent=HEADERS["User-Agent"], viewport={"width":1440,"height":1000}, extra_http_headers=HEADERS)
    page = ctx.new_page()
    try:
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        r = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if not r or r.status >= 400:
            print(f"{site} ürün HTTP: {r.status if r else 0} | {url}")
            return None
        page.wait_for_timeout(1200)
        html = page.content()
        text = page.locator("body").inner_text(timeout=10000)
        jd = parse_jsonld(html)
        current = jd.get("price") or labeled(text, ["Sepetteki Fiyat", "Sepette", "İndirimli Fiyat", "Satış Fiyatı", "Güncel Fiyat", "Fiyat"])
        if current is None:
            for sel in ['meta[itemprop="price"]', 'meta[property="product:price:amount"]', 'meta[name="price"]']:
                try:
                    current = price(page.locator(sel).first.get_attribute("content"))
                    if current:
                        break
                except Exception:
                    pass
        if current is None and site == "Amazon":
            for pat in [r'"priceAmount"\s*:\s*"?([0-9.,]+)', r'"displayPrice"\s*:\s*"[^0-9]*([0-9.,]+)']:
                m = re.search(pat, html, re.I)
                if m:
                    current = price(m.group(1))
                    if current:
                        break
        ps = prices(text)
        if current is None and ps:
            current = ps[0]
        return make_product(site, jd.get("name") or title, url, text, current, None)
    except Exception as e:
        print(f"{site} ürün sayfası hata: {type(e).__name__}: {e}")
        return None
    finally:
        ctx.close()

'''
s = s[:start] + page_func + s[end:]

# Remove any previous Akakce injection.
start = s.find("def akakce_check(")
if start >= 0:
    end = s.find("def process(p):", start)
    if end < 0:
        raise SystemExit("Akakce/process boundary not found")
    s = s[:start] + s[end:]

# Remove any Akakce call left inside process.
s = re.sub(r'^\s*ak\s*=\s*akakce_check\([^\n]*\)\s*\n(?:\s*if ak[^\n]*\n(?:\s*baseline[^\n]*\n)?)?', '', s, flags=re.M)

# Never use arbitrary higher numbers as historical prices.
s = s.replace(
    "    if not previous and ps:\n        higher=[x for x in ps if x>current*1.05]\n        if higher:previous=min(higher)",
    "    # Historical price must come from explicit data or stored history.\n",
)
s = s.replace(
    "        higher=[x for x in ps if current and x>current*1.05];previous=min(higher) if higher else None",
    "        previous=None",
)

p.write_text(s, encoding="utf-8")
print("runtime patch applied: syntax-safe discovery; Akakce disabled")
