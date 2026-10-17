"""Administración: clientes, plan de cuentas, usuarios y cuenta propia."""
import io

import pandas as pd
import streamlit as st

from core import cuentas, db
from core.validaciones import calcular_dv, normalizar_ruc
from ui.comun import encabezado, seccion

PLANTILLA_CUENTAS = pd.DataFrame([
    {"registro": "COMPRAS", "codigo": "1.01.04.01.", "nombre": "Mercaderías Gravadas Por El IVA Al 10%",
     "palabras_clave": "mercaderías para reventa"},
    {"registro": "VENTAS", "codigo": "4.01.01.", "nombre": "Ventas De Mercaderías Gravadas Por El IVA",
     "palabras_clave": "venta de mercaderías"},
])


# ---------------------------------------------------------------- clientes

def pagina_clientes() -> None:
    encabezado("Clientes", "Contribuyentes del estudio e imputación por defecto")
    lista = db.filas("SELECT * FROM clientes ORDER BY nombre")
    if lista:
        tabla = pd.DataFrame(lista)
        tabla["ruc"] = tabla["ruc"] + "-" + tabla["dv"]
        st.dataframe(tabla[["nombre", "ruc", "imputa_iva", "imputa_ire", "imputa_irp", "activo"]], hide_index=True,
                     width="stretch", column_config={
                         "nombre": "Nombre o razón social", "ruc": "RUC",
                         "imputa_iva": st.column_config.CheckboxColumn("IVA"),
                         "imputa_ire": st.column_config.CheckboxColumn("IRE"),
                         "imputa_irp": st.column_config.CheckboxColumn("IRP-RSP"),
                         "activo": st.column_config.CheckboxColumn("Activo")})

    seccion("Agregar o editar")
    existente = st.selectbox("Cliente", [None] + lista, label_visibility="collapsed",
                             format_func=lambda c: "＋ Nuevo cliente" if c is None else c["nombre"])
    c = existente or {"ruc": "", "dv": "", "nombre": "", "imputa_iva": 1, "imputa_ire": 0, "imputa_irp": 0, "activo": 1}
    with st.form("form_cliente"):
        f1, f2 = st.columns([3, 2])
        nombre = f1.text_input("Nombre o razón social", c["nombre"])
        ruc_txt = f2.text_input("RUC con DV", f"{c['ruc']}-{c['dv']}" if c["ruc"] else "", placeholder="80012345-6")
        st.caption("Imputación por defecto de las compras (se puede cambiar factura por factura)")
        f1, f2, f3, f4 = st.columns(4)
        iva = f1.checkbox("IVA", bool(c["imputa_iva"]))
        ire = f2.checkbox("IRE", bool(c["imputa_ire"]))
        irp = f3.checkbox("IRP-RSP", bool(c["imputa_irp"]))
        activo = f4.checkbox("Activo", bool(c["activo"]))
        if st.form_submit_button("Guardar", type="primary", icon=":material/save:"):
            ruc, dv = normalizar_ruc(ruc_txt)
            if not ruc or not dv or not nombre:
                st.error("Completá nombre y RUC con DV.")
            elif calcular_dv(ruc) != int(dv):
                st.error(f"El DV no corresponde: para el RUC {ruc} el DV es {calcular_dv(ruc)}.")
            elif existente:
                db.ejecutar("UPDATE clientes SET ruc=%s, dv=%s, nombre=%s, imputa_iva=%s, imputa_ire=%s, imputa_irp=%s, "
                            "activo=%s WHERE id=%s",
                            (ruc, dv, nombre, int(iva), int(ire), int(irp), int(activo), existente["id"]))
                st.toast("Cliente actualizado", icon=":material/check:")
                st.rerun()
            else:
                try:
                    db.ejecutar("INSERT INTO clientes (ruc, dv, nombre, imputa_iva, imputa_ire, imputa_irp, activo) "
                                "VALUES (%s,%s,%s,%s,%s,%s,%s)", (ruc, dv, nombre, int(iva), int(ire), int(irp), int(activo)))
                    st.toast("Cliente agregado", icon=":material/check:")
                    st.rerun()
                except Exception:  # noqa: BLE001
                    st.error("Ya existe un cliente con ese RUC.")


# ---------------------------------------------------------------- plan de cuentas

def pagina_cuentas() -> None:
    encabezado("Plan de cuentas", "Cuentas de Contadis que la IA puede elegir para cada factura")
    cliente = st.session_state.get("ctx_cliente")
    f1, f2 = st.columns([1, 2])
    with f1:
        registro = st.segmented_control("Registro", list(cuentas.REGISTROS), default="COMPRAS",
                                        format_func=str.capitalize, key="cta_registro") or "COMPRAS"
    with f2:
        opciones = ["GENERAL"] + (["CLIENTE"] if cliente else [])
        alcance = st.segmented_control(
            "Alcance", opciones, default="GENERAL", key="cta_alcance",
            format_func=lambda a: "Generales (todos los clientes)" if a == "GENERAL"
            else f"Solo {cliente['nombre']}") or "GENERAL"
    cliente_id = cliente["id"] if alcance == "CLIENTE" and cliente else None

    todas = cuentas.listar(registro, cliente_id, solo_activas=False)
    propias = [c for c in todas if (c["cliente_id"] or None) == cliente_id]
    st.caption("Editá en la tabla y guardá. Para agregar una cuenta, completá la última fila. "
               "Las **palabras clave** ayudan a la IA (por ejemplo: «nafta, gasoil, estación de servicio»). "
               "Desactivá en lugar de borrar: las facturas ya cargadas conservan su cuenta.")
    original = pd.DataFrame(propias, columns=["id", "codigo", "nombre", "palabras_clave", "activo"])
    editado = st.data_editor(
        original, num_rows="dynamic", hide_index=True, width="stretch", key=f"editor_{registro}_{cliente_id}",
        column_config={"id": None, "codigo": st.column_config.TextColumn("Código", width="medium"),
                       "nombre": st.column_config.TextColumn("Nombre", required=True, width="large"),
                       "palabras_clave": st.column_config.TextColumn("Palabras clave", width="medium"),
                       "activo": st.column_config.CheckboxColumn("Activa", default=True, width="small")})
    if st.button("Guardar cambios", type="primary", icon=":material/save:"):
        cambios = 0
        for fila in editado.to_dict("records"):
            if not str(fila.get("nombre") or "").strip():
                continue
            cuenta_id = int(fila["id"]) if pd.notna(fila.get("id")) else None
            previa = next((o for o in propias if o["id"] == cuenta_id), None)
            nueva = (str(fila.get("codigo") or ""), str(fila["nombre"]), str(fila.get("palabras_clave") or ""),
                     bool(fila.get("activo") if pd.notna(fila.get("activo")) else True))
            if previa and (previa["codigo"], previa["nombre"], previa["palabras_clave"], previa["activo"]) == nueva:
                continue
            cuentas.guardar(registro, nueva[1], nueva[0], nueva[2], cliente_id, cuenta_id, nueva[3])
            cambios += 1
        st.toast(f"{cambios} cuentas guardadas", icon=":material/check:")
        st.rerun()

    with st.expander("Importar cuentas desde Excel o CSV", icon=":material/upload:"):
        st.write("Columnas: **registro** (COMPRAS o VENTAS), **codigo**, **nombre** y, opcional, **palabras_clave**. "
                 "Si una cuenta ya existe con el mismo nombre, se actualiza.")
        plantilla = io.BytesIO()
        PLANTILLA_CUENTAS.to_excel(plantilla, index=False)
        st.download_button("Descargar plantilla", plantilla.getvalue(), file_name="plantilla_cuentas.xlsx",
                           icon=":material/download:")
        archivo = st.file_uploader("Planilla", type=["xlsx", "csv"], label_visibility="collapsed")
        if archivo and st.button("Importar", icon=":material/upload:"):
            datos = pd.read_csv(archivo, dtype=str) if archivo.name.endswith(".csv") else pd.read_excel(archivo, dtype=str)
            cantidad = cuentas.importar(datos.fillna("").to_dict("records"), cliente_id)
            st.toast(f"{cantidad} cuentas importadas", icon=":material/check:")
            st.rerun()

    recordadas = db.filas(
        "SELECT cc.ruc, c.codigo, c.nombre AS cuenta, cc.actualizado_en FROM cuentas_contraparte cc "
        "JOIN cuentas c ON c.id=cc.cuenta_id WHERE cc.cliente_id=%s AND cc.tipo_lote=%s ORDER BY cc.actualizado_en DESC",
        (cliente["id"] if cliente else 0, registro))
    if recordadas:
        seccion(f"Cuentas recordadas para {cliente['nombre']}")
        st.caption("Se guardan al aprobar una factura con cuenta. La próxima factura de ese "
                   + ("proveedor" if registro == "COMPRAS" else "comprador") + " usa la misma cuenta.")
        st.dataframe(pd.DataFrame(recordadas), hide_index=True, width="stretch", column_config={
            "ruc": "RUC / CI", "codigo": "Código", "cuenta": "Cuenta",
            "actualizado_en": st.column_config.DatetimeColumn("Última vez", format="DD/MM/YYYY")})


# ---------------------------------------------------------------- usuarios

def pagina_usuarios() -> None:
    encabezado("Usuarios", "Personas con acceso a la app")
    st.dataframe(pd.DataFrame(db.filas("SELECT usuario, nombre, es_admin, activo FROM usuarios ORDER BY usuario")),
                 hide_index=True, width="stretch", column_config={
                     "usuario": "Usuario", "nombre": "Nombre",
                     "es_admin": st.column_config.CheckboxColumn("Administrador"),
                     "activo": st.column_config.CheckboxColumn("Activo")})
    seccion("Nuevo usuario")
    with st.form("nuevo_usuario"):
        f1, f2 = st.columns(2)
        usuario = f1.text_input("Usuario")
        nombre = f2.text_input("Nombre")
        f1, f2 = st.columns(2)
        clave = f1.text_input("Contraseña inicial", type="password", help="Mínimo 8 caracteres")
        admin = f2.checkbox("Administrador")
        if st.form_submit_button("Crear", type="primary", icon=":material/person_add:"):
            if not usuario or len(clave) < 8:
                st.error("Usuario obligatorio y contraseña de al menos 8 caracteres.")
            else:
                try:
                    db.crear_usuario(usuario, nombre or usuario, clave, admin)
                    st.toast("Usuario creado", icon=":material/check:")
                    st.rerun()
                except Exception:  # noqa: BLE001
                    st.error("Ese usuario ya existe.")


def pagina_mi_cuenta() -> None:
    u = st.session_state.usuario
    encabezado("Mi cuenta", f"{u['nombre']} · usuario {u['usuario']}")
    with st.form("clave"):
        actual = st.text_input("Contraseña actual", type="password")
        nueva = st.text_input("Nueva contraseña", type="password", help="Mínimo 8 caracteres")
        if st.form_submit_button("Cambiar contraseña", type="primary", icon=":material/lock_reset:"):
            if not db.autenticar(u["usuario"], actual):
                st.error("La contraseña actual no es correcta.")
            elif len(nueva) < 8:
                st.error("Mínimo 8 caracteres.")
            else:
                db.cambiar_clave(u["id"], nueva)
                st.success("Contraseña cambiada.")
