"""Panel de inicio: estado del mes del cliente elegido."""
import pandas as pd
import streamlit as st

from core import db, indicadores
from ui.comun import cantidad, encabezado, gs, nombre_periodo, requiere_cliente, seccion


def pagina() -> None:
    ctx = requiere_cliente()
    if not ctx:
        encabezado("Panel", "Estado del mes de cada cliente")
        return
    cliente, periodo = ctx
    encabezado(cliente["nombre"], f"RUC {cliente['ruc']}-{cliente['dv']}", chips=[nombre_periodo(periodo)])

    comprobantes = db.filas("SELECT * FROM comprobantes WHERE cliente_id=%s AND periodo=%s", (cliente["id"], periodo))
    if not comprobantes:
        with st.container(border=True):
            st.markdown("#### Todavía no hay comprobantes en este período")
            st.write("Subí las facturas de compras o ventas en **Cargar facturas**. "
                     "Cada factura se asigna sola al mes de su fecha de emisión.")
        return

    r = indicadores.resumen_mes(comprobantes)
    for registro, titulo in (("COMPRAS", "Compras"), ("VENTAS", "Ventas")):
        d = r[registro]
        seccion(titulo)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Comprobantes", d["total"])
        c2.metric("Para revisar", d["revisar"], help="Facturas con algún dato a verificar")
        c3.metric("Listos", d["listos"], help="Validados solos o aprobados en la revisión")
        c4.metric("Electrónicas", d["electronicas"], help="Ya figuran en Marangatu; igual se cargan en Contadis")
        c5.metric("Sin cuenta", d["sin_cuenta"], help="Sin cuenta contable asignada para Contadis")

    seccion("IVA del mes (estimado con lo cargado)")
    c1, c2, c3 = st.columns(3)
    c1.metric("IVA crédito · compras", gs(r["COMPRAS"]["iva"]))
    c2.metric("IVA débito · ventas", gs(r["VENTAS"]["iva"]))
    saldo = r["saldo_iva"]
    c3.metric("Saldo " + ("a pagar" if saldo > 0 else "a favor"), gs(abs(saldo)),
              help="Débito menos crédito. Es una referencia: no incluye saldos anteriores ni retenciones.")

    pendientes = r["COMPRAS"]["revisar"] + r["VENTAS"]["revisar"]
    seccion("Próximos pasos")
    with st.container(border=True):
        if pendientes:
            st.markdown(f":material/rule: **{cantidad(pendientes, 'comprobante', 'comprobantes')} para revisar** "
                        "en *Revisión*.")
        else:
            st.markdown(":material/task_alt: No hay comprobantes pendientes de revisión.")
        sin_exportar = sum(1 for c in comprobantes if c["estado"] in ("OK", "APROBADA") and not c.get("exportado"))
        if sin_exportar:
            st.markdown(f":material/upload_file: **{cantidad(sin_exportar, 'comprobante listo', 'comprobantes listos')}"
                        "** sin exportar a Marangatu.")
        if r["COMPRAS"]["sin_cuenta"] + r["VENTAS"]["sin_cuenta"]:
            st.markdown(":material/account_tree: Hay comprobantes sin cuenta: se pueden completar en *Revisión*.")

    lotes = db.filas(
        "SELECT l.id AS lote, l.tipo AS registro, l.creado_en AS cargado, l.creado_por AS usuario, "
        "COUNT(c.id) AS comprobantes FROM lotes l LEFT JOIN comprobantes c ON c.lote_id=l.id "
        "WHERE l.cliente_id=%s GROUP BY l.id ORDER BY l.id DESC LIMIT 8", (cliente["id"],))
    if lotes:
        seccion("Últimas cargas del cliente")
        st.dataframe(pd.DataFrame(lotes), hide_index=True, width="stretch",
                     column_config={"cargado": st.column_config.DatetimeColumn("Cargado", format="DD/MM/YYYY HH:mm"),
                                    "lote": "Lote", "registro": "Registro", "usuario": "Usuario",
                                    "comprobantes": "Comprobantes"})
