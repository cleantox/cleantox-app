"""
CleanTox · Servicios (versión web)

- Oficina (cuentas @cleantox.mx autorizadas): ve todos los servicios, montos y pagos.
- Coordinadora: entra con el link que le mandan por WhatsApp; ve SOLO su servicio,
  sin teléfono ni correo del cliente, y reporta el efectivo que recibió.
"""

from __future__ import annotations

import urllib.parse
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import enlaces
import google_api as g
from parser_servicios import es_servicio, parsear_evento

TZ = ZoneInfo("America/Mexico_City")
st.set_page_config(page_title="CleanTox · Servicios", page_icon="icono.png", layout="wide")
st.markdown(
    "<style>[data-testid='stToolbar'],[data-testid='stDecoration'],.stAppDeployButton,"
    "[data-testid='stMainMenu']{display:none!important}</style>",
    unsafe_allow_html=True,
)


# ------------------------------------------------------------------ configuración

def cfg(nombre: str, default=None):
    try:
        return st.secrets[nombre]
    except Exception:
        return default


MODO_LOCAL = bool(cfg("MODO_LOCAL", False))
APP_URL = cfg("APP_URL", "http://localhost:8501")
CALENDAR_ID = cfg("CALENDAR_ID")
SHEET_ID = cfg("SHEET_ID")
NOTIFICAR_A = cfg("NOTIFICAR_A", "rrhh@cleantox.mx")
ADMINS = [a.lower().strip() for a in cfg("ADMINS", [])]
LINK_SECRET = cfg("LINK_SECRET")

faltan = [n for n, v in {"CALENDAR_ID": CALENDAR_ID, "SHEET_ID": SHEET_ID, "LINK_SECRET": LINK_SECRET}.items() if not v]
if faltan:
    st.error("Falta configuración: " + ", ".join(faltan) + ". Revisa la GUIA (paso de secretos).")
    st.stop()


def pesos(x: float | None) -> str:
    return "—" if x is None else f"${x:,.2f}"


def pm(x: float | None) -> str:
    """Pesos para texto con formato (evita que '$…$' se lea como fórmula)."""
    return pesos(x).replace("$", "\\$")


def md(texto: str) -> str:
    return (texto or "").replace("$", "\\$")


# ------------------------------------------------------------------ Google (cacheado)

@st.cache_resource(show_spinner=False)
def google() -> g.Google:
    token = None
    try:
        token = dict(st.secrets["google_token"])
    except Exception:
        pass
    return g.Google(g.credenciales(token))


@st.cache_data(ttl=120, show_spinner="Leyendo calendario…")
def eventos_dia(dia: date) -> list[dict]:
    return google().eventos_del_dia(CALENDAR_ID, dia)


@st.cache_data(ttl=60, show_spinner=False)
def evento(event_id: str) -> dict | None:
    return google().evento(CALENDAR_ID, event_id)


@st.cache_data(ttl=20, show_spinner=False)
def pagos() -> list[dict]:
    return google().leer_pagos(SHEET_ID)


def refrescar_pagos():
    pagos.clear()


def saldo(s, lista_pagos: list[dict]) -> float | None:
    return s.saldo_pendiente(g.sumar(g.pagos_de_evento(lista_pagos, s.event_id)))


def fecha_servicio(s) -> date:
    return s.inicio.astimezone(TZ).date() if s.inicio else datetime.now(TZ).date()


# =================================================================== COORDINADORA

def vista_coordinadora(qp) -> None:
    ok, motivo = enlaces.verificar(LINK_SECRET, qp.get("s", ""), qp.get("d", ""), qp.get("k", ""))
    if not ok:
        st.image("logo.png", width=220)
        st.error(motivo)
        st.stop()

    ev = evento(qp["s"])
    if not ev:
        st.image("logo.png", width=220)
        st.error("Este servicio ya no existe o fue cancelado. Comunícate con la oficina.")
        st.stop()

    s = parsear_evento(ev, "Servicios CleanTox")
    lista = pagos()
    pendiente = saldo(s, lista)
    hora = s.inicio.astimezone(TZ).strftime("%d/%m/%Y · %H:%M") if s.inicio else ""

    st.image("logo.png", width=170)
    st.title(md(s.cliente))
    st.caption(md(f"{hora} · {s.tipo_servicio}"))

    if s.direccion:
        maps = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(s.direccion)
        st.markdown(f"📍 **{md(s.direccion)}**")
        st.link_button("Abrir en Google Maps", maps, width="stretch")

    st.subheader("Qué se hace")
    for c in s.conceptos:
        st.markdown(f"- {md(c.texto)}")
    if not s.conceptos:
        st.caption("Sin detalle en el evento. Confirma con la oficina.")

    st.divider()
    if pendiente is None:
        st.warning("La oficina no registró el monto de este servicio. Confirma con ellos cuánto cobrar.")
    elif pendiente == 0:
        st.success("✅ Este servicio ya está pagado. No cobres nada.")
    else:
        st.metric("Falta por cobrar", pesos(pendiente))

    mios = [p for p in g.pagos_de_evento(lista, s.event_id) if p.get("Estado") == g.REPORTADO]
    for p in mios:
        st.caption(md(f"Ya reportado: {pesos(p['Monto'])} por {p.get('Registró', '')} · {p.get('Registrado', '')}"))

    aviso = st.session_state.pop("aviso_registro", None)
    if aviso:
        st.success(aviso)
        st.balloons()

    if pendiente != 0:
        st.subheader("Registrar efectivo recibido")
        with st.form("efectivo", clear_on_submit=True):
            nombre = st.text_input("Tu nombre")
            monto = st.number_input("Monto en efectivo recibido", min_value=0.0, step=50.0,
                                    value=float(pendiente or 0))
            notas = st.text_input("Notas (opcional)")
            seguro = st.checkbox("Confirmo que recibí este dinero en efectivo")
            enviar = st.form_submit_button("Registrar", type="primary", width="stretch")

        if enviar:
            if not nombre.strip():
                st.error("Escribe tu nombre.")
            elif monto <= 0:
                st.error("El monto debe ser mayor a cero.")
            elif not seguro:
                st.error("Marca la casilla para confirmar.")
            else:
                gg = google()
                pid = gg.agregar_pago(
                    SHEET_ID, fecha_servicio=fecha_servicio(s).isoformat(), event_id=s.event_id,
                    cliente=s.cliente, monto=monto, metodo="Efectivo", referencia="",
                    registro=nombre.strip(), estado=g.REPORTADO, notas=notas.strip(),
                )
                refrescar_pagos()
                restante = None if pendiente is None else max(pendiente - monto, 0)
                try:
                    gg.enviar_correo(
                        NOTIFICAR_A,
                        f"💵 Efectivo reportado {pesos(monto)} – {s.cliente}",
                        f"{nombre.strip()} reportó que recibió efectivo.\n\n"
                        f"Cliente: {s.cliente}\nServicio: {s.tipo_servicio} · {hora}\n"
                        f"Monto recibido: {pesos(monto)}\n"
                        f"Saldo que queda: {pesos(restante)}\n"
                        f"Notas: {notas.strip() or '—'}\nFolio: {pid}\n\n"
                        f"Confírmalo cuando recibas el dinero: {APP_URL}\n",
                    )
                except Exception:
                    st.warning("Se guardó, pero no se pudo mandar el aviso por correo. Avisa a la oficina.")
                st.session_state["aviso_registro"] = (
                    f"Listo. Se registraron {pesos(monto)} y se avisó a la oficina. Folio {pid}.")
                st.rerun()


# =================================================================== OFICINA

def usuario_oficina() -> str:
    if MODO_LOCAL:
        return "local"
    if not st.user.get("is_logged_in", False):
        st.image("logo.png", width=260)
        st.title("Servicios")
        st.write("Acceso para la oficina con tu cuenta **@cleantox.mx**.")
        if st.button("Iniciar sesión con Google", type="primary"):
            st.login()
        st.stop()
    correo = (st.user.get("email") or "").lower()
    if correo not in ADMINS:
        st.error(f"La cuenta {correo} no tiene acceso.")
        if st.button("Cerrar sesión"):
            st.logout()
        st.stop()
    return correo


def vista_oficina() -> None:
    usuario = usuario_oficina()
    lista = pagos()

    with st.sidebar:
        st.caption(usuario)
        dia = st.date_input("Día", value=datetime.now(TZ).date(), format="DD/MM/YYYY")
        mostrar_todo = st.toggle("Mostrar también eventos que no son servicios", value=False)
        if st.button("🔄 Recargar"):
            st.cache_data.clear()
            st.rerun()
        st.link_button("Abrir hoja de pagos", f"https://docs.google.com/spreadsheets/d/{SHEET_ID}")
        if not MODO_LOCAL and st.button("Cerrar sesión"):
            st.logout()

    # ---------------- efectivo por confirmar (todas las fechas)
    por_confirmar = [p for p in lista if p.get("Estado") == g.REPORTADO]
    if por_confirmar:
        with st.expander(f"💵 Efectivo por confirmar: {pesos(g.sumar(por_confirmar))} "
                         f"({len(por_confirmar)})", expanded=True):
            for p in por_confirmar:
                c1, c2, c3 = st.columns([5, 1, 1])
                c1.markdown(md(f"**{pesos(p['Monto'])}** · {p.get('Cliente', '')} · "
                               f"reportó {p.get('Registró', '')} · {p.get('Registrado', '')}"
                               + (f" · _{p['Notas']}_" if p.get("Notas") else "")))
                if c2.button("Confirmar", key=f"ok_{p['ID']}"):
                    google().cambiar_estado(SHEET_ID, p["_fila"], g.CONFIRMADO, usuario)
                    refrescar_pagos(); st.rerun()
                if c3.button("Rechazar", key=f"no_{p['ID']}"):
                    google().cambiar_estado(SHEET_ID, p["_fila"], g.RECHAZADO, usuario)
                    refrescar_pagos(); st.rerun()

    # ---------------- servicios del día
    servicios = []
    for ev in eventos_dia(dia):
        if mostrar_todo or es_servicio(ev):
            servicios.append(parsear_evento(ev, "Servicios CleanTox"))
    servicios.sort(key=lambda s: s.inicio or datetime.max.replace(tzinfo=TZ))

    st.image("logo.png", width=170)
    st.title(f"Servicios del {dia.strftime('%d/%m/%Y')}")
    venta = sum(s.total or 0 for s in servicios)
    anticipos = sum(s.anticipo for s in servicios)
    pagos_dia = [p for s in servicios for p in g.pagos_de_evento(lista, s.event_id)]
    confirmado = g.sumar(pagos_dia, g.CONFIRMADO)
    reportado = g.sumar(pagos_dia, g.REPORTADO)
    por_cobrar = sum(saldo(s, lista) or 0 for s in servicios)

    c1, c2, c3 = st.columns(3)
    c1.metric("Servicios", len(servicios))
    c2.metric("Venta del día", pesos(venta))
    c3.metric("Por cobrar", pesos(por_cobrar))
    c4, c5, _ = st.columns(3)
    c4.metric("Cobrado (anticipos + confirmado)", pesos(anticipos + confirmado))
    c5.metric("Efectivo sin confirmar", pesos(reportado))

    if not servicios:
        st.info("No hay servicios este día en el calendario Servicios CleanTox.")

    for s in servicios:
        tarjeta(s, lista, usuario)


def tarjeta(s, lista: list[dict], usuario: str) -> None:
    ps = g.pagos_de_evento(lista, s.event_id)
    pendiente = saldo(s, lista)
    hora = s.inicio.astimezone(TZ).strftime("%H:%M") if s.inicio else "--:--"
    estado = ("🟢 Liquidado" if pendiente == 0 else "⚪ Sin monto" if pendiente is None
              else f"🟠 Pendiente {pm(pendiente)}")

    with st.container(border=True):
        st.subheader(md(f"{hora} · {s.cliente}"))
        st.caption(f"{md(s.tipo_servicio)}  ·  {estado}" + (f"  ·  origen: {md(s.origen)}" if s.origen else ""))
        izq, der = st.columns([3, 2])

        with izq:
            st.markdown(f"📍 **Dirección:** {md(s.direccion) or '—'}")
            st.markdown(f"📞 **Teléfono:** {s.telefono or '—'}" + (f"  ·  ✉️ {s.email}" if s.email else ""))
            if s.conceptos:
                st.dataframe(pd.DataFrame([{
                    "Concepto": c.texto,
                    "Cant.": str(c.cantidad) if c.monto_unitario is not None else "",
                    "Precio": pesos(c.monto_unitario) if c.monto_unitario is not None else "",
                    "Importe": pesos(c.importe) if c.importe is not None else "",
                } for c in s.conceptos]), hide_index=True, width="stretch")
            for a in s.alertas:
                st.warning(md(a), icon="⚠️")
            if s.link_calendar:
                st.markdown(f"[Abrir en Google Calendar]({s.link_calendar})")

        with der:
            st.markdown(f"**Total:** {pm(s.total)}  \n<small>{md(s.total_fuente)}</small>", unsafe_allow_html=True)
            if s.subtotal is not None:
                st.markdown(f"Subtotal {pm(s.subtotal)} · IVA {pm(s.iva)}")
            st.markdown(f"**Anticipo (según evento):** {pm(s.anticipo)}")
            for p in ps:
                icono = "✔" if p.get("Estado") == g.CONFIRMADO else "⏳"
                st.caption(md(f"{icono} {pesos(p['Monto'])} · {p.get('Método', '')} · {p.get('Estado', '')} · "
                              f"{p.get('Registró', '')} · {p.get('Registrado', '')}"))
            st.markdown(f"### Liquidación pendiente: {pm(pendiente)}")

            # ---- link para la coordinadora
            with st.expander("📲 Enviar a coordinadora"):
                link = enlaces.crear_link(APP_URL, LINK_SECRET, s.event_id, fecha_servicio(s))
                texto = (f"Servicio CleanTox {hora} – {s.cliente}. "
                         f"Aquí ves la dirección, lo que se hace y cuánto cobrar: {link}")
                st.link_button("Mandar por WhatsApp", "https://wa.me/?text=" + urllib.parse.quote(texto),
                               width="stretch")
                st.caption("¿Usas WhatsApp Web en otro navegador? Copia el mensaje con el ícono 📋 "
                           "(esquina del recuadro) y pégalo en el chat:")
                st.code(texto, language=None, wrap_lines=True)
                st.caption("El link solo muestra este servicio, sin teléfono del cliente, "
                           "y deja de funcionar al terminar el día del servicio.")

            # ---- pago registrado por la oficina
            if pendiente:
                with st.expander("💵 Registrar pago recibido"):
                    with st.form(f"f_{s.event_id}", clear_on_submit=True):
                        monto = st.number_input("Monto", min_value=0.0, value=float(pendiente), step=100.0)
                        metodo = st.selectbox("Método", ["Transferencia", "Efectivo", "Terminal", "Otro"])
                        ref = st.text_input("Referencia / folio (opcional)")
                        if st.form_submit_button("Registrar"):
                            if monto <= 0:
                                st.error("El monto debe ser mayor a cero.")
                            else:
                                google().agregar_pago(
                                    SHEET_ID, fecha_servicio=fecha_servicio(s).isoformat(),
                                    event_id=s.event_id, cliente=s.cliente, monto=monto, metodo=metodo,
                                    referencia=ref, registro=usuario, estado=g.CONFIRMADO,
                                )
                                refrescar_pagos(); st.rerun()


# =================================================================== router

qp = st.query_params
if "s" in qp:
    vista_coordinadora(qp)
else:
    vista_oficina()
