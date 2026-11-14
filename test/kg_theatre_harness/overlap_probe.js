(() => {
  const svg = document.querySelector('.kgt-stage svg'); const out = [];
  const els = [...svg.querySelectorAll('text')].filter(t => { const o = +(t.closest('g[opacity]')?.getAttribute('opacity') ?? 1); const s = getComputedStyle(t); return t.textContent.trim() && o > 0.3 && s.visibility !== 'hidden'; });
  const rects = els.map(t => { const r = t.getBoundingClientRect(); return { t: t.textContent.trim().slice(0, 30), x: r.left, y: r.top, w: r.width, h: r.height }; }).filter(r => r.w > 0 && r.h > 0);
  for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) { const a = rects[i], b = rects[j]; if (a.x < b.x + b.w - 1 && a.x + a.w - 1 > b.x && a.y < b.y + b.h - 1 && a.y + a.h - 1 > b.y) out.push(a.t + ' ⟂ ' + b.t); }
  const st = document.querySelector('.kgt-stage').getBoundingClientRect(); const rail = document.querySelector('.kgt-rail'); const rr = rail && getComputedStyle(rail).display !== 'none' ? rail.getBoundingClientRect() : null; const right = rr && rr.width > 0 ? rr.left : st.right;   // in Inspect the trail rail owns the right of the stage: under it is off screen
  const inside = (r) => r.width > 0 && r.left >= st.left - 2 && r.right <= right + 2 && r.top >= st.top - 2 && r.bottom <= st.bottom + 2;
  const shown = [...svg.querySelectorAll('g.kgt-ent rect.box, .kgt-perimeter rect, .kgt-area rect, .kgt-receipt rect, .kgt-barrier rect, .kgt-ghost rect')].filter(el => +(el.closest('g[opacity]')?.getAttribute('opacity') ?? 1) > 0.3);
  const outside = shown.filter(el => !inside(el.getBoundingClientRect())).length;
  return JSON.stringify({ texts: rects.length, overlaps: out, shown: shown.length, outside });
})()
