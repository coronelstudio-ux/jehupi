"""Facturas → Marangatu. Interfaz web para la oficina contable.

Iniciar con:  streamlit run app.py
"""
import re
from datetime import date

import pandas as pd
import streamlit as st

from core import archivos, config, db, exportar, procesador
from core.validaciones import calcular_dv, normalizar_numero, normalizar_ruc, solo_digitos

st.set_page_config(page_title="Facturas → Marangatu", page_icon="🧾", layout="wide")
try:
    db.inicializar()
except Exception as error:  # noqa: BLE001
    st.error(f"No se pudo conectar con la base de datos: {error}")
    st.info("Revise DATABASE_URL en los secretos de la app y que se haya ejecutado supabase/esquema.sql (ver README).")
    st.stop()

ESTADOS = ["REVISAR", "OK", "APROBADA", "EXCLUIDA", "DESCARTADA"]
CLASES = ["PREIMPRESA", "AUTOIMPRESOR", "MANUAL", "TICKET", "ELECTRONICA", "OTRO"]
TIPOS = {109: "109 - Factura", 112: "112 - Ticket"}
CONDICIONES = {1: "1 - Contado", 2: "2 - Crédito"}


# ---------------------------------------------------------------- acceso

def pantalla_acceso() -> None:
    st.title("🧾 Facturas → Marangatu")
    if not db.hay_usuarios():
        st.info("Primer inicio: creá el usuario administrador.")
        with st.form("admin"):
            usuario = st.text_input("Usuario")
            nombre = st.text_input("Nombre completo")
            clave = st.text_input("Contraseña", type="password")
            clave2 = st.text_input("Repetir contraseña", type="password")
            if st.form_submit_button("Crear administrador"):
                if not usuario or len(clave) < 8:
                    st.error("Usuario obligatorio y contraseña de al menos 8 caracteres.")
                elif clave != clave2:
                    st.error("Las contraseñas no coinciden.")
                else:
                    db.crear_usuario(usuario, nombre or usuario, clave, es_admin=True)
                    st.success("Administrador creado. Ya podés ingresar.")
                    st.rerun()
        return
    with st.form("login"):
        usuario = st.text_input("Usuario")
        clave = st.text_input("Contraseña", type="password")
        if st.form_submit_button("Ingresar"):
            u = db.autenticar(usuario, clave)
            if u:
                st.session_state.usuario = u
                st.rerun()
            st.error("Usuario o contraseña incorrectos.")


# ---------------------------------------------------------------- utilidades

def clientes_activos() -> list[dict]:
    return db.filas("SELECT * FROM clientes WHERE activo=1 ORDER BY nombre")


def elegir_cliente(clave: str) -> dict | None:
    clientes = clientes_activos()
    if not clientes:
        st.warning("Primero cargá al menos un cliente en la sección Clientes.")
        return None
    return st.selectbox("Cliente", clientes, key=clave,
                        format_func=lambda c: f"{c['nombre']} — RUC {c['ruc']}-{c['dv']}")


def elegir_periodo(clave: str) -> str:
    hoy = date.today()
    mes_anterior = (hoy.month - 2) % 12 + 1
    anio = hoy.year if hoy.month > 1 else hoy.year - 1
    c1, c2 = st.columns(2)
    mes = c1.selectbox("Mes", list(range(1, 13)), index=mes_anterior - 1, key=f"{clave}_mes")
    anio = c2.number_input("Año", 2021, 2100, anio, key=f"{clave}_anio")
    return f"{mes:02d}/{anio}"


# ---------------------------------------------------------------- clientes

def pagina_clientes() -> None:
    st.header("Clientes")
    lista = db.filas("SELECT * FROM clientes ORDER BY nombre")
    if lista:
        st.dataframe(pd.DataFrame(lista)[["nombre", "ruc", "dv", "imputa_iva", "imputa_ire", "imputa_irp", "activo"]],
                     hide_index=True, width="stretch")

    st.subheader("Agregar o editar cliente")
    existente = st.selectbox("Cliente a editar", [None] + lista,
                             format_func=lambda c: "— Nuevo cliente —" if c is None else c["nombre"])
    c = existente or {"ruc": "", "dv": "", "nombre": "", "imputa_iva": 1, "imputa_ire": 0, "imputa_irp": 0, "activo": 1}
    with st.form("cliente"):
        nombre = st.text_input("Nombre o razón social", c["nombre"])
        ruc_txt = st.text_input("RUC (con DV, ej. 80012345-6)", f"{c['ruc']}-{c['dv']}" if c["ruc"] else "")
        st.caption("Imputación por defecto de las compras (se puede cambiar factura por factura):")
        col1, col2, col3 = st.columns(3)
        iva = col1.checkbox("IVA", bool(c["imputa_iva"]))
        ire = col2.checkbox("IRE", bool(c["imputa_ire"]))
        irp = col3.checkbox("IRP-RSP", bool(c["imputa_irp"]))
        activo = st.checkbox("Activo", bool(c["activo"]))
        if st.form_submit_button("Guardar"):
            ruc, dv = normalizar_ruc(ruc_txt)
            if not ruc or not dv or not nombre:
                st.error("Completá nombre y RUC con DV.")
            elif calcular_dv(ruc) != int(dv):
                st.error(f"El DV no corresponde: para el RUC {ruc} el DV es {calcular_dv(ruc)}.")
            elif existente:
                db.ejecutar("UPDATE clientes SET ruc=%s, dv=%s, nombre=%s, imputa_iva=%s, imputa_ire=%s, imputa_irp=%s, activo=%s "
                            "WHERE id=%s", (ruc, dv, nombre, int(iva), int(ire), int(irp), int(activo), existente["id"]))
                st.success("Cliente actualizado.")
                st.rerun()
            else:
                try:
                    db.ejecutar("INSERT INTO clientes (ruc, dv, nombre, imputa_iva, imputa_ire, imputa_irp, activo) "
                                "VALUES (%s,%s,%s,%s,%s,%s,%s)", (ruc, dv, nombre, int(iva), int(ire), int(irp), int(activo)))
                    st.success("Cliente agregado.")
                    st.rerun()
                except Exception:  # noqa: BLE001
                    st.error("Ya existe un cliente con ese RUC.")


# ---------------------------------------------------------------- carga

def pagina_carga() -> None:
    st.header("Cargar facturas")
    problema = config.problema_configuracion()
    if problema:
        st.error(problema)
        return
    cliente = elegir_cliente("carga_cliente")
    if not cliente:
        return
    periodo = elegir_periodo("carga")
    st.caption("Cada factura se asigna sola al mes de su fecha de emisión. "
               "El mes elegido acá se usa solo si la fecha no se puede leer.")
    tipo = st.radio("Tipo de lote", ["COMPRAS", "VENTAS"], horizontal=True,
                    captions=["", "Disponible en la Fase 2"])
    if tipo == "VENTAS":
        st.info("El registro de ventas se habilita en la Fase 2.")
        return
    archivos = st.file_uploader("PDF (una o varias facturas por archivo), fotos o escaneos",
                                type=["pdf", "jpg", "jpeg", "png", "tif", "tiff"], accept_multiple_files=True)
    if archivos and st.button("Procesar", type="primary"):
        usuario = st.session_state.usuario["usuario"]
        lote_id = db.ejecutar(
            "INSERT INTO lotes (cliente_id, tipo, periodo, archivos, creado_por) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (cliente["id"], tipo, periodo, db.json_([a.name for a in archivos]), usuario))
        barra = st.progress(0.0, "Preparando páginas…")
        resumen = procesador.procesar_lote(
            lote_id, [(a.name, a.getvalue()) for a in archivos],
            progreso=lambda i, n, msg: barra.progress(i / n, msg))
        barra.empty()
        st.success(f"Lote {lote_id} procesado: {resumen['paginas']} páginas.")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("OK", resumen["OK"])
        c2.metric("A revisar", resumen["REVISAR"])
        c3.metric("Electrónicas (excluidas)", resumen["EXCLUIDA"])
        c4.metric("Páginas con error", resumen["errores_pagina"])
        if len(resumen["periodos"]) > 1 or periodo not in resumen["periodos"]:
            detalle = ", ".join(f"{p}: {n}" for p, n in sorted(resumen["periodos"].items(),
                                                                key=lambda x: x[0][3:] + x[0][:2]))
            st.warning(f"El lote tenía comprobantes de varios meses y se repartieron así: {detalle}")
        st.info("Revisá los comprobantes marcados en la sección Revisión.")


# ---------------------------------------------------------------- revisión

def formulario_comprobante(c: dict, cliente: dict) -> None:
    paginas = db.filas("SELECT * FROM paginas WHERE id=%s OR comprobante_ref=%s ORDER BY nro",
                       (c["pagina_id"] or 0, c["id"]))
    col_img, col_form = st.columns([1, 1])
    with col_img:
        for pagina in paginas:
            imagen = archivos.bajar(pagina["ruta"])
            if imagen:
                st.image(imagen, caption=f"{pagina['archivo']} — página {pagina['nro']}", width="stretch")
            else:
                st.info(f"{pagina['archivo']} — página {pagina['nro']}: la imagen ya se borró (comprobante cerrado).")
        if not paginas:
            st.warning("Imagen no disponible.")
    with col_form:
        for e in c["errores"]:
            st.error(e)
        if c["observaciones"]:
            st.info(c["observaciones"])
        with st.form(f"comp_{c['id']}"):
            f1, f2 = st.columns(2)
            clase = f1.selectbox("Clase", CLASES, CLASES.index(c["clase"]) if c["clase"] in CLASES else 0)
            tipo = f2.selectbox("Tipo de comprobante", list(TIPOS), format_func=TIPOS.get,
                                index=list(TIPOS).index(c["tipo_comprobante"]) if c["tipo_comprobante"] in TIPOS else 0)
            f1, f2, f3 = st.columns([3, 1, 2])
            ruc = f1.text_input("RUC proveedor", c["ruc"] or "")
            dv = f2.text_input("DV", c["dv"] or "")
            ruc_receptor = f3.text_input("RUC receptor", c["ruc_receptor"] or "")
            f1, f2, f3 = st.columns(3)
            timbrado = f1.text_input("Timbrado", c["timbrado"] or "")
            numero = f2.text_input("Número", c["numero"] or "")
            fecha = f3.text_input("Fecha (dd/mm/aaaa)", c["fecha"] or "")
            f1, f2, f3 = st.columns(3)
            vig_ini = f1.text_input("Vigencia desde", c["vigencia_inicio"] or "")
            vig_fin = f2.text_input("Vigencia hasta", c["vigencia_fin"] or "")
            periodo = f3.text_input("Período a declarar (MM/AAAA)", c["periodo"])
            f1, f2, f3, f4 = st.columns(4)
            g10 = f1.number_input("Gravada 10%", 0, value=int(c["gravada_10"] or 0), step=1)
            g5 = f2.number_input("Gravada 5%", 0, value=int(c["gravada_5"] or 0), step=1)
            ex = f3.number_input("Exenta", 0, value=int(c["exenta"] or 0), step=1)
            total = f4.number_input("Total", 0, value=int(c["total"] or 0), step=1)
            f1, f2, f3 = st.columns(3)
            iva10 = f1.number_input("IVA 10%", 0, value=int(c["iva_10"] or 0), step=1)
            iva5 = f2.number_input("IVA 5%", 0, value=int(c["iva_5"] or 0), step=1)
            condicion = f3.selectbox("Condición", list(CONDICIONES), format_func=CONDICIONES.get,
                                     index=0 if c["condicion"] != 2 else 1)
            f1, f2, f3 = st.columns(3)
            imp_iva = f1.checkbox("Imputa IVA", bool(c["imputa_iva"]))
            imp_ire = f2.checkbox("Imputa IRE", bool(c["imputa_ire"]))
            imp_irp = f3.checkbox("Imputa IRP-RSP", bool(c["imputa_irp"]))
            b1, b2, b3 = st.columns(3)
            guardar = b1.form_submit_button("Guardar y validar", type="primary")
            aprobar = b2.form_submit_button("Aprobar")
            descartar = b3.form_submit_button("Descartar")

    if not (guardar or aprobar or descartar):
        return
    usuario = st.session_state.usuario["usuario"]
    if descartar:
        db.actualizar_comprobante(c["id"], {"estado": "DESCARTADA"}, usuario, "descartar")
        archivos.liberar([p["id"] for p in paginas])
        st.rerun()
    ruc_n, dv_n = normalizar_ruc(ruc, dv)
    periodo = periodo.strip()
    if periodo == c["periodo"] and fecha.strip() != (c["fecha"] or ""):
        # Si se corrigió la fecha y no se tocó el período, el período sigue a la nueva fecha.
        periodo = procesador.periodo_de_fecha(fecha) or periodo
    if not re.fullmatch(r"(0[1-9]|1[0-2])/\d{4}", periodo):
        st.error("El período debe tener el formato MM/AAAA, por ejemplo 04/2026.")
        return
    nuevo = {
        **c, "periodo": periodo, "clase": clase, "tipo_comprobante": tipo, "ruc": ruc_n, "dv": dv_n,
        "ruc_receptor": normalizar_ruc(ruc_receptor)[0], "timbrado": solo_digitos(timbrado),
        "numero": normalizar_numero(numero), "fecha": fecha.strip(), "vigencia_inicio": vig_ini.strip(),
        "vigencia_fin": vig_fin.strip(), "gravada_10": g10, "gravada_5": g5, "exenta": ex, "total": total,
        "iva_10": iva10, "iva_5": iva5, "condicion": condicion, "imputa_iva": int(imp_iva),
        "imputa_ire": int(imp_ire), "imputa_irp": int(imp_irp), "campos_dudosos": [],
    }
    procesador.evaluar(nuevo, cliente)
    if aprobar:
        accion = "aprobar" if not nuevo["errores"] else "aprobar_con_observaciones"
        nuevo["estado"] = "APROBADA"
    else:
        accion = "editar"
    db.actualizar_comprobante(c["id"], nuevo, usuario, accion)
    if aprobar:
        archivos.liberar([p["id"] for p in paginas])
    st.rerun()


def pagina_revision() -> None:
    st.header("Revisión")
    cliente = elegir_cliente("rev_cliente")
    if not cliente:
        return
    periodo = elegir_periodo("rev")
    estados = st.multiselect("Estados", ESTADOS, default=["REVISAR"])
    if not estados:
        return
    marcas = ",".join("%s" for _ in estados)
    lista = db.filas(
        f"SELECT c.*, p.archivo, p.nro AS pagina FROM comprobantes c LEFT JOIN paginas p ON p.id=c.pagina_id "
        f"WHERE c.cliente_id=%s AND c.tipo_lote='COMPRAS' AND c.periodo=%s AND c.estado IN ({marcas}) ORDER BY c.id",
        (cliente["id"], periodo, *estados))
    if not lista:
        st.success("No hay comprobantes con esos estados.")
        return
    tabla = pd.DataFrame(lista)
    tabla["problemas"] = tabla["errores"].map(lambda e: len(e or []))
    seleccion = st.dataframe(
        tabla[["id", "estado", "clase", "ruc", "timbrado", "numero", "fecha", "total", "problemas", "archivo", "pagina"]],
        hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row")
    filas_sel = seleccion.selection.rows
    if filas_sel:
        st.divider()
        formulario_comprobante(db.leer_comprobante(int(tabla.iloc[filas_sel[0]]["id"])), cliente)
    else:
        st.caption("Seleccioná una fila para ver la imagen y corregir.")


# ---------------------------------------------------------------- exportación

def pagina_exportar() -> None:
    st.header("Exportar a Marangatu")
    cliente = elegir_cliente("exp_cliente")
    if not cliente:
        return
    periodo = elegir_periodo("exp")
    comprobantes = db.filas("SELECT * FROM comprobantes WHERE cliente_id=%s AND tipo_lote='COMPRAS' AND periodo=%s",
                            (cliente["id"], periodo))
    if not comprobantes:
        st.info("No hay comprobantes cargados para ese período.")
        return
    van, no_van = exportar.exportables(comprobantes)
    pendientes = [c for c in no_van if c["estado"] == "REVISAR"]
    electronicas = [c for c in no_van if c["estado"] == "EXCLUIDA"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Van al archivo", len(van))
    c2.metric("Pendientes de revisión", len(pendientes))
    c3.metric("Electrónicas (no se importan)", len(electronicas))
    if pendientes:
        st.warning(f"Hay {len(pendientes)} comprobantes sin revisar. No se incluirán en el archivo.")

    paginas = {p["id"]: f"{p['archivo']} p.{p['nro']}" for p in db.filas(
        "SELECT DISTINCT p.* FROM paginas p JOIN comprobantes c ON c.pagina_id=p.id "
        "WHERE c.cliente_id=%s AND c.tipo_lote='COMPRAS' AND c.periodo=%s", (cliente["id"], periodo))}
    st.download_button("Descargar Excel de control", exportar.generar_excel_control(van, no_van, paginas),
                       file_name=f"control_{cliente['ruc']}_{periodo.replace('/', '')}.xlsx")

    if van and st.button("Generar archivo para Marangatu", type="primary"):
        secuencia = db.siguiente_secuencia(cliente["id"], "COMPRAS", periodo)
        st.session_state.zips = exportar.generar_zips(van, cliente["ruc"], periodo, secuencia)
        cerrados = van + [c for c in no_van if c["estado"] == "EXCLUIDA"]
        db.ejecutar("UPDATE comprobantes SET exportado = true WHERE id = ANY(%s)", ([c["id"] for c in cerrados],))
        liberadas = archivos.liberar(list({c["pagina_id"] for c in cerrados if c["pagina_id"]}))
        if liberadas:
            st.caption(f"Se borraron {liberadas} imágenes que ya no hacen falta.")
        for nombre, _, cantidad in st.session_state.zips:
            db.ejecutar("INSERT INTO exportaciones (cliente_id, tipo_lote, periodo, secuencia, archivo, cantidad, usuario) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s)", (cliente["id"], "COMPRAS", periodo, secuencia, nombre, cantidad,
                                                          st.session_state.usuario["usuario"]))
            secuencia += 1
    for nombre, contenido, cantidad in st.session_state.get("zips", []):
        if nombre.startswith(f"{cliente['ruc']}_REG_"):
            st.download_button(f"Descargar {nombre} ({cantidad} comprobantes)", contenido, file_name=nombre,
                               mime="application/zip", key=nombre)

    historial = db.filas("SELECT archivo, cantidad, usuario, fecha FROM exportaciones WHERE cliente_id=%s AND periodo=%s "
                         "ORDER BY id DESC", (cliente["id"], periodo))
    if historial:
        st.caption("Archivos generados para este período")
        st.dataframe(pd.DataFrame(historial), hide_index=True, width="stretch")


# ---------------------------------------------------------------- usuarios

def pagina_usuarios() -> None:
    st.header("Usuarios")
    st.dataframe(pd.DataFrame(db.filas("SELECT usuario, nombre, es_admin, activo FROM usuarios ORDER BY usuario")),
                 hide_index=True, width="stretch")
    with st.form("nuevo_usuario"):
        st.subheader("Nuevo usuario")
        usuario = st.text_input("Usuario")
        nombre = st.text_input("Nombre")
        clave = st.text_input("Contraseña inicial", type="password")
        admin = st.checkbox("Administrador")
        if st.form_submit_button("Crear"):
            if not usuario or len(clave) < 8:
                st.error("Usuario obligatorio y contraseña de al menos 8 caracteres.")
            else:
                try:
                    db.crear_usuario(usuario, nombre or usuario, clave, admin)
                    st.success("Usuario creado.")
                    st.rerun()
                except Exception:  # noqa: BLE001
                    st.error("Ese usuario ya existe.")


def pagina_mi_clave() -> None:
    st.header("Cambiar mi contraseña")
    with st.form("clave"):
        actual = st.text_input("Contraseña actual", type="password")
        nueva = st.text_input("Nueva contraseña", type="password")
        if st.form_submit_button("Cambiar"):
            u = st.session_state.usuario
            if not db.autenticar(u["usuario"], actual):
                st.error("La contraseña actual no es correcta.")
            elif len(nueva) < 8:
                st.error("Mínimo 8 caracteres.")
            else:
                db.cambiar_clave(u["id"], nueva)
                st.success("Contraseña cambiada.")


# ---------------------------------------------------------------- principal

if "usuario" not in st.session_state:
    pantalla_acceso()
else:
    u = st.session_state.usuario
    paginas = {"Cargar facturas": pagina_carga, "Revisión": pagina_revision, "Exportar": pagina_exportar,
               "Clientes": pagina_clientes, "Mi contraseña": pagina_mi_clave}
    if u["es_admin"]:
        paginas["Usuarios"] = pagina_usuarios
    with st.sidebar:
        st.markdown(f"**{u['nombre']}**")
        st.caption(f"IA: {config.PROVEEDOR_IA.capitalize()} · {config.nombre_modelo() or 'sin modelo'}")
        eleccion = st.radio("Menú", list(paginas), label_visibility="collapsed")
        if st.button("Salir"):
            del st.session_state.usuario
            st.rerun()
    paginas[eleccion]()
