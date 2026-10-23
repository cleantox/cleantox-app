"""
CleanTox · Servicios (versión web)

- Oficina (cuentas @cleantox.mx autorizadas): servicios, cobranza, aprobaciones, alertas, recompra.
- Coordinadora (link por WhatsApp): solo su servicio. Llegada/salida, fotos, extras, cobro y cierre.
- Cliente (link de comprobante): su comprobante de servicio.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import calculos
import enlaces
import google_api as g
import negocio
from firma import recuadro_firma
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
SERVICIOS_SHEET_ID = cfg("SERVICIOS_SHEET_ID", "")
FOTOS_FOLDER_ID = cfg("FOTOS_FOLDER_ID", "")
CIERRES_FOLDER_ID = cfg("CIERRES_FOLDER_ID", "")
NOTIFICAR_A = cfg("NOTIFICAR_A", "rrhh@cleantox.mx")
ALERTAS_CALIFICACION = list(cfg("ALERTAS_CALIFICACION", ["contacto@cleantox.mx", "rrhh@cleantox.mx"]))
ADMINS = [a.lower().strip() for a in cfg("ADMINS", [])]
APROBADORES = [a.lower().strip() for a in cfg("APROBADORES", ["rrhh@cleantox.mx", "administracion@cleantox.mx",
                                                              "contacto@cleantox.mx"])]
LINK_SECRET = cfg("LINK_SECRET")
RESENAS_URL = cfg("RESENAS_URL", "https://g.page/r/CRP6kdFQDYbWEBM/review")
PRIVACIDAD_URL = cfg("PRIVACIDAD_URL", "https://www.cleantox.mx/aviso-de-privacidad")
TERMINOS_URL = cfg("TERMINOS_URL", "https://www.cleantox.mx/terminos-condiciones")
TEXTO_LEGAL = ("Firmando este documento, acepto que el servicio se cumplió satisfactoriamente y se completó "
               "en su totalidad por lo que CleanTox quedará sin obligación y/o responsabilidad relacionada "
               "con el servicio aquí descrito.")
CARAS = negocio.CARAS
METODOS = ["Efectivo", "Transferencia", "Terminal"]

faltan = [n for n, v in {"CALENDAR_ID": CALENDAR_ID, "SHEET_ID": SHEET_ID, "LINK_SECRET": LINK_SECRET}.items() if not v]
if faltan:
    st.error("Falta configuración: " + ", ".join(faltan) + ". Revisa la GUIA (paso de secretos).")
    st.stop()


def pesos(x: float | None) -> str:
    return "—" if x is None else f"${x:,.2f}"


def pm(x: float | None) -> str:
    return pesos(x).replace("$", "\\$")


def md(texto) -> str:
    return str(texto or "").replace("$", "\\$")


def hoy() -> date:
    return datetime.now(TZ).date()


def ahora_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


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
def tabla(nombre: str) -> list[dict]:
    if nombre == "Pagos":
        return google().leer_pagos(SHEET_ID)
    return google().leer_tabla(SHEET_ID, nombre)


@st.cache_data(ttl=300, show_spinner=False)
def personal() -> list[str]:
    filas = google().leer_tabla(SHEET_ID, "Personal")
    return sorted({f.get("Nombre", "").strip() for f in filas
                   if f.get("Nombre", "").strip() and not f.get("Nombre", "").startswith("(ejemplo)")
                   and str(f.get("Activo (SI/NO)", "SI")).strip().upper() != "NO"})


@st.cache_data(ttl=300, show_spinner=False)
def tareas_config() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in google().leer_tabla(SHEET_ID, "Tareas por servicio"):
        tipo, tarea = f.get("Tipo de servicio", "").strip(), f.get("Tarea (una por renglón)", "").strip()
        if tipo and tarea and not tarea.startswith("(ejemplo)"):
            out.setdefault(tipo.lower(), []).append(tarea)
    return out


@st.cache_data(ttl=300, show_spinner=False)
def extras_lista() -> list[dict]:
    out = []
    for f in google().leer_tabla(SHEET_ID, "Extras"):
        nombre = f.get("Extra", "").strip()
        precio = g.numero(f.get("Precio sin IVA"))
        if nombre and precio and not nombre.startswith("(ejemplo)"):
            out.append({"nombre": nombre, "precio": precio, "unidad": f.get("Unidad", "") or "pieza"})
    return out


@st.cache_data(ttl=3600, show_spinner="Revisando historial de clientes…")
def recontactar() -> list[dict]:
    gg = google()
    h = []
    if SERVICIOS_SHEET_ID:
        try:
            h += negocio.historial_servicios_sheet(gg, SERVICIOS_SHEET_ID)
        except Exception:
            pass
    h += negocio.historial_calendario(gg, CALENDAR_ID, hoy() - timedelta(days=760), hoy() + timedelta(days=1))
    futuros = {x["tel"] for x in negocio.historial_calendario(gg, CALENDAR_ID, hoy() + timedelta(days=1),
                                                              hoy() + timedelta(days=370))}
    return negocio.clientes_a_recontactar(h, futuros, gg.leer_tabla(SHEET_ID, "Cierres"),
                                          gg.leer_tabla(SHEET_ID, "Recompra"), hoy())


def refrescar():
    tabla.clear()


def fecha_servicio(s) -> date:
    return s.inicio.astimezone(TZ).date() if s.inicio else hoy()


def cuenta(s) -> dict:
    return calculos.estado_cuenta(s, g.pagos_de_evento(tabla("Pagos"), s.event_id),
                                  g.de_evento(tabla("Extras servicio"), s.event_id))


def cierre_de(s) -> dict | None:
    c = g.de_evento(tabla("Cierres"), s.event_id)
    return c[0] if c else None


def hb(s) -> dict:
    return negocio.horas_bitacora(tabla("Bitacora"), s.event_id)


def hhmm(d) -> str:
    return d.astimezone(TZ).strftime("%H:%M") if d else "—"


def carpeta_fotos(s, sub: str) -> str:
    gg = google()
    raiz = FOTOS_FOLDER_ID or gg.carpeta("FOTOS SERVICIOS")
    nombre = f"{fecha_servicio(s).isoformat()} · {s.cliente} · {s.tipo_servicio.split(' · ')[0]}"
    return gg.carpeta(sub, gg.carpeta(nombre, raiz))


def tareas_de(s) -> list[str]:
    cfgt = tareas_config()
    out = []
    for tipo in s.tipo_servicio.split(" · "):
        out += cfgt.get(tipo.lower(), [])
    return out or [c.texto for c in s.conceptos]


def wa(tel: str, texto: str) -> str:
    t = negocio.tel10(tel)
    return (f"https://wa.me/52{t}?text=" if t else "https://wa.me/?text=") + urllib.parse.quote(texto)


# =================================================================== COORDINADORA

def vista_coordinadora(qp) -> None:
    ok, motivo = enlaces.verificar(LINK_SECRET, qp.get("s", ""), qp.get("d", ""), qp.get("k", ""))
    st.image("logo.png", width=170)
    if not ok:
        st.error(motivo)
        st.stop()
    ev = evento(qp["s"])
    if not ev:
        st.error("Este servicio ya no existe o fue cancelado. Comunícate con la oficina.")
        st.stop()

    gg = google()
    s = parsear_evento(ev, "Servicios CleanTox")
    aviso = st.session_state.pop("aviso", None)
    if aviso:
        st.success(aviso)

    hora = s.inicio.astimezone(TZ).strftime("%d/%m/%Y · %H:%M") if s.inicio else ""
    st.title(md(s.cliente))
    st.caption(md(f"{hora} · {s.tipo_servicio}"))
    if s.direccion:
        st.markdown(f"📍 **{md(s.direccion)}**")
        st.link_button("Abrir en Google Maps", "https://www.google.com/maps/search/?api=1&query="
                       + urllib.parse.quote(s.direccion), width="stretch")
    if s.no_fotos:
        st.error("🚫 Este cliente NO autoriza fotos. No tomes ninguna foto.")
    if s.paga_efectivo:
        st.info("💵 El cliente pagará en efectivo.")

    # ---------------- quién eres
    st.subheader("1 · ¿Quién eres?")
    nombres = personal()
    if nombres:
        quien = st.selectbox("Tu nombre", nombres, index=None, placeholder="Elige tu nombre", key="quien")
    else:
        quien = st.text_input("Tu nombre", key="quien")
    if not quien:
        st.info("Elige tu nombre para continuar.")
        st.stop()

    # ---------------- llegada
    horas = hb(s)
    st.subheader("2 · Llegada")
    if horas["llegada"]:
        st.success(f"📍 Llegada registrada a las {hhmm(horas['llegada'])} por {horas['coordinadora']}")
    elif st.button("📍 Ya llegué", type="primary", width="stretch"):
        gg.agregar(SHEET_ID, "Bitacora", {"Event ID": s.event_id, "Fecha servicio": fecha_servicio(s).isoformat(),
                                          "Cliente": s.cliente, "Evento": "Llegada", "Hora": ahora_iso(),
                                          "Coordinadora": quien, "Hora programada": hhmm(s.inicio)})
        refrescar()
        st.session_state["aviso"] = "Llegada registrada."
        st.rerun()

    # ---------------- qué se hace
    st.subheader("3 · Qué se hace")
    for c in s.conceptos:
        st.markdown(f"- {md(c.texto)}")
    if not s.conceptos:
        st.caption("Sin detalle en el evento. Confirma con la oficina.")

    # ---------------- piezas (lavado / tapetes): ajustar cantidades
    if any(t in s.tipo_servicio for t in ("Lavado de tapicería", "Entrega de tapete")):
        ajustar_piezas(s, quien, hora)

    # ---------------- fotos
    if not s.no_fotos:
        fotos(s, "Antes", "4 · Fotos de antes")

    # ---------------- extras
    st.subheader("5 · Trabajos extra")
    extras_ev = g.de_evento(tabla("Extras servicio"), s.event_id)
    for e in extras_ev:
        icono = {"Aprobado": "✔", "Por aprobar": "⏳", "Rechazado": "✖"}.get(e.get("Estado"), "")
        st.caption(md(f"{icono} {e.get('Concepto')} × {e.get('Cantidad')} = "
                      f"{pesos(g.numero(e.get('Importe sin IVA')))} + IVA · {e.get('Estado')}"))
    with st.expander("➕ Agregar trabajo extra"):
        lista = extras_lista()
        opciones = [f"{x['nombre']} · {pesos(x['precio'])} por {x['unidad']}" for x in lista] + ["Otro (no está en la lista)"]
        elegido = st.selectbox("Trabajo", opciones, index=None, placeholder="Busca o elige", key="extra_sel")
        cant = st.number_input("Cantidad (piezas o m²)", min_value=0.5, value=1.0, step=0.5, key="extra_cant")
        es_otro = elegido == opciones[-1]
        desc = st.text_input("Describe el trabajo", key="extra_desc") if es_otro else ""
        precio_otro = st.number_input("Precio acordado SIN IVA", min_value=0.0, step=50.0,
                                      key="extra_precio") if es_otro else 0.0
        if es_otro:
            st.caption("Los trabajos que no están en la lista los aprueba Evelyn o Citlalin antes de cobrarse.")
        if st.button("Agregar", key="extra_btn"):
            if not elegido or (es_otro and (not desc.strip() or precio_otro <= 0)):
                st.error("Completa el trabajo y el precio.")
            else:
                if es_otro:
                    concepto, precio, tipo, estado = desc.strip(), precio_otro, "Otro", "Por aprobar"
                else:
                    x = lista[opciones.index(elegido)]
                    concepto, precio, tipo, estado = x["nombre"], x["precio"], "Lista", "Aprobado"
                gg.agregar(SHEET_ID, "Extras servicio", {
                    "Event ID": s.event_id, "Fecha servicio": fecha_servicio(s).isoformat(), "Cliente": s.cliente,
                    "Concepto": concepto, "Cantidad": cant, "Precio sin IVA": precio,
                    "Importe sin IVA": round(precio * cant, 2), "Tipo": tipo, "Estado": estado, "Registró": quien})
                if estado == "Por aprobar":
                    try:
                        gg.enviar_correo(APROBADORES, f"⏳ Extra por aprobar – {s.cliente}",
                                         f"{quien} agregó un trabajo extra que no está en la lista:\n\n"
                                         f"{concepto} × {cant} a {pesos(precio)} sin IVA\n"
                                         f"Cliente: {s.cliente} · {hora}\n\nAprúebalo en: {APP_URL}\n")
                    except Exception:
                        pass
                refrescar()
                st.session_state["aviso"] = "Trabajo extra agregado."
                st.rerun()

    if not s.no_fotos:
        fotos(s, "Después", "6 · Fotos de después")

    # ---------------- cobro
    st.subheader("7 · Cobro")
    ec = cuenta(s)
    if ec["pendiente"] is None:
        st.warning("La oficina no registró el monto de este servicio. Confirma con ellos cuánto cobrar.")
    elif ec["nada_hoy"]:
        st.info(f"🚚 Hoy solo se recoge el tapete. No cobres nada: se cobra el día de la entrega "
                f"({pesos(s.cobro_en_entrega)} + IVA).")
    elif ec["liquidado"]:
        st.success("✅ Este servicio ya está pagado. No cobres nada.")
    else:
        if s.cobro_en_entrega:
            st.caption(f"El tapete ({pesos(s.cobro_en_entrega)} + IVA) NO se cobra hoy: se cobra en la entrega.")
        c1, c2 = st.columns(2)
        c1.metric("Si paga en efectivo", pesos(ec["efectivo"]))
        c2.metric("Si paga con transferencia o terminal", pesos(ec["transferencia"]))
        if ec["extras_por_aprobar"] > 0:
            st.caption(f"Hay {pesos(ec['extras_por_aprobar'])} + IVA en extras esperando aprobación (aún no se suman).")
        elif ec["extras_por_aprobar"] < 0:
            st.caption(f"Hay piezas quitadas por {pesos(-ec['extras_por_aprobar'])} + IVA esperando aprobación "
                       f"(el saldo todavía no baja).")
        metodo = st.radio("¿Cómo pagó?", METODOS, horizontal=True, key="metodo_cobro")
        sugerido = ec["efectivo"] if metodo == "Efectivo" else ec["transferencia"]
        with st.form("cobro", clear_on_submit=True):
            monto = st.number_input("Monto recibido", min_value=0.0, step=50.0, value=float(sugerido))
            notas = st.text_input("Notas (opcional)")
            seguro = st.checkbox("Confirmo que recibí este pago")
            enviar = st.form_submit_button("Registrar pago", type="primary", width="stretch")
        if enviar:
            if monto <= 0 or not seguro:
                st.error("Pon el monto y marca la casilla para confirmar.")
            else:
                pid = gg.agregar_pago(SHEET_ID, fecha_servicio=fecha_servicio(s).isoformat(), event_id=s.event_id,
                                      cliente=s.cliente, monto=monto, metodo=metodo, referencia="",
                                      registro=quien, estado=g.REPORTADO, notas=notas.strip())
                restante = calculos.cobrar(max(ec["pendiente"] - calculos.abono(monto, metodo), 0), metodo)
                try:
                    gg.enviar_correo(NOTIFICAR_A, f"💵 {metodo} reportado {pesos(monto)} – {s.cliente}",
                                     f"{quien} reportó un pago.\n\nCliente: {s.cliente}\nServicio: {s.tipo_servicio} · {hora}\n"
                                     f"Monto: {pesos(monto)} ({metodo})\nQueda por cobrar: {pesos(restante)} si paga igual\n"
                                     f"Notas: {notas.strip() or '—'}\nFolio: {pid}\n\nConfírmalo en: {APP_URL}\n")
                except Exception:
                    st.warning("Se guardó, pero no se pudo mandar el aviso por correo. Avisa a la oficina.")
                refrescar()
                st.session_state["aviso"] = f"Pago de {pesos(monto)} registrado. Folio {pid}."
                st.rerun()

    # ---------------- cierre
    st.subheader("8 · Terminar y cierre con el cliente")
    cierre = cierre_de(s)
    if cierre:
        st.success(f"✅ Cierre guardado. Firmó: {cierre.get('Nombre de quien firma')} · "
                   f"Satisfacción {cierre.get('Satisfacción')}")
        return
    if not horas["llegada"]:
        st.info("Primero registra tu llegada (paso 2).")
        return
    if not horas["salida"]:
        if st.button("🏁 Terminamos el servicio", width="stretch"):
            gg.agregar(SHEET_ID, "Bitacora", {"Event ID": s.event_id, "Fecha servicio": fecha_servicio(s).isoformat(),
                                              "Cliente": s.cliente, "Evento": "Salida", "Hora": ahora_iso(),
                                              "Coordinadora": quien, "Hora programada": hhmm(s.fin)})
            refrescar()
            st.session_state["aviso"] = "Salida registrada. Ahora haz el cierre con el cliente."
            st.rerun()
        st.caption("Registra la salida al terminar y luego haz el cierre con el cliente.")
        return
    st.caption(f"🏁 Salida registrada a las {hhmm(horas['salida'])}")
    formulario_cierre(s, quien, ec)


def ajustar_piezas(s, quien: str, hora: str) -> None:
    piezas = [c for c in s.conceptos if c.monto_unitario is not None
              and not (s.cobro_en_entrega and re.search(r"tapete|alfombra", c.texto, re.I))]
    if not piezas:
        return
    ajustes = [e for e in g.de_evento(tabla("Extras servicio"), s.event_id)
               if e.get("Tipo") == "Ajuste" and e.get("Estado") != "Rechazado"]
    with st.expander("🧮 Piezas: ¿se lavó todo lo cotizado?"):
        st.caption("Corrige la cantidad si el cliente agregó o quitó piezas. Si quitas piezas, la oficina "
                   "lo tiene que aprobar antes de bajar el cobro.")
        nuevos = {}
        for i, c in enumerate(piezas):
            ya = sum(g.numero(e.get("Cantidad")) for e in ajustes if e.get("Concepto") == c.texto)
            actual = c.cantidad + ya
            nuevos[i] = (c, actual, st.number_input(f"{c.texto} · cotizado {c.cantidad} · {pesos(c.monto_unitario)} c/u",
                                                     min_value=0.0, value=float(actual), step=1.0, key=f"pz_{i}"))
        if st.button("Guardar cantidades", key="pz_guardar"):
            gg = google()
            hubo = False
            for c, actual, nuevo in nuevos.values():
                delta = round(nuevo - actual, 2)
                if not delta:
                    continue
                hubo = True
                estado = "Aprobado" if delta > 0 else "Por aprobar"
                gg.agregar(SHEET_ID, "Extras servicio", {
                    "Event ID": s.event_id, "Fecha servicio": fecha_servicio(s).isoformat(), "Cliente": s.cliente,
                    "Concepto": c.texto, "Cantidad": delta, "Precio sin IVA": c.monto_unitario,
                    "Importe sin IVA": round(delta * c.monto_unitario, 2), "Tipo": "Ajuste", "Estado": estado,
                    "Registró": quien})
                if delta < 0:
                    try:
                        gg.enviar_correo(APROBADORES, f"⏳ Piezas quitadas por aprobar – {s.cliente}",
                                         f"{quien} bajó la cantidad de '{c.texto}' en {abs(delta):g} "
                                         f"({pesos(abs(delta) * c.monto_unitario)} sin IVA menos).\n"
                                         f"Cliente: {s.cliente} · {hora}\n\nAprueba o rechaza en: {APP_URL}\n")
                    except Exception:
                        pass
            if hubo:
                refrescar()
                st.session_state["aviso"] = "Cantidades guardadas."
                st.rerun()


def fotos(s, sub: str, titulo: str) -> None:
    st.subheader(titulo)
    archivos = st.file_uploader(f"Toma o elige fotos ({sub.lower()})", type=["jpg", "jpeg", "png", "heic", "webp"],
                                accept_multiple_files=True, key=f"fotos_{sub}")
    if archivos and st.button(f"Subir {len(archivos)} foto(s) de {sub.lower()}", key=f"subir_{sub}", width="stretch"):
        gg = google()
        with st.spinner("Subiendo fotos…"):
            carpeta = carpeta_fotos(s, sub)
            n = gg.contar_archivos(carpeta)
            for i, f in enumerate(archivos, start=1):
                ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else "jpg"
                gg.subir(carpeta, f"{s.cliente} - {sub.lower()} - {n + i:02d}.{ext}", f.getvalue(),
                         f.type or "image/jpeg")
        st.session_state["aviso"] = f"{len(archivos)} foto(s) de {sub.lower()} guardadas."
        st.session_state.pop(f"fotos_{sub}", None)
        st.rerun()


def formulario_cierre(s, quien: str, ec: dict) -> None:
    gg = google()
    st.markdown("**Parte de la coordinadora**")
    tareas = tareas_de(s)
    hechas = [t for i, t in enumerate(tareas) if st.checkbox(t, value=True, key=f"tarea_{i}")]
    pendientes = st.text_area("¿Quedó algo pendiente?", key="pendientes")
    c1, c2 = st.columns(2)
    metros = c1.text_input("Metros cuadrados", value=s.metros, key="metros")
    banos = c2.text_input("Baños", value=s.banos, key="banos")
    amueblado = c1.radio("Amueblado", ["Sí", "No"], horizontal=True, index=None, key="amueblado")
    cristales = c2.text_input("Cristales", key="cristales")
    otros = st.text_input("Otros", key="otros")
    operadores = st.multiselect("Operadores que fueron", personal(), default=[quien] if quien in personal() else [],
                                key="operadores")

    st.divider()
    st.markdown("**Parte del cliente** — pásale el celular al cliente")
    st.markdown(f"Servicio: **{md(s.tipo_servicio)}**  \nTotal del servicio: **{pm(ec['base'])} + IVA**  \n"
                f"Pendiente: **{pm(ec['efectivo'])}** en efectivo o **{pm(ec['transferencia'])}** con transferencia")
    satis = st.radio("¿Qué tan satisfecho quedas?", CARAS, horizontal=True, index=None, key="satis")
    trato = st.radio("El trato del operador fue:", CARAS, horizontal=True, index=None, key="trato")
    agendar = st.radio("¿Te gustaría agendar de nuevo en…?", list(negocio.INTERVALOS), horizontal=True,
                       index=None, key="agendar")
    comentarios = st.text_area("Comentarios", key="comentarios")
    nombre = st.text_input("Tu nombre", key="nombre_firma")
    st.caption(TEXTO_LEGAL)
    st.markdown(f"[Aviso de privacidad]({PRIVACIDAD_URL}) · [Términos y condiciones]({TERMINOS_URL})")
    st.markdown("**Firma aquí con el dedo** y luego toca *Usar esta firma*")
    png = recuadro_firma("firma_cliente")

    if st.button("Guardar cierre", type="primary", width="stretch"):
        faltan = [n for n, v in {"satisfacción": satis, "trato": trato, "nombre": nombre.strip(), "firma": png}.items()
                  if not v]
        if faltan:
            st.error("Falta: " + ", ".join(faltan) + ".")
            return
        with st.spinner("Guardando…"):
            raiz = CIERRES_FOLDER_ID or gg.carpeta("CIERRES CleanTox")
            fid = gg.subir(gg.carpeta(fecha_servicio(s).strftime("%Y-%m"), raiz),
                           f"{fecha_servicio(s).isoformat()} {s.cliente} - firma.png", png, "image/png")
            gg.agregar(SHEET_ID, "Cierres", {
                "Event ID": s.event_id, "Fecha servicio": fecha_servicio(s).isoformat(), "Cliente": s.cliente,
                "Teléfono": s.telefono, "Tipo de servicio": s.tipo_servicio, "Metros": metros, "Baños": banos,
                "Amueblado": amueblado or "", "Cristales": cristales, "Otros": otros,
                "Tareas hechas": " | ".join(hechas),
                "Tareas pendientes": " | ".join([t for t in tareas if t not in hechas] + ([pendientes] if pendientes else [])),
                "Operadores": ", ".join(operadores), "Coordinadora": quien, "Satisfacción": satis, "Trato": trato,
                "Agendar de nuevo": agendar or "", "Comentarios": comentarios, "Nombre de quien firma": nombre.strip(),
                "Firma (archivo)": fid, "Total sin IVA": ec["base"], "Pendiente sin IVA": ec["pendiente"]})
            if satis != CARAS[0] or trato != CARAS[0]:
                try:
                    gg.enviar_correo(ALERTAS_CALIFICACION, f"⚠️ Calificación baja – {s.cliente}",
                                     f"Cliente: {s.cliente} ({s.telefono})\nServicio: {s.tipo_servicio} · "
                                     f"{fecha_servicio(s):%d/%m/%Y}\nSatisfacción: {satis}   Trato: {trato}\n"
                                     f"Comentarios: {comentarios or '—'}\nCoordinadora: {quien}\n"
                                     f"Operadores: {', '.join(operadores)}\n\nLlámale hoy. App: {APP_URL}\n")
                except Exception:
                    pass
        refrescar()
        st.session_state["aviso"] = "¡Cierre guardado! Gracias."
        st.balloons()
        st.rerun()


# =================================================================== COMPROBANTE (cliente)

def vista_comprobante(qp) -> None:
    st.image("logo.png", width=200)
    if not enlaces.verificar_comprobante(LINK_SECRET, qp.get("c", ""), qp.get("k", "")):
        st.error("Este enlace no es válido.")
        st.stop()
    ev = evento(qp["c"])
    if not ev:
        st.error("No encontramos este servicio. Escríbenos al 56 2169 1600.")
        st.stop()
    s = parsear_evento(ev)
    c = cierre_de(s)
    ec = cuenta(s)
    st.title("Comprobante de servicio")
    st.markdown(f"**{md(s.cliente)}**  \n{fecha_servicio(s):%d/%m/%Y} · {md(s.tipo_servicio)}  \n{md(s.direccion)}")
    if c:
        st.subheader("Trabajos realizados")
        for t in [x for x in c.get("Tareas hechas", "").split(" | ") if x]:
            st.markdown(f"- {md(t)}")
    extras_ok = [e for e in g.de_evento(tabla("Extras servicio"), s.event_id) if e.get("Estado") == "Aprobado"]
    if extras_ok:
        st.subheader("Trabajos adicionales")
        for e in extras_ok:
            n = g.numero(e.get("Cantidad"))
            st.markdown(f"- {'Menos: ' if n < 0 else ''}{md(e.get('Concepto'))} × {abs(n):g}")
    st.subheader("Pagos")
    pagado = sum(p["Monto"] for p in g.pagos_de_evento(tabla("Pagos"), s.event_id)) + (s.anticipo or 0)
    st.markdown(f"Pagado: **{pm(pagado)}**")
    if ec["liquidado"]:
        st.success("✅ Servicio liquidado")
    elif ec["pendiente"] is not None:
        st.markdown(f"Saldo pendiente: **{pm(ec['transferencia'])}**")
    if c:
        st.subheader("Conformidad")
        st.markdown(f"Satisfacción: {c.get('Satisfacción')} · Trato: {c.get('Trato')}  \n"
                    f"Comentarios: {md(c.get('Comentarios') or '—')}  \nFirmó: **{md(c.get('Nombre de quien firma'))}**")
        try:
            st.image(google().descargar(c.get("Firma (archivo)")), width=260)
        except Exception:
            pass
        st.caption(TEXTO_LEGAL)
    st.markdown(f"[Aviso de privacidad]({PRIVACIDAD_URL}) · [Términos y condiciones]({TERMINOS_URL})")
    st.caption("CleanTox · Servicios de limpieza profunda · WhatsApp 56 2169 1600")


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


def puede_aprobar(usuario: str) -> bool:
    return MODO_LOCAL or usuario in APROBADORES


def vista_oficina() -> None:
    usuario = usuario_oficina()

    with st.sidebar:
        st.caption(usuario)
        dia = st.date_input("Día", value=hoy(), format="DD/MM/YYYY")
        mostrar_todo = st.toggle("Mostrar también eventos que no son servicios", value=False)
        if st.button("🔄 Recargar"):
            st.cache_data.clear()
            st.rerun()
        st.link_button("Abrir hoja de la app", f"https://docs.google.com/spreadsheets/d/{SHEET_ID}")
        if st.button("📊 Actualizar reporte semanal"):
            with st.spinner("Armando reporte…"):
                actualizar_reporte()
            st.success("Listo: pestaña 'Reporte semanal'.")
        if not MODO_LOCAL and st.button("Cerrar sesión"):
            st.logout()

    servicios = [parsear_evento(ev, "Servicios CleanTox") for ev in eventos_dia(dia) if mostrar_todo or es_servicio(ev)]
    servicios.sort(key=lambda s: s.inicio or datetime.max.replace(tzinfo=TZ))

    st.image("logo.png", width=170)
    alertas_oficina(servicios, dia, usuario)

    st.title(f"Servicios del {dia.strftime('%d/%m/%Y')}")
    cuentas = {s.event_id: cuenta(s) for s in servicios}
    venta = sum(c["base"] or 0 for c in cuentas.values())
    cobrado = sum(c["abonado"] for c in cuentas.values())
    por_cobrar = sum(c["pendiente"] or 0 for c in cuentas.values())
    sin_conf = g.sumar([p for s in servicios for p in g.pagos_de_evento(tabla("Pagos"), s.event_id)
                        if p.get("Estado") == g.REPORTADO])
    c1, c2, c3 = st.columns(3)
    c1.metric("Servicios", len(servicios))
    c2.metric("Venta del día (sin IVA)", pesos(venta))
    c3.metric("Por cobrar (sin IVA)", pesos(por_cobrar))
    c4, c5, _ = st.columns(3)
    c4.metric("Cobrado (sin IVA)", pesos(cobrado))
    c5.metric("Pagos sin confirmar", pesos(sin_conf))

    if not servicios:
        st.info("No hay servicios este día en el calendario Servicios CleanTox.")
    for s in servicios:
        tarjeta(s, cuentas[s.event_id], usuario)


def alertas_oficina(servicios: list, dia: date, usuario: str) -> None:
    gg = google()
    if dia == hoy():
        for s in negocio.retrasados(servicios, tabla("Bitacora"), datetime.now(TZ)):
            st.error(md(f"⏰ {s.cliente}: programado a las {hhmm(s.inicio)} y nadie ha registrado llegada."))

    pend = [p for p in tabla("Pagos") if p.get("Estado") == g.REPORTADO]
    if pend:
        with st.expander(f"💵 Pagos por confirmar: {pesos(g.sumar(pend))} ({len(pend)})", expanded=True):
            for p in pend:
                c1, c2, c3 = st.columns([5, 1, 1])
                c1.markdown(md(f"**{pesos(p['Monto'])}** {p.get('Método', '')} · {p.get('Cliente', '')} · "
                               f"{p.get('Registró', '')} · {p.get('Registrado', '')}"
                               + (f" · _{p['Notas']}_" if p.get("Notas") else "")))
                if c2.button("Confirmar", key=f"ok_{p['ID']}"):
                    gg.cambiar_estado(SHEET_ID, p["_fila"], g.CONFIRMADO, usuario); refrescar(); st.rerun()
                if c3.button("Rechazar", key=f"no_{p['ID']}"):
                    gg.cambiar_estado(SHEET_ID, p["_fila"], g.RECHAZADO, usuario); refrescar(); st.rerun()

    por_aprobar = [e for e in tabla("Extras servicio") if e.get("Estado") == "Por aprobar"]
    if por_aprobar:
        with st.expander(f"⏳ Extras por aprobar ({len(por_aprobar)})", expanded=True):
            for e in por_aprobar:
                c1, c2, c3 = st.columns([5, 1, 1])
                quitar = g.numero(e.get("Cantidad")) < 0
                c1.markdown(md(f"{'➖ Quitar ' if quitar else ''}**{e.get('Concepto')}** × {abs(g.numero(e.get('Cantidad'))):g} = "
                               f"{'−' if quitar else ''}{pesos(abs(g.numero(e.get('Importe sin IVA'))))} + IVA · {e.get('Cliente')} · "
                               f"{e.get('Registró')}"))
                if puede_aprobar(usuario):
                    for col, est, k in ((c2, "Aprobado", "ap"), (c3, "Rechazado", "re")):
                        if col.button("Aprobar" if k == "ap" else "Rechazar", key=f"{k}_{e['ID']}"):
                            gg.actualizar(SHEET_ID, "Extras servicio", e["_fila"], {
                                "Estado": est, "Aprobó": usuario,
                                "Fecha aprobación": datetime.now(TZ).strftime("%Y-%m-%d %H:%M")})
                            refrescar(); st.rerun()
                else:
                    c2.caption("Aprueban rrhh o administración")

    hace7 = (hoy() - timedelta(days=7)).isoformat()
    bajas = [c for c in tabla("Cierres") if c.get("Fecha servicio", "") >= hace7
             and (c.get("Satisfacción") != CARAS[0] or c.get("Trato") != CARAS[0])]
    if bajas:
        with st.expander(f"⚠️ Calificaciones bajas en los últimos 7 días ({len(bajas)})"):
            for c in bajas:
                st.markdown(md(f"**{c.get('Cliente')}** {c.get('Fecha servicio')} · Satisfacción {c.get('Satisfacción')}"
                               f" · Trato {c.get('Trato')} · {c.get('Comentarios') or ''} · Tel {c.get('Teléfono')}"))

    with st.expander("📞 Clientes para recontactar (recompra)"):
        if st.button("Ver lista", key="ver_recompra") or st.session_state.get("ver_recompra_on"):
            st.session_state["ver_recompra_on"] = True
            lista = recontactar()
            st.caption(f"{len(lista)} cliente(s) cumplieron su plazo sin nuevo servicio.")
            for r in lista[:60]:
                c1, c2, c3 = st.columns([4, 1, 1])
                c1.markdown(md(f"**{r['cliente']}** · {r['tel']} · último: {r['ultimo']:%d/%m/%Y} ({r['tipo']})"))
                primer = (r["cliente"].split() or [""])[0]
                texto = (f"Hola {primer}, te saluda CleanTox 💚. Ya pasaron {r['meses']} meses desde tu último "
                         f"servicio con nosotros. ¿Te gustaría agendar tu próxima limpieza? Con gusto te cotizamos.")
                c2.link_button("WhatsApp", wa(r["tel"], texto))
                if c3.button("Contactado", key=f"rc_{r['tel']}"):
                    gg.agregar(SHEET_ID, "Recompra", {"Teléfono": r["tel"], "Cliente": r["cliente"],
                                                      "Contactado": datetime.now(TZ).strftime("%Y-%m-%d"),
                                                      "Por": usuario})
                    recontactar.clear(); st.rerun()


def tarjeta(s, ec: dict, usuario: str) -> None:
    pendiente = ec["pendiente"]
    hora = hhmm(s.inicio)
    estado = ("🚚 Recolección: se cobra en la entrega" if ec["nada_hoy"] else
              "🟢 Liquidado" if ec["liquidado"] else "⚪ Sin monto" if pendiente is None
              else f"🟠 Pendiente {pm(pendiente)} + IVA")
    horas = hb(s)
    cierre = cierre_de(s)

    with st.container(border=True):
        st.subheader(md(f"{hora} · {s.cliente}"))
        bit = f"📍 {hhmm(horas['llegada'])} · 🏁 {hhmm(horas['salida'])}" + (
            f" · {md(horas['coordinadora'])}" if horas["coordinadora"] else "")
        st.caption(f"{md(s.tipo_servicio)}  ·  {estado}  ·  {bit}"
                   + (f"  ·  ✍️ {cierre.get('Satisfacción')}" if cierre else "")
                   + ("  ·  🚫 sin fotos" if s.no_fotos else ""))
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
            if cierre:
                st.markdown(md(f"**Cierre:** firmó {cierre.get('Nombre de quien firma')} · Satisfacción "
                               f"{cierre.get('Satisfacción')} · Trato {cierre.get('Trato')} · Agendar: "
                               f"{cierre.get('Agendar de nuevo') or '—'}"))
                if cierre.get("Tareas pendientes"):
                    st.warning(md("Pendiente: " + cierre["Tareas pendientes"]))
            if s.link_calendar:
                st.markdown(f"[Abrir en Google Calendar]({s.link_calendar})")

        with der:
            st.markdown(f"**Precio sin IVA:** {pm(s.base)}  \n<small>{md(s.base_fuente)}</small>", unsafe_allow_html=True)
            if ec["extras"] or ec["extras_por_aprobar"]:
                st.markdown(f"Extras aprobados: {pm(ec['extras'])}" +
                            (f" · por aprobar: {pm(ec['extras_por_aprobar'])}" if ec["extras_por_aprobar"] else ""))
            if s.cobro_en_entrega:
                st.markdown(f"🚚 Tapete por cobrar en la entrega: {pm(s.cobro_en_entrega)} + IVA")
            st.markdown(f"Anticipo: {pm(s.anticipo)} {md(s.anticipo_metodo or '(sin forma de pago)')}")
            for p in g.pagos_de_evento(tabla("Pagos"), s.event_id):
                icono = "✔" if p.get("Estado") == g.CONFIRMADO else "⏳"
                st.caption(md(f"{icono} {pesos(p['Monto'])} · {p.get('Método', '')} · {p.get('Estado', '')} · "
                              f"{p.get('Registró', '')}"))
            if pendiente:
                st.markdown(f"### Falta: {pm(ec['efectivo'])} efectivo  \n"
                            f"o {pm(ec['transferencia'])} transferencia")
            elif ec["liquidado"]:
                st.markdown("### ✅ Liquidado")

            with st.expander("📲 Enviar a coordinadora"):
                link = enlaces.crear_link(APP_URL, LINK_SECRET, s.event_id, fecha_servicio(s))
                texto = (f"Servicio CleanTox {hora} – {s.cliente}. "
                         f"Aquí ves la dirección, lo que se hace y cuánto cobrar: {link}")
                st.link_button("Mandar por WhatsApp", wa("", texto), width="stretch")
                st.caption("¿WhatsApp Web en otro navegador? Copia el mensaje con 📋 y pégalo en el chat:")
                st.code(texto, language=None, wrap_lines=True)

            if cierre:
                with st.expander("📄 Mandar comprobante al cliente"):
                    link = enlaces.crear_link_comprobante(APP_URL, LINK_SECRET, s.event_id)
                    primer = (s.cliente.split() or [""])[0]
                    texto = (f"Hola {primer}, gracias por confiar en CleanTox 💚. Aquí está el comprobante de tu "
                             f"servicio del {fecha_servicio(s):%d/%m/%Y}: {link}")
                    if cierre.get("Satisfacción") == CARAS[0]:
                        texto += (f"\n\n¿Nos ayudas con una reseña? {RESENAS_URL}\nComo agradecimiento tienes 10% de "
                                  f"descuento en tu próximo servicio (aplican restricciones).")
                    st.link_button("Mandar por WhatsApp", wa(s.telefono, texto), width="stretch")
                    st.code(texto, language=None, wrap_lines=True)

            if pendiente:
                with st.expander("💵 Registrar pago recibido"):
                    metodo = st.radio("Método", METODOS, horizontal=True, key=f"m_{s.event_id}")
                    sugerido = ec["efectivo"] if metodo == "Efectivo" else ec["transferencia"]
                    with st.form(f"f_{s.event_id}", clear_on_submit=True):
                        monto = st.number_input("Monto", min_value=0.0, value=float(sugerido), step=100.0)
                        ref = st.text_input("Referencia / folio (opcional)")
                        if st.form_submit_button("Registrar"):
                            if monto <= 0:
                                st.error("El monto debe ser mayor a cero.")
                            else:
                                google().agregar_pago(SHEET_ID, fecha_servicio=fecha_servicio(s).isoformat(),
                                                      event_id=s.event_id, cliente=s.cliente, monto=monto,
                                                      metodo=metodo, referencia=ref, registro=usuario,
                                                      estado=g.CONFIRMADO)
                                refrescar(); st.rerun()


def actualizar_reporte() -> None:
    gg = google()
    lunes = hoy() - timedelta(days=hoy().weekday() + 7)       # semana pasada
    servicios = negocio.servicios_entre(gg, CALENDAR_ID, lunes, lunes + timedelta(days=7))
    filas = negocio.reporte_semanal(servicios, tabla("Pagos"), tabla("Extras servicio"), tabla("Bitacora"),
                                    tabla("Cierres"), recontactar(), lunes)
    gg.escribir_pestana(SHEET_ID, "Reporte semanal", filas)


# =================================================================== router

qp = st.query_params
if "s" in qp:
    vista_coordinadora(qp)
elif "c" in qp:
    vista_comprobante(qp)
else:
    vista_oficina()
