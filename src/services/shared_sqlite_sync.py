"""Motor de sincronización Local-First mediante SQLite en carpeta compartida (SMB/CIFS / UNC).

Reglas de oro de seguridad en red implementadas:
- Prohibición estricta de WAL sobre red: fuerza ``PRAGMA journal_mode = DELETE`` en cada conexión.
- ``PRAGMA synchronous = FULL`` para garantizar volcado físico en el servidor de archivos.
- ``PRAGMA busy_timeout = 30000`` (30 segundos) para tolerar bloqueos transitorios.
- Conexiones efímeras y atómicas (conectar -> BEGIN IMMEDIATE -> lote -> COMMIT -> cerrar).
- Reintentos con backoff exponencial + jitter (0.4s, 1.2s, 2.5s) ante ``locked`` / ``busy``.
- Auto-bootstrap de esquema e índices si la base compartida no existe o está vacía.
"""

from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from ..data.database import DatabaseManager
from ..data.sync.outbox_queue import OutboxQueue

logger = logging.getLogger("reno.services.shared_sqlite_sync")

T = TypeVar("T")

RETRY_DELAYS_SEC = (0.4, 1.2, 2.5)

# Metadatos ordenados por dependencias FK:
# (tabla, (pk_cols...), timestamp_col, tiene_autoincrement_id)
TABLAS_SINCRONIZABLES: tuple[tuple[str, tuple[str, ...], str, bool], ...] = (
    ("dotaciones", ("nombre",), "updated_at", False),
    ("tipos_novedad", ("nombre",), "updated_at", False),
    ("personal_estacion", ("nombre",), "updated_at", False),
    ("destinatarios_informe", ("email",), "updated_at", False),
    ("destinatarios_ordenamiento", ("email",), "updated_at", False),
    ("destinatarios_ciclo", ("email",), "updated_at", False),
    ("roles", ("nombre",), "updated_at", False),
    ("usuarios", ("username",), "updated_at", False),
    ("empleados", ("legajo",), "updated_at", False),
    ("novedades", ("id",), "updated_at", True),
    ("novedad_reemplazos", ("id",), "creado_en", True),
    ("cambios_turno", ("id",), "updated_at", True),
    ("ciclo_jornadas", ("legajo", "fecha"), "updated_at", False),
    ("turnos_itinerario", ("id",), "updated_at", True),
    ("turnos_vueltas", ("id",), "updated_at", True),
    ("tren_paradas", ("id",), "updated_at", True),
    ("ordenamientos", ("id",), "updated_at", True),
    ("ordenamiento_tramos", ("id",), "updated_at", True),
    ("via_sectores", ("codigo",), "updated_at", False),
    ("via_conocimientos", ("legajo", "sector_id", "sentido"), "updated_at", False),
    ("remociones", ("id",), "updated_at", True),
    ("empleado_historial_turnos", ("id",), "updated_at", True),
    ("auditoria", ("id",), "creado_en", True),
)

TABLAS_META_MAP: dict[str, tuple[tuple[str, ...], str, bool]] = {
    tabla: (pks, ts_col, auto_id) for tabla, pks, ts_col, auto_id in TABLAS_SINCRONIZABLES
}


def _normalizar_ts_comparable(valor: Any) -> str:
    """Normaliza timestamps ISO/locales para comparación lexicográfica consistente."""
    if valor is None:
        return ""
    s = str(valor).strip()
    if not s or s in ("None", "null", "-"):
        return ""
    # Normalizar 'T' -> espacio para comparar 'YYYY-MM-DD HH:MM:SS'
    return s.replace("T", " ")[:19]


def _es_error_bloqueo(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _extraer_valores_pk(tabla: str, registro_pk: str, payload: dict[str, Any] | None) -> tuple[Any, ...]:
    """Resuelve los valores de clave primaria simples o compuestas."""
    meta = TABLAS_META_MAP.get(tabla)
    if not meta:
        return (registro_pk,)
    pk_cols = meta[0]
    if len(pk_cols) == 1:
        col = pk_cols[0]
        if payload and col in payload and payload[col] is not None:
            return (payload[col],)
        return (registro_pk,)

    # Clave compuesta (ej. ciclo_jornadas: legajo,fecha)
    if payload:
        vals = [payload.get(c) for c in pk_cols]
        if all(v is not None for v in vals):
            return tuple(vals)

    raw = str(registro_pk or "")
    for sep in ("|", ",", "::", "/"):
        if sep in raw:
            partes = [p.strip() for p in raw.split(sep, len(pk_cols) - 1)]
            if len(partes) == len(pk_cols):
                return tuple(partes)
    return tuple([raw] + [""] * (len(pk_cols) - 1))


class SharedSQLiteSyncEngine:
    """Motor bidireccional Local-First para SQLite compartida en red SMB/CIFS."""

    CLAVE_LAST_PULL = "sync_last_pull_timestamp"

    def __init__(
        self,
        db: DatabaseManager,
        outbox: OutboxQueue | None = None,
        shared_path: str | Path | None = None,
    ) -> None:
        self.db = db
        self.outbox = outbox if isinstance(outbox, OutboxQueue) else OutboxQueue(db)
        self.shared_path = str(shared_path).strip() if shared_path else ""
        self._lock = threading.Lock()

    def _resolver_ruta(self, path: str | Path | None = None) -> Path:
        raw = str(path if path is not None else self.shared_path).strip()
        if not raw:
            raise ValueError("No se especificó la ruta de la base SQLite compartida.")
        return Path(raw)

    @staticmethod
    def _with_retry(fn: Callable[[], T]) -> T:
        """Ejecuta una operación SQLite con reintentos exponenciales + jitter ante locked/busy."""
        intento = 0
        while True:
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                if not _es_error_bloqueo(exc) or intento >= len(RETRY_DELAYS_SEC):
                    raise
                base_delay = RETRY_DELAYS_SEC[intento]
                jitter = random.uniform(-0.2 * base_delay, 0.2 * base_delay)
                espera = max(0.1, base_delay + jitter)
                intento += 1
                logger.warning(
                    "Base compartida ocupada (%s). Reintento %d/%d en %.2fs...",
                    exc,
                    intento,
                    len(RETRY_DELAYS_SEC),
                    espera,
                )
                import time

                time.sleep(espera)

    @staticmethod
    def get_shared_connection(path: str | Path) -> sqlite3.Connection:
        """Abre una conexión efímera y blindada contra corrupción en red SMB/CIFS.

        PROHIBICIÓN ESTRICTA DE WAL: fuerza ``journal_mode = DELETE``,
        ``synchronous = FULL`` y ``busy_timeout = 30000``.
        """
        ruta = Path(str(path).strip())
        if not str(ruta):
            raise ValueError("Ruta compartida vacía.")

        conn = sqlite3.connect(
            str(ruta),
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        try:
            row_jm = conn.execute("PRAGMA journal_mode = DELETE;").fetchone()
            jm_actual = str(row_jm[0]).strip().lower() if row_jm else ""
            if jm_actual == "wal":
                # Reintentar forzar DELETE explícitamente si venía de otro estado
                row_jm = conn.execute("PRAGMA journal_mode = DELETE;").fetchone()
                jm_actual = str(row_jm[0]).strip().lower() if row_jm else ""
            if jm_actual == "wal":
                raise RuntimeError("No se permite journal_mode=WAL en la base compartida de red.")

            conn.execute("PRAGMA synchronous = FULL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
            conn.execute("PRAGMA foreign_keys = ON;")
            return conn
        except Exception:
            conn.close()
            raise

    @classmethod
    def test_connection(cls, path: str | Path) -> tuple[bool, str]:
        """Diagnostica conectividad, permisos de escritura en red y salud SQLite."""
        raw = str(path or "").strip()
        if not raw:
            return False, "Debe especificar una ruta válida para la base de datos compartida."

        ruta = Path(raw)
        carpeta = ruta.parent
        if not str(carpeta) or not carpeta.exists():
            return False, f"La carpeta destino no es accesible o no existe:\n{carpeta}"

        if not carpeta.is_dir():
            return False, f"La ruta padre no es un directorio válido:\n{carpeta}"

        # 1. Prueba real de permisos de lectura/escritura/borrado en la carpeta compartida
        probe_file = carpeta / f".reno_probe_{uuid.uuid4().hex[:10]}.tmp"
        try:
            probe_file.write_text("reno_smb_probe", encoding="utf-8")
            contenido = probe_file.read_text(encoding="utf-8")
            if contenido != "reno_smb_probe":
                return False, "Error verificando contenido de prueba en carpeta compartida."
        except Exception as ex:
            return False, f"Sin permisos de escritura en la carpeta compartida ({carpeta}): {ex}"
        finally:
            try:
                if probe_file.exists():
                    probe_file.unlink()
            except Exception as ex_del:
                return False, f"No se pudo eliminar archivo temporal de prueba: {ex_del}"

        # 2. Si el archivo ya existe y no está vacío, validar journal_mode y quick_check
        if ruta.exists() and ruta.stat().st_size > 0:
            def _check_db() -> tuple[str, str]:
                conn = cls.get_shared_connection(ruta)
                try:
                    jm_row = conn.execute("PRAGMA journal_mode;").fetchone()
                    jm = str(jm_row[0]).strip().lower() if jm_row else ""
                    if jm == "wal":
                        return jm, "ERROR: journal_mode es WAL (prohibido en red)"
                    qc_row = conn.execute("PRAGMA quick_check;").fetchone()
                    qc = str(qc_row[0]).strip().lower() if qc_row else ""
                    return jm, qc
                finally:
                    conn.close()

            try:
                jm, qc = cls._with_retry(_check_db)
            except Exception as ex:
                return False, f"Error abriendo base SQLite compartida: {ex}"

            if jm == "wal":
                return False, "La base compartida usa modo WAL, lo cual está prohibido en carpetas de red."
            if qc != "ok":
                return False, f"PRAGMA quick_check falló en la base compartida: {qc}"

            return (
                True,
                f"Conexión exitosa. Permisos OK, journal_mode={jm.upper()}, synchronous=FULL, quick_check=OK.",
            )

        return (
            True,
            f"Carpeta accesible con permisos de escritura. El archivo '{ruta.name}' se inicializará automáticamente al sincronizar.",
        )

    def bootstrap_shared_database(
        self,
        shared_path: str | Path | None = None,
        local_db_path: str | Path | None = None,
    ) -> bool:
        """Inicializa la base compartida clonando el esquema e información inicial desde local.

        Retorna True si se realizó el bootstrap, False si ya estaba inicializada.
        """
        ruta_compartida = self._resolver_ruta(shared_path)
        if ruta_compartida.exists() and ruta_compartida.stat().st_size > 0:
            # Verificar que tenga al menos las tablas de negocio
            def _tiene_esquema() -> bool:
                conn = self.get_shared_connection(ruta_compartida)
                try:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='novedades';"
                    ).fetchone()
                    return bool(row and int(row[0]) > 0)
                finally:
                    conn.close()

            try:
                if self._with_retry(_tiene_esquema):
                    return False
            except Exception:
                pass

        ruta_compartida.parent.mkdir(parents=True, exist_ok=True)
        local_src = Path(local_db_path) if local_db_path else self.db.db_path

        # Leer DDL de tablas e índices desde la base local (excluyendo sync_outbox y sqlite_*)
        tablas_ddl: list[str] = []
        indices_ddl: list[str] = []
        datos_iniciales: dict[str, tuple[list[str], list[tuple[Any, ...]]]] = {}

        if local_src.exists() and local_src.stat().st_size > 0:
            with self.db.read_connection() as lconn:
                for row in lconn.execute(
                    """SELECT type, name, sql FROM sqlite_master
                       WHERE sql IS NOT NULL
                         AND name NOT LIKE 'sqlite_%'
                         AND tbl_name != 'sync_outbox'
                       ORDER BY CASE WHEN type = 'table' THEN 0 ELSE 1 END, rowid;"""
                ).fetchall():
                    if row["type"] == "table":
                        tablas_ddl.append(row["sql"])
                    elif row["type"] == "index":
                        indices_ddl.append(row["sql"])

                for tabla, _, _, _ in TABLAS_SINCRONIZABLES:
                    try:
                        cols_info = lconn.execute(f"PRAGMA table_info({tabla});").fetchall()
                        cols = [c[1] for c in cols_info]
                        if not cols:
                            continue
                        filas = lconn.execute(f"SELECT {', '.join(cols)} FROM {tabla};").fetchall()
                        if filas:
                            datos_iniciales[tabla] = (cols, [tuple(f) for f in filas])
                    except Exception:
                        continue

        def _crear_compartida() -> None:
            conn = self.get_shared_connection(ruta_compartida)
            try:
                conn.execute("PRAGMA foreign_keys = OFF;")
                conn.execute("BEGIN IMMEDIATE;")
                for ddl in tablas_ddl:
                    conn.execute(ddl)
                for idx_ddl in indices_ddl:
                    conn.execute(idx_ddl)

                for tabla, (cols, filas) in datos_iniciales.items():
                    placeholders = ", ".join("?" for _ in cols)
                    cols_sql = ", ".join(cols)
                    conn.executemany(
                        f"INSERT OR IGNORE INTO {tabla} ({cols_sql}) VALUES ({placeholders});",
                        filas,
                    )

                conn.execute("COMMIT;")
                conn.execute("PRAGMA foreign_keys = ON;")
            except Exception:
                if conn.in_transaction:
                    try:
                        conn.execute("ROLLBACK;")
                    except Exception:
                        pass
                raise
            finally:
                conn.close()

        self._with_retry(_crear_compartida)
        logger.info("Base compartida inicializada en %s (journal_mode=DELETE, synchronous=FULL).", ruta_compartida)
        return True

    def push_changes(
        self,
        shared_path: str | Path | None = None,
        modo_anillo: bool = False,
    ) -> int:
        """Envía los cambios pendientes de ``sync_outbox`` hacia la base SQLite compartida."""
        ruta_compartida = self._resolver_ruta(shared_path)
        self.bootstrap_shared_database(ruta_compartida)

        enviados = 0
        for _ in range(100):
            pendientes = self.outbox.obtener_pendientes(
                limite=200,
                canal="smb" if modo_anillo else None,
            )
            if not pendientes:
                break

            ids_procesados: list[int] = []
            registros_sincronizados: list[tuple[str, str]] = []

            # Pre-cargar filas locales si algún item no trae payload completo
            payloads_preparados: list[tuple[int, str, str, str, dict[str, Any] | None]] = []
            with self.db.read_connection() as lconn:
                for item in pendientes:
                    meta = TABLAS_META_MAP.get(item.tabla)
                    if not meta:
                        ids_procesados.append(item.id)
                        continue
                    pl: dict[str, Any] | None = None
                    if item.payload_json:
                        try:
                            pl = json.loads(item.payload_json)
                        except Exception:
                            pl = None
                    op = (item.operacion or "UPSERT").upper()
                    if op != "DELETE" and not pl:
                        pk_cols = meta[0]
                        pk_vals = _extraer_valores_pk(item.tabla, item.registro_pk, None)
                        where_sql = " AND ".join(f"{c} = ?" for c in pk_cols)
                        row_loc = lconn.execute(
                            f"SELECT * FROM {item.tabla} WHERE {where_sql};",
                            pk_vals,
                        ).fetchone()
                        if row_loc:
                            pl = dict(row_loc)
                    payloads_preparados.append((item.id, item.tabla, item.registro_pk, op, pl))

            def _aplicar_lote() -> list[tuple[int, str, str]]:
                aplicados_lote: list[tuple[int, str, str]] = []
                conn = self.get_shared_connection(ruta_compartida)
                try:
                    cols_cache: dict[str, list[str]] = {}
                    conn.execute("BEGIN IMMEDIATE;")
                    for outbox_id, tabla, reg_pk, op, pl in payloads_preparados:
                        meta = TABLAS_META_MAP.get(tabla)
                        if not meta:
                            aplicados_lote.append((outbox_id, tabla, reg_pk))
                            continue

                        pk_cols, ts_col, _ = meta
                        if tabla not in cols_cache:
                            cols_cache[tabla] = [
                                r[1] for r in conn.execute(f"PRAGMA table_info({tabla});").fetchall()
                            ]
                        cols_remotas = cols_cache[tabla]
                        if not cols_remotas:
                            aplicados_lote.append((outbox_id, tabla, reg_pk))
                            continue

                        pk_vals = _extraer_valores_pk(tabla, reg_pk, pl)
                        where_pk = " AND ".join(f"{c} = ?" for c in pk_cols)

                        if op == "DELETE":
                            conn.execute(f"DELETE FROM {tabla} WHERE {where_pk};", pk_vals)
                            aplicados_lote.append((outbox_id, tabla, reg_pk))
                            continue

                        if not pl:
                            aplicados_lote.append((outbox_id, tabla, reg_pk))
                            continue

                        # Resolución de conflictos por timestamp más reciente (LWW)
                        ts_entrante = _normalizar_ts_comparable(
                            pl.get(ts_col) or pl.get("updated_at") or pl.get("creado_en") or pl.get("registrado_en")
                        )
                        if ts_col in cols_remotas:
                            row_rem = conn.execute(
                                f"SELECT {ts_col} FROM {tabla} WHERE {where_pk};",
                                pk_vals,
                            ).fetchone()
                            if row_rem is not None:
                                ts_rem = _normalizar_ts_comparable(row_rem[0])
                                if ts_rem and ts_entrante and ts_rem > ts_entrante:
                                    # La versión compartida es más reciente; no sobrescribir
                                    aplicados_lote.append((outbox_id, tabla, reg_pk))
                                    continue

                        # Filtrar columnas existentes en la tabla compartida
                        cols_insert = [c for c in cols_remotas if c in pl]
                        if not cols_insert:
                            aplicados_lote.append((outbox_id, tabla, reg_pk))
                            continue

                        vals_insert = [
                            ("synced" if c == "sync_status" else pl[c]) for c in cols_insert
                        ]
                        placeholders = ", ".join("?" for _ in cols_insert)
                        pk_conflict = ", ".join(pk_cols)
                        update_cols = [c for c in cols_insert if c not in pk_cols]

                        if update_cols:
                            set_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
                            sql = (
                                f"INSERT INTO {tabla} ({', '.join(cols_insert)}) "
                                f"VALUES ({placeholders}) "
                                f"ON CONFLICT({pk_conflict}) DO UPDATE SET {set_clause};"
                            )
                        else:
                            sql = (
                                f"INSERT OR IGNORE INTO {tabla} ({', '.join(cols_insert)}) "
                                f"VALUES ({placeholders});"
                            )
                        conn.execute(sql, vals_insert)
                        aplicados_lote.append((outbox_id, tabla, reg_pk))

                    conn.execute("COMMIT;")
                    return aplicados_lote
                except Exception:
                    if conn.in_transaction:
                        try:
                            conn.execute("ROLLBACK;")
                        except Exception:
                            pass
                    raise
                finally:
                    conn.close()

            aplicados = self._with_retry(_aplicar_lote)
            for outbox_id, tabla, reg_pk in aplicados:
                ids_procesados.append(outbox_id)
                registros_sincronizados.append((tabla, reg_pk))

            if not ids_procesados:
                break

            if modo_anillo:
                self.outbox.marcar_entregado_canal(ids_procesados, "smb")
            else:
                self.outbox.eliminar_procesados(ids_procesados)
            enviados += len(ids_procesados)

            if registros_sincronizados:
                with self.db.write_transaction() as lconn:
                    for tab, r_pk in registros_sincronizados:
                        meta = TABLAS_META_MAP.get(tab)
                        if not meta:
                            continue
                        pk_cols = meta[0]
                        pk_vals = _extraer_valores_pk(tab, r_pk, None)
                        where_pk = " AND ".join(f"{c} = ?" for c in pk_cols)
                        try:
                            lconn.execute(f"UPDATE {tab} SET sync_status = 'synced' WHERE {where_pk};", pk_vals)
                        except Exception:
                            pass

        return enviados

    def pull_changes(
        self,
        shared_path: str | Path | None = None,
        propagar_a_canal_opuesto: bool = False,
    ) -> int:
        """Descarga cambios posteriores a ``sync_last_pull_timestamp`` sin re-encolar hacia SMB."""
        ruta_compartida = self._resolver_ruta(shared_path)
        self.bootstrap_shared_database(ruta_compartida)

        with self.db.read_connection() as lconn:
            row_ts = lconn.execute(
                "SELECT valor FROM configuracion WHERE clave = ?;",
                (self.CLAVE_LAST_PULL,),
            ).fetchone()
            last_pull = str(row_ts[0]).strip() if row_ts and row_ts[0] else ""

        def _leer_cambios_remotos() -> tuple[dict[str, list[dict[str, Any]]], str]:
            cambios: dict[str, list[dict[str, Any]]] = {}
            max_ts = last_pull
            conn = self.get_shared_connection(ruta_compartida)
            try:
                for tabla, _, ts_col, _ in TABLAS_SINCRONIZABLES:
                    cols_info = conn.execute(f"PRAGMA table_info({tabla});").fetchall()
                    cols = [c[1] for c in cols_info]
                    if not cols:
                        continue
                    if last_pull and ts_col in cols:
                        filas = conn.execute(
                            f"SELECT * FROM {tabla} WHERE COALESCE({ts_col}, '') > ? ORDER BY {ts_col} ASC;",
                            (last_pull,),
                        ).fetchall()
                    else:
                        filas = conn.execute(f"SELECT * FROM {tabla};").fetchall()

                    if filas:
                        lista = [dict(f) for f in filas]
                        cambios[tabla] = lista
                        for d in lista:
                            ts_val = _normalizar_ts_comparable(d.get(ts_col))
                            if ts_val and ts_val > max_ts:
                                max_ts = ts_val
                return cambios, max_ts
            finally:
                conn.close()

        cambios_por_tabla, max_ts_visto = self._with_retry(_leer_cambios_remotos)
        recibidos = 0

        with self.db.write_transaction() as lconn:
            # Incorporar registros SIN re-encolar hacia SMB (evita bucles); en modo anillo puentea solo a Supabase
            for tabla, pk_cols, ts_col, auto_id in TABLAS_SINCRONIZABLES:
                filas = cambios_por_tabla.get(tabla)
                if not filas:
                    continue

                cols_locales = [r[1] for r in lconn.execute(f"PRAGMA table_info({tabla});").fetchall()]
                if not cols_locales:
                    continue

                for r in filas:
                    pk_vals = tuple(r.get(c) for c in pk_cols)
                    if any(v is None for v in pk_vals):
                        continue
                    where_pk = " AND ".join(f"{c} = ?" for c in pk_cols)
                    row_loc = lconn.execute(
                        f"SELECT * FROM {tabla} WHERE {where_pk};",
                        pk_vals,
                    ).fetchone()

                    if row_loc is not None:
                        d_loc = dict(row_loc)
                        ts_rem = _normalizar_ts_comparable(r.get(ts_col))
                        ts_loc = _normalizar_ts_comparable(d_loc.get(ts_col))

                        # Anti-colisión de IDs autoincrementales si el registro local pendiente es otro distinto
                        if auto_id and d_loc.get("sync_status") == "pending" and tabla in ("novedades", "cambios_turno", "ordenamientos"):
                            es_mismo = False
                            if tabla == "novedades":
                                es_mismo = str(d_loc.get("legajo")) == str(r.get("legajo")) and str(d_loc.get("fecha_inicio")) == str(r.get("fecha_inicio"))
                            elif tabla == "cambios_turno":
                                es_mismo = str(d_loc.get("legajo_1")) == str(r.get("legajo_1")) and str(d_loc.get("fecha_cambio")) == str(r.get("fecha_cambio"))
                            elif tabla == "ordenamientos":
                                es_mismo = str(d_loc.get("nro_orden")) == str(r.get("nro_orden"))

                            if not es_mismo:
                                max_id = lconn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {tabla};").fetchone()[0]
                                nuevo_id = int(max_id) + 1
                                lconn.execute(f"UPDATE {tabla} SET id = ? WHERE id = ?;", (nuevo_id, r["id"]))
                                if tabla == "novedades":
                                    lconn.execute("UPDATE novedad_reemplazos SET novedad_id = ? WHERE novedad_id = ?;", (nuevo_id, r["id"]))
                                elif tabla == "ordenamientos":
                                    lconn.execute("UPDATE ordenamiento_tramos SET ordenamiento_id = ? WHERE ordenamiento_id = ?;", (nuevo_id, r["id"]))
                                lconn.execute(
                                    "UPDATE sync_outbox SET registro_pk = ? WHERE tabla = ? AND registro_pk = ?;",
                                    (str(nuevo_id), tabla, str(r["id"])),
                                )
                            elif ts_loc and ts_rem and ts_loc > ts_rem:
                                continue
                        elif d_loc.get("sync_status") == "pending" and ts_loc and ts_rem and ts_loc > ts_rem:
                            continue
                        elif ts_loc and ts_rem and ts_loc > ts_rem:
                            continue

                    cols_insert = [c for c in cols_locales if c in r]
                    if not cols_insert:
                        continue
                    vals = [("synced" if c == "sync_status" else r[c]) for c in cols_insert]
                    placeholders = ", ".join("?" for _ in cols_insert)
                    pk_conflict = ", ".join(pk_cols)
                    update_cols = [c for c in cols_insert if c not in pk_cols]

                    if update_cols:
                        set_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
                        sql = (
                            f"INSERT INTO {tabla} ({', '.join(cols_insert)}) "
                            f"VALUES ({placeholders}) "
                            f"ON CONFLICT({pk_conflict}) DO UPDATE SET {set_clause};"
                        )
                    else:
                        sql = (
                            f"INSERT OR IGNORE INTO {tabla} ({', '.join(cols_insert)}) "
                            f"VALUES ({placeholders});"
                        )
                    lconn.execute(sql, vals)
                    recibidos += 1

                    # Efecto Puente (Bridge): si el registro es nuevo o cambió respecto al local,
                    # re-encolar SOLO para Supabase (synced_smb=1, synced_supabase=0)
                    if propagar_a_canal_opuesto:
                        cambio_real = row_loc is None or (
                            _normalizar_ts_comparable(r.get(ts_col)) != _normalizar_ts_comparable(dict(row_loc).get(ts_col))
                        )
                        if cambio_real:
                            pk_str = "_".join(str(v) for v in pk_vals)
                            OutboxQueue.encolar_puente(lconn, tabla, pk_str, r, canal_origen="smb")

            nuevo_ts = max_ts_visto or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            lconn.execute(
                "INSERT OR REPLACE INTO configuracion(clave, valor) VALUES (?, ?);",
                (self.CLAVE_LAST_PULL, nuevo_ts),
            )

        return recibidos

    def sincronizar_completo(
        self,
        shared_path: str | Path | None = None,
        modo_anillo: bool = False,
        propagar_a_canal_opuesto: bool = False,
    ) -> tuple[int, int]:
        """Ejecuta Push + Pull atómicos en exclusión mutua."""
        if not self._lock.acquire(blocking=False):
            return 0, 0
        try:
            ruta = self._resolver_ruta(shared_path)
            enviados = self.push_changes(ruta, modo_anillo=modo_anillo)
            recibidos = self.pull_changes(ruta, propagar_a_canal_opuesto=propagar_a_canal_opuesto)
            return enviados, recibidos
        finally:
            self._lock.release()
