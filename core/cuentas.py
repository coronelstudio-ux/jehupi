"""Plan de cuentas: la IA elige la cuenta de cada factura y la app recuerda la de cada proveedor.

Prioridad al asignar la cuenta de un comprobante:
  1. la que se usó la última vez para el mismo proveedor (o comprador) del mismo cliente;
  2. la que elige la IA de la lista de cuentas activas;
  3. ninguna (se completa en la revisión).
"""
from . import db
from .validaciones import NUMERO_SIN_NOMBRE

REGISTROS = ("COMPRAS", "VENTAS")


def listar(registro: str, cliente_id: int | None = None, solo_activas: bool = True) -> list[dict]:
    """Cuentas generales más las propias del cliente, ordenadas por código y nombre."""
    return db.filas(
        "SELECT * FROM cuentas WHERE registro=%s AND (cliente_id IS NULL OR cliente_id=%s) "
        + ("AND activo " if solo_activas else "")
        + "ORDER BY NULLIF(codigo, '') NULLS LAST, codigo, nombre",
        (registro, cliente_id or 0))


def etiqueta(cuenta: dict | None) -> str:
    if not cuenta:
        return ""
    return f"{cuenta['codigo']} {cuenta['nombre']}".strip()


def por_id(cuenta_id) -> dict | None:
    return db.fila("SELECT * FROM cuentas WHERE id=%s", (cuenta_id,)) if cuenta_id else None


def guardar(registro: str, nombre: str, codigo: str = "", palabras_clave: str = "",
            cliente_id: int | None = None, cuenta_id: int | None = None, activo: bool = True) -> int:
    nombre, codigo = nombre.strip(), codigo.strip()
    if registro not in REGISTROS or not nombre:
        raise ValueError("La cuenta necesita registro (COMPRAS o VENTAS) y nombre.")
    if cuenta_id:
        db.ejecutar("UPDATE cuentas SET registro=%s, codigo=%s, nombre=%s, palabras_clave=%s, cliente_id=%s, "
                    "activo=%s WHERE id=%s",
                    (registro, codigo, nombre, palabras_clave.strip(), cliente_id, activo, cuenta_id))
        return cuenta_id
    existente = db.fila("SELECT id FROM cuentas WHERE registro=%s AND lower(nombre)=lower(%s) "
                        "AND coalesce(cliente_id, 0)=%s", (registro, nombre, cliente_id or 0))
    if existente:
        return guardar(registro, nombre, codigo, palabras_clave, cliente_id, existente["id"], activo)
    return db.ejecutar("INSERT INTO cuentas (registro, codigo, nombre, palabras_clave, cliente_id, activo) "
                       "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                       (registro, codigo, nombre, palabras_clave.strip(), cliente_id, activo))


def importar(filas: list[dict], cliente_id: int | None = None) -> int:
    """Importa cuentas desde una planilla. Columnas: registro, codigo, nombre, palabras_clave (opcional).

    Si una cuenta con el mismo nombre ya existe, se actualiza su código y palabras clave.
    """
    cantidad = 0
    for f in filas:
        claves = {str(k).strip().lower(): v for k, v in f.items()}
        registro = str(claves.get("registro") or "").strip().upper()
        nombre = str(claves.get("nombre") or "").strip()
        if registro in REGISTROS and nombre and nombre.lower() != "nan":
            codigo = str(claves.get("codigo") or claves.get("código") or "").strip()
            guardar(registro, nombre, "" if codigo.lower() == "nan" else codigo,
                    str(claves.get("palabras_clave") or "").replace("nan", ""), cliente_id)
            cantidad += 1
    return cantidad


# ---------- memoria por proveedor / comprador ----------

def _recordable(ruc: str | None) -> bool:
    return bool(ruc) and ruc != NUMERO_SIN_NOMBRE


def recordada(cliente_id: int, tipo_lote: str, ruc: str | None) -> int | None:
    if not _recordable(ruc):
        return None
    r = db.fila("SELECT cc.cuenta_id FROM cuentas_contraparte cc JOIN cuentas c ON c.id=cc.cuenta_id "
                "WHERE cc.cliente_id=%s AND cc.tipo_lote=%s AND cc.ruc=%s AND c.activo",
                (cliente_id, tipo_lote, ruc))
    return r["cuenta_id"] if r else None


def recordar(cliente_id: int, tipo_lote: str, ruc: str | None, cuenta_id: int | None) -> None:
    if not (_recordable(ruc) and cuenta_id):
        return
    db.ejecutar(
        "INSERT INTO cuentas_contraparte (cliente_id, tipo_lote, ruc, cuenta_id) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (cliente_id, tipo_lote, ruc) DO UPDATE SET cuenta_id=EXCLUDED.cuenta_id, actualizado_en=now()",
        (cliente_id, tipo_lote, ruc, cuenta_id))


def asignar(c: dict, cuenta_ia: str | None, disponibles: list[dict]) -> dict:
    """Completa cuenta_id y cuenta_origen del comprobante `c` según la prioridad del módulo."""
    ids = {str(x["id"]) for x in disponibles}
    recordada_id = recordada(c["cliente_id"], c["tipo_lote"], c.get("ruc"))
    if recordada_id and str(recordada_id) in ids:
        c["cuenta_id"], c["cuenta_origen"] = recordada_id, "PROVEEDOR" if c["tipo_lote"] == "COMPRAS" else "CLIENTE"
    elif cuenta_ia and str(cuenta_ia) in ids:
        c["cuenta_id"], c["cuenta_origen"] = int(cuenta_ia), "IA"
    else:
        c["cuenta_id"], c["cuenta_origen"] = None, ""
    return c


# ---------- descripción al estilo de Contadis ----------

def descripcion_contadis(c: dict, cuenta: dict | None, razon_social: str) -> str:
    """Como la arma Contadis: "<CUENTA> S/ Factura Nro. 001-001-0000123 Proveedor: <razón social>"."""
    documento = "Ticket" if c.get("tipo_comprobante") == 112 else "Factura"
    if c.get("tipo_lote") == "VENTAS":
        return f"{(cuenta or {}).get('nombre', '').upper()} S/ {documento} N° {c.get('numero') or ''} Cliente: {razon_social}".strip()
    return f"{(cuenta or {}).get('nombre', '').upper()} S/ {documento} Nro. {c.get('numero') or ''} Proveedor: {razon_social}".strip()
