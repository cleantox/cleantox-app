"""Vista semanal tipo Google Calendar (lunes a domingo). Al tocar un servicio devuelve su ID."""

from __future__ import annotations

import streamlit as st

HTML = '<div class="bar"><button class="nv" data-n="prev">‹</button><div class="tit"></div>' \
       '<button class="nv" data-n="next">›</button><button class="hoyb" data-n="hoy">Hoy</button></div><div class="wrap"><div class="grid"></div></div><div class="ley"></div>'

CSS = """
.bar { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; font-family: sans-serif; }
.tit { flex: 1; text-align: center; font-weight: 700; font-size: 16px; color: #202124; white-space: nowrap; }
.nv, .hoyb { border: 1px solid #dadce0; background: #fff; border-radius: 18px; height: 34px; cursor: pointer;
             font: 600 14px sans-serif; color: #3c4043; }
.nv { width: 34px; font-size: 20px; line-height: 1; }
.hoyb { padding: 0 14px; }
.nv:active, .hoyb:active { background: #f1f3f4; }
.wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; scroll-snap-type: x proximity;
        border: 1px solid #e3e6e8; border-radius: 10px; background: #fff; }
.grid { display: grid; grid-template-columns: repeat(7, minmax(118px, 1fr)); min-width: 826px; }
.col { border-right: 1px solid #eef0f2; min-height: 160px; scroll-snap-align: start; }
.col:last-child { border-right: none; }
.cab { position: sticky; top: 0; text-align: center; padding: 6px 2px 5px; border-bottom: 1px solid #e3e6e8;
       font: 600 12px/1.2 sans-serif; color: #5f6368; background: #fff; }
.cab .num { display: block; font-size: 20px; color: #3c4043; margin-top: 2px; }
.cab .cnt { display: block; font-size: 10px; font-weight: 500; color: #80868b; margin-top: 2px; }
.hoy .cab { color: #3d7a16; }
.hoy .cab .num { background: #86b02a; color: #fff; border-radius: 50%; width: 32px; height: 32px;
                 line-height: 32px; margin: 2px auto 0; }
.hoy { background: #f7fbef; }
.sab { background: repeating-linear-gradient(135deg, #fafafa, #fafafa 6px, #f1f1f1 6px, #f1f1f1 12px); }
.sab .cab .num { color: #9aa0a6; }
.lista { padding: 4px; display: flex; flex-direction: column; gap: 4px; }
.ev { border-radius: 6px; padding: 4px 6px; cursor: pointer; font: 12px/1.25 sans-serif; color: #202124;
      border-left: 4px solid #9aa0a6; background: #f1f3f4; }
.ev:active { filter: brightness(.92); }
.ev .h { font-weight: 700; font-size: 11px; }
.ev .c { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.ev .m { font-size: 11px; font-weight: 600; margin-top: 1px; }
.ev .t { font-size: 10px; color: #5f6368; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pend { border-left-color: #f29900; background: #fef7e0; }
.liq  { border-left-color: #1e8e3e; background: #e6f4ea; }
.ent  { border-left-color: #1a73e8; background: #e8f0fe; }
.tarde{ border-left-color: #d93025; background: #fce8e6; }
.nada { border-left-color: #9aa0a6; background: #f1f3f4; }
.reag { border-left-color: #9aa0a6; background: #fff; color: #5f6368; border-style: dashed; border-width: 1px 1px 1px 4px; }
.vacio { color: #bdc1c6; text-align: center; font-size: 12px; padding-top: 14px; }
.ley { font: 11px/1.6 sans-serif; color: #5f6368; margin-top: 6px; }
.ley b { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin: 0 3px 0 8px; vertical-align: middle; }
"""

JS = """
export default function(component) {
  const { parentElement, data, setTriggerValue } = component;
  const grid = parentElement.querySelector('.grid');
  const wrap = parentElement.querySelector('.wrap');
  const ley = parentElement.querySelector('.ley');
  const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  parentElement.querySelector('.tit').textContent = data?.titulo || '';
  parentElement.querySelectorAll('[data-n]').forEach((b) => { b.onclick = () => setTriggerValue('nav', b.dataset.n); });
  grid.innerHTML = '';
  let hoyCol = null;
  for (const d of (data?.dias || [])) {
    const col = document.createElement('div');
    col.className = 'col' + (d.hoy ? ' hoy' : '') + (d.sabado ? ' sab' : '');
    col.innerHTML = `<div class="cab">${esc(d.nombre)}<span class="num">${esc(d.num)}</span>` +
                    `<span class="cnt">${esc(d.resumen)}</span></div><div class="lista"></div>`;
    const lista = col.querySelector('.lista');
    if (!d.eventos.length) lista.innerHTML = '<div class="vacio">—</div>';
    for (const e of d.eventos) {
      const el = document.createElement('div');
      el.className = 'ev ' + e.clase;
      el.innerHTML = `<div class="h">${esc(e.hora)} ${esc(e.marcas)}</div><div class="c">${esc(e.cliente)}</div>` +
                     `<div class="t">${esc(e.tipo)}</div><div class="m">${esc(e.monto)}</div>`;
      el.onclick = () => setTriggerValue('abrir', e.id);
      lista.appendChild(el);
    }
    grid.appendChild(col);
    if (d.hoy) hoyCol = col;
  }
  ley.innerHTML = '<b style="background:#f29900"></b>falta cobrar <b style="background:#1e8e3e"></b>liquidado' +
                  '<b style="background:#1a73e8"></b>se cobra en entrega <b style="background:#d93025"></b>sin llegada' +
                  ' · montos sin IVA · 📍 llegó · ✍️ cierre · sábado = reagendados';
  if (hoyCol) requestAnimationFrame(() => { wrap.scrollLeft = Math.max(hoyCol.offsetLeft - 8, 0); });
}
"""

_comp = None


def agenda_semanal(dias: list[dict], titulo: str, key: str = "agenda") -> tuple[str | None, str | None]:
    """Devuelve (id_abierto, nav) con nav en prev|next|hoy. dias: [{nombre, num, resumen, hoy, sabado, eventos:[{id,hora,cliente,tipo,monto,clase,marcas}]}]"""
    global _comp
    if _comp is None:
        _comp = st.components.v2.component("cleantox_agenda", html=HTML, css=CSS, js=JS)
    r = _comp(key=key, data={"dias": dias, "titulo": titulo}, on_abrir_change=lambda: None,
              on_nav_change=lambda: None)
    if r is None:
        return None, None
    return getattr(r, "abrir", None), getattr(r, "nav", None)
