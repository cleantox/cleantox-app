"""Recuadro para firmar con el dedo (devuelve la firma como imagen PNG)."""

from __future__ import annotations

import base64

import streamlit as st

HTML = """
<div class="firma">
  <canvas width="600" height="220"></canvas>
  <div class="botones">
    <button type="button" class="borrar">Borrar</button>
    <button type="button" class="guardar">Usar esta firma</button>
  </div>
  <div class="estado"></div>
</div>
"""

CSS = """
.firma { font-family: sans-serif; }
canvas { width: 100%; max-width: 600px; height: 200px; border: 2px dashed #9aa; border-radius: 8px;
         background: #fff; touch-action: none; display: block; }
.botones { display: flex; gap: 8px; margin-top: 8px; max-width: 600px; }
button { flex: 1; padding: 10px; border-radius: 8px; border: 1px solid #999; background: #f4f4f4; font-size: 15px; }
button.guardar { background: #86b02a; color: #fff; border-color: #86b02a; }
.estado { margin-top: 6px; font-size: 13px; color: #3a7d1f; }
"""

JS = """
export default function(component) {
  const { parentElement, setStateValue } = component;
  const canvas = parentElement.querySelector('canvas');
  const ctx = canvas.getContext('2d');
  const estado = parentElement.querySelector('.estado');
  ctx.lineWidth = 3; ctx.lineCap = 'round'; ctx.strokeStyle = '#111';
  let dibujando = false, trazos = 0;
  const pos = (e) => {
    const r = canvas.getBoundingClientRect();
    return [(e.clientX - r.left) * canvas.width / r.width, (e.clientY - r.top) * canvas.height / r.height];
  };
  canvas.onpointerdown = (e) => { dibujando = true; trazos++; const [x, y] = pos(e); ctx.beginPath(); ctx.moveTo(x, y);
                                  canvas.setPointerCapture(e.pointerId); };
  canvas.onpointermove = (e) => { if (!dibujando) return; const [x, y] = pos(e); ctx.lineTo(x, y); ctx.stroke(); };
  canvas.onpointerup = () => { dibujando = false; };
  canvas.onpointerleave = () => { dibujando = false; };
  parentElement.querySelector('.borrar').onclick = () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height); trazos = 0; estado.textContent = '';
    setStateValue('firma', '');
  };
  parentElement.querySelector('.guardar').onclick = () => {
    if (!trazos) { estado.textContent = 'Primero firma en el recuadro.'; return; }
    setStateValue('firma', canvas.toDataURL('image/png'));
    estado.textContent = '✔ Firma lista';
  };
}
"""

_componente = None


def recuadro_firma(key: str) -> bytes | None:
    global _componente
    if _componente is None:
        _componente = st.components.v2.component("cleantox_firma", html=HTML, css=CSS, js=JS)
    r = _componente(key=key, on_firma_change=lambda: None)
    dato = getattr(r, "firma", None) if r is not None else None
    if dato and str(dato).startswith("data:image/png;base64,"):
        return base64.b64decode(str(dato).split(",", 1)[1])
    return None
