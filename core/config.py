"""Configuración general.

Los valores se leen, en este orden, de:
  1. los secretos de Streamlit Cloud (.streamlit/secrets.toml o el panel de la app),
  2. variables de entorno o un archivo .env (para pruebas locales).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _cargar_secretos_streamlit() -> None:
    """Copia los secretos de Streamlit a variables de entorno (sin pisar las que ya existan)."""
    try:
        import streamlit as st
        secretos = dict(st.secrets)
    except Exception:  # noqa: BLE001 - fuera de Streamlit o sin secrets.toml
        return
    for clave, valor in secretos.items():
        if isinstance(valor, (str, int, float)):
            os.environ.setdefault(clave, str(valor))


_cargar_secretos_streamlit()

# Base de datos (Supabase): cadena de conexión del "Session pooler".
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Archivos (Supabase Storage). Si no se configura, se guardan en una carpeta local (solo para pruebas).
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "facturas")
ARCHIVOS_LOCALES = Path(os.getenv("ARCHIVOS_LOCALES", str(BASE_DIR / "data" / "archivos")))

# Proveedor de IA para leer las facturas: "gemini" o "claude".
PROVEEDOR_IA = os.getenv("PROVEEDOR_IA", "gemini").strip().lower()

# Claude: modelo y nivel de esfuerzo (low / medium / high).
MODELO = os.getenv("CLAUDE_MODELO", "claude-opus-5-5")
ESFUERZO = os.getenv("CLAUDE_ESFUERZO", "medium")

# Gemini: el nombre del modelo cambia seguido; verificar el vigente en Google AI Studio.
GEMINI_MODELO = os.getenv("GEMINI_MODELO", "").strip()

VARIABLE_CLAVE = {"claude": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}

# Páginas que se envían a la IA al mismo tiempo. El plan gratuito de Gemini limita las
# solicitudes por minuto: con 2 se evita la mayoría de los rechazos.
HILOS = int(os.getenv("HILOS_PARALELOS", "2"))
# Reintentos cuando la IA responde "demasiadas solicitudes" (límite por minuto).
REINTENTOS_IA = int(os.getenv("REINTENTOS_IA", "5"))

# Resolución con la que se convierten las páginas de PDF a imagen.
DPI_PDF = int(os.getenv("DPI_PDF", "200"))
MAX_LADO_PX = 2000

# Marangatu acepta hasta 5.000 filas por archivo de importación.
MAX_FILAS_ARCHIVO = 5000


def problema_configuracion() -> str | None:
    """Mensaje con lo que falta para procesar facturas, o None si está todo bien."""
    if PROVEEDOR_IA not in VARIABLE_CLAVE:
        return f"PROVEEDOR_IA debe ser 'gemini' o 'claude' (dice '{PROVEEDOR_IA}')."
    if not os.getenv(VARIABLE_CLAVE[PROVEEDOR_IA]):
        return f"Falta configurar {VARIABLE_CLAVE[PROVEEDOR_IA]} en los secretos de la app."
    if PROVEEDOR_IA == "gemini" and not GEMINI_MODELO:
        return "Falta configurar GEMINI_MODELO en los secretos de la app."
    return None


def nombre_modelo() -> str:
    return MODELO if PROVEEDOR_IA == "claude" else GEMINI_MODELO
