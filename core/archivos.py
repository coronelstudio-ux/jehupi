"""Imágenes de las facturas: Supabase Storage (carpeta privada) o una carpeta local para pruebas.

Las imágenes son temporales: se borran cuando todos los comprobantes de la página quedan
cerrados (aprobados, descartados o exportados). Así no se llena el 1 GB del plan gratuito.
"""
from pathlib import Path

from . import config, db


class _Supabase:
    def __init__(self):
        from supabase import create_client
        self._bucket = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY).storage.from_(config.SUPABASE_BUCKET)

    def subir(self, ruta: str, datos: bytes) -> None:
        self._bucket.upload(ruta, datos, {"content-type": "image/jpeg", "upsert": "true"})

    def bajar(self, ruta: str) -> bytes:
        return self._bucket.download(ruta)

    def borrar(self, rutas: list[str]) -> None:
        for i in range(0, len(rutas), 100):
            self._bucket.remove(rutas[i:i + 100])


class _Local:
    def __init__(self, base: Path):
        self._base = base

    def subir(self, ruta: str, datos: bytes) -> None:
        destino = self._base / ruta
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(datos)

    def bajar(self, ruta: str) -> bytes:
        return (self._base / ruta).read_bytes()

    def borrar(self, rutas: list[str]) -> None:
        for ruta in rutas:
            (self._base / ruta).unlink(missing_ok=True)


_almacen = None


def almacen():
    global _almacen
    if _almacen is None:
        if config.SUPABASE_URL and config.SUPABASE_SERVICE_KEY:
            _almacen = _Supabase()
        else:
            _almacen = _Local(config.ARCHIVOS_LOCALES)
    return _almacen


def bajar(ruta: str | None) -> bytes | None:
    if not ruta:
        return None
    try:
        return almacen().bajar(ruta)
    except Exception:  # noqa: BLE001 - la imagen pudo haberse borrado
        return None


def liberar(pagina_ids: list[int]) -> int:
    """Borra las imágenes de las páginas que ya no se necesitan. Devuelve cuántas borró."""
    paginas = db.paginas_para_liberar(pagina_ids)
    if not paginas:
        return 0
    almacen().borrar([p["ruta"] for p in paginas])
    db.marcar_imagenes_borradas([p["id"] for p in paginas])
    return len(paginas)
