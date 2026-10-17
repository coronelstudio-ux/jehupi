"""Jehupi: comprobantes de compras y ventas → Marangatu y Contadis.

Iniciar con:  streamlit run app.py
"""
import streamlit as st

from core import config, db
from ui import acceso, admin, carga, exportacion, inicio, revision
from ui.comun import aplicar_estilo, selector_contexto

st.set_page_config(page_title="Jehupi · Estudio contable", page_icon="static/icono.svg", layout="wide")
aplicar_estilo()

try:
    db.inicializar()
except Exception as error:  # noqa: BLE001
    st.error(f"No se pudo conectar con la base de datos: {error}", icon=":material/cloud_off:")
    st.info("Revise DATABASE_URL en los secretos de la app y que se haya ejecutado supabase/esquema.sql (ver README).")
    st.stop()

if "usuario" not in st.session_state:
    acceso.pantalla_acceso()
    st.stop()

usuario = st.session_state.usuario
st.logo("static/logo.svg", icon_image="static/icono.svg", size="large")

paginas = {
    "Trabajo": [
        st.Page(inicio.pagina, title="Panel", icon=":material/space_dashboard:", url_path="panel", default=True),
        st.Page(carga.pagina, title="Cargar facturas", icon=":material/upload_file:", url_path="cargar"),
        st.Page(revision.pagina, title="Revisión", icon=":material/fact_check:", url_path="revision"),
        st.Page(exportacion.pagina, title="Exportar", icon=":material/ios_share:", url_path="exportar"),
    ],
    "Configuración": [
        st.Page(admin.pagina_clientes, title="Clientes", icon=":material/business:", url_path="clientes"),
        st.Page(admin.pagina_cuentas, title="Plan de cuentas", icon=":material/account_tree:", url_path="cuentas"),
    ],
    "Cuenta": [st.Page(admin.pagina_mi_cuenta, title="Mi cuenta", icon=":material/person:", url_path="mi-cuenta")],
}
if usuario["es_admin"]:
    paginas["Configuración"].append(
        st.Page(admin.pagina_usuarios, title="Usuarios", icon=":material/group:", url_path="usuarios"))
navegacion = st.navigation(paginas)

with st.sidebar:
    selector_contexto()
    st.divider()
    st.markdown(f"<div class='jh-usuario'>{usuario['nombre']}<br>"
                f"IA: {config.PROVEEDOR_IA.capitalize()} · {config.nombre_modelo() or 'sin modelo'}</div>",
                unsafe_allow_html=True)
    if st.button("Salir", icon=":material/logout:", width="stretch"):
        for clave in ("usuario", "zips"):
            st.session_state.pop(clave, None)
        st.rerun()

navegacion.run()
