from pathlib import Path

# marketplace_scanner.py artık güvenilir fiyat geçmişi/doğrulama mantığını kendi içinde taşıyor.
# Eski runtime metin patch'i kodu bozabildiği için burada dosyaya dokunmuyoruz.
P = Path("marketplace_scanner.py")
compile(P.read_text(encoding="utf-8"), str(P), "exec")
print("Scanner runtime patch: değişiklik yok; marketplace_scanner.py sözdizimi doğrulandı")
