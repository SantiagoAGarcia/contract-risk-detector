"""
manage.py
---------
Administración de la base de datos y de los tenants. Usa una conexión de
administrador (ADMIN_DATABASE_URL), distinta de la que usa la API.

Comandos:
    python manage.py init-db                         # crea rol app_user, tablas y políticas RLS
    python manage.py create-tenant empresa-1 "Empresa Uno" [--llm]
    python manage.py set-llm empresa-1 on|off        # opt-in del modo LLM
    python manage.py create-key empresa-1 [--descripcion "..."]
    python manage.py revoke-key <prefijo>
    python manage.py list-tenants
    python manage.py seed-demo                       # 2 organizaciones de demostración

Variables de entorno:
    ADMIN_DATABASE_URL  conexión de administrador (dueño del esquema)
    APP_DB_USER         rol de la API (por defecto app_user)
    APP_DB_PASSWORD     contraseña del rol de la API (requerida en init-db)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from db import generate_api_key, hash_api_key

BASE_DIR = Path(__file__).parent
SCHEMA = BASE_DIR / "db" / "schema.sql"

DEMO_TENANTS = [
    ("empresa-demo-1", "Constructora Demo Uno S.A.S.", True),
    ("empresa-demo-2", "Suministros Demo Dos Ltda.", False),
]

DEMO_API_KEYS = {
    "empresa-demo-1": "crd_demo_empresa_uno_llm_key",
    "empresa-demo-2": "crd_demo_empresa_dos_reglas_key",
}


def connect() -> psycopg.Connection:
    url = os.getenv("ADMIN_DATABASE_URL")
    if not url:
        sys.exit("Defina ADMIN_DATABASE_URL (conexión de administrador).")
    return psycopg.connect(url, autocommit=False)


# ===========================================================================
# Comandos
# ===========================================================================
def init_db(_args) -> None:
    app_user = os.getenv("APP_DB_USER", "app_user")
    password = os.getenv("APP_DB_PASSWORD")
    if not password:
        sys.exit("Defina APP_DB_PASSWORD (contraseña del rol de la API).")

    with connect() as conn:
        existe = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app_user,)).fetchone()
        accion = "ALTER" if existe else "CREATE"
        # NOSUPERUSER + NOBYPASSRLS: requisito para que RLS se aplique a la API.
        conn.execute(
            sql.SQL("{} ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
                sql.SQL(accion), sql.Identifier(app_user), sql.Literal(password)
            )
        )
        # El esquema usa el nombre "app_user" literal; si se cambia el rol, se reemplaza.
        script = SCHEMA.read_text(encoding="utf-8").replace("app_user", app_user)
        conn.execute(script)
    print(f"Esquema aplicado. Rol de la API: {app_user}")


def create_tenant(args) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO tenants (id, nombre, llm_habilitado) VALUES (%s, %s, %s)",
            (args.tenant_id, args.nombre, args.llm),
        )
    print(f"Tenant creado: {args.tenant_id} (LLM {'habilitado' if args.llm else 'deshabilitado'})")


def set_llm(args) -> None:
    with connect() as conn:
        n = conn.execute("UPDATE tenants SET llm_habilitado = %s WHERE id = %s",
                         (args.estado == "on", args.tenant_id)).rowcount
    if not n:
        sys.exit(f"No existe el tenant {args.tenant_id}")
    print(f"LLM {args.estado} para {args.tenant_id}")


def _crear_key(conn: psycopg.Connection, tenant_id: str, descripcion: str | None) -> str:
    clave = generate_api_key()
    conn.execute(
        "INSERT INTO api_keys (tenant_id, key_hash, prefijo, descripcion) VALUES (%s, %s, %s, %s)",
        (tenant_id, hash_api_key(clave), clave[:12], descripcion),
    )
    return clave


def create_key(args) -> None:
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM tenants WHERE id = %s", (args.tenant_id,)).fetchone():
            sys.exit(f"No existe el tenant {args.tenant_id}")
        clave = _crear_key(conn, args.tenant_id, args.descripcion)
    print(f"API key para {args.tenant_id} (guárdela, no se vuelve a mostrar):\n{clave}")


def revoke_key(args) -> None:
    with connect() as conn:
        n = conn.execute(
            "UPDATE api_keys SET revoked_at = now() WHERE prefijo = %s AND revoked_at IS NULL",
            (args.prefijo,),
        ).rowcount
    print(f"Claves revocadas: {n}")


def list_tenants(_args) -> None:
    with connect() as conn:
        filas = conn.execute(
            "SELECT t.id, t.nombre, t.llm_habilitado, "
            "count(k.id) FILTER (WHERE k.revoked_at IS NULL) AS claves_activas "
            "FROM tenants t LEFT JOIN api_keys k ON k.tenant_id = t.id "
            "GROUP BY t.id ORDER BY t.id"
        ).fetchall()
    for tid, nombre, llm, claves in filas:
        print(f"{tid:<20} LLM={'sí' if llm else 'no':<3} claves={claves}  {nombre}")


def seed_demo(_args) -> None:
    with connect() as conn:
        for tid, nombre, llm in DEMO_TENANTS:
            conn.execute(
                "INSERT INTO tenants (id, nombre, llm_habilitado) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET llm_habilitado = EXCLUDED.llm_habilitado",
                (tid, nombre, llm),
            )
            clave = DEMO_API_KEYS.get(tid, generate_api_key())
            khash = hash_api_key(clave)
            conn.execute(
                "INSERT INTO api_keys (tenant_id, key_hash, prefijo, descripcion) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (key_hash) DO NOTHING",
                (tid, khash, clave[:12], "demo"),
            )
            print(f"{tid} (LLM {'sí' if llm else 'no'}): {clave}")
    print("\nGuarde estas claves: no se vuelven a mostrar.")


# ===========================================================================
def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    parser = argparse.ArgumentParser(description="Administración de la base de datos")
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("init-db").set_defaults(func=init_db)

    p = sub.add_parser("create-tenant")
    p.add_argument("tenant_id")
    p.add_argument("nombre")
    p.add_argument("--llm", action="store_true", help="Habilita el modo LLM (opt-in)")
    p.set_defaults(func=create_tenant)

    p = sub.add_parser("set-llm")
    p.add_argument("tenant_id")
    p.add_argument("estado", choices=["on", "off"])
    p.set_defaults(func=set_llm)

    p = sub.add_parser("create-key")
    p.add_argument("tenant_id")
    p.add_argument("--descripcion")
    p.set_defaults(func=create_key)

    p = sub.add_parser("revoke-key")
    p.add_argument("prefijo", help="Primeros 12 caracteres de la clave")
    p.set_defaults(func=revoke_key)

    sub.add_parser("list-tenants").set_defaults(func=list_tenants)
    sub.add_parser("seed-demo").set_defaults(func=seed_demo)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
