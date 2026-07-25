"""
Ejecuta las migraciones pendientes contra Neon.
Uso: python migrations/run_migration.py
"""
import asyncio
import os
import sys
from pathlib import Path

# Encoding fix para Windows
sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)

# Cargar .env del módulo
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import asyncpg


async def run():
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("ERROR: DATABASE_URL no encontrado en .env")
        sys.exit(1)

    # asyncpg usa URL sin el prefijo +asyncpg
    pg_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(pg_url)

    migrations_dir = Path(__file__).parent
    migration_files = sorted(migrations_dir.glob("0*.sql"))

    if not migration_files:
        print("No hay archivos de migración (.sql) en el directorio.")
        return

    for mf in migration_files:
        print(f"\n{'='*60}")
        print(f"Migracion: {mf.name}")
        print(f"{'='*60}")
        sql = mf.read_text(encoding="utf-8")

        # Filtrar comentarios puros y vacíos; separar por ;
        statements = []
        for raw in sql.split(";"):
            stripped = raw.strip()
            if not stripped:
                continue
            # Quitar líneas que son solo comentario
            lines = [l for l in stripped.splitlines() if not l.strip().startswith("--")]
            clean = "\n".join(lines).strip()
            if clean:
                statements.append(clean)

        ok = 0
        errors = 0
        for stmt in statements:
            try:
                await conn.execute(stmt)
                print(f"  OK  | {stmt[:80].replace(chr(10), ' ')}")
                ok += 1
            except Exception as e:
                print(f"  ERR | {stmt[:80].replace(chr(10), ' ')}")
                print(f"       -> {e}")
                errors += 1

        print(f"\n  Resultado: {ok} ok, {errors} errores")

    await conn.close()
    print("\nMigraciones completadas.")


if __name__ == "__main__":
    asyncio.run(run())
