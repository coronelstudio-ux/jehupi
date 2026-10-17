"""Pantalla de ingreso y creación del primer administrador."""
import streamlit as st

from core import db


def pantalla_acceso() -> None:
    _, centro, _ = st.columns([1, 1.15, 1])
    with centro:
        st.markdown("<div style='height:6vh'></div>", unsafe_allow_html=True)
        st.image("static/icono.svg", width=56)
        st.markdown("<p class='jh-titulo'>Jehupi</p>"
                    "<p class='jh-subtitulo'>Comprobantes de compras y ventas · Marangatu · Contadis</p>",
                    unsafe_allow_html=True)
        with st.container(border=True):
            if not db.hay_usuarios():
                st.markdown("**Primer inicio** · creá el usuario administrador")
                with st.form("admin", border=False):
                    usuario = st.text_input("Usuario")
                    nombre = st.text_input("Nombre completo")
                    clave = st.text_input("Contraseña", type="password", help="Mínimo 8 caracteres")
                    clave2 = st.text_input("Repetir contraseña", type="password")
                    if st.form_submit_button("Crear administrador", type="primary", width="stretch"):
                        if not usuario or len(clave) < 8:
                            st.error("Usuario obligatorio y contraseña de al menos 8 caracteres.")
                        elif clave != clave2:
                            st.error("Las contraseñas no coinciden.")
                        else:
                            db.crear_usuario(usuario, nombre or usuario, clave, es_admin=True)
                            st.success("Administrador creado. Ya podés ingresar.")
                            st.rerun()
                return
            with st.form("login", border=False):
                usuario = st.text_input("Usuario")
                clave = st.text_input("Contraseña", type="password")
                if st.form_submit_button("Ingresar", type="primary", width="stretch"):
                    u = db.autenticar(usuario, clave)
                    if u:
                        st.session_state.usuario = u
                        st.rerun()
                    st.error("Usuario o contraseña incorrectos.")
        st.caption("Acceso exclusivo para el personal del estudio.")
