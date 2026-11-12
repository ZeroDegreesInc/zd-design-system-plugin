// /port-tokens: the documentation on the Styles pages (everything but the Colors page, which /color-cleanup owns).
// docs.py fills in INPUT. Two modes:
//   "read":  every text layer on those pages, whether it's bound to a variable, and the current token values. Writes nothing.
//   "write": the approved edits: add a section for a new typeface, bind a text layer to a variable, set its characters,
//            or set a specimen's font style.
const INPUT = __INPUT__;

// variables the config excludes, such as the file's own documentation/* styling, aren't part of the system
const EXCLUDE = (INPUT.exclude || []).map(g => new RegExp('^' + g.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*') + '$'));
const vars = (await figma.variables.getLocalVariablesAsync()).filter(v => !EXCLUDE.some(re => re.test(v.name)));
const byName = {}, byId = {};
vars.forEach(v => { byName[v.name] = v; byId[v.id] = v; });

if (INPUT.mode === 'write') {
  const done = [], errors = [];
  // a new typeface: copy an existing section (its "Font Info" frame and the weights that follow it), then point
  // every layer that followed the source family's variable at the new one, and relabel it
  for (const op of INPUT.ops.filter(o => o.typeface)) {
    try {
      const src = await figma.getNodeByIdAsync(op.typeface.info), srcW = op.typeface.weights ? await figma.getNodeByIdAsync(op.typeface.weights) : null;
      const from = byName[op.typeface.from], to = byName[op.typeface.to];
      if (!src || !from || !to) throw new Error('source section or variable missing');
      const parts = [src, srcW].filter(Boolean);
      for (const t of parts.flatMap(x => x.findAll(n => n.type === 'TEXT'))) if (t.fontName !== figma.mixed) await figma.loadFontAsync(t.fontName);
      const styles = (await figma.listAvailableFontsAsync()).filter(f => f.fontName.family === op.typeface.family).map(f => f.fontName);
      for (const f of styles) await figma.loadFontAsync(f);
      const after = srcW || src, parent = after.parent;
      let at = parent.children.indexOf(after) + 1;
      const made = [];
      for (const x of parts) { const c = x.clone(); parent.insertChild(at++, c); made.push(c); }
      for (const t of made.flatMap(x => x.findAll(n => n.type === 'TEXT'))) {
        const b = t.boundVariables || {};
        for (const field of ['fontFamily', 'characters']) {
          const ref = b[field] ? (Array.isArray(b[field]) ? b[field][0] : b[field]) : null;
          if (!ref || ref.id !== from.id) continue;
          if (field === 'fontFamily') {
            const style = t.fontName === figma.mixed ? 'Regular' : t.fontName.style;
            const f = styles.find(s => s.style === style) || styles.find(s => s.style === 'Regular') || styles[0];
            if (f) t.fontName = f;
          }
          t.setBoundVariable(field, to);
        }
        if (op.typeface.label && t.characters === op.typeface.label[0]) t.characters = op.typeface.label[1];
      }
      done.push(...made.map(x => x.id));
    } catch (e) { errors.push({ node: op.typeface.info, error: String(e && e.message || e) }); }
  }
  INPUT.ops = INPUT.ops.filter(o => !o.typeface);
  const nodes = {};
  for (const op of INPUT.ops) nodes[op.node] = await figma.getNodeByIdAsync(op.node);
  // fonts: every text layer touched needs its current font loaded, and a specimen its new one
  const fonts = {};
  for (const op of INPUT.ops) {
    const n = nodes[op.node];
    if (n && n.type === 'TEXT' && n.fontName !== figma.mixed) fonts[n.fontName.family + '|' + n.fontName.style] = n.fontName;
    if (op.fontName) fonts[op.fontName.family + '|' + op.fontName.style] = op.fontName;
  }
  for (const f of Object.values(fonts)) { try { await figma.loadFontAsync(f); } catch (e) { /* reported per op below */ } }
  for (const op of INPUT.ops) {
    const n = nodes[op.node];
    try {
      if (!n || n.type !== 'TEXT') throw new Error('not a text layer');
      if (op.bind) { const v = byName[op.bind]; if (!v) throw new Error('no variable ' + op.bind); n.setBoundVariable('characters', v); }
      else if (op.characters !== undefined) n.characters = op.characters;
      else if (op.fontName) {
        // a specimen's own style; setting it can drop the family's binding, so bind it again
        n.fontName = op.fontName;
        if (op.family_var && byName[op.family_var]) n.setBoundVariable('fontFamily', byName[op.family_var]);
      }
      done.push(op.node);
    } catch (e) { errors.push({ node: op.node, error: String(e && e.message || e) }); }
  }
  return JSON.stringify({ ds_sync_figma_docs: { nonce: INPUT.nonce, done, errors } });
}

// ── read ──
// Figma caps a result at 48 KB, so only text that could need an edit comes back: a bare number some token
// holds, running text holding a moved token's old value, and type specimens with their labels. Each board
// also reports which token names it mentions and how many of its layers are already bound.
const cols = await figma.variables.getLocalVariableCollectionsAsync();
const plain = (v) => {
  const col = cols.find(c => c.id === v.variableCollectionId);
  let x = v.valuesByMode[col.defaultModeId];
  for (let i = 0; i < 10 && x && x.type === 'VARIABLE_ALIAS'; i++) { const t = byId[x.id]; const tc = cols.find(c => c.id === t.variableCollectionId); x = t.valuesByMode[tc.defaultModeId]; }
  return typeof x === 'number' || typeof x === 'string' ? x : null;
};
const tokens = {};
for (const v of vars) if (v.resolvedType === 'FLOAT' || v.resolvedType === 'STRING') tokens[v.name] = { t: v.resolvedType === 'FLOAT' ? 'n' : (v.scopes.includes('FONT_FAMILY') ? 'family' : 's'), v: plain(v) };

const families = new Set(Object.values(tokens).filter(t => t.t === 'family').map(t => t.v));
const styles = {};
for (const f of await figma.listAvailableFontsAsync()) if (families.has(f.fontName.family)) (styles[f.fontName.family] = styles[f.fontName.family] || []).push(f.fontName.style);

const numbers = new Set(Object.values(tokens).filter(t => t.t === 'n').map(t => t.v));
const moved = Object.entries(INPUT.old || {}).filter(([n, o]) => tokens[n] && o !== null && tokens[n].v !== o);
const needles = [];
for (const [, o] of moved) { if (typeof o === 'number') { numbers.add(o); needles.push(new RegExp('(?<![\\d.])(' + o + '|' + (o - 1) + ')(?![\\d.])')); } else if (!String(o).startsWith('#')) needles.push(new RegExp('(?<![\\w-])' + String(o).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?![\\w-])')); }
const names = Object.keys(tokens).sort((a, b) => b.length - a.length);
const famOf = t => { const b = t.boundVariables || {}; const f = b.fontFamily ? (Array.isArray(b.fontFamily) ? b.fontFamily[0] : b.fontFamily) : null; return f && byId[f.id] ? f : null; };

const pages = [], typefaces = [];
for (const pid of INPUT.pages) {
  const page = await figma.getNodeByIdAsync(pid); if (!page) continue; await page.loadAsync();
  const all = page.findAllWithCriteria({ types: ['TEXT'] });
  // a typeface section: a frame holding a text whose characters show a font-family variable (the family's name),
  // usually followed by a frame of weight samples
  for (const t of all) {
    const c = (t.boundVariables || {}).characters, v = c && byId[c.id];
    if (!v || !v.scopes.includes('FONT_FAMILY')) continue;
    const info = t.parent, sibs = info.parent ? info.parent.children : [], next = sibs[sibs.indexOf(info) + 1];
    const label = info.findAll(n => n.type === 'TEXT' && n !== t && !famOf(n) && n.characters.length <= 40)[0];
    typefaces.push({ page: page.name.trim(), info: info.id, weights: next && /weight/i.test(next.name) ? next.id : null, fv: v.name, label: label ? label.characters : null });
  }
  const specimenParents = new Set(all.filter(famOf).map(t => t.parent.id));
  const boards = {};
  for (const t of all) {
    const chain = []; let p = t.parent; while (p && p.type !== 'PAGE') { chain.unshift(p); p = p.parent; }
    const board = (chain[1] || chain[0] || page).name;
    const B = boards[board] = boards[board] || { mentions: new Set(), bound: 0, texts: [] };
    const s = t.characters;
    for (const n of names) if (s.includes(n)) B.mentions.add(n);
    if ((t.boundVariables || {}).characters) { B.bound++; continue; }
    const bare = /^\s*\d+(\.\d+)?\s*$/.test(s);
    const keep = (bare && numbers.has(+s)) || (!bare && needles.some(re => re.test(s))) || specimenParents.has(t.parent.id);
    if (!keep) continue;
    const fam = famOf(t);
    // a specimen's label: a short text beside it, or beside its container, not itself in a system font
    let lab = null;
    if (fam) for (const scope of [t.parent, t.parent.parent]) {
      const near = scope && 'findAll' in scope ? scope.findAll(n => n.type === 'TEXT' && n !== t && !famOf(n) && n.characters.length <= 24) : [];
      if (near.length === 1) { lab = near[0].characters.trim(); break; }
    }
    B.texts.push({ id: t.id, s: s.slice(0, 200), p: t.parent.id, at: chain.slice(-2).map(n => n.name).join(' › '),
      ...(fam ? { fv: byId[fam.id].name, f: t.fontName === figma.mixed ? null : t.fontName, ...(lab ? { lab } : {}) } : {}), ...(t.id.startsWith('I') ? { i: 1 } : {}) });
  }
  pages.push({ id: pid, name: page.name.trim(), boards: Object.fromEntries(Object.entries(boards).map(([k, b]) => [k, { mentions: [...b.mentions], bound: b.bound, texts: b.texts }])) });
}
return JSON.stringify({ ds_sync_figma_docs: { nonce: INPUT.nonce, tokens, styles, pages, typefaces } });
