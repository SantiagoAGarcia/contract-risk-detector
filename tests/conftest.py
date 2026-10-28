"""
Configuración de pruebas.

Las pruebas de integración usan un PostgreSQL real (las políticas RLS no se
pueden simular). Requieren la variable TEST_ADMIN_DATABASE_URL apuntando a un
servidor con usuario administrador, por ejemplo el de docker compose:

    docker compose up -d db
    TEST_ADMIN_DATABASE_URL=postgresql://admin:admin_dev@localhost:5432/postgres pytest

Se crea (o reutiliza) la base de datos "contracts_test", separada de la de
desarrollo. Si la variable no está definida, las pruebas de integración se omiten.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

TEST_DB = "contracts_test"
APP_PASSWORD = "app_test"


def _con_db(url: str, dbname: str, user: str | None = None, password: str | None = None) -> str:
    """Devuelve la misma URL cambiando la base de datos (y opcionalmente el usuario)."""
    p = urlparse(url)
    netloc = p.netloc
    if user:
        host = netloc.split("@", 1)[-1]
        netloc = f"{user}:{password}@{host}"
    return urlunparse(p._replace(netloc=netloc, path=f"/{dbname}"))


@pytest.fixture(scope="session")
def database_urls():
    admin_url = os.getenv("TEST_ADMIN_DATABASE_URL")
    if not admin_url:
        pytest.skip("TEST_ADMIN_DATABASE_URL no definida: se omiten las pruebas de integración")

    # Crear la base de datos de prueba si no existe.
    with psycopg.connect(admin_url, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)).fetchone():
            conn.execute(f'CREATE DATABASE "{TEST_DB}"')

    admin_test = _con_db(admin_url, TEST_DB)
    app_test = _con_db(admin_url, TEST_DB, user="app_user", password=APP_PASSWORD)

    # Aplicar el esquema con el mismo comando que se usa en producción.
    env = {**os.environ, "ADMIN_DATABASE_URL": admin_test, "APP_DB_PASSWORD": APP_PASSWORD}
    subprocess.run([sys.executable, "manage.py", "init-db"], cwd=BASE_DIR, env=env, check=True,
                   capture_output=True)
    return {"admin": admin_test, "app": app_test}


@pytest.fixture(scope="session")
def tenants(database_urls):
    """Crea dos organizaciones: A con LLM habilitado y B sin LLM. Retorna sus API keys."""
    from db import generate_api_key, hash_api_key

    datos = {"org-a": True, "org-b": False}
    claves = {}
    with psycopg.connect(database_urls["admin"]) as conn:
        conn.execute("TRUNCATE tenants CASCADE")
        for tid, llm in datos.items():
            conn.execute("INSERT INTO tenants (id, nombre, llm_habilitado) VALUES (%s, %s, %s)",
                         (tid, f"Organización {tid}", llm))
            clave = generate_api_key()
            conn.execute("INSERT INTO api_keys (tenant_id, key_hash, prefijo) VALUES (%s, %s, %s)",
                         (tid, hash_api_key(clave), clave[:12]))
            claves[tid] = clave
    return claves


@pytest.fixture
def client(database_urls, tenants, monkeypatch):
    """Cliente de la API conectado a la base de prueba, con tablas de datos vacías."""
    with psycopg.connect(database_urls["admin"]) as conn:
        conn.execute("TRUNCATE contracts, clause_results, audit_log")

    monkeypatch.setenv("DATABASE_URL", database_urls["app"])
    import db
    db.get_pool.cache_clear()

    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as c:
        yield c
    db.get_pool().close()
    db.get_pool.cache_clear()


@pytest.fixture
def headers(tenants):
    """headers("org-a") -> encabezados autenticados de esa organización."""
    return lambda tid: {"X-Tenant-ID": tid, "X-API-Key": tenants[tid]}


@pytest.fixture(scope="session")
def contrato_pdf() -> bytes:
    return (BASE_DIR / "samples" / "contrato_ejemplo.pdf").read_bytes()
