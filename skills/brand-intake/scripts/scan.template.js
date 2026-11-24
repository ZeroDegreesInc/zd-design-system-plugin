// /brand-intake: measure a live site's visual system. It changes nothing.
// intake.py fills in INPUT. Run it in the browser on any page of the site: it loads each page in
// INPUT.pages in a hidden same-origin frame, at each width in INPUT.widths, and measures computed
// styles. Results are merged across pages; every item keeps a count and the pages it was seen on.
await (async () => {
const INPUT = __INPUT__;

const MAX_EL = INPUT.max_elements || 4000;

// pages: the ones given ({url, kind}), or the home page plus one page of each kind of template the
// links suggest, then one per remaining section. Kinds are guesses from paths and link text.
const KINDS = [
  ['form', /\b(apply|request|inquir\w*|contact|register|rsvp|sign-?up)\b|\bform\b/i],
  ['news or story', /\b(news|stor(y|ies)|articles?|blog|press)\b/i],
  ['event', /\b(events?|calendar)\b/i],
  ['program or product', /\b(programs?|degrees?|majors?|minors?|courses?|products?)\b/i],
  ['people', /\b(faculty|staff|directory|team|people|profiles?)\b/i],
];
// a path's folders below the site's own prefix: a first folder nearly every link shares (such as /nextgen/),
// locale folders (/us/, /en/, /global/) and a trailing .html don't count. Depth is how many are left.
const segs = p => p.replace(/\.html?$/, '').split('/').filter(Boolean);
const LOCALE = /^([a-z]{2}([-_][a-z]{2})?|global|intl|international)$/i;
function sitePath(paths) {
  const lead = {};
  for (const p of paths) { const s = segs(p)[0]; if (s) lead[s] = (lead[s] || 0) + 1; }
  const common = new Set(Object.entries(lead).filter(([, n]) => n >= paths.length * 0.8).map(([s]) => s));
  return p => { const s = segs(p); return s.filter((x, i) => i === s.length - 1 || !((i === 0 && common.has(x)) || LOCALE.test(x))); };
}
INPUT.pages = (INPUT.pages || []).map(p => typeof p === 'string' ? { url: p, kind: '' } : p);
if (INPUT.auto_pages) {
  const links = [...document.querySelectorAll('header a[href], nav a[href], main a[href], footer a[href]')]
    .map(a => { try { return { u: new URL(a.href), text: (a.innerText || '').trim() }; } catch (e) { return null; } })
    .filter(l => l && l.u.origin === location.origin && !l.u.hash && !/\.(pdf|jpe?g|png|docx?|xlsx?|zip)$/i.test(l.u.pathname) && l.u.pathname.length > 1);
  const rel = sitePath(links.map(l => l.u.pathname));
  const depth = l => rel(l.u.pathname).length;
  const kindOf = l => (KINDS.find(([, re]) => re.test(l.u.pathname.replace(/[-_/]/g, ' ') + ' ' + l.text)) || [])[0] || (depth(l) === 1 ? 'section landing' : 'content');
  const picks = [{ url: location.origin + location.pathname, kind: 'home' }], used = new Set(['/', location.pathname]);
  const take = (l, kind) => { if (l && !used.has(l.u.pathname)) { used.add(l.u.pathname); picks.push({ url: l.u.origin + l.u.pathname, kind }); } };
  take(links.find(l => kindOf(l) === 'section landing' && !used.has(l.u.pathname)), 'section landing');
  take([...links].filter(l => kindOf(l) === 'content').sort((a, b) => depth(b) - depth(a))[0], 'content');
  for (const [k] of KINDS) take([...links].filter(l => kindOf(l) === k).sort((a, b) => depth(b) - depth(a))[0], k);
  const sections = new Set(picks.map(p => rel(new URL(p.url).pathname)[0]));
  for (const l of [...links].sort((a, b) => depth(b) - depth(a))) {
    if (picks.length >= INPUT.auto_pages) break;
    const top = rel(l.u.pathname)[0];
    if (!sections.has(top)) { sections.add(top); take(l, kindOf(l)); }
  }
  INPUT.pages = picks.slice(0, INPUT.auto_pages);
}
const hex2 = n => Math.round(n).toString(16).padStart(2, '0');
function color(c) {
  const m = /^rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)$/.exec(c || '');
  if (!m) return c && c !== 'none' ? c : null;
  const a = m[4] === undefined ? 1 : +m[4];
  if (a === 0) return null;
  return '#' + hex2(m[1]) + hex2(m[2]) + hex2(m[3]) + (a < 1 ? '/' + Math.round(a * 100) / 100 : '');
}
const px = v => { const n = parseFloat(v); return isNaN(n) ? null : Math.round(n * 10) / 10; };
const first = f => (f || '').split(',')[0].replace(/["']/g, '').trim();
const short = t => (t || '').replace(/\s+/g, ' ').trim().slice(0, 40);

const out = { nonce: INPUT.nonce, origin: location.origin, scanned_at: new Date().toISOString(), pages: [], widths: {} };

function bump(map, key, by, page, extra) {
  if (key === null || key === undefined) return;
  const e = map[key] || (map[key] = { n: 0, pages: [] });
  e.n += by;
  if (!e.pages.includes(page)) e.pages.push(page);
  if (extra && !e.ex) e.ex = extra;
}

function backgroundOf(el, win) {
  const box = el.getBoundingClientRect();
  const overlaps = m => { const r = m.getBoundingClientRect(); return r.width > 40 && r.left < box.right && r.right > box.left && r.top < box.bottom && r.bottom > box.top; };
  for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
    const s = win.getComputedStyle(e);
    if (s.backgroundImage && s.backgroundImage !== 'none') return 'image';
    // a photo or video laid behind the text as its own element, not as a CSS background
    if (e !== el && [...e.querySelectorAll('img, video, picture, canvas')].some(m => !el.contains(m) && overlaps(m))) return 'image';
    const c = color(s.backgroundColor);
    if (c && !c.includes('/')) return c;
    if (c && parseFloat(c.split('/')[1]) >= 0.5) return c.split('/')[0];
  }
  return '#ffffff';
}

const PATTERNS = {
  'navigation': 'nav, [role=navigation]',
  'mega menu': '[class*=mega], nav li ul ul',
  'breadcrumbs': '[aria-label*=readcrumb], [class*=breadcrumb]',
  'tabs': '[role=tablist], [class*=tabs]',
  'accordion': 'details, [class*=accordion], [class*=collapse][aria-expanded], button[aria-expanded][aria-controls]',
  'carousel': '[class*=carousel], [class*=slider], [class*=swiper], [class*=slick], [class*=splide], [class*=glide]',
  'modal': 'dialog, [role=dialog], [class*=modal]',
  'table': 'table',
  'pagination': '[class*=pagination], nav[aria-label*=agination]',
  'search': 'input[type=search], [role=search], form[action*=search]',
  'form': 'form',
  'select': 'select',
  'checkbox': 'input[type=checkbox]',
  'radio': 'input[type=radio]',
  'video': 'video, iframe[src*=youtube], iframe[src*=vimeo]',
  'card': '[class*=card]',
  'badge or tag': '[class*=badge], [class*=tag], [class*=chip], [class*=pill], [class*=label]:not(label)',
  'alert or banner': '[role=alert], [class*=alert], [class*=notice], [class*=announcement]',
  'tooltip': '[role=tooltip], [data-toggle=tooltip], [data-bs-toggle=tooltip], [class*=tooltip]',
  'avatar': '[class*=avatar], [class*=headshot], [class*=profile] img',
  'hero': '[class*=hero], [class*=masthead], [class*=jumbotron]',
  'stat or counter': '[class*=stat], [class*=counter], [class*=number]',
  'quote or testimonial': 'blockquote, [class*=testimonial], [class*=quote]',
  'event listing': '[class*=event]',
  'news listing': '[class*=news], [class*=article]',
  'social links': 'a[href*="facebook.com"], a[href*="instagram.com"], a[href*="linkedin.com"], a[href*="youtube.com"], a[href*="x.com"], a[href*="twitter.com"]',
  'skip link': 'a[href^="#main"], a[class*=skip]',
  'cookie notice': '[class*=cookie], [id*=cookie], [class*=consent]',
  'back to top': '[class*=back-to-top], [class*=backtotop], a[href="#top"]',
  'icon': 'svg, i[class*=fa-], [class*=icon]',
};

function scanDoc(doc, win, page, W) {
  const els = [...doc.body.querySelectorAll('*')].filter(e => {
    const r = e.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const s = win.getComputedStyle(e);
    return s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity !== 0;
  }).slice(0, MAX_EL);
  for (const el of els) {
    const s = win.getComputedStyle(el), tag = el.tagName.toLowerCase();
    const r = el.getBoundingClientRect();
    const bg = color(s.backgroundColor);
    if (bg) bump(W.colors_bg, bg, Math.round(r.width * r.height / 1000), page);
    const bw = px(s.borderTopWidth) || px(s.borderBottomWidth);
    if (bw && s.borderTopStyle !== 'none') { bump(W.colors_border, color(s.borderTopColor) || color(s.borderBottomColor), 1, page); bump(W.border_widths, bw, 1, page); }
    const rad = px(s.borderTopLeftRadius);
    if (rad) bump(W.radii, rad > 999 ? 'full' : rad, 1, page, tag + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : ''));
    if (s.boxShadow && s.boxShadow !== 'none') bump(W.shadows, s.boxShadow, 1, page);
    for (const k of ['paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'rowGap', 'columnGap', 'marginTop', 'marginBottom']) {
      const v = px(s[k]);
      if (v && v > 0 && v <= 200) bump(W.spacing, v, 1, page);
    }
    const text = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
    if (text) {
      const fg = color(s.color), back = backgroundOf(el, win);
      const size = px(s.fontSize), weight = +s.fontWeight;
      bump(W.colors_text, fg, text.length, page);
      // disabled controls are exempt from WCAG contrast, so they don't count as pairs. Some sites mark them with a class only
      const control = el.closest('a, button, [role=button], input, select, textarea, label');
      const disabled = el.closest(':disabled, [disabled], [aria-disabled=true]') || (control && control.matches('.disabled, .is-disabled'));
      if (!disabled) bump(W.text_pairs, [fg, back, size, weight].join('|'), text.length, page, short(text));
      bump(W.type, [first(s.fontFamily), size, weight, px(s.lineHeight) || 'normal', s.letterSpacing === 'normal' ? 0 : px(s.letterSpacing), s.textTransform].join('|'), text.length, page, tag);
      const role = /^h[1-6]$/.test(tag) ? tag : tag === 'p' || tag === 'li' ? 'body' : (tag === 'button' || el.closest('button,[role=button],.btn')) ? 'button' : tag === 'a' ? 'link' : tag === 'label' ? 'label' : tag === 'small' ? 'small' : null;
      if (role) bump(W.type_by_role, [role, size, weight, first(s.fontFamily)].join('|'), text.length, page, short(text));
      if (tag === 'a' && el.closest('p, li') && el.closest('main, article, [role=main]')) bump(W.inline_links, [fg, s.textDecorationLine, weight].join('|'), 1, page, short(text));
    }
    const looksButton = tag === 'button' || el.getAttribute('role') === 'button' || (tag === 'input' && /submit|button/.test(el.type))
      || (tag === 'a' && /btn|button|cta/i.test(String(el.className)));
    if (looksButton) {
      // measure what you see: sites often put the fill and padding on an inner span
      let box = el, bs = s;
      while (box.children.length === 1 && !color(bs.backgroundColor) && !px(bs.paddingTop) && !px(bs.paddingLeft) && !(px(bs.borderTopWidth))) {
        box = box.children[0]; bs = win.getComputedStyle(box);
      }
      const label = [box, ...box.querySelectorAll('*')].find(x => [...x.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()));
      const ls = label ? win.getComputedStyle(label) : bs;
      const sig = [color(bs.backgroundColor) || 'none', color(ls.color), (px(bs.borderTopWidth) || 0) + ' ' + (color(bs.borderTopColor) || ''), px(bs.borderTopLeftRadius) || 0,
        [bs.paddingTop, bs.paddingRight, bs.paddingBottom, bs.paddingLeft].map(px).join(' '), px(ls.fontSize), +ls.fontWeight, first(ls.fontFamily), ls.textTransform, ls.letterSpacing === 'normal' ? 0 : px(ls.letterSpacing),
        Math.round(box.getBoundingClientRect().height)].join('|');
      bump(W.buttons, sig, 1, page, short(el.innerText || el.value || el.getAttribute('aria-label')));
    }
    if (/^(input|select|textarea)$/.test(tag) && !/hidden|submit|button|checkbox|radio/.test(el.type || '')) {
      const ph = color(win.getComputedStyle(el, '::placeholder').color);
      const sig = [color(s.backgroundColor) || 'none', color(s.color), (px(s.borderTopWidth) || 0) + ' ' + (color(s.borderTopColor) || ''), (px(s.borderBottomWidth) || 0) + ' ' + (color(s.borderBottomColor) || ''), rad || 0, px(r.height), px(s.fontSize), ph].join('|');
      bump(W.inputs, sig, 1, page, tag + (el.type ? '[' + el.type + ']' : ''));
    }
  }
  for (const [name, sel] of Object.entries(PATTERNS)) {
    let found = [];
    try { found = [...doc.querySelectorAll(sel)].filter(e => e.getBoundingClientRect().width > 0); } catch (e) { continue; }
    if (found.length) bump(W.patterns, name, found.length, page, found[0].tagName.toLowerCase() + (found[0].className && typeof found[0].className === 'string' ? '.' + found[0].className.trim().split(/\s+/).slice(0, 2).join('.') : ''));
  }
  const imgs = [...doc.images].filter(i => i.getBoundingClientRect().width > 0);
  bump(W.patterns, 'image', imgs.length, page);
  const noAlt = imgs.filter(i => !i.hasAttribute('alt')).length;
  if (noAlt) bump(W.issues, 'images without alt', noAlt, page);
  const hs = [...doc.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(h => +h.tagName[1]);
  if (hs.filter(h => h === 1).length !== 1) bump(W.issues, 'pages without exactly one h1', 1, page, hs.filter(h => h === 1).length + ' h1s');
  if (hs.some((h, i) => i && h > hs[i - 1] + 1)) bump(W.issues, 'skipped heading levels', 1, page);
  const bare = focusless(doc);
  if (bare.length) bump(W.issues, 'controls whose focus outline is removed with no replacement', bare.length, page, bare.slice(0, 3).join(', '));
}

// Visible controls whose focus outline a :focus rule removes, with no other focus rule giving them a visible change
// (outline, shadow, border, background, color or underline). Focus can't be triggered in a hidden frame, so this reads the CSS.
function focusless(doc) {
  const removing = [], showing = [];
  const strip = sel => sel.replace(/:focus(-visible|-within)?/g, '').replace(/:not\(\s*\)/g, '').trim() || '*';
  const none = v => /^(none|0(px)?)(\s|$)/.test(v || '');
  const shows = st => (st.outlineStyle && !none(st.outlineStyle)) || (st.outline && !none(st.outline)) || (st.boxShadow && st.boxShadow !== 'none')
    || st.border || st.borderColor || st.borderBottom || st.borderBottomColor || st.background || st.backgroundColor || st.color || st.textDecoration || st.textDecorationLine;
  const walk = list => { for (const r of list) {
    if (r.cssRules && !r.selectorText) { walk(r.cssRules); continue; }
    if (!r.selectorText || !/:focus/.test(r.selectorText)) continue;
    for (const part of r.selectorText.split(',')) {
      // :focus:not(:focus-visible) hides the ring for mouse users only, which is the recommended pattern
      if (!/:focus/.test(part) || /:not\(:focus-visible\)/.test(part)) continue;
      if (none(r.style.outlineStyle || r.style.outline) && !(r.style.boxShadow && r.style.boxShadow !== 'none')) removing.push(strip(part));
      else if (shows(r.style)) showing.push(strip(part));
    }
  } };
  for (const sh of doc.styleSheets) { try { walk(sh.cssRules); } catch (e) {} }
  const valid = sel => { try { doc.querySelector(sel); return true; } catch (e) { return false; } };
  const fixed = showing.filter(valid), bare = [];
  for (const sel of new Set(removing.filter(valid))) {
    const els = [...doc.querySelectorAll(sel)].filter(e => e.getBoundingClientRect().width > 0 && !fixed.some(f => e.matches(f)));
    if (els.length) bare.push(sel.slice(0, 60));
  }
  return bare;
}

const ruleInfo = { root_vars: {}, breakpoints: {}, font_faces: [] };
for (const sh of document.styleSheets) {
  let rules; try { rules = sh.cssRules; } catch (e) { continue; }
  const walk = list => { for (const r of list) {
    if (r.media) { for (const m of r.media.mediaText.matchAll(/(min|max)-width:\s*([\d.]+)px/g)) ruleInfo.breakpoints[m[1] + ' ' + m[2]] = (ruleInfo.breakpoints[m[1] + ' ' + m[2]] || 0) + 1; }
    if (r.cssRules) walk(r.cssRules);
    if (r.selectorText === ':root' || r.selectorText === 'html') for (const p of r.style) if (p.startsWith('--')) ruleInfo.root_vars[p] = r.style.getPropertyValue(p).trim().slice(0, 80);
    if (r.type === 5) ruleInfo.font_faces.push([r.style.getPropertyValue('font-family').replace(/["']/g, ''), r.style.getPropertyValue('font-weight'), (r.style.getPropertyValue('src').match(/url\(["']?([^"')]+)/) || [])[1] || ''].join('|'));
  } };
  walk(rules);
}
ruleInfo.font_faces = [...new Set(ruleInfo.font_faces)].slice(0, 40);
const vars = Object.entries(ruleInfo.root_vars);
ruleInfo.root_vars = Object.fromEntries(vars.filter(([k]) => !/^--bs-/.test(k)).slice(0, 120));
ruleInfo.root_vars_skipped = vars.filter(([k]) => /^--bs-/.test(k)).length;
out.rules = ruleInfo;
out.loaded_fonts = [...new Set([...document.fonts].filter(f => f.status === 'loaded').map(f => f.family.replace(/["']/g, '') + ' ' + f.weight + (f.style !== 'normal' ? ' ' + f.style : '')))];
const logo = document.querySelector('img[alt*=logo i], [class*=logo] img, [id*=logo] img, header a[href="/"] img, header a[href="/"] svg, img[src*=logo i]');
out.logo = logo ? (logo.currentSrc || logo.src || 'inline svg') : null;

for (const width of INPUT.widths) {
  const W = { colors_text: {}, colors_bg: {}, colors_border: {}, text_pairs: {}, type: {}, type_by_role: {}, inline_links: {}, spacing: {}, radii: {}, border_widths: {}, shadows: {}, buttons: {}, inputs: {}, patterns: {}, issues: {} };
  for (const { url, kind } of (width === INPUT.widths[0] ? INPUT.pages : INPUT.pages.slice(0, INPUT.mobile_pages || 4))) {
    const f = document.createElement('iframe');
    f.style.cssText = `position:absolute;left:-20000px;top:0;width:${width}px;height:1200px;border:0`;
    f.src = url;
    document.body.appendChild(f);
    const loaded = await new Promise(res => { f.onload = () => res(true); setTimeout(() => res(false), 20000); });
    const page = INPUT.pages.findIndex(p => p.url === url);
    if (!loaded) { out.pages[page] = out.pages[page] || { url, kind, error: 'timed out' }; f.remove(); continue; }
    await new Promise(res => setTimeout(res, 1500));
    try {
      const d = f.contentDocument, w = f.contentWindow;
      w.scrollTo(0, d.body.scrollHeight); await new Promise(res => setTimeout(res, 500)); w.scrollTo(0, 0);
      if (!out.pages[page]) out.pages[page] = { url, kind, title: d.title, elements: d.body.querySelectorAll('*').length, widths: [] };
      out.pages[page].widths.push(width);
      // a form in the page's own content, not the site-wide newsletter or search; an embedded form tool counts too
      const main = d.querySelector('main, [role=main]') || d.body;
      const fields = [...main.querySelectorAll('form input:not([type=hidden]):not([type=search]), form select, form textarea')].filter(x => !x.closest('footer, header'));
      const embed = [...main.querySelectorAll('iframe')].find(x => /form|apply|inquir|request|slate|hubspot|formstack|jotform|typeform/i.test(x.src + ' ' + (x.title || '')));
      const vendor = [...d.scripts].map(x => x.src).find(x => /technolutions|slate|heyhalda|hubspot|formstack|jotform|typeform|wufoo|marketo|pardot|formassembly|cognitoforms/i.test(x));
      out.pages[page].has_form = fields.length ? 'fields' : embed ? 'embedded: ' + new URL(embed.src, url).host
        : vendor && /form|request|apply|inquir|contact|register/i.test(url + ' ' + d.title) ? 'embedded: ' + new URL(vendor).host : false;
      scanDoc(d, w, page, W);
      // what each heading level looks like on this site, even where the pages don't use it
      if (!W.heading_probe) {
        W.heading_probe = {};
        const host = d.querySelector('main, [role=main]') || d.body;
        for (let n = 1; n <= 6; n++) {
          const h = d.createElement('h' + n);
          h.textContent = 'Heading';
          h.style.cssText = 'position:absolute;left:-9999px;top:0';
          host.appendChild(h);
          const cs = w.getComputedStyle(h);
          W.heading_probe['h' + n] = [px(cs.fontSize), +cs.fontWeight, first(cs.fontFamily), px(cs.lineHeight) || 'normal'];
          h.remove();
        }
      }
    } catch (e) { out.pages[page] = { url, kind, error: String(e.message || e) }; }
    f.remove();
  }
  // keep the result small: the most-used items of each kind
  const cap = { colors_text: 40, colors_bg: 40, colors_border: 30, text_pairs: 60, type: 45, type_by_role: 50, inline_links: 10, spacing: 40, radii: 20, border_widths: 10, shadows: 15, buttons: 20, inputs: 12, patterns: 40, issues: 10 };
  for (const [k, n] of Object.entries(cap)) W[k] = Object.fromEntries(Object.entries(W[k]).sort((a, b) => b[1].n - a[1].n).slice(0, n));
  out.widths[width] = W;
}
return JSON.stringify({ brand_intake_scan: out });
})();
