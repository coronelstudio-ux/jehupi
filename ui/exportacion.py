"""Exportación: archivo de importación de Marangatu (RG 90) y resumen para cargar en Contadis."""
import pandas as pd
import streamlit as st

from core import archivos, contadis, cuentas, db, exportar
from ui.comun import cantidad, encabezado, nombre_periodo, requiere_cliente, seccion


def _paginas(cliente_id: int, periodo: str) -> dict[int, str]:
    return {p["id"]: f"{p['archivo']} p.{p['nro']}" for p in db.filas(
        "SELECT DISTINCT p.* FROM paginas p JOIN comprobantes c ON c.pagina_id=p.id "
        "WHERE c.cliente_id=%s AND c.periodo=%s", (cliente_id, periodo))}


def _tarjeta_contadis(cliente: dict, periodo: str, del_mes: list[dict], paginas: dict) -> None:
    with st.container(border=True):
        st.markdown("#### :material/menu_book: Contadis")
        st.caption("Compras y ventas del mes en un Excel, en el orden de carga de Contadis: gravadas sin IVA, "
                   "IVA aparte, cuenta, descripción, concepto y detalle.")
        compras = [c for c in del_mes if c["tipo_lote"] == "COMPRAS"]
        ventas = [c for c in del_mes if c["tipo_lote"] == "VENTAS"]
        listos_c, pend_c = contadis.separar(compras)
        listos_v, pend_v = contadis.separar(ventas)
        c1, c2, c3 = st.columns(3)
        c1.metric("Compras", len(listos_c))
        c2.metric("Ventas", len(listos_v))
        c3.metric("Pendientes", len(pend_c) + len(pend_v))
        por_id = {x["id"]: x for r in cuentas.REGISTROS for x in cuentas.listar(r, cliente["id"], solo_activas=False)}
        st.download_button(
            "Descargar resumen para Contadis", type="primary", icon=":material/download:", width="stretch",
            data=contadis.generar_resumen(compras, ventas, cliente, periodo, paginas, por_id),
            file_name=f"contadis_{cliente['ruc']}_{periodo.replace('/', '')}.xlsx", disabled=not del_mes)


def _tarjeta_marangatu(cliente: dict, periodo: str, paginas: dict) -> None:
    with st.container(border=True):
        st.markdown("#### :material/account_balance: Marangatu · RG 90")
        st.caption("Archivo ZIP de importación del Registro de Comprobantes. Las electrónicas no se incluyen.")
        tipo_lote = st.segmented_control("Registro", ["COMPRAS", "VENTAS"], default="COMPRAS",
                                         format_func=str.capitalize, key="exp_tipo") or "COMPRAS"
        comprobantes = db.filas("SELECT * FROM comprobantes WHERE cliente_id=%s AND tipo_lote=%s AND periodo=%s",
                                (cliente["id"], tipo_lote, periodo))
        van, no_van = exportar.exportables(comprobantes)
        pendientes = [c for c in no_van if c["estado"] == "REVISAR"]
        electronicas = [c for c in no_van if c["estado"] == "EXCLUIDA"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Van al archivo", len(van))
        c2.metric("Para revisar", len(pendientes))
        c3.metric("Electrónicas", len(electronicas))
        if pendientes:
            aviso = cantidad(len(pendientes), "comprobante sin revisar no se incluirá",
                             "comprobantes sin revisar no se incluirán")
            st.warning(f"{aviso}.", icon=":material/rule:")
        b1, b2 = st.columns(2)
        b1.download_button("Excel de control", exportar.generar_excel_control(van, no_van, paginas, tipo_lote),
                           file_name=f"control_{tipo_lote.lower()}_{cliente['ruc']}_{periodo.replace('/', '')}.xlsx",
                           icon=":material/table_view:", width="stretch", disabled=not comprobantes)
        if b2.button("Generar ZIP", type="primary", icon=":material/folder_zip:", width="stretch", disabled=not van):
            secuencia = db.siguiente_secuencia(cliente["id"], tipo_lote, periodo)
            st.session_state.zips = exportar.generar_zips(van, cliente["ruc"], periodo, secuencia, tipo_lote)
            cerrados = van + electronicas
            db.ejecutar("UPDATE comprobantes SET exportado = true WHERE id = ANY(%s)", ([c["id"] for c in cerrados],))
            liberadas = archivos.liberar(list({c["pagina_id"] for c in cerrados if c["pagina_id"]}))
            if liberadas:
                st.caption(f"Se borraron {liberadas} imágenes que ya no hacen falta.")
            for nombre, _, n_comp in st.session_state.zips:
                db.ejecutar("INSERT INTO exportaciones (cliente_id, tipo_lote, periodo, secuencia, archivo, cantidad, "
                            "usuario) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            (cliente["id"], tipo_lote, periodo, secuencia, nombre, n_comp,
                             st.session_state.usuario["usuario"]))
                secuencia += 1
        prefijo = f"{cliente['ruc']}_REG_{periodo[:2]}{periodo[3:]}_{exportar.LETRA_LOTE[tipo_lote]}"
        for nombre, contenido, n_comp in st.session_state.get("zips", []):
            if nombre.startswith(prefijo):
                st.download_button(f"{nombre} · {n_comp} comprobantes", contenido, file_name=nombre,
                                   mime="application/zip", key=nombre, icon=":material/download:", width="stretch")


def pagina() -> None:
    encabezado("Exportar", "Archivos para Marangatu y para Contadis")
    ctx = requiere_cliente()
    if not ctx:
        return
    cliente, periodo = ctx
    del_mes = db.filas("SELECT * FROM comprobantes WHERE cliente_id=%s AND periodo=%s", (cliente["id"], periodo))
    if not del_mes:
        st.info(f"No hay comprobantes en {nombre_periodo(periodo)} para {cliente['nombre']}.", icon=":material/inbox:")
        return
    paginas = _paginas(cliente["id"], periodo)
    izquierda, derecha = st.columns(2, gap="large")
    with izquierda:
        _tarjeta_contadis(cliente, periodo, del_mes, paginas)
    with derecha:
        _tarjeta_marangatu(cliente, periodo, paginas)

    historial = db.filas("SELECT tipo_lote AS registro, archivo, cantidad, usuario, fecha FROM exportaciones "
                         "WHERE cliente_id=%s AND periodo=%s ORDER BY id DESC", (cliente["id"], periodo))
    if historial:
        seccion("Archivos generados para Marangatu en este período")
        st.dataframe(pd.DataFrame(historial), hide_index=True, width="stretch",
                     column_config={"fecha": st.column_config.DatetimeColumn("Fecha", format="DD/MM/YYYY HH:mm"),
                                    "registro": "Registro", "archivo": "Archivo", "cantidad": "Comprobantes",
                                    "usuario": "Usuario"})
