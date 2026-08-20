import bot

# Hepsiburada + Trendyol keşfi bot.py'deki ortak, daha geniş arama-motoru
# keşif katmanını kullanır. Amazon run_bot.py tarafından ayrı çalıştırılır.
bot.SEEDS = {
    'Hepsiburada': 'https://www.hepsiburada.com/ara?q=indirim',
    'Trendyol': 'https://www.trendyol.com/sr?q=indirim',
}

# Marketplace tarafında 5 ürünlük örnek yerine daha geniş tarama yap.
bot.MAX_PRODUCTS_PER_SITE = 12

# bot.main() zaten doğrudan sayfa + arama motoru fallback akışını yürütüyor.
bot.main()
