"""Piezas compartidas de la interfaz: estilo, encabezados, formato y contexto (cliente y período)."""
from datetime import date

import streamlit as st

from core import db

MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre",
         "Noviembre", "Diciembre"]

# Estados con ícono + texto (el color nunca va solo).
ESTADOS = {
    "REVISAR": "🟠 Revisar",
    "OK": "🟢 Listo",
    "APROBADA": "🔵 Aprobada",
    "EXCLUIDA": "⚪ Electrónica",
    "DESCARTADA": "⚫ Descartada",
}

CSS = """
<style>
/* Ancho de lectura y aire */
.block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1400px;}
/* Encabezado de página */
div[data-testid="stMarkdownContainer"] p.jh-titulo {font-size: 1.75rem !important; font-weight: 700 !important;
    color: #0F2742 !important; margin: 0 !important; line-height: 1.25 !important; letter-spacing: -.01em;}
div[data-testid="stMarkdownContainer"] p.jh-subtitulo {color: #64748B !important; margin: .35rem 0 1.4rem 0 !important;
    font-size: .95rem !important;}
.jh-chip {display: inline-block; background: #EAF1FB; color: #1E4E8C; border-radius: 999px;
          padding: .15rem .65rem; font-size: .8rem; font-weight: 600; margin-right: .35rem;}
/* Tarjetas de indicadores */
div[data-testid="stMetric"] {background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: .75rem;
                             padding: .9rem 1rem; box-shadow: 0 1px 2px rgba(15, 39, 66, .04);}
div[data-testid="stMetricLabel"] p {color: #64748B; font-weight: 500;}
div[data-testid="stMetricValue"] {color: #0F2742; font-weight: 700;}
/* Secciones */
div[data-testid="stMarkdownContainer"] p.jh-seccion {font-size: .78rem; letter-spacing: .08em; text-transform: uppercase; color: #64748B;
             font-weight: 600; margin: 1.2rem 0 .5rem 0;}
/* Barra lateral */
section[data-testid="stSidebar"] .jh-usuario {font-size: .85rem; color: #9FB3CC;}
/* Botón principal un poco más presente */
button[kind="primary"] {font-weight: 600;}
</style>
"""


def aplicar_estilo() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def encabezado(titulo: str, subtitulo: str = "", chips: list[str] | None = None) -> None:
    chips_html = "".join(f'<span class="jh-chip">{c}</span>' for c in (chips or []))
    st.markdown(f'<p class="jh-titulo">{titulo}</p>'
                f'<p class="jh-subtitulo">{chips_html}{subtitulo}</p>', unsafe_allow_html=True)


def seccion(texto: str) -> None:
    st.markdown(f'<p class="jh-seccion">{texto}</p>', unsafe_allow_html=True)


def gs(valor) -> str:
    """Guaraníes con punto de miles: 1.100.000"""
    return f"{int(valor or 0):,}".replace(",", ".")


def cantidad(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def nombre_periodo(periodo: str) -> str:
    mes, anio = periodo.split("/")
    return f"{MESES[int(mes) - 1]} {anio}"


# ---------------------------------------------------------------- contexto

def clientes_activos() -> list[dict]:
    return db.filas("SELECT * FROM clientes WHERE activo=1 ORDER BY nombre")


def selector_contexto() -> None:
    """Cliente y período de trabajo, elegidos una vez en la barra lateral y usados en todas las páginas."""
    clientes = clientes_activos()
    if not clientes:
        st.session_state.ctx_cliente = None
        st.caption("Todavía no hay clientes.")
        return
    ids = [c["id"] for c in clientes]
    actual = st.session_state.get("cliente_id")
    elegido = st.selectbox("Cliente", ids, index=ids.index(actual) if actual in ids else 0,
                           format_func=lambda i: next(c["nombre"] for c in clientes if c["id"] == i))
    st.session_state.cliente_id = elegido
    st.session_state.ctx_cliente = next(c for c in clientes if c["id"] == elegido)

    hoy = date.today()
    por_defecto = (hoy.month - 2) % 12 + 1, hoy.year if hoy.month > 1 else hoy.year - 1
    mes_actual, anio_actual = st.session_state.get("mes", por_defecto[0]), st.session_state.get("anio", por_defecto[1])
    c1, c2 = st.columns([3, 2])
    mes = c1.selectbox("Mes", list(range(1, 13)), index=mes_actual - 1, format_func=lambda m: MESES[m - 1])
    anio = c2.selectbox("Año", list(range(2021, hoy.year + 2)), index=list(range(2021, hoy.year + 2)).index(anio_actual))
    st.session_state.mes, st.session_state.anio = mes, anio
    st.session_state.ctx_periodo = f"{mes:02d}/{anio}"


def contexto() -> tuple[dict | None, str]:
    return st.session_state.get("ctx_cliente"), st.session_state.get("ctx_periodo", "")


def requiere_cliente() -> tuple[dict, str] | None:
    cliente, periodo = contexto()
    if not cliente:
        st.info("Agregá un cliente en **Clientes** (menú de la izquierda) para empezar.",
                icon=":material/person_add:")
        return None
    return cliente, periodo
