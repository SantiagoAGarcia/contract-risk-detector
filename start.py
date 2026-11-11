"""
start.py
--------
Punto de entrada de producción para el contenedor en plataformas como Render, Railway o Docker.
1. Espera a que PostgreSQL esté disponible.
2. Si ADMIN_DATABASE_URL está configurada, ejecuta init-db y seed-demo.
3. Deriva DATABASE_URL para el rol app_user si no fue definida explícitamente.
4. Inicia el servidor Uvicorn en el puerto asignado ($PORT o 8000).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.parse


def wait_for_db(url: str, timeout: int = 45) -> bool:
    print("[*] Verificando conexión a PostgreSQL...")
    start_time = time.time()
    try:
        import psycopg
    except ImportError:
        print("[!] Advertencia: psycopg no disponible para pre-chequeo")
        return True

    while time.time() - start_time < timeout:
        try:
            with psycopg.connect(url, connect_timeout=4) as _conn:
                print("[+] Base de datos lista y respondiendo conexiones.")
                return True
        except Exception as err:
            print(f"[-] Esperando a la base de datos ({err})...")
            time.sleep(2)
    print("[!] Tiempo de espera agotado para la base de datos.")
    return False


def setup_database() -> None:
    admin_url = os.environ.get("ADMIN_DATABASE_URL")
    if not admin_url:
        print("[*] ADMIN_DATABASE_URL no definida. Omitiendo inicialización automática.")
        return

    # Esperar a que la base de datos esté lista
    wait_for_db(admin_url)

    app_pwd = os.environ.get("APP_DB_PASSWORD", "app_dev_secure")
    os.environ["APP_DB_PASSWORD"] = app_pwd

    # Derivar DATABASE_URL para app_user si no está establecida
    if not os.environ.get("DATABASE_URL"):
        u = urllib.parse.urlsplit(admin_url)
        quoted_pwd = urllib.parse.quote_plus(app_pwd)
        port_part = f":{u.port}" if u.port else ""
        netloc = f"app_user:{quoted_pwd}@{u.hostname}{port_part}"
        scheme = "postgresql" if u.scheme in ("postgres", "postgresql") else u.scheme
        derived_url = urllib.parse.urlunsplit((scheme, netloc, u.path, u.query, u.fragment))
        os.environ["DATABASE_URL"] = derived_url
        print("[+] DATABASE_URL configurada automáticamente para el rol app_user.")

    # Ejecutar init-db
    print("[*] Aplicando esquema de base de datos y políticas RLS (manage.py init-db)...")
    try:
        subprocess.run([sys.executable, "manage.py", "init-db"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"[!] Error ejecutando init-db: {e}")

    # Ejecutar seed-demo
    print("[*] Inicializando organizaciones y claves de demostración (manage.py seed-demo)...")
    try:
        subprocess.run([sys.executable, "manage.py", "seed-demo"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"[!] Error ejecutando seed-demo: {e}")


def main() -> None:
    print("=" * 60)
    print("Iniciando Contract Risk Detector en Producción")
    print("=" * 60)

    # Si se recibe DATABASE_URL pero no ADMIN_DATABASE_URL (común en despliegues simples de PaaS)
    if os.environ.get("DATABASE_URL") and not os.environ.get("ADMIN_DATABASE_URL"):
        db_url = os.environ["DATABASE_URL"]
        if "app_user" not in db_url:
            os.environ["ADMIN_DATABASE_URL"] = db_url
            del os.environ["DATABASE_URL"]

    setup_database()

    port = int(os.environ.get("PORT", "8000"))
    print(f"[*] Arrancando FastAPI con Uvicorn en 0.0.0.0:{port}...")

    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
