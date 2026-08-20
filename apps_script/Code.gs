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

    // Önce doğrudan marketplace denenir. 403/boş sonuçta Bing devreye girer.
    const direct = directSearch(site, term);
    if (direct.products.length) {
      return json({ok:true,site:site,q:term,source:'marketplace',http_status:direct.status,products:direct.products.slice(0,20)});
    }

    const bing = bingSearch(site, term);
    return json({
      ok:true,
      site:site,
      q:term,
      source:'bing',
      http_status:direct.status,
      products:bing.slice(0,20)
    });
  } catch (err) {
    return json({ok:false,error:String(err)});
  }
}

function directSearch(site, term) {
  const cfg = SITES[site];
  try {
    const res = UrlFetchApp.fetch(cfg.search + encodeURIComponent(term), {
      muteHttpExceptions:true,
      followRedirects:true,
      headers:{
        'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36',
        'Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'
      }
    });
    const status = res.getResponseCode();
    const products = extractProducts(site, res.getContentText());
    return {status:status,products:products};
  } catch (_) {
    return {status:0,products:[]};
  }
}

function bingSearch(site, term) {
  const cfg = SITES[site];
  const query = 'site:' + cfg.host + ' ' + term;
  const url = 'https://www.bing.com/search?format=rss&count=50&q=' + encodeURIComponent(query);
  const out = [];
  const seen = {};

  try {
    const res = UrlFetchApp.fetch(url, {
      muteHttpExceptions:true,
      followRedirects:true,
      headers:{
        'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36',
        'Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'
      }
    });
    const xml = res.getContentText();

    // Bing RSS'teki <link> alanları çoğunlukla doğrudan sonuç URL'sini verir.
    const links = xml.match(/<link>([\s\S]*?)<\/link>/gi) || [];
    for (const tag of links) {
      const raw = decodeXml(tag.replace(/^<link>/i,'').replace(/<\/link>$/i,''));
      add(site, raw, out, seen);
    }

    // Bazı Bing cevaplarında URL HTML içinde farklı biçimde gömülü olabilir.
    if (out.length < 20) {
      const direct = xml.match(new RegExp('https?:\\/\\/(?:www\\.)?' + escapeRegex(cfg.host) + '\\/[^\\s<>&"]+', 'ig')) || [];
      for (const u of direct) add(site, u, out, seen);
    }
  } catch (_) {}

  return out;
}

function extractProducts(site, html) {
  const cfg = SITES[site];
  const out = [];
  const seen = {};

  const re = /href\s*=\s*["']([^"']+)["']/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    const raw = decodeEntities(m[1]).replace(/\\\//g,'/');
    const urls = raw.match(new RegExp('https?:\\/\\/(?:www\\.)?' + escapeRegex(cfg.host) + '\\/[^\\s"<>]+', 'ig')) || [];
    for (const u0 of urls) add(site, u0, out, seen);
    if (out.length >= 20) break;
  }

  if (out.length < 20) {
    const re2 = new RegExp('https?:\\/\\/(?:www\\.)?' + escapeRegex(cfg.host) + '\\/[^\\s"<>\\\\]+', 'ig');
    const matches = html.match(re2) || [];
    for (const u0 of matches) add(site, u0, out, seen);
  }
  return out;
}

function add(site, raw, out, seen) {
  const cfg = SITES[site];
  let u = decodeEntities(raw)
    .replace(/\\\//g,'/')
    .replace(/["'<>]+$/g,'')
    .trim();
  if (!/^https?:\/\//i.test(u)) return;

  try {
    const x = new URL(u);
    const host = x.hostname.toLowerCase().replace(/^www\./,'');
    const clean = 'https://www.' + cfg.host + x.pathname.replace(/\/$/,'');
    if (host !== cfg.host || !cfg.re.test(x.pathname) || seen[clean]) return;
    seen[clean] = true;
    out.push({url:clean});
  } catch (_) {}
}

function decodeEntities(s) {
  return String(s || '')
    .replace(/&amp;/g,'&').replace(/&quot;/g,'"').replace(/&#39;/g,"'")
    .replace(/&lt;/g,'<').replace(/&gt;/g,'>');
}

function decodeXml(s) {
  return decodeEntities(String(s || '').replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g,'$1'));
}

function escapeRegex(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
