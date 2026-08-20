from pathlib import Path

# marketplace_scanner.py artık kendi doğrulama mantığını taşıyor.
# Eski runtime metin patch'i dosyayı bozabildiği için burada dosyaya dokunmuyoruz.
P = Path("marketplace_scanner.py")
compile(P.read_text(encoding="utf-8"), str(P), "exec")
print("Marketplace runtime patch: değişiklik yok; marketplace_scanner.py sözdizimi doğrulandı")
