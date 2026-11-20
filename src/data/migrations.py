"""Inicialización y migración automática no destructiva de la base SQLite para RENO v2.0.0."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime

from .database import DatabaseManager, DatabaseBusyError, _try_lock, _unlock

logger = logging.getLogger("reno.data.migrations")

SCHEMA_VERSION_V2 = 2


def init_database_schema(db: DatabaseManager) -> None:
    """Crea o migra todas las tablas de RENO preservando el 100% de los datos existentes."""
    with db.write_transaction() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS empleados (
                id INTEGER PRIMARY KEY,
                legajo INTEGER NOT NULL UNIQUE,
                apellidos_nombres TEXT NOT NULL DEFAULT '',
                especialidad TEXT NOT NULL DEFAULT '',
                dotacion TEXT NOT NULL DEFAULT '',
                turnos TEXT NOT NULL DEFAULT '',
                franco TEXT NOT NULL DEFAULT '',
                franco_horario TEXT NOT NULL DEFAULT '',
                jornada_horas INTEGER NOT NULL DEFAULT 6 CHECK (jornada_horas IN (6, 7)),
                activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
                actualizado_en TEXT NOT NULL,
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS tipos_novedad (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL COLLATE NOCASE UNIQUE,
                dias INTEGER NOT NULL DEFAULT 0,
                exportar_descubierto INTEGER NOT NULL DEFAULT 1 CHECK (exportar_descubierto IN (0, 1)),
                activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS dotaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL COLLATE NOCASE UNIQUE,
                activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS personal_estacion (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL COLLATE NOCASE UNIQUE,
                dotacion TEXT,
                activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS destinatarios_informe (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS destinatarios_ordenamiento (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL COLLATE NOCASE UNIQUE,
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS permisos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo TEXT NOT NULL COLLATE NOCASE UNIQUE
            );

            CREATE TABLE IF NOT EXISTS usuario_roles (
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
                rol_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                PRIMARY KEY (usuario_id, rol_id)
            );

            CREATE TABLE IF NOT EXISTS rol_permisos (
                rol_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                permiso_id INTEGER NOT NULL REFERENCES permisos(id) ON DELETE CASCADE,
                PRIMARY KEY (rol_id, permiso_id)
            );

            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                nombre TEXT NOT NULL DEFAULT '',
                legajo INTEGER,
                password_hash TEXT NOT NULL,
                activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
                creado_en TEXT NOT NULL,
                ultimo_ingreso TEXT,
                password_cambiado_en TEXT,
                debe_cambiar_clave INTEGER NOT NULL DEFAULT 0,
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS novedades (
                id INTEGER PRIMARY KEY,
                registrado_en TEXT NOT NULL,
                legajo INTEGER NOT NULL,
                apellidos_nombres TEXT NOT NULL DEFAULT '',
                especialidad TEXT NOT NULL DEFAULT '',
                dotacion TEXT NOT NULL DEFAULT '',
                turnos TEXT NOT NULL DEFAULT '',
                franco TEXT NOT NULL DEFAULT '',
                novedad TEXT NOT NULL,
                fecha_inicio TEXT NOT NULL,
                fecha_fin TEXT,
                referencia_estacion TEXT NOT NULL,
                supervisor TEXT NOT NULL,
                observaciones TEXT,
                usuario_windows TEXT,
                usuario_id INTEGER REFERENCES usuarios(id),
                activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS novedad_reemplazos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                novedad_id INTEGER NOT NULL REFERENCES novedades(id) ON DELETE CASCADE,
                legajo_reemplazo INTEGER NOT NULL,
                apellidos_nombres_reemplazo TEXT NOT NULL DEFAULT '',
                turnos_reemplazo TEXT NOT NULL DEFAULT '',
                franco_reemplazo TEXT NOT NULL DEFAULT '',
                fecha_inicio TEXT NOT NULL,
                fecha_fin TEXT,
                usuario_windows TEXT,
                usuario_id INTEGER REFERENCES usuarios(id),
                creado_en TEXT NOT NULL,
                activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS cambios_turno (
                id INTEGER PRIMARY KEY,
                registrado_en TEXT NOT NULL,
                legajo_1 INTEGER NOT NULL,
                apellidos_nombres_1 TEXT NOT NULL DEFAULT '',
                especialidad_1 TEXT NOT NULL DEFAULT '',
                dotacion_1 TEXT NOT NULL DEFAULT '',
                turnos_1 TEXT NOT NULL DEFAULT '',
                franco_1 TEXT NOT NULL DEFAULT '',
                legajo_2 INTEGER NOT NULL,
                apellidos_nombres_2 TEXT NOT NULL DEFAULT '',
                especialidad_2 TEXT NOT NULL DEFAULT '',
                dotacion_2 TEXT NOT NULL DEFAULT '',
                turnos_2 TEXT NOT NULL DEFAULT '',
                franco_2 TEXT NOT NULL DEFAULT '',
                fecha_cambio TEXT NOT NULL,
                referencia_estacion TEXT NOT NULL,
                supervisor TEXT NOT NULL,
                observaciones TEXT,
                usuario_windows TEXT,
                usuario_id INTEGER REFERENCES usuarios(id),
                activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS ciclo_jornadas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                legajo INTEGER NOT NULL,
                fecha TEXT NOT NULL,
                toma TEXT,
                deja TEXT,
                franco_anticipado INTEGER NOT NULL DEFAULT 0 CHECK (franco_anticipado IN (0, 1)),
                observaciones TEXT,
                actualizado_en TEXT NOT NULL,
                usuario_windows TEXT,
                usuario_id INTEGER REFERENCES usuarios(id),
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(legajo, fecha)
            );

            CREATE TABLE IF NOT EXISTS configuracion (
                clave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auditoria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER REFERENCES usuarios(id),
                usuario_windows TEXT,
                accion TEXT NOT NULL,
                entidad TEXT NOT NULL,
                entidad_id INTEGER,
                datos_anteriores TEXT,
                datos_nuevos TEXT,
                creado_en TEXT NOT NULL,
                sync_status TEXT NOT NULL DEFAULT 'pending'
            );

            -- Cola transaccional Outbox para sincronización asíncrona hacia Supabase
            CREATE TABLE IF NOT EXISTS sync_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tabla TEXT NOT NULL,
                registro_pk TEXT NOT NULL,
                operacion TEXT NOT NULL DEFAULT 'UPSERT',
                payload_json TEXT,
                creado_en TEXT NOT NULL,
                intentos INTEGER NOT NULL DEFAULT 0,
                ultimo_error TEXT,
                UNIQUE(tabla, registro_pk)
            );

            CREATE INDEX IF NOT EXISTS idx_novedades_legajo ON novedades(legajo);
            CREATE INDEX IF NOT EXISTS idx_novedades_registrado ON novedades(registrado_en DESC);
            CREATE INDEX IF NOT EXISTS idx_reemplazos_novedad ON novedad_reemplazos(novedad_id, activo);
            CREATE INDEX IF NOT EXISTS idx_reemplazos_legajo ON novedad_reemplazos(legajo_reemplazo, activo);
            CREATE INDEX IF NOT EXISTS idx_ciclo_jornadas_legajo_fecha ON ciclo_jornadas(legajo, fecha);
            CREATE INDEX IF NOT EXISTS idx_cambios_legajo_1 ON cambios_turno(legajo_1);
            CREATE INDEX IF NOT EXISTS idx_cambios_legajo_2 ON cambios_turno(legajo_2);
            CREATE INDEX IF NOT EXISTS idx_cambios_registrado ON cambios_turno(registrado_en DESC);
            CREATE INDEX IF NOT EXISTS idx_sync_outbox_tabla ON sync_outbox(tabla);

            -- Base documental de Itinerarios de Conducción Eléctrica
            CREATE TABLE IF NOT EXISTS turnos_itinerario (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_turno TEXT NOT NULL,
                regimen TEXT NOT NULL DEFAULT 'TODOS',
                turno_base TEXT NOT NULL,
                subindice INTEGER,
                tipo_turno TEXT NOT NULL,
                deposito_base TEXT NOT NULL,
                toma_servicio TEXT,
                deje_servicio TEXT,
                duracion_jornada TEXT,
                tiempo_toma TEXT,
                tiempo_deje TEXT,
                itinerario_vigente TEXT NOT NULL,
                observaciones_turno TEXT,
                activo INTEGER NOT NULL DEFAULT 1,
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(codigo_turno, regimen, itinerario_vigente)
            );

            CREATE TABLE IF NOT EXISTS turnos_vueltas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turno_id INTEGER NOT NULL REFERENCES turnos_itinerario(id) ON DELETE CASCADE,
                nro_vuelta INTEGER NOT NULL,
                ref TEXT,
                nro_tren TEXT,
                origen TEXT,
                hora_salida TEXT,
                destino TEXT,
                hora_llegada TEXT,
                observaciones_tramo TEXT,
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT,
                updated_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_turnos_itin_base ON turnos_itinerario(turno_base);
            CREATE INDEX IF NOT EXISTS idx_turnos_itin_deposito ON turnos_itinerario(deposito_base);
            CREATE INDEX IF NOT EXISTS idx_turnos_vueltas_tren ON turnos_vueltas(nro_tren);
            CREATE INDEX IF NOT EXISTS idx_turnos_vueltas_turno ON turnos_vueltas(turno_id);

            -- Estaciones intermedias y horarios de paso por tren
            CREATE TABLE IF NOT EXISTS tren_paradas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nro_tren TEXT NOT NULL,
                itinerario TEXT NOT NULL DEFAULT '',
                regimen TEXT NOT NULL DEFAULT 'H',
                orden_parada INTEGER NOT NULL DEFAULT 1,
                estacion TEXT NOT NULL,
                hora_paso TEXT NOT NULL,
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_tren_paradas_tren ON tren_paradas(nro_tren);
            CREATE INDEX IF NOT EXISTS idx_tren_paradas_itin ON tren_paradas(itinerario);

            -- Catálogo de estaciones, siglas y alias operativos
            CREATE TABLE IF NOT EXISTS estaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_origen INTEGER,
                nombre TEXT UNIQUE NOT NULL,
                sigla TEXT NOT NULL DEFAULT '',
                aliases TEXT NOT NULL DEFAULT '',
                activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_estaciones_nombre ON estaciones(nombre);
            CREATE INDEX IF NOT EXISTS idx_estaciones_sigla ON estaciones(sigla);

            -- Módulo de Ordenamientos Operativos
            CREATE TABLE IF NOT EXISTS ordenamientos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nro_orden TEXT UNIQUE NOT NULL,
                fecha_emision TEXT NOT NULL,
                hora_emision TEXT NOT NULL,
                legajo_personal INTEGER NOT NULL,
                nombre_personal TEXT NOT NULL DEFAULT '',
                deposito TEXT NOT NULL DEFAULT 'PC',
                itinerario TEXT NOT NULL DEFAULT '',
                ciclo_o_turno TEXT NOT NULL DEFAULT '',
                regimen TEXT NOT NULL DEFAULT 'H',
                motivo_orden TEXT NOT NULL DEFAULT '',
                observacion_pie TEXT NOT NULL DEFAULT '',
                ref_pct TEXT NOT NULL DEFAULT '',
                emisor TEXT NOT NULL DEFAULT '',
                usuario_id INTEGER REFERENCES usuarios(id),
                usuario_windows TEXT,
                estado TEXT NOT NULL DEFAULT 'activo',
                activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                deleted_at TEXT
            );

            CREATE TABLE IF NOT EXISTS ordenamiento_tramos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ordenamiento_id INTEGER NOT NULL REFERENCES ordenamientos(id) ON DELETE CASCADE,
                nro_secuencia INTEGER NOT NULL,
                ref TEXT NOT NULL DEFAULT '',
                nro_tren TEXT NOT NULL DEFAULT '',
                origen TEXT NOT NULL DEFAULT '',
                hora_salida TEXT NOT NULL DEFAULT '',
                destino TEXT NOT NULL DEFAULT '',
                hora_llegada TEXT NOT NULL DEFAULT '',
                observaciones TEXT NOT NULL DEFAULT '',
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_ordenamientos_fecha ON ordenamientos(fecha_emision);
            CREATE INDEX IF NOT EXISTS idx_ordenamientos_legajo ON ordenamientos(legajo_personal);
            CREATE INDEX IF NOT EXISTS idx_ordenamientos_estado ON ordenamientos(estado, activo);
            CREATE INDEX IF NOT EXISTS idx_ord_tramos_ord_id ON ordenamiento_tramos(ordenamiento_id);
            CREATE INDEX IF NOT EXISTS idx_ord_tramos_tren ON ordenamiento_tramos(nro_tren);

            -- Catálogo editable de sectores de vía (Conocimiento de Vía - Personal de Conducción)
            CREATE TABLE IF NOT EXISTS via_sectores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo TEXT NOT NULL COLLATE NOCASE UNIQUE,
                origen_sigla TEXT NOT NULL DEFAULT '',
                destino_sigla TEXT NOT NULL DEFAULT '',
                descripcion TEXT NOT NULL DEFAULT '',
                orden INTEGER NOT NULL DEFAULT 0,
                activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            -- Registro de fecha de conocimiento de vía por agente, sector y sentido (ASC / DESC)
            CREATE TABLE IF NOT EXISTS via_conocimientos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                legajo INTEGER NOT NULL REFERENCES empleados(legajo) ON DELETE CASCADE,
                sector_id INTEGER NOT NULL REFERENCES via_sectores(id) ON DELETE CASCADE,
                sentido TEXT NOT NULL CHECK (sentido IN ('ASC', 'DESC')),
                fecha_conocimiento TEXT NOT NULL,
                origen_registro TEXT NOT NULL DEFAULT 'manual',
                turno_referencia TEXT NOT NULL DEFAULT '',
                observaciones TEXT NOT NULL DEFAULT '',
                usuario_id INTEGER REFERENCES usuarios(id),
                usuario_windows TEXT,
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(legajo, sector_id, sentido)
            );

            CREATE INDEX IF NOT EXISTS idx_via_conocimientos_legajo ON via_conocimientos(legajo);
            CREATE INDEX IF NOT EXISTS idx_via_conocimientos_sector ON via_conocimientos(sector_id);
            """
        )

        # Migraciones no destructivas de columnas existentes si provienen de v1
        tablas_sincronizables = [
            "empleados",
            "tipos_novedad",
            "dotaciones",
            "personal_estacion",
            "destinatarios_informe",
            "destinatarios_ordenamiento",
            "usuarios",
            "roles",
            "novedades",
            "novedad_reemplazos",
            "cambios_turno",
            "ciclo_jornadas",
            "auditoria",
            "turnos_itinerario",
            "turnos_vueltas",
            "tren_paradas",
            "ordenamientos",
            "ordenamiento_tramos",
            "via_sectores",
            "via_conocimientos",
        ]
        for t in tablas_sincronizables:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({t});").fetchall()}
            if "sync_status" not in cols:
                conn.execute(f"ALTER TABLE {t} ADD COLUMN sync_status TEXT NOT NULL DEFAULT 'pending';")
            if "created_at" not in cols and t != "auditoria":
                conn.execute(f"ALTER TABLE {t} ADD COLUMN created_at TEXT;")
                conn.execute(f"UPDATE {t} SET created_at = datetime('now', 'localtime') WHERE created_at IS NULL;")
            if "updated_at" not in cols and t != "auditoria":
                conn.execute(f"ALTER TABLE {t} ADD COLUMN updated_at TEXT;")
                conn.execute(f"UPDATE {t} SET updated_at = datetime('now', 'localtime') WHERE updated_at IS NULL;")

        # Empleados: franco_horario y jornada_horas
        emp_cols = {row[1] for row in conn.execute("PRAGMA table_info(empleados);").fetchall()}
        if "franco_horario" not in emp_cols:
            conn.execute("ALTER TABLE empleados ADD COLUMN franco_horario TEXT NOT NULL DEFAULT '';")
        if "jornada_horas" not in emp_cols:
            conn.execute("ALTER TABLE empleados ADD COLUMN jornada_horas INTEGER NOT NULL DEFAULT 6;")
            conn.execute("UPDATE empleados SET jornada_horas = 7 WHERE LOWER(especialidad) LIKE '%guarda%';")

        # Tipos de novedad: dias y exportar_descubierto
        tn_cols = {row[1] for row in conn.execute("PRAGMA table_info(tipos_novedad);").fetchall()}
        if "dias" not in tn_cols:
            conn.execute("ALTER TABLE tipos_novedad ADD COLUMN dias INTEGER NOT NULL DEFAULT 0;")
        if "exportar_descubierto" not in tn_cols:
            conn.execute("ALTER TABLE tipos_novedad ADD COLUMN exportar_descubierto INTEGER NOT NULL DEFAULT 1;")

        # Parámetros de configuración por defecto
        config_defaults = [
            ("sesion_minutos", "30"),
            ("editar_horas", "24"),
            ("eliminar_horas", "72"),
            ("backup_activo", "1"),
            ("backup_retencion", "10"),
            ("notificaciones_activo", "1"),
            ("toast_duracion", "6"),
            ("fila_alto", "30"),
            ("clave_expiracion_dias", "90"),
            ("correo_dominio", "@trenesargentinos.gob.ar"),
        ]
        for k, v in config_defaults:
            conn.execute("INSERT OR IGNORE INTO configuracion(clave, valor) VALUES (?, ?);", (k, v))

        for nom in ("PC", "LLV", "TY", "LP", "OA", "K5", "RE", "CÑ", "AK"):
            conn.execute("INSERT OR IGNORE INTO dotaciones(nombre) VALUES (?);", (nom,))

        conn.execute("INSERT OR IGNORE INTO tipos_novedad(nombre, dias, exportar_descubierto) VALUES ('Informe', 1, 0);")
        conn.execute("INSERT OR IGNORE INTO tipos_novedad(nombre, dias, exportar_descubierto) VALUES ('Dona sangre', 1, 1);")

        # Poblar catálogo base de estaciones y abreviaturas si está vacío
        cnt_est = conn.execute("SELECT COUNT(*) FROM estaciones;").fetchone()[0]
        if cnt_est == 0:
            from src.domain.estaciones import ESTACIONES_BASE

            conn.executemany(
                """
                INSERT OR IGNORE INTO estaciones (codigo_origen, nombre, sigla, aliases, activo)
                VALUES (?, ?, ?, ?, 1);
                """,
                [
                    (
                        st.get("codigo_origen"),
                        st["nombre"],
                        st.get("sigla", ""),
                        ", ".join(st.get("aliases") or ()),
                    )
                    for st in ESTACIONES_BASE
                ],
            )

        # Poblar catálogo base de sectores de vía (19 sectores Línea Roca) si está vacío
        cnt_sec = conn.execute("SELECT COUNT(*) FROM via_sectores;").fetchone()[0]
        if cnt_sec == 0:
            from src.domain.conocimiento_via import SECTORES_VIA_BASE

            conn.executemany(
                """
                INSERT OR IGNORE INTO via_sectores (codigo, origen_sigla, destino_sigla, descripcion, orden, activo)
                VALUES (?, ?, ?, ?, ?, 1);
                """,
                [
                    (
                        sec["codigo"],
                        sec["origen_sigla"],
                        sec["destino_sigla"],
                        sec.get("descripcion", ""),
                        sec.get("orden", 0),
                    )
                    for sec in SECTORES_VIA_BASE
                ],
            )

        # Poblar tren_paradas automáticamente desde turnos_vueltas si está vacía
        cnt_paradas = conn.execute("SELECT COUNT(*) FROM tren_paradas;").fetchone()[0]
        if cnt_paradas == 0:
            vueltas_rows = conn.execute(
                """
                SELECT v.nro_tren, t.itinerario_vigente, t.tipo_turno,
                       v.origen, v.hora_salida, v.destino, v.hora_llegada
                FROM turnos_vueltas v
                JOIN turnos_itinerario t ON t.id = v.turno_id
                WHERE v.nro_tren IS NOT NULL AND TRIM(v.nro_tren) != ''
                ORDER BY v.nro_tren, v.hora_salida
                """
            ).fetchall()
            vistas: set[tuple[str, str, str, str]] = set()
            orden_por_tren: dict[tuple[str, str], int] = {}
            for r in vueltas_rows:
                nt = str(r[0]).strip()
                itin = str(r[1] or "").strip()
                reg = str(r[2] or "H").strip()
                if reg.upper() in ("LV", "L-V", "LAV", "L A V", "HABIL", "HÁBIL", "DIA HABIL", "DÍA HÁBIL"):
                    reg = "H"
                orig = str(r[3] or "").strip()
                hs = str(r[4] or "").strip()
                dest = str(r[5] or "").strip()
                hl = str(r[6] or "").strip()
                for est, hr in ((orig, hs), (dest, hl)):
                    if est and hr:
                        key = (nt, itin, est, hr)
                        if key not in vistas:
                            vistas.add(key)
                            ord_k = (nt, itin)
                            orden_por_tren[ord_k] = orden_por_tren.get(ord_k, 0) + 1
                            conn.execute(
                                """
                                INSERT INTO tren_paradas(nro_tren, itinerario, regimen, orden_parada, estacion, hora_paso)
                                VALUES (?, ?, ?, ?, ?, ?);
                                """,
                                (nt, itin, reg, orden_por_tren[ord_k], est, hr),
                            )

        # Normalizar régimen histórico 'LV'/'L-V'/'HABIL' a 'H' (Hábil) en paradas y ordenamientos
        legacy_habil_vals = "('LV', 'L-V', 'LAV', 'L A V', 'HABIL', 'HÁBIL', 'DIA HABIL', 'DÍA HÁBIL', 'LUNES A VIERNES')"
        conn.execute(
            f"""UPDATE tren_paradas
                SET regimen = 'H', sync_status = 'pending', updated_at = datetime('now', 'localtime')
                WHERE UPPER(TRIM(COALESCE(regimen, ''))) IN {legacy_habil_vals};"""
        )
        conn.execute(
            f"""UPDATE ordenamientos
                SET regimen = 'H', sync_status = 'pending', updated_at = datetime('now', 'localtime')
                WHERE UPPER(TRIM(COALESCE(regimen, ''))) IN {legacy_habil_vals};"""
        )

        cols_outbox = {row[1] for row in conn.execute("PRAGMA table_info(sync_outbox);").fetchall()}
        if "estado" not in cols_outbox:
            conn.execute("ALTER TABLE sync_outbox ADD COLUMN estado TEXT NOT NULL DEFAULT 'PENDIENTE';")

        conn.execute(
            "INSERT OR REPLACE INTO schema_metadata(key, value) VALUES (?, ?);",
            ("version", str(SCHEMA_VERSION_V2)),
        )

    _asegurar_migracion_turnos_itinerario(db)
    logger.info("Esquema SQLite verificado y actualizado a v%s.", SCHEMA_VERSION_V2)


def _asegurar_migracion_turnos_itinerario(db: DatabaseManager) -> None:
    """Migra tablas legacy de turnos_itinerario (UNIQUE(codigo_turno) -> UNIQUE(codigo_turno, regimen, itinerario_vigente))."""
    with db.read_connection() as conn:
        cols_ti = {row[1] for row in conn.execute("PRAGMA table_info(turnos_itinerario);").fetchall()}
        needs_rebuild = "regimen" not in cols_ti
        if not needs_rebuild:
            for idx_row in conn.execute("PRAGMA index_list(turnos_itinerario);").fetchall():
                if idx_row[2]:
                    idx_name = str(idx_row[1]).replace("'", "''")
                    idx_cols = [c[2] for c in conn.execute(f"PRAGMA index_info('{idx_name}');").fetchall()]
                    if idx_cols == ["codigo_turno"]:
                        needs_rebuild = True
                        break

    if not needs_rebuild:
        with db.write_transaction() as conn:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turnos_itin_regimen ON turnos_itinerario(regimen);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turnos_itin_vigente ON turnos_itinerario(itinerario_vigente);")
            conn.execute(
                """UPDATE turnos_itinerario
                   SET regimen = 'H', sync_status = 'pending', updated_at = datetime('now', 'localtime')
                   WHERE UPPER(TRIM(COALESCE(regimen, ''))) IN ('LV', 'L-V', 'LAV', 'L A V', 'HABIL', 'HÁBIL', 'LUNES A VIERNES')
                     AND NOT EXISTS (
                         SELECT 1 FROM turnos_itinerario t2
                         WHERE t2.codigo_turno = turnos_itinerario.codigo_turno
                           AND UPPER(TRIM(COALESCE(t2.regimen, ''))) = 'H'
                           AND t2.itinerario_vigente = turnos_itinerario.itinerario_vigente
                     );"""
            )
        return

    lock_file = None
    if db.journal_mode == "DELETE":
        started = time.monotonic()
        while True:
            try:
                lock_file = open(db.lock_path, "a+b")
                if os.path.getsize(db.lock_path) == 0:
                    lock_file.write(b"0")
                    lock_file.flush()
                if _try_lock(lock_file):
                    break
                raise OSError("Lock ocupado")
            except OSError:
                if lock_file:
                    lock_file.close()
                    lock_file = None
                if time.monotonic() - started >= db.lock_timeout:
                    raise DatabaseBusyError("La base de datos está siendo utilizada por otra terminal.")
                time.sleep(0.12)

    conn = db.connect()
    try:
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute("BEGIN IMMEDIATE;")

        reg_expr = "COALESCE(NULLIF(TRIM(regimen), ''), 'TODOS')" if "regimen" in cols_ti else "'TODOS'"
        sync_expr = "COALESCE(sync_status, 'pending')" if "sync_status" in cols_ti else "'pending'"
        ca_expr = "created_at" if "created_at" in cols_ti else "datetime('now', 'localtime')"
        ua_expr = "updated_at" if "updated_at" in cols_ti else "datetime('now', 'localtime')"

        conn.execute("DROP TABLE IF EXISTS _tmp_turnos_vueltas_old;")
        conn.execute("DROP TABLE IF EXISTS _tmp_turnos_itinerario_old;")
        conn.execute("ALTER TABLE turnos_vueltas RENAME TO _tmp_turnos_vueltas_old;")
        conn.execute("ALTER TABLE turnos_itinerario RENAME TO _tmp_turnos_itinerario_old;")

        conn.execute(
            """
            CREATE TABLE turnos_itinerario (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_turno TEXT NOT NULL,
                regimen TEXT NOT NULL DEFAULT 'TODOS',
                turno_base TEXT NOT NULL,
                subindice INTEGER,
                tipo_turno TEXT NOT NULL,
                deposito_base TEXT NOT NULL,
                toma_servicio TEXT,
                deje_servicio TEXT,
                duracion_jornada TEXT,
                tiempo_toma TEXT,
                tiempo_deje TEXT,
                itinerario_vigente TEXT NOT NULL,
                observaciones_turno TEXT,
                activo INTEGER NOT NULL DEFAULT 1,
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(codigo_turno, regimen, itinerario_vigente)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE turnos_vueltas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turno_id INTEGER NOT NULL REFERENCES turnos_itinerario(id) ON DELETE CASCADE,
                nro_vuelta INTEGER NOT NULL,
                ref TEXT,
                nro_tren TEXT,
                origen TEXT,
                hora_salida TEXT,
                destino TEXT,
                hora_llegada TEXT,
                observaciones_tramo TEXT,
                sync_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT,
                updated_at TEXT
            );
            """
        )

        conn.execute(
            f"""
            INSERT INTO turnos_itinerario (
                id, codigo_turno, regimen, turno_base, subindice, tipo_turno, deposito_base,
                toma_servicio, deje_servicio, duracion_jornada, tiempo_toma, tiempo_deje,
                itinerario_vigente, observaciones_turno, activo, sync_status, created_at, updated_at
            )
            SELECT
                id, codigo_turno, {reg_expr}, turno_base, subindice, tipo_turno, deposito_base,
                toma_servicio, deje_servicio, duracion_jornada, tiempo_toma, tiempo_deje,
                itinerario_vigente, observaciones_turno, activo, {sync_expr}, {ca_expr}, {ua_expr}
            FROM _tmp_turnos_itinerario_old;
            """
        )
        conn.execute(
            """
            INSERT INTO turnos_vueltas (
                id, turno_id, nro_vuelta, ref, nro_tren, origen, hora_salida,
                destino, hora_llegada, observaciones_tramo, sync_status, created_at, updated_at
            )
            SELECT
                id, turno_id, nro_vuelta, ref, nro_tren, origen, hora_salida,
                destino, hora_llegada, observaciones_tramo, COALESCE(sync_status, 'pending'), created_at, updated_at
            FROM _tmp_turnos_vueltas_old;
            """
        )

        conn.execute("DROP TABLE _tmp_turnos_vueltas_old;")
        conn.execute("DROP TABLE _tmp_turnos_itinerario_old;")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_turnos_itin_base ON turnos_itinerario(turno_base);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_turnos_itin_deposito ON turnos_itinerario(deposito_base);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_turnos_itin_regimen ON turnos_itinerario(regimen);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_turnos_itin_vigente ON turnos_itinerario(itinerario_vigente);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_turnos_vueltas_tren ON turnos_vueltas(nro_tren);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_turnos_vueltas_turno ON turnos_vueltas(turno_id);")

        conn.execute("COMMIT;")
        conn.execute("PRAGMA foreign_keys = ON;")
        fk_errs = conn.execute("PRAGMA foreign_key_check;").fetchall()
        if fk_errs:
            raise RuntimeError(f"Error de integridad referencial tras migración de turnos: {fk_errs}")
        logger.info("Tabla turnos_itinerario migrada exitosamente a clave compuesta (codigo_turno, regimen, itinerario_vigente).")
    except Exception:
        if conn.in_transaction:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
        raise
    finally:
        conn.close()
        if lock_file is not None:
            _unlock(lock_file)
            lock_file.close()
