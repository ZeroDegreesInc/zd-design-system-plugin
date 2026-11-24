// /port-tokens: the Colors page. colors.py fills in INPUT. Two modes:
//   "read":  every swatch with its variable and properties, the palette rows, and the Color Scales ramps. Writes nothing.
//   "write": the approved changes: swatch properties, regenerated ramps, and new swatches, rows and scales.
// A swatch is an instance of `.Color Swatch`: its fill is bound to the variable it documents, and everything written on it
// (name, hex or alias, the four verdicts) is a component property. `Text Color` is a variant: Black or White labels.
const INPUT = __INPUT__;

const EXCLUDE = (INPUT.exclude || []).map(g => new RegExp('^' + g.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*') + '$'));
const vars = (await figma.variables.getLocalVariablesAsync()).filter(v => !EXCLUDE.some(re => re.test(v.name)));
const cols = await figma.variables.getLocalVariableCollectionsAsync();
const byId = {}, byName = {};
vars.forEach(v => { byId[v.id] = v; byName[v.name] = v; });
const page = figma.root.children.find(p => /^colors$/i.test(p.name.trim()));
await figma.setCurrentPageAsync(page);
const isSwatch = n => n.type === 'INSTANCE' && n.name === '.Color Swatch';
const varOf = s => { const b = s.boundVariables && s.boundVariables.fills; return b && b.length && byId[b[0].id] ? byId[b[0].id].name : null; };

if (INPUT.mode === 'write') {
  const done = [], errors = [];
  const fail = (what, e) => errors.push({ what, error: String(e && e.message || e) });
  const variants = INPUT.variants;
  const paint = v => figma.variables.setBoundVariableForPaint({ type: 'SOLID', color: { r: 0, g: 0, b: 0 } }, 'color', v);
  const loadAll = async n => { for (const t of n.findAll(x => x.type === 'TEXT')) if (t.fontName !== figma.mixed) await figma.loadFontAsync(t.fontName); };
  const newSwatch = async (spec) => {
    const comp = await figma.getNodeByIdAsync(variants[spec.variant]);
    const inst = comp.createInstance();
    inst.name = '.Color Swatch';
    inst.fills = [paint(byName[spec.var])];
    await loadAll(inst);
    inst.setProperties(spec.props);
    return inst;
  };
  const hexRgb = h => ({ r: parseInt(h.slice(1, 3), 16) / 255, g: parseInt(h.slice(3, 5), 16) / 255, b: parseInt(h.slice(5, 7), 16) / 255 });
  const writeRamp = async (rampFrame, name, hexes) => {
    rampFrame.name = name;
    const groups = rampFrame.children.filter(g => / - #/.test(g.name));
    for (let i = 0; i < groups.length && i < hexes.length; i++) {
      const g = groups[i], hex = hexes[i], lower = hex.toLowerCase(), step = g.name.split(' - ')[0];
      const old = g.name.split(' - ')[1];
      const rect = g.findOne(n => n.type === 'RECTANGLE');
      if (rect && rect.boundVariables && rect.boundVariables.fills) { errors.push({ what: `${name} step ${step}`, error: 'fill is bound to a variable; left alone' }); continue; }
      g.name = `${step} - ${lower}`;
      if (rect) { const f = JSON.parse(JSON.stringify(rect.fills)); f[0].color = hexRgb(hex); rect.fills = f; rect.name = `Swatch - ${lower}`; }
      const label = g.findOne(n => n.type === 'FRAME' && /^Label - /.test(n.name));
      if (label) {
        label.name = `Label - ${lower}`;
        const t = label.findOne(n => n.type === 'TEXT' && n.characters.trim().toLowerCase() === old.toLowerCase());
        if (t) { await figma.loadFontAsync(t.fontName); if (t.name === t.characters) t.name = hex; t.characters = hex; }
      }
    }
  };
  for (const op of INPUT.ops) {
    try {
      if (op.t === 'props') {
        const s = await figma.getNodeByIdAsync(op.id);
        await loadAll(s);
        s.setProperties(op.props);       // the label-colour variant and the text properties together
        done.push(op.id);
      } else if (op.t === 'ramp') {
        await writeRamp(await figma.getNodeByIdAsync(op.frame), op.name, op.steps);
        done.push(op.frame);
      } else if (op.t === 'swatch') {
        const ct = await figma.getNodeByIdAsync(op.ct);
        const inst = await newSwatch(op);
        const after = op.after ? ct.children.findIndex(c => c.id === op.after) : -1;
        ct.insertChild(after >= 0 ? after + 1 : ct.children.length, inst);
        done.push(inst.id);
      } else if (op.t === 'row') {
        const src = await figma.getNodeByIdAsync(op.clone);
        const row = src.clone();
        src.parent.insertChild(src.parent.children.indexOf(src) + 1, row);
        row.name = op.name;
        const title = row.findOne(n => n.type === 'TEXT' && n.name === 'Title'), desc = row.findOne(n => n.type === 'TEXT' && n.name === 'Description');
        for (const [t, s] of [[title, op.title], [desc, op.desc]]) if (t) { await figma.loadFontAsync(t.fontName); t.characters = s; }
        const ct = row.findOne(n => n.name === 'Color Swatches');
        ct.children.filter(isSwatch).forEach(c => c.remove());
        for (const spec of op.swatches) ct.appendChild(await newSwatch(spec));
        done.push(row.id);
      } else if (op.t === 'family') {
        const src = await figma.getNodeByIdAsync(op.clone);
        const fam = src.clone();
        src.parent.insertChild(src.parent.children.indexOf(src) + 1, fam);
        fam.name = op.name;
        const sw = fam.children.find(isSwatch);
        sw.fills = [paint(byName[op.anchor])];
        await loadAll(sw);
        sw.setProperties(op.props);
        await writeRamp(fam.children.find(c => /^#[0-9A-Fa-f]{6}$/.test(c.name)), op.rampName, op.steps);
        done.push(fam.id);
      }
    } catch (e) { fail(op.t + ' ' + (op.id || op.frame || op.var || op.name || ''), e); }
  }
  return JSON.stringify({ ds_sync_figma_colors: { nonce: INPUT.nonce, done, errors } });
}

// ── read ──
const hex = c => '#' + [c.r, c.g, c.b].map(n => Math.round(n * 255).toString(16).padStart(2, '0')).join('').toUpperCase();
const resolve = v => {
  let x = v, alpha = 1;
  for (let i = 0; i < 10; i++) {
    const col = cols.find(c => c.id === x.variableCollectionId);
    const raw = x.valuesByMode[col.defaultModeId];
    if (raw && raw.type === 'VARIABLE_ALIAS') { x = byId[raw.id] || x; if (!byId[raw.id]) return null; continue; }
    if (raw && raw.color) {           // a colour with its own opacity
      alpha *= raw.opacity === undefined ? 1 : raw.opacity / 100;
      if (raw.color.type === 'VARIABLE_ALIAS') { x = byId[raw.color.id]; if (!x) return null; continue; }
      return { hex: hex(raw.color), a: alpha * (raw.color.a === undefined ? 1 : raw.color.a) };
    }
    if (raw && raw.r !== undefined) return { hex: hex(raw), a: alpha * (raw.a === undefined ? 1 : raw.a) };
    return null;
  }
  return null;
};
const colorVars = {};
for (const v of vars) {
  if (v.resolvedType !== 'COLOR') continue;
  const col = cols.find(c => c.id === v.variableCollectionId);
  const raw = v.valuesByMode[col.defaultModeId];
  const r = resolve(v);
  colorVars[v.name] = { h: r ? r.hex : null, a: r ? Math.round(r.a * 1000) / 1000 : null, al: raw && raw.type === 'VARIABLE_ALIAS' && byId[raw.id] ? byId[raw.id].name : null, c: col.name };
}
const all = page.findAll(isSwatch);
const keys = all.length ? Object.keys(all[0].componentProperties) : [];
const boardOf = n => { let p = n; while (p.parent && p.parent.parent && p.parent.parent.type !== 'PAGE' && p.parent.parent.parent && p.parent.parent.parent.type !== 'PAGE') p = p.parent; return p.name; };
const swatches = all.map(s => ({ id: s.id, v: varOf(s), b: boardOf(s), ct: s.parent.id, p: keys.map(k => (s.componentProperties[k] || {}).value) }));
const main = all.length ? await all[0].getMainComponentAsync() : null;
const variants = {};
if (main) for (const c of main.parent.children) { const m = /Text Color=(\w+)/.exec(c.name); if (m) variants[m[1]] = c.id; }
// palette rows: a frame whose "Color Swatches" child holds swatches
const rows = page.findAll(n => n.type === 'FRAME' && n.children && n.children.some(c => c.name === 'Color Swatches')).map(r => {
  const ct = r.children.find(c => c.name === 'Color Swatches');
  const title = r.findOne(n => n.type === 'TEXT' && n.name === 'Title');
  return { id: r.id, name: r.name, b: boardOf(r), ct: ct.id, title: title ? title.characters : r.name, vars: ct.children.filter(isSwatch).map(varOf) };
});
// Color Scales: a family frame holds an anchor swatch and a ramp frame named for the anchor's hex
const scales = page.findAll(n => n.type === 'FRAME' && n.children && n.children.some(isSwatch) && n.children.some(c => /^#[0-9A-Fa-f]{6}$/.test(c.name))).map(f => {
  const sw = f.children.find(isSwatch), r = f.children.find(c => /^#[0-9A-Fa-f]{6}$/.test(c.name));
  return { id: f.id, name: f.name, anchor: varOf(sw), swatch: sw.id, frame: r.id, frameName: r.name,
    steps: r.children.filter(g => / - #/.test(g.name)).map(g => { const rect = g.findOne(n => n.type === 'RECTANGLE'); return [g.name.split(' - ')[0], g.name.split(' - ')[1], !!(rect && rect.boundVariables && rect.boundVariables.fills)]; }) };
});
return JSON.stringify({ ds_sync_figma_colors: { nonce: INPUT.nonce, vars: colorVars, keys, swatches, variants, rows, scales } });
