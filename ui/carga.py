"""Carga de facturas: PDF, fotos o escaneos."""
import pandas as pd
import streamlit as st

from core import config, db, procesador
from ui.comun import encabezado, nombre_periodo, requiere_cliente, seccion


def pagina() -> None:
    encabezado("Cargar facturas", "PDF con una o varias facturas, fotos o escaneos")
    ctx = requiere_cliente()
    if not ctx:
        return
    cliente, periodo = ctx
    problema = config.problema_configuracion()
    if problema:
        st.error(problema, icon=":material/key_off:")
        return

    with st.container(border=True):
        seccion("1 · Registro")
        tipo = st.segmented_control("Registro", ["COMPRAS", "VENTAS"], default="COMPRAS", label_visibility="collapsed",
                                    format_func=lambda t: "Compras · facturas recibidas" if t == "COMPRAS"
                                    else "Ventas · facturas emitidas") or "COMPRAS"
        seccion("2 · Archivos")
        archivos = st.file_uploader("Archivos", type=["pdf", "jpg", "jpeg", "png", "tif", "tiff"],
                                    accept_multiple_files=True, label_visibility="collapsed")
        st.caption(f"Cliente **{cliente['nombre']}** · período por defecto **{nombre_periodo(periodo)}**. "
                   "Cada factura va al mes de su fecha; ese período se usa solo si la fecha no se lee. "
                   "Recomendado: lotes de hasta 100 páginas.")
        procesar = st.button("Procesar facturas", type="primary", disabled=not archivos, icon=":material/bolt:")

    if not (archivos and procesar):
        return
    usuario = st.session_state.usuario["usuario"]
    lote_id = db.ejecutar(
        "INSERT INTO lotes (cliente_id, tipo, periodo, archivos, creado_por) VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (cliente["id"], tipo, periodo, db.json_([a.name for a in archivos]), usuario))
    with st.status("Procesando facturas…", expanded=True) as estado:
        barra = st.progress(0.0, "Preparando páginas…")
        resumen = procesador.procesar_lote(lote_id, [(a.name, a.getvalue()) for a in archivos],
                                           progreso=lambda i, n, msg: barra.progress(i / n if n else 0, msg))
        barra.empty()
        estado.update(label=f"Lote {lote_id} procesado · {resumen['paginas']} páginas", state="complete")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Listos", resumen["OK"])
    c2.metric("Para revisar", resumen["REVISAR"])
    c3.metric("Electrónicas", resumen["EXCLUIDA"])
    c4.metric("Páginas con error", resumen["errores_pagina"])
    if len(resumen["periodos"]) > 1 or periodo not in resumen["periodos"]:
        detalle = ", ".join(f"{nombre_periodo(p)}: {n}" for p, n in sorted(
            resumen["periodos"].items(), key=lambda x: x[0][3:] + x[0][:2]))
        st.info(f"El lote tenía comprobantes de varios meses y se repartieron así: {detalle}",
                icon=":material/calendar_month:")
    if resumen["REVISAR"]:
        st.warning("Hay comprobantes para revisar: abrí **Revisión** en el menú.", icon=":material/rule:")
    sin_comprobante = [d for d in resumen["detalle"] if d["resultado"].startswith("Sin comprobante")]
    if sin_comprobante:
        st.warning(f"{len(sin_comprobante)} página(s) no se cargaron como factura. Revisalas en la tabla "
                   "de abajo: si son facturas, cargalas a mano en Contadis.", icon=":material/warning:")
    if resumen["detalle"]:
        seccion("Detalle por página")
        st.dataframe(pd.DataFrame(resumen["detalle"]), hide_index=True, width="stretch",
                     column_config={"archivo": "Archivo", "pagina": "Página", "resultado": "Resultado"})
