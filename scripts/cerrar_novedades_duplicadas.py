"""Script de mantenimiento para cerrar novedades abiertas duplicadas/triplicadas.

Agrupa novedades activas sin fecha de fin por (legajo, tipo de novedad), conserva
abierta únicamente la más reciente y cierra las anteriores asignándoles
fecha_fin = fecha_inicio y dejando trazabilidad en observaciones, auditoría y
cola de sincronización (sync_outbox).

Uso:
    python scripts/cerrar_novedades_duplicadas.py --dry-run
    python scripts/cerrar_novedades_duplicadas.py --aplicar
    python scripts/cerrar_novedades_duplicadas.py --db ruta/a/base.sqlite --aplicar
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import obtener_ruta_base_datos
from src.data.database import DatabaseManager
from src.domain.validators import parsear_fecha


def _es_fecha_fin_abierta(valor: Any) -> bool:
    s = str(valor or "").strip()
    return s in ("", "-", "None", "null", "NULL")


def _parsear_fecha_segura(valor: Any) -> date:
    try:
        dt = parsear_fecha(str(valor or "").strip(), "Fecha inicio", obligatorio=False)
        if dt:
            return dt.date()
    except Exception:
        pass
    return date.min


def _norm_tipo(valor: Any) -> str:
    return " ".join(str(valor or "").strip().upper().split())


def analizar_y_cerrar_novedades_duplicadas(
    db: DatabaseManager,
    aplicar: bool = False,
    windows_user: str = "script_cierre_duplicadas",
) -> Dict[str, Any]:
    """Detecta y opcionalmente cierra novedades abiertas duplicadas por legajo y tipo."""
    with db.read_connection() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                """SELECT * FROM novedades
                   WHERE activo = 1
                   ORDER BY legajo ASC, id ASC"""
            ).fetchall()
        ]

    abiertas = [r for r in rows if _es_fecha_fin_abierta(r.get("fecha_fin"))]
    grupos: Dict[Tuple[int, str], List[Dict[str, Any]]] = {}
    for r in abiertas:
        key = (int(r["legajo"]), _norm_tipo(r.get("novedad")))
        grupos.setdefault(key, []).append(r)

    acciones: List[Dict[str, Any]] = []
    grupos_duplicados = 0

    for (legajo, tipo_norm), items in sorted(grupos.items(), key=lambda kv: kv[0]):
        if len(items) <= 1:
            continue
        grupos_duplicados += 1
        ordenados = sorted(
            items,
            key=lambda x: (_parsear_fecha_segura(x.get("fecha_inicio")), int(x["id"])),
            reverse=True,
        )
        mas_reciente = ordenados[0]
        reciente_id = int(mas_reciente["id"])
        nota_cierre = (
            f"La novedad se cerró porque tiene la novedad número #{reciente_id} más reciente. "
            f"No se cuenta con fecha fin."
        )

        for ant in ordenados[1:]:
            obs_actual = str(ant.get("observaciones") or "").strip()
            if nota_cierre in obs_actual:
                obs_nueva = obs_actual
            elif obs_actual:
                obs_nueva = f"{obs_actual} | {nota_cierre}"
            else:
                obs_nueva = nota_cierre

            acciones.append(
                {
                    "id": int(ant["id"]),
                    "legajo": legajo,
                    "apellidos_nombres": ant.get("apellidos_nombres") or "",
                    "novedad": ant.get("novedad") or tipo_norm,
                    "fecha_inicio": str(ant.get("fecha_inicio") or "").strip(),
                    "fecha_fin_anterior": ant.get("fecha_fin"),
                    "fecha_fin_nueva": str(ant.get("fecha_inicio") or "").strip(),
                    "observaciones_anterior": obs_actual,
                    "observaciones_nueva": obs_nueva,
                    "reemplazada_por_id": reciente_id,
                    "reemplazada_por_fecha_inicio": str(mas_reciente.get("fecha_inicio") or "").strip(),
                }
            )

    cerradas = 0
    if aplicar and acciones:
        ahora = db.now()
        with db.write_transaction() as conn:
            nov_cols = {r[1] for r in conn.execute("PRAGMA table_info(novedades)").fetchall()}
            tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            tiene_sync_status = "sync_status" in nov_cols
            tiene_updated_at = "updated_at" in nov_cols
            tiene_auditoria = "auditoria" in tablas
            tiene_outbox = "sync_outbox" in tablas

            set_clauses = ["fecha_fin = ?", "observaciones = ?"]
            if tiene_sync_status:
                set_clauses.append("sync_status = 'pending'")
            if tiene_updated_at:
                set_clauses.append("updated_at = ?")
            update_sql = f"UPDATE novedades SET {', '.join(set_clauses)} WHERE id = ?"

            for acc in acciones:
                nid = acc["id"]
                before = conn.execute("SELECT * FROM novedades WHERE id = ?", (nid,)).fetchone()
                if not before:
                    continue
                params: List[Any] = [acc["fecha_fin_nueva"], acc["observaciones_nueva"]]
                if tiene_updated_at:
                    params.append(ahora)
                params.append(nid)
                conn.execute(update_sql, params)
                after = dict(conn.execute("SELECT * FROM novedades WHERE id = ?", (nid,)).fetchone())
                if tiene_auditoria:
                    conn.execute(
                        """INSERT INTO auditoria
                           (creado_en, usuario_id, usuario_windows, accion, entidad, entidad_id, datos_anteriores, datos_nuevos)
                           VALUES (?, NULL, ?, 'modificar', 'novedad', ?, ?, ?)""",
                        (
                            ahora,
                            windows_user,
                            nid,
                            json.dumps(dict(before), ensure_ascii=False),
                            json.dumps(after, ensure_ascii=False),
                        ),
                    )
                if tiene_outbox:
                    conn.execute(
                        """INSERT INTO sync_outbox (tabla, registro_pk, operacion, payload_json, creado_en)
                           VALUES ('novedades', ?, 'UPDATE', ?, ?)
                           ON CONFLICT(tabla, registro_pk) DO UPDATE SET
                               operacion = excluded.operacion,
                               payload_json = excluded.payload_json,
                               creado_en = excluded.creado_en""",
                        (str(nid), json.dumps(after, ensure_ascii=False), ahora),
                    )
                cerradas += 1

    return {
        "total_abiertas": len(abiertas),
        "grupos_duplicados": grupos_duplicados,
        "a_cerrar": len(acciones),
        "cerradas": cerradas,
        "acciones": acciones,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cierra novedades activas sin fecha fin duplicadas/triplicadas dejando abierta la más reciente."
    )
    parser.add_argument("--db", type=str, default=None, help="Ruta opcional al archivo SQLite de RENO.")
    parser.add_argument("--dry-run", action="store_true", help="Solo muestra las novedades que se cerrarían sin modificar la base.")
    parser.add_argument("--aplicar", action="store_true", help="Aplica los cambios directamente sin pedir confirmación.")
    args = parser.parse_args(argv)

    if args.db:
        db_path = Path(args.db)
    else:
        db_path, _ = obtener_ruta_base_datos()

    if not db_path.exists():
        print(f"❌ No se encontró la base de datos en: {db_path}")
        return 1

    print(f"Base de datos: {db_path}")
    db = DatabaseManager(db_path, journal_mode="DELETE")

    res_prev = analizar_y_cerrar_novedades_duplicadas(db, aplicar=False)
    print(f"Novedades activas sin fecha fin: {res_prev['total_abiertas']}")
    print(f"Grupos con duplicados (legajo + tipo): {res_prev['grupos_duplicados']}")
    print(f"Novedades a cerrar: {res_prev['a_cerrar']}")

    if not res_prev["acciones"]:
        print("✅ No hay novedades abiertas duplicadas para cerrar.")
        return 0

    for acc in res_prev["acciones"]:
        print(
            f"  - Novedad #{acc['id']} | Leg. {acc['legajo']} ({acc['apellidos_nombres']}) | "
            f"{acc['novedad']} | Inicio: {acc['fecha_inicio']} -> Fin: {acc['fecha_fin_nueva']} "
            f"(se conserva abierta #{acc['reemplazada_por_id']} del {acc['reemplazada_por_fecha_inicio']})"
        )

    if args.dry_run:
        print("\n[DRY-RUN] No se realizaron modificaciones.")
        return 0

    aplicar = args.aplicar
    if not aplicar:
        resp = input("\n¿Desea aplicar el cierre de estas novedades? [s/N]: ").strip().lower()
        aplicar = resp in ("s", "si", "sí", "y", "yes")

    if not aplicar:
        print("Operación cancelada.")
        return 0

    res_final = analizar_y_cerrar_novedades_duplicadas(db, aplicar=True)
    print(f"✅ Se cerraron {res_final['cerradas']} novedad(es) duplicada(s) y se encolaron para sincronización.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
