import re
import bot

bot.SEEDS = {
    'Hepsiburada': 'https://www.hepsiburada.com/ara?q=indirim',
    'Trendyol': 'https://www.trendyol.com/sr?q=indirim',
}


def diagnostic_discover(site, seed, browser):
    page = browser.new_page(locale='tr-TR')
    page.set_default_timeout(5000)
    page.set_default_navigation_timeout(20000)
    try:
        r = page.goto(seed, wait_until='domcontentloaded')
        status = r.status if r else 0
        page.wait_for_timeout(2200)
        html = page.content()
        body = ''
        try:
            body = page.locator('body').inner_text(timeout=3000)
        except Exception:
            pass
        got = bot.candidates(site, html, seed)
        print(f'{site} web HTTP: {status}')
        print(f'{site} final URL: {page.url}')
        print(f'{site} title: {page.title()}')
        print(f'{site} sayfa aday: {len(got)}')
        if not got:
            compact = re.sub(r'\s+', ' ', body or '')[:500]
            print(f'{site} sayfa debug: {compact}')
        return got
    except Exception as e:
        print(f'{site} discover hata: {type(e).__name__}: {e}')
        return []
    finally:
        page.close()


# Arama motoru fallback'i bu testte kapatıyoruz; yalnızca sitelerin VPS'e ne döndürdüğünü görelim.
bot.discover = diagnostic_discover
bot.search_fallback = lambda site: []
bot.main()
