"""Pruebas con una base PostgreSQL real (la misma estructura que en Supabase).

Para las pruebas que usan la base, definir TEST_DATABASE_URL apuntando a un PostgreSQL de pruebas
VACÍO (se borra todo su contenido), por ejemplo uno local con Docker:
    docker run -d --name pg-pruebas -e POSTGRES_PASSWORD=pruebas -p 5433:5432 postgres:17
    TEST_DATABASE_URL=postgresql://postgres:pruebas@localhost:5433/postgres pytest
Sin esa variable, esas pruebas se saltean.
"""
import os
from pathlib import Path

import pytest

from core import archivos, config, db

ESQUEMA = Path(__file__).resolve().parent.parent / "supabase" / "esquema.sql"
URL = os.getenv("TEST_DATABASE_URL", "")
TABLAS = "historial, exportaciones, cuentas_contraparte, comprobantes, paginas, lotes, cuentas, clientes, usuarios"


@pytest.fixture(scope="session")
def _esquema():
    if not URL:
        pytest.skip("Defina TEST_DATABASE_URL para las pruebas con base de datos")
    import psycopg
    with psycopg.connect(URL, autocommit=True) as con:
        con.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
        # En Supabase el esquema "storage" ya existe; aquí se simula lo mínimo.
        con.execute("CREATE SCHEMA IF NOT EXISTS storage; "
                    "CREATE TABLE IF NOT EXISTS storage.buckets (id text primary key, name text, public boolean)")
        con.execute(ESQUEMA.read_text())
        con.execute(ESQUEMA.read_text())  # debe poder ejecutarse dos veces sin error


@pytest.fixture
def base(_esquema, tmp_path, monkeypatch):
    import psycopg
    monkeypatch.setattr(config, "DATABASE_URL", URL)
    monkeypatch.setattr(config, "SUPABASE_URL", "")
    monkeypatch.setattr(config, "ARCHIVOS_LOCALES", tmp_path / "archivos")
    monkeypatch.setattr(config, "HILOS", 1)
    with psycopg.connect(URL, autocommit=True) as con:
        con.execute(f"TRUNCATE {TABLAS} RESTART IDENTITY CASCADE")
        con.execute(ESQUEMA.read_text())  # vuelve a cargar las cuentas iniciales
    db.cerrar()
    archivos._almacen = None
    yield tmp_path / "archivos"
    db.cerrar()
    archivos._almacen = None
