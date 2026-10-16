"""Base de datos en Supabase (PostgreSQL), compartida por todos los usuarios."""
import atexit
import hashlib
import json
import secrets
import threading
from datetime import datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import config

CAMPOS_EDITABLES = [
    "periodo", "clase", "tipo_comprobante", "cdc", "ruc", "dv", "razon_social", "ruc_receptor", "timbrado",
    "vigencia_inicio", "vigencia_fin", "numero", "fecha", "gravada_10", "gravada_5", "exenta", "total",
    "iva_10", "iva_5", "condicion", "moneda", "imputa_iva", "imputa_ire", "imputa_irp", "estado",
    "errores", "campos_dudosos", "observaciones", "exportado",
]

_pool: ConnectionPool | None = None
_candado = threading.Lock()


def ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _obtener_pool() -> ConnectionPool:
    global _pool
    with _candado:
        if _pool is None:
            if not config.DATABASE_URL:
                raise RuntimeError("Falta configurar DATABASE_URL en los secretos de la app.")
            # prepare_threshold=None: compatible con el pooler de Supabase.
            _pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=5, open=True,
                                   kwargs={"row_factory": dict_row, "prepare_threshold": None,
                                           "autocommit": False})
        return _pool


def cerrar() -> None:
    global _pool
    with _candado:
        if _pool is not None:
            _pool.close()
            _pool = None


atexit.register(cerrar)


def _adaptar(params):
    """Los diccionarios van como JSON; las listas, como listas de PostgreSQL (para ANY(...)).
    Una lista que deba guardarse como JSON se pasa envuelta con json_()."""
    return tuple(Jsonb(p) if isinstance(p, dict) else p for p in params)


def json_(valor) -> Jsonb:
    return Jsonb(valor)


def filas(sql: str, params=()) -> list[dict]:
    with _obtener_pool().connection() as con:
        return [dict(r) for r in con.execute(sql, _adaptar(params)).fetchall()]


def fila(sql: str, params=()) -> dict | None:
    resultado = filas(sql, params)
    return resultado[0] if resultado else None


def ejecutar(sql: str, params=()) -> int | None:
    """Ejecuta y devuelve el id si la sentencia termina en RETURNING id."""
    with _obtener_pool().connection() as con:
        cursor = con.execute(sql, _adaptar(params))
        if cursor.description:
            r = cursor.fetchone()
            return r["id"] if r else None
        return None


def inicializar() -> None:
    """Comprueba que las tablas existan (se crean con supabase/esquema.sql)."""
    if not fila("SELECT to_regclass('public.comprobantes') AS t")["t"]:
        raise RuntimeError("Las tablas no existen: ejecute supabase/esquema.sql en el SQL Editor de Supabase.")


# ---------- usuarios ----------

def _hash(clave: str, sal: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", clave.encode(), bytes.fromhex(sal), 200_000).hex()


def crear_usuario(usuario: str, nombre: str, clave: str, es_admin: bool = False) -> None:
    sal = secrets.token_hex(16)
    ejecutar("INSERT INTO usuarios (usuario, nombre, hash, sal, es_admin) VALUES (%s,%s,%s,%s,%s)",
             (usuario.strip().lower(), nombre.strip(), _hash(clave, sal), sal, es_admin))


def cambiar_clave(usuario_id: int, clave: str) -> None:
    sal = secrets.token_hex(16)
    ejecutar("UPDATE usuarios SET hash=%s, sal=%s WHERE id=%s", (_hash(clave, sal), sal, usuario_id))


def autenticar(usuario: str, clave: str) -> dict | None:
    u = fila("SELECT * FROM usuarios WHERE usuario=%s AND activo", (usuario.strip().lower(),))
    if u and secrets.compare_digest(u["hash"], _hash(clave, u["sal"])):
        return u
    return None


def hay_usuarios() -> bool:
    return fila("SELECT COUNT(*) AS n FROM usuarios")["n"] > 0


# ---------- comprobantes ----------

def insertar_comprobante(c: dict) -> int:
    datos = dict(c)
    for k in ("errores", "campos_dudosos"):
        datos[k] = Jsonb(list(datos.get(k) or []))
    columnas = ", ".join(datos)
    marcas = ", ".join("%s" for _ in datos)
    return ejecutar(f"INSERT INTO comprobantes ({columnas}) VALUES ({marcas}) RETURNING id", tuple(datos.values()))


def leer_comprobante(comprobante_id: int) -> dict | None:
    c = fila("SELECT * FROM comprobantes WHERE id=%s", (comprobante_id,))
    if c:
        c["errores"] = c["errores"] or []
        c["campos_dudosos"] = c["campos_dudosos"] or []
    return c


def actualizar_comprobante(comprobante_id: int, cambios: dict, usuario: str, accion: str) -> None:
    anterior = leer_comprobante(comprobante_id)
    datos = {k: v for k, v in cambios.items() if k in CAMPOS_EDITABLES}
    diferencias = {k: [anterior.get(k), v] for k, v in datos.items() if anterior.get(k) != v}
    for k in ("errores", "campos_dudosos"):
        if k in datos:
            datos[k] = Jsonb(list(datos[k] or []))
    datos["actualizado_por"], datos["actualizado_en"] = usuario, datetime.now()
    asignaciones = ", ".join(f"{k}=%s" for k in datos)
    with _obtener_pool().connection() as con:
        con.execute(f"UPDATE comprobantes SET {asignaciones} WHERE id=%s",
                    _adaptar((*datos.values(), comprobante_id)))
        con.execute("INSERT INTO historial (comprobante_id, usuario, accion, detalle) VALUES (%s,%s,%s,%s)",
                    (comprobante_id, usuario, accion,
                     Jsonb(json.loads(json.dumps(diferencias, ensure_ascii=False, default=str)))))


def es_duplicado(cliente_id: int, tipo_lote: str, ruc: str, timbrado: str, numero: str, excluir_id: int = 0) -> bool:
    if not (ruc and timbrado and numero):
        return False
    return fila(
        "SELECT 1 AS si FROM comprobantes WHERE cliente_id=%s AND tipo_lote=%s AND ruc=%s AND timbrado=%s "
        "AND numero=%s AND id<>%s AND estado <> 'DESCARTADA'",
        (cliente_id, tipo_lote, ruc, timbrado, numero, excluir_id),
    ) is not None


def siguiente_secuencia(cliente_id: int, tipo_lote: str, periodo: str) -> int:
    r = fila("SELECT MAX(secuencia) AS s FROM exportaciones WHERE cliente_id=%s AND tipo_lote=%s AND periodo=%s",
             (cliente_id, tipo_lote, periodo))
    return (r["s"] or 0) + 1


# ---------- imágenes que ya no hacen falta ----------

def paginas_para_liberar(pagina_ids: list[int]) -> list[dict]:
    """Páginas con imagen cuyos comprobantes están todos cerrados.

    Cerrado = aprobado o descartado a mano, o bien OK / electrónica ya exportado en un ZIP.
    Una página que continúa un comprobante (comprobante_ref) depende de ese comprobante.
    """
    if not pagina_ids:
        return []
    return filas(
        """
        SELECT p.id, p.ruta FROM paginas p
        WHERE p.id = ANY(%s) AND p.ruta IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM comprobantes c
              WHERE (c.pagina_id = p.id OR c.id = p.comprobante_ref)
                AND NOT (c.estado IN ('APROBADA', 'DESCARTADA')
                         OR (c.exportado AND c.estado IN ('OK', 'EXCLUIDA')))
          )
        """,
        (list(pagina_ids),),
    )


def marcar_imagenes_borradas(pagina_ids: list[int]) -> None:
    if pagina_ids:
        ejecutar("UPDATE paginas SET ruta = NULL WHERE id = ANY(%s)", (list(pagina_ids),))
