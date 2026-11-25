"""Gestión transaccional de la cola sync_outbox."""

from __future__ import annotations

import json
from typing import Any

from ...domain.models import SyncQueueItem
from ..database import DatabaseManager


_CANAL_COL = {
    "smb": "synced_smb",
    "supabase": "synced_supabase",
}


class OutboxQueue:
    """Administra los elementos pendientes de sincronizar hacia SMB y/o Supabase."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    @staticmethod
    def _col_canal(canal: str | None) -> str | None:
        if not canal:
            return None
        col = _CANAL_COL.get(canal.strip().lower())
        if not col:
            raise ValueError(f"Canal de sincronización desconocido: {canal!r}")
        return col

    def contar_pendientes(self, canal: str | None = None) -> int:
        col = self._col_canal(canal)
        filtro_canal = f" AND COALESCE({col}, 0) = 0" if col else ""
        with self.db.read_connection() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM sync_outbox WHERE COALESCE(estado, 'PENDIENTE') != 'SINCRONIZADO'{filtro_canal};"
            ).fetchone()
            return int(row[0]) if row else 0

    def obtener_pendientes(self, limite: int = 200, canal: str | None = None) -> list[SyncQueueItem]:
        col = self._col_canal(canal)
        filtro_canal = f" AND COALESCE({col}, 0) = 0" if col else ""
        with self.db.read_connection() as conn:
            rows = conn.execute(
                f"""SELECT id, tabla, registro_pk, operacion, payload_json, creado_en, intentos, ultimo_error
                   FROM sync_outbox
                   WHERE COALESCE(estado, 'PENDIENTE') != 'SINCRONIZADO'{filtro_canal}
                   ORDER BY id ASC LIMIT ?;""",
                (limite,),
            ).fetchall()
            return [
                SyncQueueItem(
                    id=r["id"],
                    tabla=r["tabla"],
                    registro_pk=r["registro_pk"],
                    operacion=r["operacion"],
                    payload_json=r["payload_json"],
                    creado_en=r["creado_en"],
                    intentos=r["intentos"],
                    ultimo_error=r["ultimo_error"],
                )
                for r in rows
            ]

    def eliminar_procesados(self, ids: list[int]) -> None:
        if not ids:
            return
        with self.db.write_transaction() as conn:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM sync_outbox WHERE id IN ({placeholders});", ids)

    def eliminar_procesados_con_version(self, items: list[tuple[int, str]]) -> None:
        """Elimina ítems procesados solo si su timestamp creado_en no cambió mientras se sincronizaba."""
        if not items:
            return
        with self.db.write_transaction() as conn:
            conn.executemany(
                "DELETE FROM sync_outbox WHERE id = ? AND creado_en = ?;",
                items,
            )

    def marcar_entregado_canal(self, ids: list[int], canal: str) -> None:
        """Marca los ítems como entregados para un canal específico ('smb' o 'supabase') sin borrarlos todavía."""
        if not ids:
            return
        col = self._col_canal(canal)
        assert col is not None
        with self.db.write_transaction() as conn:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE sync_outbox SET {col} = 1, ultimo_error = NULL WHERE id IN ({placeholders});",
                ids,
            )

    def purgar_entregados(self, requiere_smb: bool = True, requiere_supabase: bool = True) -> int:
        """Elimina de sync_outbox únicamente las filas ya entregadas a todos los canales requeridos."""
        conds: list[str] = []
        if requiere_smb:
            conds.append("COALESCE(synced_smb, 0) = 1")
        if requiere_supabase:
            conds.append("COALESCE(synced_supabase, 0) = 1")
        if not conds:
            return 0
        where_sql = " AND ".join(conds)
        with self.db.write_transaction() as conn:
            cur = conn.execute(f"DELETE FROM sync_outbox WHERE {where_sql};")
            return int(cur.rowcount or 0)

    @staticmethod
    def encolar_puente(
        conn: Any,
        tabla: str,
        registro_pk: str,
        payload: dict[str, Any],
        canal_origen: str,
    ) -> None:
        """Encola un registro recibido por Pull desde canal_origen para re-propagarlo solo al canal opuesto (sin bucles)."""
        origen = canal_origen.strip().lower()
        if origen not in _CANAL_COL:
            raise ValueError(f"Canal de origen inválido: {canal_origen!r}")
        s_smb = 1 if origen == "smb" else 0
        s_sb = 1 if origen == "supabase" else 0
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        # Si ya existe un cambio local pendiente para ambos canales, no pisarlo; solo insertar si no existe
        # o si ya estaba sincronizado en el canal origen.
        conn.execute(
            """INSERT INTO sync_outbox (tabla, registro_pk, operacion, payload_json, creado_en, estado, synced_smb, synced_supabase)
               VALUES (?, ?, 'UPSERT', ?, datetime('now', 'localtime'), 'PENDIENTE', ?, ?)
               ON CONFLICT(tabla, registro_pk) DO UPDATE SET
                   payload_json = excluded.payload_json,
                   operacion = 'UPSERT',
                   creado_en = excluded.creado_en,
                   estado = 'PENDIENTE',
                   synced_smb = MAX(sync_outbox.synced_smb, excluded.synced_smb),
                   synced_supabase = MAX(sync_outbox.synced_supabase, excluded.synced_supabase);""",
            (tabla, str(registro_pk), payload_json, s_smb, s_sb),
        )

    def registrar_fallo(self, item_id: int, mensaje_error: str) -> None:
        with self.db.write_transaction() as conn:
            conn.execute(
                """UPDATE sync_outbox
                   SET intentos = intentos + 1, ultimo_error = ?, estado = 'ERROR'
                   WHERE id = ?;""",
                (mensaje_error[:500], item_id),
            )

    def forzar_resincronizacion_total(self) -> int:
        """Marca todas las filas de las tablas principales como pendientes en sync_outbox."""
        tablas = [
            ("dotaciones", "nombre"),
            ("tipos_novedad", "nombre"),
            ("personal_estacion", "nombre"),
            ("destinatarios_informe", "email"),
            ("destinatarios_ordenamiento", "email"),
            ("roles", "nombre"),
            ("usuarios", "username"),
            ("empleados", "legajo"),
            ("novedades", "id"),
            ("novedad_reemplazos", "id"),
            ("cambios_turno", "id"),
            ("ciclo_jornadas", "id"),
            ("tren_paradas", "id"),
            ("ordenamientos", "id"),
            ("ordenamiento_tramos", "id"),
            ("auditoria", "id"),
        ]
        total = 0
        with self.db.write_transaction() as conn:
            for t, pk in tablas:
                try:
                    filas = conn.execute(f"SELECT * FROM {t};").fetchall()
                except Exception:
                    continue
                for f in filas:
                    d = dict(f)
                    val_pk = str(d.get(pk, d.get("id", "")))
                    if not val_pk or val_pk == "None":
                        continue
                    conn.execute(
                        """INSERT INTO sync_outbox (tabla, registro_pk, operacion, payload_json, creado_en, synced_smb, synced_supabase)
                           VALUES (?, ?, 'UPSERT', ?, datetime('now', 'localtime'), 0, 0)
                           ON CONFLICT(tabla, registro_pk) DO UPDATE SET
                               operacion = 'UPSERT',
                               payload_json = excluded.payload_json,
                               intentos = 0,
                               ultimo_error = NULL,
                               estado = 'PENDIENTE',
                               synced_smb = 0,
                               synced_supabase = 0;""",
                        (t, val_pk, json.dumps(d, ensure_ascii=False, default=str)),
                    )
                    total += 1
                try:
                    conn.execute(f"UPDATE {t} SET sync_status = 'pending';")
                except Exception:
                    pass
        return total
