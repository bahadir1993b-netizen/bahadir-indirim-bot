import bot

# Amazon'da .a-price-fraction gibi yalnızca kuruşu gösteren elemanların
# gerçek ürün fiyatı sanılmasını engelle.
_original_product_page = bot.product_page

def product_page(site, url, title, browser, search_ps=None):
    if site != 'Amazon':
        return _original_product_page(site, url, title, browser, search_ps)

    # Amazon sayfasını kendi fonksiyonundan alıyoruz; ancak 91 TL gibi
    # yalnızca kuruş değerleri oluşursa güvenli bir fiyat kontrolü uyguluyoruz.
    p = _original_product_page(site, url, title, browser, search_ps)
    if p and p.get('price', 0) < 100:
        # Şüpheli: Amazon ürünlerinde 100 TL altı fiyatı doğrudan kabul etme.
        # Aynı ürünün Supabase geçmişi varsa geçmişe göre gerçek fiyatı koru.
        hist = bot.history(p['url'])
        if hist:
            plausible = [x for x in hist if x >= 100]
            if plausible:
                p['price'] = min(plausible)
                p['previous'] = max(plausible)
                return p
        return None
    return p

bot.product_page = product_page
bot.main()
