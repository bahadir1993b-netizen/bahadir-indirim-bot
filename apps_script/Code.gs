const SITES = {
  Hepsiburada: {
    search: 'https://www.hepsiburada.com/ara?q=',
    host: 'hepsiburada.com',
    re: /-p-[A-Za-z0-9]+(?:[/?#&]|$)/i
  },
  Trendyol: {
    search: 'https://www.trendyol.com/sr?q=',
    host: 'trendyol.com',
    re: /-p-\d+(?:[/?#&]|$)/i
  }
};

function doGet(e) {
  try {
    const site = String((e.parameter && e.parameter.site) || '').trim();
    const term = String((e.parameter && e.parameter.q) || '').trim();
    if (!SITES[site] || !term) return json({ok:false,error:'site ve q gerekli'});

    const cfg = SITES[site];
    const url = cfg.search + encodeURIComponent(term);
    const res = UrlFetchApp.fetch(url, {
      muteHttpExceptions: true,
      followRedirects: true,
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; GoogleAppsScript/1.0)',
        'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8'
      }
    });

    const status = res.getResponseCode();
    const html = res.getContentText();
    const products = extractProducts(site, html);

    return json({ok:true,site:site,q:term,http_status:status,products:products.slice(0,20)});
  } catch (err) {
    return json({ok:false,error:String(err)});
  }
}

function extractProducts(site, html) {
  const cfg = SITES[site];
  const out = [];
  const seen = {};

  // href="..." / href='...' içindeki gerçek marketplace URL'leri.
  const re = /href\\s*=\\s*["']([^"']+)["']/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    const raw = decodeEntities(m[1]).replace(/\\\\\//g,'/');
    const urls = raw.match(new RegExp('https?:\\/\\/(?:www\\.)?' + escapeRegex(cfg.host) + '\\/[^\\s"<>]+', 'ig')) || [];
    for (const u0 of urls) add(site, u0, out, seen);
    if (out.length >= 20) break;
  }

  // HTML içinde href olmadan gömülü tam URL kalmışsa.
  if (out.length < 20) {
    const re2 = new RegExp('https?:\\/\\/(?:www\\.)?' + escapeRegex(cfg.host) + '\\/[^\\s"<>\\\\]+', 'ig');
    const matches = html.match(re2) || [];
    for (const u0 of matches) add(site, u0, out, seen);
  }
  return out;
}

function add(site, raw, out, seen) {
  const cfg = SITES[site];
  let u = decodeEntities(raw).replace(/\\\\\//g,'/').replace(/["'<>]+$/g,'');
  if (!/^https?:\\/\\//i.test(u)) u = 'https://www.' + cfg.host + (u.startsWith('/') ? u : '/' + u);
  try {
    const x = new URL(u);
    const clean = 'https://www.' + cfg.host + x.pathname.replace(/\\/$/,'');
    if (!cfg.re.test(x.pathname) || seen[clean]) return;
    seen[clean] = true;
    out.push({url:clean});
  } catch (_) {}
}

function decodeEntities(s) {
  return String(s || '')
    .replace(/&amp;/g,'&').replace(/&quot;/g,'"').replace(/&#39;/g,"'")
    .replace(/&lt;/g,'<').replace(/&gt;/g,'>');
}

function escapeRegex(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
