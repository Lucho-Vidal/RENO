"""Casos de uso y operaciones de negocio para RENO v2.0.0.

Orquesta la persistencia local en SQLite, las reglas de dominio,
la auditoría y el encolamiento para sincronización Supabase.
"""

import calendar
import json
import re
import sqlite3
import unicodedata
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from src.core.config import obtener_usuario_windows
from src.core.logger import get_logger
from src.data.database import DatabaseManager
from src.domain.enums import EstadoCobertura, EstadoDiaCiclo, SyncOperation
from src.domain.estaciones import (
    ESTACIONES_BASE,
    CatalogoEstacionesIndex,
    _clave_compacta_estacion,
    normalizar_texto_estacion,
    parsear_aliases_estacion,
)
from src.domain.models import (
    CambioTurno,
    CicloJornada,
    DiaCalendarioCiclo,
    Empleado,
    Novedad,
    PersonalEstacion,
    TipoNovedad,
    TramoReemplazo,
)
from src.domain.validators import (
    DIAS_SEMANA,
    DIAS_SEMANA_NORM,
    calcular_descansos_ciclo,
    es_turno_ciclo,
    normalizar_dia_semana,
    parsear_fecha,
    validar_datos_ordenamiento,
    validar_tramos_reemplazo,
)

logger = get_logger("RecordsService")


def _normalizar_especialidad(texto: Optional[str]) -> str:
    """Normaliza una especialidad eliminando tildes, espacios extra y unificando Guardatren/Guarda."""
    raw = " ".join(str(texto or "").strip().split())
    nfkd = unicodedata.normalize("NFKD", raw)
    sin_tildes = "".join(c for c in nfkd if not unicodedata.combining(c)).lower()
    return re.sub(r"\bguarda\s*tren\b", "guarda", sin_tildes)


def _coincide_especialidad(valor_db: Optional[str], filtro: Optional[str]) -> bool:
    """Compara especialidades de forma insensible a mayúsculas, tildes y prefijo Guardatren/Guarda."""
    f_norm = _normalizar_especialidad(filtro)
    if not f_norm or f_norm == "todas":
        return True
    v_norm = _normalizar_especialidad(valor_db)
    return v_norm == f_norm or f_norm in v_norm


class RecordsService:
    """Servicio principal de negocio para novedades, cambios de turno, ciclo y dashboard."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    # ---------------------------------------------------------
    # Auditoría y Cola de Sincronización
    # ---------------------------------------------------------

    def _audit(
        self,
        conn: sqlite3.Connection,
        user_id: Optional[int],
        windows_user: Optional[str],
        action: str,
        entity: str,
        entity_id: Optional[int],
        before: Optional[Dict[str, Any]],
        after: Optional[Dict[str, Any]],
    ) -> None:
        win_usr = (windows_user or "").strip() or obtener_usuario_windows()
        conn.execute(
            """INSERT INTO auditoria
               (usuario_id, usuario_windows, accion, entidad, entidad_id,
                datos_anteriores, datos_nuevos, creado_en)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                win_usr,
                action,
                entity,
                entity_id,
                json.dumps(before, ensure_ascii=False, default=str) if before else None,
                json.dumps(after, ensure_ascii=False, default=str) if after else None,
                self.db.now(),
            ),
        )

    def _enqueue_sync(
        self,
        conn: sqlite3.Connection,
        tabla: str,
        registro_id: Any,
        operacion: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn.execute(
            """INSERT INTO sync_outbox (tabla, registro_pk, operacion, payload_json, creado_en)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(tabla, registro_pk) DO UPDATE SET
                   operacion = excluded.operacion,
                   payload_json = excluded.payload_json,
                   creado_en = excluded.creado_en""",
            (
                tabla,
                str(registro_id),
                operacion,
                json.dumps(payload, ensure_ascii=False, default=str) if payload else None,
                self.db.now(),
            ),
        )

    # ---------------------------------------------------------
    # Fechas y Ventanas de Edición / Borrado
    # ---------------------------------------------------------

    def parsear_registrado_en(self, valor: Any) -> Optional[datetime]:
        """Parsea una fecha de registro en formatos ISO o DD/MM/YYYY con o sin hora."""
        if valor is None:
            return None
        texto = str(valor).strip()
        if not texto:
            return None

        if len(texto) >= 10 and texto[4] == "-" and texto[7] == "-":
            try:
                dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
                if dt.tzinfo is not None:
                    dt = dt.astimezone().replace(tzinfo=None)
                return dt
            except ValueError:
                pass
        elif len(texto) >= 10 and texto[2] == "/" and texto[5] == "/":
            if len(texto) == 10:
                try:
                    return datetime.strptime(texto, "%d/%m/%Y")
                except ValueError:
                    pass
            elif len(texto) == 16:
                try:
                    return datetime.strptime(texto, "%d/%m/%Y %H:%M")
                except ValueError:
                    pass
            elif len(texto) >= 19:
                try:
                    return datetime.strptime(texto[:19], "%d/%m/%Y %H:%M:%S")
                except ValueError:
                    pass

        for formato in (
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(texto, formato)
            except ValueError:
                continue

        try:
            return datetime(1899, 12, 30) + timedelta(days=float(texto))
        except ValueError:
            return None

    def _horas_limite(self, conn: sqlite3.Connection, clave: str, default: int) -> int:
        row = conn.execute("SELECT valor FROM configuracion WHERE clave=?", (clave,)).fetchone()
        try:
            return max(1, int(row[0])) if row else default
        except (TypeError, ValueError):
            return default

    def dentro_de_ventana(self, registrado_en: Any, clave: str) -> bool:
        default = 24 if clave == "editar_horas" else 72
        dt = self.parsear_registrado_en(registrado_en)
        if dt is None:
            return False
        with self.db.read_connection() as conn:
            horas = self._horas_limite(conn, clave, default)
        return (datetime.now() - dt).total_seconds() <= horas * 3600

    # ---------------------------------------------------------
    # Tipos de Novedad CRUD
    # ---------------------------------------------------------

    def listar_tipos(self, incluir_inactivos: bool = True) -> List[Dict[str, Any]]:
        with self.db.read_connection() as conn:
            query = "SELECT id, nombre, dias, exportar_descubierto, activo FROM tipos_novedad"
            if not incluir_inactivos:
                query += " WHERE activo=1"
            query += " ORDER BY nombre"
            rows = conn.execute(query).fetchall()
            return [dict(r) for r in rows]

    def crear_tipo(
        self,
        nombre: str,
        dias: int = 0,
        exportar_descubierto: int = 1,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        nombre = (nombre or "").strip()
        if not nombre:
            raise ValueError("El nombre no puede estar vacío.")
        try:
            dias_val = max(0, int(dias or 0))
        except (ValueError, TypeError):
            dias_val = 0
        exportar_val = 1 if exportar_descubierto else 0

        with self.db.write_transaction() as conn:
            cur = conn.execute(
                "INSERT INTO tipos_novedad(nombre, dias, exportar_descubierto) VALUES (?, ?, ?)",
                (nombre, dias_val, exportar_val),
            )
            item_id = cur.lastrowid
            payload = {"nombre": nombre, "dias": dias_val, "exportar_descubierto": exportar_val}
            self._audit(conn, user_id, windows_user, "crear", "tipo_novedad", item_id, None, payload)
            self._enqueue_sync(conn, "tipos_novedad", item_id, "INSERT", payload)
            return item_id

    def actualizar_tipo(
        self,
        type_id: int,
        nombre: str,
        dias: int = 0,
        exportar_descubierto: int = 1,
        activo: int = 1,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        nombre = (nombre or "").strip()
        if not nombre:
            raise ValueError("El nombre no puede estar vacío.")
        try:
            dias_val = max(0, int(dias or 0))
        except (ValueError, TypeError):
            dias_val = 0
        exportar_val = 1 if exportar_descubierto else 0

        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM tipos_novedad WHERE id=?", (type_id,)).fetchone()
            if not before:
                raise ValueError("El tipo de novedad no existe.")
            conn.execute(
                "UPDATE tipos_novedad SET nombre=?, dias=?, exportar_descubierto=?, activo=? WHERE id=?",
                (nombre, dias_val, exportar_val, int(activo), type_id),
            )
            after = {"nombre": nombre, "dias": dias_val, "exportar_descubierto": exportar_val, "activo": int(activo)}
            self._audit(conn, user_id, windows_user, "modificar", "tipo_novedad", type_id, dict(before), after)
            self._enqueue_sync(conn, "tipos_novedad", type_id, "UPDATE", after)

    # ---------------------------------------------------------
    # Dotaciones CRUD
    # ---------------------------------------------------------

    def listar_dotaciones(self, incluir_inactivos: bool = True) -> List[Dict[str, Any]]:
        with self.db.read_connection() as conn:
            query = "SELECT id, nombre, activo FROM dotaciones"
            if not incluir_inactivos:
                query += " WHERE activo=1"
            rows = conn.execute(query + " ORDER BY nombre").fetchall()
            return [dict(r) for r in rows]

    def crear_dotacion(
        self,
        nombre: str,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        nombre = (nombre or "").strip()
        if not nombre:
            raise ValueError("La dotación no puede estar vacía.")
        with self.db.write_transaction() as conn:
            cur = conn.execute("INSERT INTO dotaciones(nombre) VALUES (?)", (nombre,))
            item_id = cur.lastrowid
            payload = {"nombre": nombre}
            self._audit(conn, user_id, windows_user, "crear", "dotacion", item_id, None, payload)
            self._enqueue_sync(conn, "dotaciones", item_id, "INSERT", payload)
            return item_id

    def actualizar_dotacion(
        self,
        dotacion_id: int,
        nombre: str,
        activo: int,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        nombre = (nombre or "").strip()
        if not nombre:
            raise ValueError("La dotación no puede estar vacía.")
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT id, nombre, activo FROM dotaciones WHERE id=?", (dotacion_id,)).fetchone()
            if not before:
                raise ValueError("La dotación no existe.")
            conn.execute("UPDATE dotaciones SET nombre=?, activo=? WHERE id=?", (nombre, int(activo), dotacion_id))
            after = {"nombre": nombre, "activo": int(activo)}
            self._audit(conn, user_id, windows_user, "modificar", "dotacion", dotacion_id, dict(before), after)
            self._enqueue_sync(conn, "dotaciones", dotacion_id, "UPDATE", after)

    # ---------------------------------------------------------
    # Personal de Estación CRUD
    # ---------------------------------------------------------

    def listar_personal_estacion(self, incluir_inactivos: bool = True) -> List[Dict[str, Any]]:
        with self.db.read_connection() as conn:
            query = "SELECT id, nombre, dotacion, activo FROM personal_estacion"
            if not incluir_inactivos:
                query += " WHERE activo=1"
            rows = conn.execute(query + " ORDER BY nombre").fetchall()
            return [dict(r) for r in rows]

    def crear_personal_estacion(
        self,
        nombre: str,
        dotacion: Optional[str] = None,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        nombre = (nombre or "").strip()
        if not nombre:
            raise ValueError("El nombre no puede estar vacío.")
        dotacion = (dotacion or "").strip() or None
        with self.db.write_transaction() as conn:
            cur = conn.execute(
                "INSERT INTO personal_estacion(nombre, dotacion) VALUES (?, ?)",
                (nombre, dotacion),
            )
            item_id = cur.lastrowid
            payload = {"nombre": nombre, "dotacion": dotacion}
            self._audit(conn, user_id, windows_user, "crear", "personal_estacion", item_id, None, payload)
            self._enqueue_sync(conn, "personal_estacion", item_id, "INSERT", payload)
            return item_id

    def actualizar_personal_estacion(
        self,
        item_id: int,
        nombre: str,
        activo: int,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
        *,
        dotacion: Optional[str] = None,
        cambiar_dotacion: bool = False,
    ) -> None:
        nombre = (nombre or "").strip()
        if cambiar_dotacion:
            dotacion = (dotacion or "").strip() or None
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT id, nombre, dotacion, activo FROM personal_estacion WHERE id=?", (item_id,)).fetchone()
            if not before:
                raise ValueError("El personal de estación no existe.")
            despues_dotacion = dotacion if cambiar_dotacion else before["dotacion"]
            conn.execute(
                "UPDATE personal_estacion SET nombre=?, dotacion=?, activo=? WHERE id=?",
                (nombre, despues_dotacion, int(activo), item_id),
            )
            after = {"nombre": nombre, "dotacion": despues_dotacion, "activo": int(activo)}
            self._audit(conn, user_id, windows_user, "modificar", "personal_estacion", item_id, dict(before), after)
            self._enqueue_sync(conn, "personal_estacion", item_id, "UPDATE", after)

    # ---------------------------------------------------------
    # Empleados (BASE) CRUD
    # ---------------------------------------------------------

    def listar_empleados(self, incluir_inactivos: bool = True) -> List[Dict[str, Any]]:
        with self.db.read_connection() as conn:
            query = """SELECT id, legajo, apellidos_nombres, especialidad, dotacion, turnos,
                              franco, franco_horario, jornada_horas, activo
                       FROM empleados"""
            if not incluir_inactivos:
                query += " WHERE activo=1"
            rows = conn.execute(query + " ORDER BY apellidos_nombres").fetchall()
            return [dict(r) for r in rows]

    def listar_especialidades_empleados(self) -> List[str]:
        """Devuelve el listado ordenado de especialidades únicas activas existentes en la base de datos."""
        with self.db.read_connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT especialidad FROM empleados
                   WHERE activo = 1 AND especialidad IS NOT NULL AND TRIM(especialidad) != ''
                   ORDER BY especialidad ASC"""
            ).fetchall()
            return [r["especialidad"] for r in rows]

    def listar_empleados_ciclo(self) -> List[Dict[str, Any]]:
        """Devuelve el personal activo correspondiente a régimen de ciclo mensual."""
        with self.db.read_connection() as conn:
            rows = conn.execute(
                """SELECT id, legajo, apellidos_nombres, especialidad, dotacion, turnos,
                          franco, franco_horario, jornada_horas, activo
                   FROM empleados
                   WHERE activo = 1 AND UPPER(TRIM(turnos)) LIKE 'CICLO%'
                   ORDER BY dotacion, apellidos_nombres"""
            ).fetchall()
            return [dict(r) for r in rows]

    def obtener_empleado(self, empleado_id: int) -> Optional[Dict[str, Any]]:
        with self.db.read_connection() as conn:
            row = conn.execute("SELECT * FROM empleados WHERE id=?", (empleado_id,)).fetchone()
            return dict(row) if row else None

    def obtener_empleado_por_legajo(self, legajo: int) -> Optional[Dict[str, Any]]:
        with self.db.read_connection() as conn:
            row = conn.execute("SELECT * FROM empleados WHERE legajo=?", (int(legajo),)).fetchone()
            return dict(row) if row else None

    def _validar_empleado(self, data: Dict[str, Any]) -> Tuple[int, str]:
        try:
            legajo = int(str(data.get("legajo")).strip())
        except (TypeError, ValueError):
            raise ValueError("El legajo debe ser un número entero.")
        apellidos = (data.get("apellidos_nombres") or "").strip()
        if not apellidos:
            raise ValueError("Los apellidos y nombres son obligatorios.")
        return legajo, apellidos

    def crear_empleado(
        self,
        data: Dict[str, Any],
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        legajo, apellidos = self._validar_empleado(data)
        valores = {
            "legajo": legajo,
            "apellidos_nombres": apellidos,
            "especialidad": (data.get("especialidad") or "").strip(),
            "dotacion": (data.get("dotacion") or "").strip(),
            "turnos": (data.get("turnos") or "").strip(),
            "franco": (data.get("franco") or "").strip(),
            "franco_horario": (data.get("franco_horario") or "").strip(),
            "jornada_horas": 7 if str(data.get("jornada_horas")).strip() == "7" else 6,
        }
        with self.db.write_transaction() as conn:
            try:
                cur = conn.execute(
                    """INSERT INTO empleados
                       (legajo, apellidos_nombres, especialidad, dotacion, turnos, franco, franco_horario, jornada_horas, actualizado_en)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        valores["legajo"],
                        valores["apellidos_nombres"],
                        valores["especialidad"],
                        valores["dotacion"],
                        valores["turnos"],
                        valores["franco"],
                        valores["franco_horario"],
                        valores["jornada_horas"],
                        self.db.now(),
                    ),
                )
                item_id = cur.lastrowid
            except sqlite3.IntegrityError:
                raise ValueError(f"Ya existe un empleado con el legajo {legajo}.")
            self._audit(conn, user_id, windows_user, "crear", "empleado", item_id, None, valores)
            self._enqueue_sync(conn, "empleados", item_id, "INSERT", valores)
            return item_id

    def actualizar_empleado(
        self,
        empleado_id: int,
        data: Dict[str, Any],
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        legajo, apellidos = self._validar_empleado(data)
        valores = {
            "legajo": legajo,
            "apellidos_nombres": apellidos,
            "especialidad": (data.get("especialidad") or "").strip(),
            "dotacion": (data.get("dotacion") or "").strip(),
            "turnos": (data.get("turnos") or "").strip(),
            "franco": (data.get("franco") or "").strip(),
            "franco_horario": (data.get("franco_horario") or "").strip(),
            "jornada_horas": 7 if str(data.get("jornada_horas")).strip() == "7" else 6,
        }
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM empleados WHERE id=?", (empleado_id,)).fetchone()
            if not before:
                raise ValueError("El empleado no existe.")
            try:
                conn.execute(
                    """UPDATE empleados SET legajo=?, apellidos_nombres=?, especialidad=?, dotacion=?,
                                            turnos=?, franco=?, franco_horario=?, jornada_horas=?, actualizado_en=?
                       WHERE id=?""",
                    (
                        valores["legajo"],
                        valores["apellidos_nombres"],
                        valores["especialidad"],
                        valores["dotacion"],
                        valores["turnos"],
                        valores["franco"],
                        valores["franco_horario"],
                        valores["jornada_horas"],
                        self.db.now(),
                        empleado_id,
                    ),
                )
            except sqlite3.IntegrityError:
                raise ValueError(f"Ya existe un empleado con el legajo {legajo}.")
            after = dict(conn.execute("SELECT * FROM empleados WHERE id=?", (empleado_id,)).fetchone())
            self._audit(conn, user_id, windows_user, "modificar", "empleado", empleado_id, dict(before), after)
            self._enqueue_sync(conn, "empleados", empleado_id, "UPDATE", after)

    def cambiar_estado_empleado(
        self,
        empleado_id: int,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM empleados WHERE id=?", (empleado_id,)).fetchone()
            if not before:
                raise ValueError("El empleado no existe.")
            nuevo_estado = 1 - before["activo"]
            conn.execute("UPDATE empleados SET activo=?, sync_status='pending' WHERE id=?", (nuevo_estado, empleado_id))
            after = dict(conn.execute("SELECT * FROM empleados WHERE id=?", (empleado_id,)).fetchone())
            accion = "desactivar" if nuevo_estado == 0 else "reactivar"
            self._audit(conn, user_id, windows_user, accion, "empleado", empleado_id, dict(before), {"activo": nuevo_estado})
            self._enqueue_sync(conn, "empleados", after["legajo"], "UPDATE", after)
            return nuevo_estado

    # ---------------------------------------------------------
    # Destinatarios de Informe CRUD
    # ---------------------------------------------------------

    def listar_destinatarios_informe(self, incluir_inactivos: bool = True) -> List[Dict[str, Any]]:
        with self.db.read_connection() as conn:
            query = "SELECT id, nombre, email, activo FROM destinatarios_informe"
            if not incluir_inactivos:
                query += " WHERE activo=1"
            rows = conn.execute(query + " ORDER BY nombre").fetchall()
            return [dict(r) for r in rows]

    def crear_destinatario_informe(
        self,
        nombre: str,
        email: str,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        nombre, email = (nombre or "").strip(), (email or "").strip()
        if not email or "@" not in email:
            raise ValueError("Ingrese un correo electrónico válido.")
        with self.db.write_transaction() as conn:
            cur = conn.execute(
                "INSERT INTO destinatarios_informe(nombre, email) VALUES (?, ?)",
                (nombre, email),
            )
            item_id = cur.lastrowid
            payload = {"nombre": nombre, "email": email}
            self._audit(conn, user_id, windows_user, "crear", "destinatario_informe", item_id, None, payload)
            self._enqueue_sync(conn, "destinatarios_informe", item_id, "INSERT", payload)
            return item_id

    def actualizar_destinatario_informe(
        self,
        item_id: int,
        nombre: str,
        email: str,
        activo: int,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        nombre, email = (nombre or "").strip(), (email or "").strip()
        if not email or "@" not in email:
            raise ValueError("Ingrese un correo electrónico válido.")
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT id, nombre, email, activo FROM destinatarios_informe WHERE id=?", (item_id,)).fetchone()
            if not before:
                raise ValueError("El destinatario no existe.")
            conn.execute(
                "UPDATE destinatarios_informe SET nombre=?, email=?, activo=? WHERE id=?",
                (nombre, email, int(activo), item_id),
            )
            after = {"nombre": nombre, "email": email, "activo": int(activo)}
            self._audit(conn, user_id, windows_user, "modificar", "destinatario_informe", item_id, dict(before), after)
            self._enqueue_sync(conn, "destinatarios_informe", item_id, "UPDATE", after)

    # ---------------------------------------------------------
    # Destinatarios de Ordenamiento CRUD
    # ---------------------------------------------------------

    def listar_destinatarios_ordenamiento(self, incluir_inactivos: bool = True) -> List[Dict[str, Any]]:
        with self.db.read_connection() as conn:
            query = "SELECT id, nombre, email, activo FROM destinatarios_ordenamiento"
            if not incluir_inactivos:
                query += " WHERE activo=1"
            rows = conn.execute(query + " ORDER BY nombre").fetchall()
            return [dict(r) for r in rows]

    def crear_destinatario_ordenamiento(
        self,
        nombre: str,
        email: str,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        nombre, email = (nombre or "").strip(), (email or "").strip()
        if not email or "@" not in email:
            raise ValueError("Ingrese un correo electrónico válido.")
        with self.db.write_transaction() as conn:
            cur = conn.execute(
                "INSERT INTO destinatarios_ordenamiento(nombre, email) VALUES (?, ?)",
                (nombre, email),
            )
            item_id = cur.lastrowid
            payload = {"nombre": nombre, "email": email}
            self._audit(conn, user_id, windows_user, "crear", "destinatario_ordenamiento", item_id, None, payload)
            self._enqueue_sync(conn, "destinatarios_ordenamiento", item_id, "INSERT", payload)
            return item_id

    def actualizar_destinatario_ordenamiento(
        self,
        item_id: int,
        nombre: str,
        email: str,
        activo: int,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        nombre, email = (nombre or "").strip(), (email or "").strip()
        if not email or "@" not in email:
            raise ValueError("Ingrese un correo electrónico válido.")
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT id, nombre, email, activo FROM destinatarios_ordenamiento WHERE id=?", (item_id,)).fetchone()
            if not before:
                raise ValueError("El destinatario no existe.")
            conn.execute(
                "UPDATE destinatarios_ordenamiento SET nombre=?, email=?, activo=? WHERE id=?",
                (nombre, email, int(activo), item_id),
            )
            after = {"nombre": nombre, "email": email, "activo": int(activo)}
            self._audit(conn, user_id, windows_user, "modificar", "destinatario_ordenamiento", item_id, dict(before), after)
            self._enqueue_sync(conn, "destinatarios_ordenamiento", item_id, "UPDATE", after)

    # ---------------------------------------------------------
    # Novedades CRUD
    # ---------------------------------------------------------

    def listar_novedades(
        self,
        filtros: Optional[Dict[str, Any]] = None,
        incluir_inactivos: bool = False,
    ) -> List[Dict[str, Any]]:
        with self.db.read_connection() as conn:
            query = """SELECT id, registrado_en, legajo, apellidos_nombres, especialidad,
                              dotacion, turnos, franco, novedad, fecha_inicio, fecha_fin,
                              referencia_estacion, supervisor, observaciones, usuario_windows,
                              usuario_id, activo
                       FROM novedades"""
            conds = []
            params = []
            if not incluir_inactivos:
                conds.append("activo = 1")

            if filtros:
                if filtros.get("legajo"):
                    conds.append("legajo = ?")
                    params.append(int(filtros["legajo"]))
                if filtros.get("dotacion"):
                    conds.append("LOWER(TRIM(dotacion)) = LOWER(TRIM(?))")
                    params.append(str(filtros["dotacion"]))
                if filtros.get("novedad"):
                    conds.append("LOWER(TRIM(novedad)) = LOWER(TRIM(?))")
                    params.append(str(filtros["novedad"]))

            if conds:
                query += " WHERE " + " AND ".join(conds)
            query += " ORDER BY id DESC"

            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def obtener_novedad(self, record_id: int) -> Optional[Dict[str, Any]]:
        with self.db.read_connection() as conn:
            row = conn.execute("SELECT * FROM novedades WHERE id=?", (record_id,)).fetchone()
            return dict(row) if row else None

    def validar_novedad_superpuesta(
        self,
        legajo: int,
        fecha_inicio_str: str,
        fecha_fin_str: Optional[str] = None,
        excluir_novedad_id: Optional[int] = None,
    ) -> None:
        """Verifica que el agente no posea una novedad activa que se solape en el período."""
        f_ini = parsear_fecha(fecha_inicio_str, "Fecha de inicio")
        if not f_ini:
            return
        f_fin = parsear_fecha(fecha_fin_str, "Fecha de fin", obligatorio=False) if (fecha_fin_str and fecha_fin_str != "-") else None

        with self.db.read_connection() as conn:
            query = "SELECT id, novedad, fecha_inicio, fecha_fin FROM novedades WHERE activo = 1 AND legajo = ?"
            params: List[Any] = [int(legajo)]
            if excluir_novedad_id:
                query += " AND id != ?"
                params.append(int(excluir_novedad_id))
            rows = conn.execute(query, params).fetchall()

        for r in rows:
            r_ini = parsear_fecha(r["fecha_inicio"])
            if not r_ini:
                continue
            r_fin_raw = r["fecha_fin"]
            r_fin = parsear_fecha(r_fin_raw) if (r_fin_raw and r_fin_raw != "-") else None

            # Solapamiento si: f_ini <= (r_fin or +inf) and (f_fin or +inf) >= r_ini
            if (r_fin is None or f_ini.date() <= r_fin.date()) and (f_fin is None or f_fin.date() >= r_ini.date()):
                fin_display = r_fin_raw if (r_fin_raw and r_fin_raw != "-") else "Abierta"
                raise ValueError(
                    f"El agente con legajo {legajo} ya posee una novedad activa (#{r['id']} - {r['novedad']}) "
                    f"que abarca el período {r['fecha_inicio']} a {fin_display}."
                )

    def validar_reemplazo_disponible(
        self,
        legajo_reemplazo: int,
        fecha_inicio_str: str,
        fecha_fin_str: Optional[str] = None,
        excluir_novedad_id: Optional[int] = None,
        excluir_reemplazo_id: Optional[int] = None,
    ) -> None:
        """Verifica que el personal de reemplazo no tenga novedad activa ni esté relevando otra novedad en esa fecha."""
        f_ini = parsear_fecha(fecha_inicio_str, "Fecha de inicio relevo")
        if not f_ini:
            return
        f_fin = parsear_fecha(fecha_fin_str, "Fecha de fin relevo", obligatorio=False) if (fecha_fin_str and fecha_fin_str != "-") else None

        with self.db.read_connection() as conn:
            # 1. Comprobar si el reemplazo tiene novedad activa propia
            novs = conn.execute(
                "SELECT id, novedad, fecha_inicio, fecha_fin FROM novedades WHERE activo = 1 AND legajo = ?",
                (int(legajo_reemplazo),),
            ).fetchall()
            for n in novs:
                n_ini = parsear_fecha(n["fecha_inicio"])
                if not n_ini:
                    continue
                n_fin = parsear_fecha(n["fecha_fin"]) if (n["fecha_fin"] and n["fecha_fin"] != "-") else None
                if (n_fin is None or f_ini.date() <= n_fin.date()) and (f_fin is None or f_fin.date() >= n_ini.date()):
                    fin_disp = n["fecha_fin"] if (n["fecha_fin"] and n["fecha_fin"] != "-") else "Abierta"
                    raise ValueError(
                        f"El agente de relevo legajo {legajo_reemplazo} no está disponible: "
                        f"posee una novedad activa propia (#{n['id']} - {n['novedad']}) del {n['fecha_inicio']} al {fin_disp}."
                    )

            # 2. Comprobar si ya está relevando otra novedad activa en el mismo tramo
            query_r = """SELECT nr.id, nr.novedad_id, nr.fecha_inicio, nr.fecha_fin, n.apellidos_nombres, n.turnos
                         FROM novedad_reemplazos nr
                         JOIN novedades n ON n.id = nr.novedad_id
                         WHERE nr.activo = 1 AND n.activo = 1 AND nr.legajo_reemplazo = ?"""
            params_r: List[Any] = [int(legajo_reemplazo)]
            if excluir_reemplazo_id:
                query_r += " AND nr.id != ?"
                params_r.append(int(excluir_reemplazo_id))
            if excluir_novedad_id:
                query_r += " AND nr.novedad_id != ?"
                params_r.append(int(excluir_novedad_id))

            relevos = conn.execute(query_r, params_r).fetchall()
            for r in relevos:
                r_ini = parsear_fecha(r["fecha_inicio"])
                if not r_ini:
                    continue
                r_fin = parsear_fecha(r["fecha_fin"]) if (r["fecha_fin"] and r["fecha_fin"] != "-") else None
                if (r_fin is None or f_ini.date() <= r_fin.date()) and (f_fin is None or f_fin.date() >= r_ini.date()):
                    fin_r = r["fecha_fin"] if (r["fecha_fin"] and r["fecha_fin"] != "-") else "Sin fecha fin"
                    raise ValueError(
                        f"El agente legajo {legajo_reemplazo} ya se encuentra cubriendo la novedad #{r['novedad_id']} "
                        f"(Titular: {r['apellidos_nombres']} - Turno: {r['turnos']}) en el período {r['fecha_inicio']} al {fin_r}."
                    )

    def crear_novedad(
        self,
        data: Dict[str, Any],
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        fields = (
            "legajo", "apellidos_nombres", "especialidad", "dotacion", "turnos",
            "franco", "novedad", "fecha_inicio", "fecha_fin", "referencia_estacion",
            "supervisor", "observaciones",
        )
        leg_val = int(str(data["legajo"]).strip())
        self.validar_novedad_superpuesta(leg_val, data.get("fecha_inicio"), data.get("fecha_fin"))

        win_usr = (windows_user or data.get("usuario_windows") or "").strip() or obtener_usuario_windows()
        registrado_en = data.get("registrado_en") or self.db.now()
        with self.db.write_transaction() as conn:
            columns = ("registrado_en",) + fields + ("usuario_windows", "usuario_id", "activo")
            placeholders = ", ".join("?" for _ in columns)
            values = (
                registrado_en,
                int(str(data["legajo"]).strip()),
                str(data.get("apellidos_nombres") or "").strip(),
                str(data.get("especialidad") or "").strip(),
                str(data.get("dotacion") or "").strip(),
                str(data.get("turnos") or "").strip(),
                str(data.get("franco") or "").strip(),
                str(data.get("novedad") or "").strip(),
                str(data.get("fecha_inicio") or "").strip(),
                str(data.get("fecha_fin") or "").strip(),
                str(data.get("referencia_estacion") or "").strip(),
                str(data.get("supervisor") or "").strip(),
                str(data.get("observaciones") or "").strip(),
                win_usr,
                user_id,
                1,
            )
            cur = conn.execute(f"INSERT INTO novedades ({', '.join(columns)}) VALUES ({placeholders})", values)
            record_id = cur.lastrowid
            after = dict(conn.execute("SELECT * FROM novedades WHERE id=?", (record_id,)).fetchone())
            self._audit(conn, user_id, win_usr, "crear", "novedad", record_id, None, after)
            self._enqueue_sync(conn, "novedades", record_id, "INSERT", after)
            return record_id

    def actualizar_novedad(
        self,
        record_id: int,
        data: Dict[str, Any],
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
        bypass_window: bool = False,
    ) -> None:
        fields = (
            "legajo", "apellidos_nombres", "especialidad", "dotacion", "turnos",
            "franco", "novedad", "fecha_inicio", "fecha_fin", "referencia_estacion",
            "supervisor", "observaciones",
        )
        leg_val = int(str(data["legajo"]).strip())
        self.validar_novedad_superpuesta(
            leg_val,
            data.get("fecha_inicio"),
            data.get("fecha_fin"),
            excluir_novedad_id=record_id,
        )
        win_usr = (windows_user or data.get("usuario_windows") or "").strip() or obtener_usuario_windows()
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM novedades WHERE id=?", (record_id,)).fetchone()
            if not before:
                raise ValueError("La novedad no existe.")
            if not bypass_window and not self.dentro_de_ventana(before["registrado_en"], "editar_horas"):
                raise ValueError("El registro superó el tiempo permitido para editar.")

            assignments = ", ".join(f"{field}=?" for field in fields) + ", usuario_windows=?"
            values = tuple(data[field] for field in fields) + (win_usr, record_id)
            conn.execute(f"UPDATE novedades SET {assignments} WHERE id=?", values)
            after = dict(conn.execute("SELECT * FROM novedades WHERE id=?", (record_id,)).fetchone())
            self._audit(conn, user_id, win_usr, "modificar", "novedad", record_id, dict(before), after)
            self._enqueue_sync(conn, "novedades", record_id, "UPDATE", after)

    def actualizar_fecha_fin_novedad(
        self,
        record_id: int,
        fecha_fin: Optional[str],
        nueva_observacion: Optional[str] = None,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        fecha_fin_str = (fecha_fin or "").strip()
        nueva_obs = (nueva_observacion or "").strip()
        if not fecha_fin_str and not nueva_obs:
            raise ValueError("Debe ingresar la fecha de fin o una observación.")

        val_fin = None
        if fecha_fin_str:
            val_fin = parsear_fecha(fecha_fin_str, "Fecha de Fin", obligatorio=True)

        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM novedades WHERE id=?", (record_id,)).fetchone()
            if not before:
                raise ValueError("La novedad no existe.")

            fecha_fin_anterior = (before["fecha_fin"] or "").strip()
            if fecha_fin_str:
                if fecha_fin_anterior and fecha_fin_anterior != "-":
                    raise ValueError("El registro ya cuenta con una fecha de fin.")
                fecha_inicio_str = (before["fecha_inicio"] or "").strip()
                if fecha_inicio_str:
                    val_ini = parsear_fecha(fecha_inicio_str, "Fecha de Inicio", obligatorio=False)
                    if val_ini and val_fin < val_ini:
                        raise ValueError("La fecha de fin no puede ser anterior a la fecha de inicio.")
                fecha_guardar = fecha_fin_str
            else:
                fecha_guardar = before["fecha_fin"]

            obs_existente = (before["observaciones"] or "").strip()
            if nueva_obs:
                obs_final = f"{obs_existente}\n{nueva_obs}".strip() if obs_existente else nueva_obs
            else:
                obs_final = before["observaciones"]

            conn.execute(
                "UPDATE novedades SET fecha_fin=?, observaciones=? WHERE id=?",
                (fecha_guardar, obs_final, record_id),
            )
            after = dict(conn.execute("SELECT * FROM novedades WHERE id=?", (record_id,)).fetchone())
            self._audit(conn, user_id, windows_user, "modificar", "novedad", record_id, dict(before), after)
            self._enqueue_sync(conn, "novedades", record_id, "UPDATE", after)

    def eliminar_novedad(
        self,
        record_id: int,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
        bypass_window: bool = False,
    ) -> None:
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM novedades WHERE id=?", (record_id,)).fetchone()
            if not before:
                raise ValueError("La novedad no existe.")
            if not bypass_window and not self.dentro_de_ventana(before["registrado_en"], "eliminar_horas"):
                raise ValueError("El registro superó el tiempo permitido para eliminar.")
            conn.execute("UPDATE novedades SET activo=0, sync_status='pending' WHERE id=?", (record_id,))
            after = dict(conn.execute("SELECT * FROM novedades WHERE id=?", (record_id,)).fetchone())
            self._audit(conn, user_id, windows_user, "eliminar", "novedad", record_id, dict(before), {"activo": 0})
            self._enqueue_sync(conn, "novedades", record_id, "UPDATE", after)

    # ---------------------------------------------------------
    # Cambios de Turno CRUD
    # ---------------------------------------------------------

    def listar_cambios(
        self,
        filtros: Optional[Dict[str, Any]] = None,
        incluir_inactivos: bool = False,
    ) -> List[Dict[str, Any]]:
        with self.db.read_connection() as conn:
            query = """SELECT id, registrado_en, legajo_1, apellidos_nombres_1, especialidad_1,
                              dotacion_1, turnos_1, franco_1, legajo_2, apellidos_nombres_2,
                              especialidad_2, dotacion_2, turnos_2, franco_2, fecha_cambio,
                              referencia_estacion, supervisor, observaciones, usuario_windows,
                              usuario_id, activo
                       FROM cambios_turno"""
            conds = []
            params = []
            if not incluir_inactivos:
                conds.append("activo = 1")

            if filtros:
                if filtros.get("legajo"):
                    conds.append("(legajo_1 = ? OR legajo_2 = ?)")
                    params.extend([int(filtros["legajo"]), int(filtros["legajo"])])
                if filtros.get("dotacion"):
                    conds.append("(LOWER(TRIM(dotacion_1)) = LOWER(TRIM(?)) OR LOWER(TRIM(dotacion_2)) = LOWER(TRIM(?)))")
                    params.extend([str(filtros["dotacion"]), str(filtros["dotacion"])])

            if conds:
                query += " WHERE " + " AND ".join(conds)
            query += " ORDER BY id DESC"

            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def obtener_cambio(self, record_id: int) -> Optional[Dict[str, Any]]:
        with self.db.read_connection() as conn:
            row = conn.execute("SELECT * FROM cambios_turno WHERE id=?", (record_id,)).fetchone()
            return dict(row) if row else None

    def crear_cambio(
        self,
        data: Dict[str, Any],
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        fields = (
            "legajo_1", "apellidos_nombres_1", "especialidad_1", "dotacion_1", "turnos_1", "franco_1",
            "legajo_2", "apellidos_nombres_2", "especialidad_2", "dotacion_2", "turnos_2", "franco_2",
            "fecha_cambio", "referencia_estacion", "supervisor", "observaciones",
        )
        win_usr = (windows_user or data.get("usuario_windows") or "").strip() or obtener_usuario_windows()
        registrado_en = data.get("registrado_en") or self.db.now()
        with self.db.write_transaction() as conn:
            columns = ("registrado_en",) + fields + ("usuario_windows", "usuario_id", "activo")
            placeholders = ", ".join("?" for _ in columns)
            values = (
                registrado_en,
                int(str(data["legajo_1"]).strip()),
                str(data.get("apellidos_nombres_1") or "").strip(),
                str(data.get("especialidad_1") or "").strip(),
                str(data.get("dotacion_1") or "").strip(),
                str(data.get("turnos_1") or "").strip(),
                str(data.get("franco_1") or "").strip(),
                int(str(data["legajo_2"]).strip()),
                str(data.get("apellidos_nombres_2") or "").strip(),
                str(data.get("especialidad_2") or "").strip(),
                str(data.get("dotacion_2") or "").strip(),
                str(data.get("turnos_2") or "").strip(),
                str(data.get("franco_2") or "").strip(),
                str(data.get("fecha_cambio") or "").strip(),
                str(data.get("referencia_estacion") or "").strip(),
                str(data.get("supervisor") or "").strip(),
                str(data.get("observaciones") or "").strip(),
                win_usr,
                user_id,
                1,
            )
            cur = conn.execute(f"INSERT INTO cambios_turno ({', '.join(columns)}) VALUES ({placeholders})", values)
            record_id = cur.lastrowid
            after = dict(conn.execute("SELECT * FROM cambios_turno WHERE id=?", (record_id,)).fetchone())
            self._audit(conn, user_id, win_usr, "crear", "cambio_turno", record_id, None, after)
            self._enqueue_sync(conn, "cambios_turno", record_id, "INSERT", after)
            return record_id

    def actualizar_cambio(
        self,
        record_id: int,
        data: Dict[str, Any],
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
        bypass_window: bool = False,
    ) -> None:
        fields = (
            "legajo_1", "apellidos_nombres_1", "especialidad_1", "dotacion_1", "turnos_1", "franco_1",
            "legajo_2", "apellidos_nombres_2", "especialidad_2", "dotacion_2", "turnos_2", "franco_2",
            "fecha_cambio", "referencia_estacion", "supervisor", "observaciones",
        )
        win_usr = (windows_user or data.get("usuario_windows") or "").strip() or obtener_usuario_windows()
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM cambios_turno WHERE id=?", (record_id,)).fetchone()
            if not before:
                raise ValueError("El cambio de turno no existe.")
            if not bypass_window and not self.dentro_de_ventana(before["registrado_en"], "editar_horas"):
                raise ValueError("El registro superó el tiempo permitido para editar.")

            assignments = ", ".join(f"{field}=?" for field in fields) + ", usuario_windows=?"
            values = tuple(data[field] for field in fields) + (win_usr, record_id)
            conn.execute(f"UPDATE cambios_turno SET {assignments} WHERE id=?", values)
            after = dict(conn.execute("SELECT * FROM cambios_turno WHERE id=?", (record_id,)).fetchone())
            self._audit(conn, user_id, win_usr, "modificar", "cambio_turno", record_id, dict(before), after)
            self._enqueue_sync(conn, "cambios_turno", record_id, "UPDATE", after)

    def eliminar_cambio(
        self,
        record_id: int,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
        bypass_window: bool = False,
    ) -> None:
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM cambios_turno WHERE id=?", (record_id,)).fetchone()
            if not before:
                raise ValueError("La cambio de turno no existe.")
            if not bypass_window and not self.dentro_de_ventana(before["registrado_en"], "eliminar_horas"):
                raise ValueError("El registro superó el tiempo permitido para eliminar.")
            conn.execute("UPDATE cambios_turno SET activo=0, sync_status='pending' WHERE id=?", (record_id,))
            after = dict(conn.execute("SELECT * FROM cambios_turno WHERE id=?", (record_id,)).fetchone())
            self._audit(conn, user_id, windows_user, "eliminar", "cambio_turno", record_id, dict(before), {"activo": 0})
            self._enqueue_sync(conn, "cambios_turno", record_id, "UPDATE", after)

    # ---------------------------------------------------------
    # Reemplazos y Personal de Ciclo
    # ---------------------------------------------------------

    def obtener_candidatos_ciclo(
        self,
        dotacion: str,
        especialidad: str,
        excluir_legajo: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        dot_norm = (dotacion or "").strip()
        esp_norm = (especialidad or "").strip()
        if not dot_norm or not esp_norm:
            return []
        with self.db.read_connection() as conn:
            query = """
                SELECT legajo, apellidos_nombres, especialidad, dotacion, turnos, franco, franco_horario
                FROM empleados
                WHERE activo = 1
                  AND LOWER(TRIM(dotacion)) = LOWER(TRIM(?))
                  AND LOWER(TRIM(especialidad)) = LOWER(TRIM(?))
                  AND UPPER(TRIM(turnos)) LIKE 'CICLO%'
            """
            params = [dot_norm, esp_norm]
            if excluir_legajo is not None:
                try:
                    query += " AND legajo != ?"
                    params.append(int(excluir_legajo))
                except (ValueError, TypeError):
                    pass
            query += " ORDER BY apellidos_nombres"
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def listar_reemplazos_por_novedad(self, novedad_id: int) -> List[Dict[str, Any]]:
        with self.db.read_connection() as conn:
            rows = conn.execute(
                """SELECT id, novedad_id, legajo_reemplazo, apellidos_nombres_reemplazo,
                          turnos_reemplazo, franco_reemplazo, fecha_inicio, fecha_fin,
                          usuario_windows, usuario_id, creado_en
                   FROM novedad_reemplazos
                   WHERE novedad_id = ? AND activo = 1
                   ORDER BY id ASC""",
                (novedad_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def _rango_fechas_reemplazo(
        self,
        fecha_ini_str: str,
        fecha_fin_str: Optional[str],
        fecha_fin_nov_str: Optional[str] = None,
    ) -> Tuple[Optional[date], Optional[date]]:
        try:
            dt_ini = parsear_fecha(fecha_ini_str, "Fecha inicio reemplazo", obligatorio=False)
        except Exception:
            dt_ini = None
        if not dt_ini:
            return None, None
        f_ini = dt_ini.date()

        f_fin = None
        raw_fin = (fecha_fin_str or "").strip()
        if raw_fin and raw_fin != "-":
            try:
                dt_fin = parsear_fecha(raw_fin, "Fecha fin reemplazo", obligatorio=False)
                if dt_fin:
                    f_fin = dt_fin.date()
            except Exception:
                f_fin = None
        elif fecha_fin_nov_str and str(fecha_fin_nov_str).strip() not in ("", "-"):
            try:
                dt_fin_nov = parsear_fecha(fecha_fin_nov_str, "Fecha fin novedad", obligatorio=False)
                if dt_fin_nov:
                    f_fin = dt_fin_nov.date()
            except Exception:
                f_fin = None
        return f_ini, f_fin

    def _validar_disponibilidad_ciclo_conn(
        self,
        conn: sqlite3.Connection,
        legajo_reemplazo: int,
        fecha_inicio: str,
        fecha_fin: Optional[str],
        excluir_novedad_id: Optional[int] = None,
    ) -> None:
        dt_ini = parsear_fecha(fecha_inicio, "Fecha inicio reemplazo", obligatorio=True)
        dt_fin = parsear_fecha(fecha_fin, "Fecha fin reemplazo", obligatorio=False)
        t_ini = dt_ini.date()
        t_fin = dt_fin.date() if dt_fin else date.max

        query = """
            SELECT nr.*, n.legajo AS legajo_titular, n.apellidos_nombres AS apellidos_titular,
                   n.novedad AS tipo_novedad, n.fecha_fin AS fecha_fin_novedad
            FROM novedad_reemplazos nr
            JOIN novedades n ON n.id = nr.novedad_id
            WHERE nr.legajo_reemplazo = ? AND nr.activo = 1 AND n.activo = 1
        """
        params = [int(legajo_reemplazo)]
        if excluir_novedad_id is not None:
            query += " AND nr.novedad_id != ?"
            params.append(int(excluir_novedad_id))

        existentes = conn.execute(query, params).fetchall()
        for row in existentes:
            r_ini, r_fin_opt = self._rango_fechas_reemplazo(row["fecha_inicio"], row["fecha_fin"], row["fecha_fin_novedad"])
            if not r_ini:
                continue
            r_fin = r_fin_opt if r_fin_opt is not None else date.max
            if t_ini <= r_fin and r_ini <= t_fin:
                hasta_txt = row["fecha_fin"] or row["fecha_fin_novedad"] or "sin fecha fin"
                raise ValueError(
                    f"El personal de ciclo {row['apellidos_nombres_reemplazo']} (Leg. {legajo_reemplazo}) "
                    f"ya reemplaza a {row['apellidos_titular']} (Novedad #{row['novedad_id']}) "
                    f"del {row['fecha_inicio']} al {hasta_txt}."
                )

    def validar_disponibilidad_ciclo(
        self,
        legajo_reemplazo: int,
        fecha_inicio: str,
        fecha_fin: Optional[str],
        excluir_novedad_id: Optional[int] = None,
    ) -> None:
        with self.db.read_connection() as conn:
            self._validar_disponibilidad_ciclo_conn(
                conn, legajo_reemplazo, fecha_inicio, fecha_fin, excluir_novedad_id=excluir_novedad_id
            )

    def guardar_reemplazos_novedad(
        self,
        novedad_id: int,
        tramos: List[Dict[str, Any]],
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        tramos_norm = []
        with self.db.write_transaction() as conn:
            nov = conn.execute("SELECT * FROM novedades WHERE id = ?", (novedad_id,)).fetchone()
            if not nov:
                raise ValueError("La novedad no existe.")

            fecha_fin_nov = (nov["fecha_fin"] or "").strip()
            if fecha_fin_nov == "-":
                fecha_fin_nov = ""

            for tramo in (tramos or []):
                leg = int(str(tramo.get("legajo_reemplazo") or tramo.get("legajo")).strip())
                f_ini = str(tramo.get("fecha_inicio") or nov["fecha_inicio"] or "").strip()
                f_fin = str(tramo.get("fecha_fin") or "").strip()
                if f_fin == "-":
                    f_fin = ""
                if not f_fin and fecha_fin_nov and len(tramos) == 1:
                    f_fin = fecha_fin_nov

                emp = conn.execute(
                    "SELECT legajo, apellidos_nombres, especialidad, dotacion, turnos, franco FROM empleados WHERE legajo = ?",
                    (leg,),
                ).fetchone()

                nombre = str(tramo.get("apellidos_nombres_reemplazo") or tramo.get("apellidos_nombres") or (emp["apellidos_nombres"] if emp else "")).strip()
                turno_r = str(tramo.get("turnos_reemplazo") or tramo.get("turnos") or (emp["turnos"] if emp else "")).strip()
                franco_r = str(tramo.get("franco_reemplazo") or tramo.get("franco") or (emp["franco"] if emp else "")).strip()

                if emp:
                    if (emp["dotacion"] or "").strip().casefold() != (nov["dotacion"] or "").strip().casefold():
                        raise ValueError(f"El reemplazo {nombre} (Leg. {leg}) no pertenece a la misma dotación ({nov['dotacion']}).")
                    if (emp["especialidad"] or "").strip().casefold() != (nov["especialidad"] or "").strip().casefold():
                        raise ValueError(f"El reemplazo {nombre} (Leg. {leg}) no tiene la misma especialidad ({nov['especialidad']}).")
                    if not es_turno_ciclo(emp["turnos"]):
                        raise ValueError(f"El reemplazo {nombre} (Leg. {leg}) no tiene un turno que comience con CICLO.")
                elif not es_turno_ciclo(turno_r):
                    raise ValueError(f"El reemplazo {nombre} (Leg. {leg}) debe pertenecer a un turno que comience con CICLO.")

                tramos_norm.append({
                    "legajo_reemplazo": leg,
                    "apellidos_nombres_reemplazo": nombre,
                    "turnos_reemplazo": turno_r,
                    "franco_reemplazo": franco_r,
                    "fecha_inicio": f_ini,
                    "fecha_fin": f_fin or None,
                })

            es_valido, err = validar_tramos_reemplazo(
                nov["fecha_inicio"], fecha_fin_nov or None, nov["turnos"], tramos_norm
            )
            if not es_valido:
                raise ValueError(err)

            for t in tramos_norm:
                self._validar_disponibilidad_ciclo_conn(
                    conn, t["legajo_reemplazo"], t["fecha_inicio"], t["fecha_fin"], excluir_novedad_id=novedad_id
                )

            anteriores = [
                dict(r) for r in conn.execute(
                    "SELECT legajo_reemplazo, apellidos_nombres_reemplazo, fecha_inicio, fecha_fin FROM novedad_reemplazos WHERE novedad_id=? AND activo=1",
                    (novedad_id,),
                ).fetchall()
            ]
            reemplazos_previos = [
                r["id"] for r in conn.execute(
                    "SELECT id FROM novedad_reemplazos WHERE novedad_id=? AND activo=1",
                    (novedad_id,),
                ).fetchall()
            ]
            conn.execute("UPDATE novedad_reemplazos SET activo = 0, sync_status = 'pending' WHERE novedad_id = ? AND activo = 1", (novedad_id,))
            for rp_id in reemplazos_previos:
                rp_row = dict(conn.execute("SELECT * FROM novedad_reemplazos WHERE id=?", (rp_id,)).fetchone())
                self._enqueue_sync(conn, "novedad_reemplazos", rp_id, "UPDATE", rp_row)

            ahora = self.db.now()
            win_usr = (windows_user or "").strip() or obtener_usuario_windows()
            for t in tramos_norm:
                cur = conn.execute(
                    """INSERT INTO novedad_reemplazos
                       (novedad_id, legajo_reemplazo, apellidos_nombres_reemplazo, turnos_reemplazo,
                        franco_reemplazo, fecha_inicio, fecha_fin, usuario_windows, usuario_id, creado_en, activo, sync_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'pending')""",
                    (
                        novedad_id, t["legajo_reemplazo"], t["apellidos_nombres_reemplazo"],
                        t["turnos_reemplazo"], t["franco_reemplazo"], t["fecha_inicio"],
                        t["fecha_fin"], win_usr, user_id, ahora,
                    ),
                )
                r_id = cur.lastrowid
                after = dict(conn.execute("SELECT * FROM novedad_reemplazos WHERE id=?", (r_id,)).fetchone())
                self._enqueue_sync(conn, "novedad_reemplazos", r_id, "INSERT", after)

            self._audit(
                conn, user_id, win_usr, "reemplazos", "novedad", novedad_id,
                {"reemplazos": anteriores}, {"reemplazos": tramos_norm},
            )

    def desactivar_reemplazo(
        self,
        reemplazo_id: int,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        """Desactiva un tramo de relevo individual en novedad_reemplazos."""
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM novedad_reemplazos WHERE id = ?", (int(reemplazo_id),)).fetchone()
            if not before:
                return
            conn.execute("UPDATE novedad_reemplazos SET activo = 0, sync_status = 'pending' WHERE id = ?", (int(reemplazo_id),))
            after = dict(conn.execute("SELECT * FROM novedad_reemplazos WHERE id = ?", (int(reemplazo_id),)).fetchone())
            self._audit(conn, user_id, windows_user, "eliminar", "novedad_reemplazo", int(reemplazo_id), dict(before), {"activo": 0})
            self._enqueue_sync(conn, "novedad_reemplazos", int(reemplazo_id), "UPDATE", after)

    def asignar_reemplazo_rapido(
        self,
        novedad_id: int,
        legajo_reemplazo: int,
        fecha_inicio: str,
        fecha_fin: Optional[str] = None,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        """Asigna un agente de ciclo como relevo de una novedad para una fecha o rango."""
        self.validar_reemplazo_disponible(
            int(legajo_reemplazo),
            fecha_inicio,
            fecha_fin,
            excluir_novedad_id=novedad_id,
        )
        with self.db.write_transaction() as conn:
            emp = conn.execute(
                "SELECT legajo, apellidos_nombres, especialidad, dotacion, turnos, franco FROM empleados WHERE legajo = ?",
                (int(legajo_reemplazo),),
            ).fetchone()
            if not emp:
                raise ValueError(f"No existe el empleado con legajo {legajo_reemplazo}")

            f_fin_val = fecha_fin if fecha_fin and str(fecha_fin).strip() not in ("", "-", "None") else None
            ahora = self.db.now()
            cur = conn.execute(
                """INSERT INTO novedad_reemplazos
                   (novedad_id, legajo_reemplazo, apellidos_nombres_reemplazo, turnos_reemplazo,
                    franco_reemplazo, fecha_inicio, fecha_fin, usuario_windows, usuario_id, creado_en, activo)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (
                    int(novedad_id), int(emp["legajo"]), emp["apellidos_nombres"],
                    emp["turnos"], emp["franco"], str(fecha_inicio).strip(),
                    f_fin_val, windows_user, user_id, ahora,
                ),
            )
            r_id = cur.lastrowid
            after = dict(conn.execute("SELECT * FROM novedad_reemplazos WHERE id=?", (r_id,)).fetchone())
            self._audit(conn, user_id, windows_user, "crear", "novedad_reemplazo", r_id, None, after)
            self._enqueue_sync(conn, "novedad_reemplazos", r_id, "INSERT", after)
            return r_id

    def calcular_estado_cobertura(
        self,
        fila_novedad: Union[int, Dict[str, Any], sqlite3.Row],
        tramos_o_fecha_ini: Any = None,
        fecha_fin: Optional[str] = None,
        turnos: Optional[str] = None,
    ) -> Union[Dict[str, str], Tuple[str, str]]:
        """Calcula el estado de cobertura ('Sí', 'Parcial', 'No', 'N/A (Ciclo)') y textos descriptivos."""
        modo_tupla = isinstance(fila_novedad, int) or (turnos is not None or (fecha_fin is not None and not isinstance(tramos_o_fecha_ini, list)))
        if modo_tupla:
            novedad_id = int(fila_novedad)  # type: ignore
            tramos = self.listar_reemplazos_por_novedad(novedad_id)
            fila_dict = {
                "id": novedad_id,
                "fecha_inicio": tramos_o_fecha_ini,
                "fecha_fin": fecha_fin,
                "turnos": turnos or "",
            }
        else:
            fila_dict = dict(fila_novedad)  # type: ignore
            tramos = tramos_o_fecha_ini or []

        turno_titular = fila_dict.get("turnos") or ""
        if es_turno_ciclo(turno_titular):
            res = {
                "estado": "N/A (Ciclo)",
                "resumen": "N/A (Ciclo)",
                "detalle": "Personal de CICLO (sin reemplazo)",
            }
            return (res["estado"], res["detalle"]) if modo_tupla else res

        if not tramos:
            res = {
                "estado": "No",
                "resumen": "No",
                "detalle": "Sin reemplazo asignado",
            }
            return (res["estado"], res["detalle"]) if modo_tupla else res

        f_ini_nov_str = fila_dict.get("fecha_inicio") or ""
        f_fin_nov_str = (fila_dict.get("fecha_fin") or "").strip()
        if f_fin_nov_str == "-":
            f_fin_nov_str = ""

        try:
            dt_ini_nov = parsear_fecha(f_ini_nov_str, "Inicio", obligatorio=False)
            f_ini_nov = dt_ini_nov.date() if dt_ini_nov else None
        except Exception:
            f_ini_nov = None

        try:
            dt_fin_nov = parsear_fecha(f_fin_nov_str, "Fin", obligatorio=False) if f_fin_nov_str else None
            f_fin_nov = dt_fin_nov.date() if dt_fin_nov else None
        except Exception:
            f_fin_nov = None

        intervalos = []
        nombres_vistos = []
        partes_detalle = []
        for t in tramos:
            leg = t["legajo_reemplazo"]
            nom = t["apellidos_nombres_reemplazo"]
            etiqueta = f"{nom} ({leg})"
            if etiqueta not in nombres_vistos:
                nombres_vistos.append(etiqueta)
            hasta_txt = t["fecha_fin"] or "abierto"
            partes_detalle.append(f"{leg} - {nom} [{t['fecha_inicio']} a {hasta_txt}]")
            r_ini, r_fin = self._rango_fechas_reemplazo(t["fecha_inicio"], t["fecha_fin"], f_fin_nov_str or None)
            if r_ini:
                intervalos.append((r_ini, r_fin))

        intervalos.sort(key=lambda item: (item[0], item[1] or date.max))
        cubierto_total = False
        if f_ini_nov and intervalos:
            primero_ini, _ = intervalos[0]
            if primero_ini <= f_ini_nov:
                cursor = intervalos[0][1]
                sin_huecos = True
                for sig_ini, sig_fin in intervalos[1:]:
                    if cursor is None:
                        break
                    if sig_ini > cursor + timedelta(days=1):
                        sin_huecos = False
                        break
                    if sig_fin is None or sig_fin > cursor:
                        cursor = sig_fin
                if sin_huecos:
                    if f_fin_nov is not None:
                        if cursor is None or cursor >= f_fin_nov:
                            cubierto_total = True
                    else:
                        if cursor is None:
                            cubierto_total = True

        estado = "Sí" if cubierto_total else "Parcial"
        resumen = f"{estado}: {', '.join(nombres_vistos)}"
        detalle = " | ".join(partes_detalle)
        res = {
            "estado": estado,
            "resumen": resumen,
            "detalle": detalle,
        }
        return (res["estado"], res["detalle"]) if modo_tupla else res

    def obtener_mapa_cobertura_novedades(self) -> Dict[int, Dict[str, str]]:
        """Devuelve un diccionario {novedad_id: info_cobertura} para todas las novedades activas."""
        with self.db.read_connection() as conn:
            novedades = conn.execute(
                "SELECT id, turnos, fecha_inicio, fecha_fin FROM novedades WHERE activo = 1"
            ).fetchall()
            reemplazos = conn.execute(
                """SELECT id, novedad_id, legajo_reemplazo, apellidos_nombres_reemplazo,
                          turnos_reemplazo, franco_reemplazo, fecha_inicio, fecha_fin
                   FROM novedad_reemplazos WHERE activo = 1 ORDER BY id ASC"""
            ).fetchall()

        por_novedad: Dict[int, List[Dict[str, Any]]] = {}
        for r in reemplazos:
            por_novedad.setdefault(r["novedad_id"], []).append(dict(r))

        mapa: Dict[int, Dict[str, str]] = {}
        for nov in novedades:
            res = self.calcular_estado_cobertura(nov, por_novedad.get(nov["id"], []))
            if isinstance(res, dict):
                mapa[nov["id"]] = res
        return mapa

    def obtener_turnos_sin_cubrir(
        self,
        fecha_consulta: str,
        dotacion: Optional[str] = None,
        especialidad: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        f_sol = parsear_fecha(fecha_consulta, "Fecha a consultar", obligatorio=True)
        with self.db.read_connection() as conn:
            query = """
                SELECT n.id, n.registrado_en, n.legajo, n.apellidos_nombres, n.especialidad,
                       n.dotacion, n.turnos, n.franco, n.novedad, n.fecha_inicio, n.fecha_fin,
                       n.referencia_estacion, n.supervisor, n.observaciones
                FROM novedades n
                JOIN tipos_novedad t ON LOWER(n.novedad) = LOWER(t.nombre)
                WHERE n.activo = 1 AND t.exportar_descubierto = 1
            """
            params = []
            if dotacion and dotacion != "Todas":
                query += " AND n.dotacion = ?"
                params.append(dotacion)
            query += " ORDER BY n.dotacion, n.turnos, n.apellidos_nombres"
            filas = conn.execute(query, params).fetchall()
            if especialidad and especialidad != "Todas":
                filas = [f for f in filas if _coincide_especialidad(f["especialidad"], especialidad)]

            reemplazos_rows = conn.execute(
                """SELECT id, novedad_id, legajo_reemplazo, apellidos_nombres_reemplazo,
                          turnos_reemplazo, franco_reemplazo, fecha_inicio, fecha_fin
                   FROM novedad_reemplazos WHERE activo = 1 ORDER BY id ASC"""
            ).fetchall()

        reemplazos_por_nov: Dict[int, List[Dict[str, Any]]] = {}
        for r in reemplazos_rows:
            reemplazos_por_nov.setdefault(r["novedad_id"], []).append(dict(r))

        wd_sol = f_sol.weekday()
        reg_sol = "H" if wd_sol < 5 else ("S" if wd_sol == 5 else "D")
        turnos_map = self.listar_turnos_itinerario_map(regimen=reg_sol)
        from src.domain.validators import resolver_jornada_itinerario

        descubiertos = []
        for fila in filas:
            try:
                f_ini = parsear_fecha(fila["fecha_inicio"], "Fecha de Inicio", obligatorio=False)
            except Exception:
                f_ini = None
            if not f_ini:
                continue

            fecha_fin_raw = (fila["fecha_fin"] or "").strip()
            f_fin = None
            if fecha_fin_raw and fecha_fin_raw != "-":
                try:
                    f_fin = parsear_fecha(fecha_fin_raw, "Fecha de Fin", obligatorio=False)
                except Exception:
                    f_fin = None

            if f_ini.date() <= f_sol.date():
                if f_fin is None or f_sol.date() <= f_fin.date():
                    item = dict(fila)
                    tramos = reemplazos_por_nov.get(fila["id"], [])
                    reemplazo_hoy = None
                    for t in tramos:
                        r_ini, r_fin_opt = self._rango_fechas_reemplazo(t["fecha_inicio"], t["fecha_fin"], fecha_fin_raw or None)
                        if r_ini and r_ini <= f_sol.date() and (r_fin_opt is None or f_sol.date() <= r_fin_opt):
                            reemplazo_hoy = t
                            break

                    turno_base_titular = item.get("turnos") or ""
                    franco_titular = item.get("franco") or "Domingo"
                    fecha_iso_sol = f_sol.strftime("%Y-%m-%d")

                    turno_reemplazado = "-"
                    turno_a_resolver = turno_base_titular
                    franco_a_resolver = franco_titular

                    if es_turno_ciclo(turno_base_titular):
                        # Verificar si este personal de ciclo estaba reemplazando una novedad en esta fecha
                        tit_cubierto = None
                        for r in reemplazos_rows:
                            if int(r["legajo_reemplazo"]) == int(fila["legajo"]):
                                r_ini_c, r_fin_c = self._rango_fechas_reemplazo(r["fecha_inicio"], r["fecha_fin"], None)
                                if r_ini_c and r_ini_c <= f_sol.date() and (r_fin_c is None or f_sol.date() <= r_fin_c):
                                    tit_cubierto = r
                                    break
                        if tit_cubierto:
                            with self.db.read_connection() as c_nov:
                                row_tit = c_nov.execute("SELECT legajo, apellidos_nombres, turnos, franco FROM novedades WHERE id=?", (tit_cubierto["novedad_id"],)).fetchone()
                                if row_tit:
                                    turno_a_resolver = row_tit["turnos"]
                                    franco_a_resolver = row_tit["franco"] or "Domingo"
                                    turno_reemplazado = f"{row_tit['turnos']} (Titular: {row_tit['apellidos_nombres']})"
                                else:
                                    turno_reemplazado = f"Novedad #{tit_cubierto['novedad_id']}"
                            item["cubierto"] = "No"
                            item["reemplazo"] = ""
                        else:
                            item["cubierto"] = "N/A (Ciclo)"
                            item["reemplazo"] = ""
                            turno_reemplazado = "Sin asignación"
                    elif reemplazo_hoy:
                        item["cubierto"] = "Sí"
                        item["reemplazo"] = f"{reemplazo_hoy['legajo_reemplazo']} - {reemplazo_hoy['apellidos_nombres_reemplazo']} ({reemplazo_hoy['turnos_reemplazo']})"
                        turno_reemplazado = f"Cubierto por Leg. {reemplazo_hoy['legajo_reemplazo']} ({reemplazo_hoy['turnos_reemplazo']})"
                    else:
                        item["cubierto"] = "No"
                        item["reemplazo"] = ""
                        turno_reemplazado = "-"

                    # Resolver turno del día, hora toma y hora deja
                    try:
                        res_jor = resolver_jornada_itinerario(
                            turno_base=turno_a_resolver,
                            fecha_target=fecha_iso_sol,
                            franco_agente=franco_a_resolver,
                            turnos_dict=turnos_map,
                        )
                        if res_jor.get("es_franco"):
                            item["turno_dia"] = "FRANCO"
                            item["hora_toma"] = "-"
                            item["hora_deja"] = "-"
                        else:
                            item["turno_dia"] = res_jor.get("codigo_turno") or turno_a_resolver
                            item["hora_toma"] = res_jor.get("toma") or "-"
                            item["hora_deja"] = res_jor.get("deje") or "-"
                    except Exception:
                        item["turno_dia"] = turno_a_resolver
                        item["hora_toma"] = "-"
                        item["hora_deja"] = "-"

                    if es_turno_ciclo(turno_base_titular) and tit_cubierto and "row_tit" in locals() and row_tit:
                        item["turno_reemplazado"] = f"{row_tit['turnos']} (Día {item['turno_dia']}) - {row_tit['apellidos_nombres']}"
                    else:
                        item["turno_reemplazado"] = turno_reemplazado

                    info_cob = self.calcular_estado_cobertura(fila, tramos)
                    if isinstance(info_cob, dict):
                        item["cobertura_resumen"] = info_cob["resumen"]
                        item["reemplazos_detalle"] = info_cob["detalle"]
                    descubiertos.append(item)

        # Ordenar por hora_toma cronológicamente ascendente (francos o sin hora al final)
        def _hora_toma_sort_key(it: Dict[str, Any]):
            ht = str(it.get("hora_toma") or "").strip()
            if not ht or ht == "-":
                return (1, "99:99", str(it.get("dotacion") or ""), str(it.get("turnos") or ""))
            return (0, ht, str(it.get("dotacion") or ""), str(it.get("turnos") or ""))

        descubiertos.sort(key=_hora_toma_sort_key)
        return descubiertos

    def obtener_estado_personal_ciclo(
        self,
        fecha_consulta: str,
        dotacion: Optional[str] = None,
        especialidad: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        f_sol = parsear_fecha(fecha_consulta, "Fecha de consulta", obligatorio=True).date()
        with self.db.read_connection() as conn:
            query_emp = """
                SELECT legajo, apellidos_nombres, especialidad, dotacion, turnos, franco, franco_horario
                FROM empleados
                WHERE activo = 1 AND UPPER(TRIM(turnos)) LIKE 'CICLO%'
            """
            params_emp = []
            if dotacion and dotacion != "Todas":
                query_emp += " AND LOWER(TRIM(dotacion)) = LOWER(TRIM(?))"
                params_emp.append(dotacion)
            query_emp += " ORDER BY dotacion, apellidos_nombres"
            empleados_ciclo = [dict(r) for r in conn.execute(query_emp, params_emp).fetchall()]
            if especialidad and especialidad != "Todas":
                empleados_ciclo = [
                    emp for emp in empleados_ciclo
                    if _coincide_especialidad(emp.get("especialidad"), especialidad)
                ]

            reemplazos_activos = conn.execute(
                """SELECT nr.*, n.legajo AS legajo_titular, n.apellidos_nombres AS apellidos_titular,
                          n.turnos AS turno_titular, n.novedad AS tipo_novedad,
                          n.fecha_fin AS fecha_fin_novedad, n.dotacion AS dotacion_titular
                   FROM novedad_reemplazos nr
                   JOIN novedades n ON n.id = nr.novedad_id
                   WHERE nr.activo = 1 AND n.activo = 1"""
            ).fetchall()

            novedades_propias_rows = conn.execute(
                """SELECT id, legajo, novedad, fecha_inicio, fecha_fin, observaciones
                   FROM novedades
                   WHERE activo = 1
                   ORDER BY id DESC"""
            ).fetchall()

        con_novedad_por_legajo: Dict[int, Dict[str, Any]] = {}
        for np in novedades_propias_rows:
            np_ini, np_fin_opt = self._rango_fechas_reemplazo(np["fecha_inicio"], np["fecha_fin"], None)
            if np_ini and np_ini <= f_sol and (np_fin_opt is None or f_sol <= np_fin_opt):
                leg_np = int(np["legajo"])
                if leg_np not in con_novedad_por_legajo:
                    con_novedad_por_legajo[leg_np] = dict(np)

        ocupados_por_legajo = {}
        for r in reemplazos_activos:
            r_ini, r_fin_opt = self._rango_fechas_reemplazo(r["fecha_inicio"], r["fecha_fin"], r["fecha_fin_novedad"])
            if r_ini and r_ini <= f_sol and (r_fin_opt is None or f_sol <= r_fin_opt):
                ocupados_por_legajo[int(r["legajo_reemplazo"])] = dict(r)

        libres = []
        ocupados = []
        con_novedad = []
        for emp in empleados_ciclo:
            leg = int(emp["legajo"])
            nov_propia = con_novedad_por_legajo.get(leg)
            if nov_propia:
                item_nov = dict(emp)
                item_nov["novedad_propia_id"] = nov_propia["id"]
                item_nov["novedad_propia"] = nov_propia["novedad"]
                item_nov["novedad_fecha_inicio"] = nov_propia["fecha_inicio"]
                item_nov["novedad_fecha_fin"] = nov_propia["fecha_fin"] or "-"
                con_novedad.append(item_nov)
                continue

            asignacion = ocupados_por_legajo.get(leg)
            if asignacion:
                item = dict(emp)
                item["reemplazo_id"] = asignacion["id"]
                item["novedad_id"] = asignacion["novedad_id"]
                item["legajo_titular"] = asignacion["legajo_titular"]
                item["apellidos_titular"] = asignacion["apellidos_titular"]
                item["reemplazando_legajo"] = asignacion["legajo_titular"]
                item["reemplazando_nombre"] = asignacion["apellidos_titular"]
                item["reemplaza_a"] = f"{asignacion['legajo_titular']} - {asignacion['apellidos_titular']}"
                item["turno_titular"] = asignacion["turno_titular"]
                item["tipo_novedad"] = asignacion["tipo_novedad"]
                item["novedad_titular"] = asignacion["tipo_novedad"]
                item["fecha_inicio_relevo"] = asignacion["fecha_inicio"]
                item["fecha_fin_relevo"] = asignacion["fecha_fin"] or asignacion["fecha_fin_novedad"] or "-"
                item["reemplazo_desde"] = asignacion["fecha_inicio"]
                item["reemplazo_hasta"] = asignacion["fecha_fin"] or asignacion["fecha_fin_novedad"] or "Sin fecha fin"
                ocupados.append(item)
            else:
                libres.append(dict(emp))

        return {"libres": libres, "ocupados": ocupados, "con_novedad": con_novedad}

    def guardar_jornada_ciclo(
        self,
        legajo: int,
        fecha: str,
        toma: Optional[str] = None,
        deja: Optional[str] = None,
        franco_anticipado: int = 0,
        observaciones: Optional[str] = None,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        fecha_obj = parsear_fecha(fecha, "Fecha", obligatorio=True).date()
        fecha_iso = fecha_obj.strftime("%Y-%m-%d")
        toma_str = (toma or "").strip()
        deja_str = (deja or "").strip()
        obs_str = (observaciones or "").strip()
        fa_val = 1 if bool(franco_anticipado) else 0

        for val, campo in ((toma_str, "Toma"), (deja_str, "Deja")):
            if val:
                try:
                    partes = val.split(":")
                    if len(partes) != 2:
                        raise ValueError()
                    h, m = int(partes[0]), int(partes[1])
                    if not (0 <= h <= 23 and 0 <= m <= 59):
                        raise ValueError()
                except Exception:
                    raise ValueError(f"El horario de {campo} debe tener formato HH:MM (ej. 06:00).")

        with self.db.write_transaction() as conn:
            before = conn.execute(
                "SELECT * FROM ciclo_jornadas WHERE legajo=? AND fecha=?",
                (int(legajo), fecha_iso),
            ).fetchone()
            conn.execute(
                """INSERT INTO ciclo_jornadas
                   (legajo, fecha, toma, deja, franco_anticipado, observaciones, actualizado_en, usuario_windows, usuario_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(legajo, fecha) DO UPDATE SET
                       toma=excluded.toma,
                       deja=excluded.deja,
                       franco_anticipado=excluded.franco_anticipado,
                       observaciones=excluded.observaciones,
                       actualizado_en=excluded.actualizado_en,
                       usuario_windows=excluded.usuario_windows,
                       usuario_id=excluded.usuario_id""",
                (
                    int(legajo), fecha_iso, toma_str or None, deja_str or None, fa_val,
                    obs_str or None, self.db.now(), windows_user, user_id,
                ),
            )
            after = dict(conn.execute(
                "SELECT * FROM ciclo_jornadas WHERE legajo=? AND fecha=?",
                (int(legajo), fecha_iso),
            ).fetchone())
            self._audit(
                conn, user_id, windows_user,
                "modificar" if before else "crear",
                "ciclo_jornada",
                after["id"] if after else None,
                dict(before) if before else None,
                after,
            )
            self._enqueue_sync(conn, "ciclo_jornadas", after["id"], "UPSERT", after)

    def obtener_jornadas_ciclo_rango(
        self,
        legajo: int,
        fecha_desde: date,
        fecha_hasta: date,
    ) -> Dict[str, Dict[str, Any]]:
        with self.db.read_connection() as conn:
            rows = conn.execute(
                """SELECT id, legajo, fecha, toma, deja, franco_anticipado, observaciones
                   FROM ciclo_jornadas
                   WHERE legajo=? AND fecha BETWEEN ? AND ?""",
                (int(legajo), fecha_desde.strftime("%Y-%m-%d"), fecha_hasta.strftime("%Y-%m-%d")),
            ).fetchall()
            return {r["fecha"]: dict(r) for r in rows}

    def obtener_calendario_mensual_ciclo(
        self,
        legajo_ciclo: int,
        anio: int,
        mes: int,
    ) -> List[Dict[str, Any]]:
        anio = int(anio)
        mes = int(mes)
        _, num_dias = calendar.monthrange(anio, mes)

        with self.db.read_connection() as conn:
            emp = conn.execute(
                "SELECT legajo, apellidos_nombres, especialidad, dotacion, turnos, franco, franco_horario, jornada_horas FROM empleados WHERE legajo = ?",
                (int(legajo_ciclo),),
            ).fetchone()
            reemplazos = conn.execute(
                """SELECT nr.*, n.legajo AS legajo_titular, n.apellidos_nombres AS apellidos_titular,
                          n.turnos AS turno_titular, n.franco AS franco_titular, n.novedad AS tipo_novedad,
                          n.fecha_fin AS fecha_fin_novedad
                   FROM novedad_reemplazos nr
                   JOIN novedades n ON n.id = nr.novedad_id
                   WHERE nr.legajo_reemplazo = ? AND nr.activo = 1 AND n.activo = 1
                   ORDER BY nr.id ASC""",
                (int(legajo_ciclo),),
            ).fetchall()
            nov_propias_rows = conn.execute(
                """SELECT id, novedad, fecha_inicio, fecha_fin, observaciones
                   FROM novedades
                   WHERE legajo = ? AND activo = 1
                   ORDER BY id DESC""",
                (int(legajo_ciclo),),
            ).fetchall()

        tramos_parsed = []
        for r in reemplazos:
            r_ini, r_fin_opt = self._rango_fechas_reemplazo(r["fecha_inicio"], r["fecha_fin"], r["fecha_fin_novedad"])
            if r_ini:
                tramos_parsed.append((r_ini, r_fin_opt, dict(r)))

        nov_propias_parsed = []
        for np in nov_propias_rows:
            np_ini, np_fin_opt = self._rango_fechas_reemplazo(np["fecha_inicio"], np["fecha_fin"], None)
            if np_ini:
                nov_propias_parsed.append((np_ini, np_fin_opt, dict(np)))

        emp_jh = 6
        if emp:
            if "jornada_horas" in emp.keys() and emp["jornada_horas"] in (6, 7):
                emp_jh = int(emp["jornada_horas"])
            elif "guarda" in (emp["especialidad"] or "").lower():
                emp_jh = 7

        descanso_entre_jornada = 18 if emp_jh == 6 else 17
        descanso_post_franco = 42 if emp_jh == 6 else 41

        franco_emp = (emp["franco"] if emp else "") or ""
        franco_horario = ((emp["franco_horario"] if emp and "franco_horario" in emp.keys() else "") or "").strip()
        franco_norm = normalizar_dia_semana(franco_emp)

        fecha_primer_dia = date(anio, mes, 1)
        fecha_inicio_calc = fecha_primer_dia - timedelta(days=21)
        fecha_fin_calc = date(anio, mes, num_dias)
        jornadas_map = self.obtener_jornadas_ciclo_rango(legajo_ciclo, fecha_inicio_calc, fecha_fin_calc)
        turnos_maps_por_reg = {
            "H": self.listar_turnos_itinerario_map(regimen="H"),
            "S": self.listar_turnos_itinerario_map(regimen="S"),
            "D": self.listar_turnos_itinerario_map(regimen="D"),
        }

        def _parsear_hm(val_str: Optional[str]) -> Optional[Tuple[int, int]]:
            if not val_str:
                return None
            try:
                p = val_str.strip().split(":")
                return int(p[0]), int(p[1])
            except Exception:
                return None

        def _calc_hs_trabajadas(t_str: Optional[str], d_str: Optional[str]) -> float:
            hm_t = _parsear_hm(t_str)
            hm_d = _parsear_hm(d_str)
            if not hm_t or not hm_d:
                return 0.0
            min_t = hm_t[0] * 60 + hm_t[1]
            min_d = hm_d[0] * 60 + hm_d[1]
            if min_d < min_t:
                min_d += 24 * 60
            return round((min_d - min_t) / 60.0, 2)

        def _calc_deja_dt(dia_date: date, t_str: Optional[str], d_str: Optional[str]) -> Optional[datetime]:
            hm_d = _parsear_hm(d_str)
            if not hm_d:
                return None
            hm_t = _parsear_hm(t_str)
            if hm_t:
                min_t = hm_t[0] * 60 + hm_t[1]
                min_d = hm_d[0] * 60 + hm_d[1]
                if min_d < min_t:
                    return datetime.combine(dia_date + timedelta(days=1), time(hm_d[0], hm_d[1]))
            return datetime.combine(dia_date, time(hm_d[0], hm_d[1]))

        dias_historia: Dict[date, Dict[str, Any]] = {}
        acum_semana = 0.0

        d_step = fecha_inicio_calc
        while d_step <= fecha_fin_calc:
            f_iso = d_step.strftime("%Y-%m-%d")
            dia_semana_str = DIAS_SEMANA[d_step.weekday()]
            dia_sem_norm = normalizar_dia_semana(dia_semana_str)
            es_franco_reg = bool(franco_norm and franco_norm in dia_sem_norm)

            relevo_dia = None
            for r_ini, r_fin_opt, r_data in tramos_parsed:
                if r_ini <= d_step and (r_fin_opt is None or d_step <= r_fin_opt):
                    relevo_dia = r_data
                    break

            nov_propia_dia = None
            for np_ini, np_fin_opt, np_data in nov_propias_parsed:
                if np_ini <= d_step and (np_fin_opt is None or d_step <= np_fin_opt):
                    nov_propia_dia = np_data
                    break

            jornada_db = jornadas_map.get(f_iso)

            if nov_propia_dia:
                estado = f"Novedad ({nov_propia_dia['novedad']})"
                es_franco = False
                novedad_id = nov_propia_dia["id"]
                reemplaza_a = "-"
                turno_cubierto = "-"
                motivo = f"Novedad propia #{nov_propia_dia['id']} ({nov_propia_dia['novedad']})"
                periodo = f"{nov_propia_dia['fecha_inicio']} a {nov_propia_dia['fecha_fin'] or 'abierto'}"
                reemplazando_legajo = None
                reemplazando_nombre = None
                novedad_titular = None
                tramo_inicio = nov_propia_dia["fecha_inicio"]
                tramo_fin = nov_propia_dia["fecha_fin"]
                toma = ""
                deja = ""
            elif relevo_dia:
                estado = "Reemplazando"
                es_franco = False
                novedad_id = relevo_dia["novedad_id"]
                reemplaza_a = f"{relevo_dia['legajo_titular']} - {relevo_dia['apellidos_titular']}"
                turno_cubierto = relevo_dia["turno_titular"] or "-"
                motivo = f"#{relevo_dia['novedad_id']} ({relevo_dia['tipo_novedad']})"
                periodo = f"{relevo_dia['fecha_inicio']} a {relevo_dia['fecha_fin'] or relevo_dia['fecha_fin_novedad'] or 'abierto'}"
                reemplazando_legajo = relevo_dia["legajo_titular"]
                reemplazando_nombre = relevo_dia["apellidos_titular"]
                novedad_titular = relevo_dia["tipo_novedad"]
                tramo_inicio = relevo_dia["fecha_inicio"]
                tramo_fin = relevo_dia["fecha_fin"] or relevo_dia["fecha_fin_novedad"]
                toma = jornada_db.get("toma") or "" if jornada_db else ""
                deja = jornada_db.get("deja") or "" if jornada_db else ""

                # Si no hay horario manual ingresado, resolver automáticamente desde turnos_itinerario
                reg_step = "H" if d_step.weekday() < 5 else ("S" if d_step.weekday() == 5 else "D")
                turnos_map_step = turnos_maps_por_reg.get(reg_step, {})
                if not toma and not deja and turno_cubierto and turno_cubierto != "-" and turnos_map_step:
                    from src.domain.validators import resolver_jornada_itinerario
                    franco_titular = relevo_dia.get("franco_titular") or "Domingo"
                    res_jornada = resolver_jornada_itinerario(
                        turno_base=turno_cubierto,
                        fecha_target=f_iso,
                        franco_agente=franco_titular,
                        turnos_dict=turnos_map_step,
                    )
                    if res_jornada.get("es_franco"):
                        es_franco = True
                        estado = "Franco por Relevo"
                        toma = ""
                        deja = ""
                    else:
                        toma = res_jornada.get("toma") or ""
                        deja = res_jornada.get("deje") or ""
                        cod_variante = res_jornada.get("codigo_turno") or turno_cubierto
                        reemplaza_a = f"{relevo_dia['legajo_titular']} - {relevo_dia['apellidos_titular']} (Turno {cod_variante})"
            else:
                novedad_id = ""
                reemplaza_a = "-"
                turno_cubierto = "-"
                motivo = "-"
                periodo = "-"
                reemplazando_legajo = None
                reemplazando_nombre = None
                novedad_titular = None
                tramo_inicio = None
                tramo_fin = None
                if jornada_db and jornada_db.get("franco_anticipado"):
                    es_franco = True
                    estado = "Franco Anticipado"
                    toma = ""
                    deja = ""
                elif es_franco_reg:
                    es_franco = True
                    estado = f"Franco (desde {franco_horario})" if franco_horario else "Franco"
                    toma = ""
                    deja = ""
                else:
                    es_franco = False
                    estado = "Disponible"
                    toma = jornada_db.get("toma") or "" if jornada_db else ""
                    deja = jornada_db.get("deja") or "" if jornada_db else ""

            hs_trab = _calc_hs_trabajadas(toma, deja) if (toma and deja) else 0.0
            deja_dt = _calc_deja_dt(d_step, toma, deja) if deja else None

            if es_franco or nov_propia_dia:
                acum_semana = 0.0
                hs_semana_str = "-"
            else:
                acum_semana += hs_trab
                hs_semana_str = f"{acum_semana:.1f} hs" if acum_semana > 0 else "-"

            if es_franco or nov_propia_dia:
                hora_disp_str = "-"
            else:
                d_prev = d_step - timedelta(days=1)
                disp_dt = None
                if d_prev in dias_historia and not dias_historia[d_prev]["es_franco"]:
                    prev_deja_dt = dias_historia[d_prev]["deja_dt"]
                    if prev_deja_dt:
                        disp_dt = prev_deja_dt + timedelta(hours=descanso_entre_jornada)
                else:
                    d_busca = d_prev
                    while d_busca in dias_historia and dias_historia[d_busca]["es_franco"]:
                        d_busca -= timedelta(days=1)
                    if d_busca in dias_historia and dias_historia[d_busca]["deja_dt"]:
                        prev_deja_dt = dias_historia[d_busca]["deja_dt"]
                        disp_dt = prev_deja_dt + timedelta(hours=descanso_post_franco)

                if disp_dt:
                    if disp_dt.date() == d_step:
                        hora_disp_str = disp_dt.strftime("%H:%M")
                    elif disp_dt.date() < d_step:
                        hora_disp_str = "00:00"
                    else:
                        hora_disp_str = disp_dt.strftime("%d/%m %H:%M")
                else:
                    hora_disp_str = "-"

            obs_calculada = ""
            if nov_propia_dia:
                obs_calculada = str(nov_propia_dia.get("observaciones") or "").strip()
            elif jornada_db:
                obs_calculada = str(jornada_db.get("observaciones") or "").strip()

            dias_historia[d_step] = {
                "d": d_step,
                "es_franco": es_franco,
                "estado": estado,
                "toma": toma,
                "deja": deja,
                "deja_dt": deja_dt,
                "hs_trabajadas": hs_trab,
                "hs_trabajadas_str": f"{hs_trab:.1f} hs" if hs_trab > 0 else "-",
                "hs_semana_str": hs_semana_str,
                "hora_disponible": hora_disp_str,
                "franco_anticipado": bool(jornada_db and jornada_db.get("franco_anticipado")),
                "observaciones": obs_calculada,
                "novedad_id": novedad_id,
                "reemplaza_a": reemplaza_a,
                "turno_cubierto": turno_cubierto,
                "motivo": motivo,
                "periodo": periodo,
                "reemplazando_legajo": reemplazando_legajo,
                "reemplazando_nombre": reemplazando_nombre,
                "novedad_titular": novedad_titular,
                "tramo_inicio": tramo_inicio,
                "tramo_fin": tramo_fin,
            }

            d_step += timedelta(days=1)

        dias_mes = []
        for dia_num in range(1, num_dias + 1):
            f_actual = date(anio, mes, dia_num)
            info = dias_historia[f_actual]
            dias_mes.append({
                "dia": dia_num,
                "fecha": f_actual.strftime("%d/%m/%Y"),
                "fecha_iso": f_actual.strftime("%Y-%m-%d"),
                "dia_semana": DIAS_SEMANA[f_actual.weekday()],
                "estado": info["estado"],
                "hora_disponible": info["hora_disponible"],
                "toma": info["toma"] or "-",
                "deja": info["deja"] or "-",
                "horas_trabajadas": info["hs_trabajadas_str"],
                "hs_semana": info["hs_semana_str"],
                "novedad_id": info["novedad_id"],
                "reemplaza_a": info["reemplaza_a"],
                "turno_cubierto": info["turno_cubierto"],
                "motivo": info["motivo"],
                "periodo": info["periodo"],
                "reemplazando_legajo": info["reemplazando_legajo"],
                "reemplazando_nombre": info["reemplazando_nombre"],
                "novedad_titular": info["novedad_titular"],
                "tramo_inicio": info["tramo_inicio"],
                "tramo_fin": info["tramo_fin"],
                "franco_anticipado": info["franco_anticipado"],
                "observaciones": info["observaciones"],
            })

        return dias_mes

    # ---------------------------------------------------------
    # Registros Eliminados y Recuperación
    # ---------------------------------------------------------

    def _tabla_entidad(self, tipo: str) -> Tuple[str, str]:
        if tipo.lower() in ("novedad", "novedades"):
            return "novedades", "novedad"
        if tipo.lower() in ("cambio", "cambios", "cambio_turno", "cambios_turno"):
            return "cambios_turno", "cambio_turno"
        if tipo.lower() in ("ordenamiento", "ordenamientos"):
            return "ordenamientos", "ordenamiento"
        raise ValueError("Tipo de registro inválido.")

    def recuperar_registro(
        self,
        tipo: str,
        record_id: int,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        table, entidad = self._tabla_entidad(tipo)
        with self.db.write_transaction() as conn:
            before = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
            if not before:
                raise ValueError("El registro no existe.")
            if table == "ordenamientos":
                conn.execute(
                    "UPDATE ordenamientos SET activo=1, estado='activo', deleted_at=NULL, updated_at=datetime('now', 'localtime'), sync_status='pending' WHERE id=?",
                    (record_id,),
                )
            else:
                conn.execute(f"UPDATE {table} SET activo=1, sync_status='pending' WHERE id=?", (record_id,))
            after = dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone())
            self._audit(conn, user_id, windows_user, "recuperar", entidad, record_id, dict(before), after)
            self._enqueue_sync(conn, table, record_id, "UPDATE", after)

    def borrar_definitivo(
        self,
        tipo: str,
        record_id: int,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        table, entidad = self._tabla_entidad(tipo)
        with self.db.write_transaction() as conn:
            before = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
            if not before:
                raise ValueError("El registro no existe.")
            if table == "ordenamientos":
                conn.execute("DELETE FROM ordenamiento_tramos WHERE ordenamiento_id=?", (record_id,))
            conn.execute(f"DELETE FROM {table} WHERE id=?", (record_id,))
            self._audit(conn, user_id, windows_user, "borrar_definitivo", entidad, record_id, dict(before), None)
            self._enqueue_sync(conn, table, record_id, "DELETE", None)

    def listar_eliminados(self) -> List[Dict[str, Any]]:
        with self.db.read_connection() as conn:
            rows = conn.execute(
                """SELECT 'Novedad' AS tipo, id, registrado_en, legajo, apellidos_nombres,
                          dotacion, novedad AS detalle, observaciones
                   FROM novedades WHERE activo=0
                   UNION ALL
                   SELECT 'Cambio' AS tipo, id, registrado_en, legajo_1 AS legajo, apellidos_nombres_1 AS apellidos_nombres,
                          dotacion_1 AS dotacion, fecha_cambio AS detalle, observaciones
                   FROM cambios_turno WHERE activo=0
                   UNION ALL
                   SELECT 'Ordenamiento' AS tipo, id, COALESCE(deleted_at, created_at, fecha_emision) AS registrado_en,
                          legajo_personal AS legajo, nombre_personal AS apellidos_nombres,
                          deposito AS dotacion, (nro_orden || ' - ' || motivo_orden) AS detalle, observacion_pie AS observaciones
                   FROM ordenamientos WHERE activo=0 OR estado='eliminado'
                   ORDER BY registrado_en DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------------------------------------------------------
    # Auditoría
    # ---------------------------------------------------------

    def listar_auditoria(self, text_filter: str = "") -> List[Dict[str, Any]]:
        with self.db.read_connection() as conn:
            like = f"%{text_filter.strip()}%"
            rows = conn.execute(
                """SELECT a.id, a.creado_en, COALESCE(u.username, a.usuario_windows, '') AS usuario,
                          a.accion, a.entidad, a.entidad_id, a.datos_anteriores, a.datos_nuevos
                   FROM auditoria a LEFT JOIN usuarios u ON u.id=a.usuario_id
                   WHERE ?='' OR COALESCE(u.username, a.usuario_windows, '') LIKE ?
                         OR a.accion LIKE ? OR a.entidad LIKE ?
                   ORDER BY a.id DESC""",
                (text_filter.strip(), like, like, like),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------------------------------------------------------
    # Dashboard y Métricas
    # ---------------------------------------------------------

    def _es_dia_semana(self, valor: Any) -> bool:
        texto = normalizar_dia_semana(str(valor or ""))
        return texto in DIAS_SEMANA_NORM

    def obtener_metricas_dashboard(self, dias: int = 30) -> Dict[str, Any]:
        """Calcula todas las métricas del panel en una sola lectura optimizada."""
        dias = max(1, int(dias))
        hoy = datetime.now()
        desde = hoy - timedelta(days=dias - 1)
        fechas = [(desde + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(dias)]
        serie = {fecha: {"novedades": 0, "cambios": 0} for fecha in fechas}

        conteo_tipo: Dict[str, int] = {}
        conteo_dotacion: Dict[str, int] = {}
        conteo_dia_semana: Dict[str, int] = {dia: 0 for dia in DIAS_SEMANA}

        resumen = {
            "novedades_hoy": 0, "cambios_hoy": 0,
            "novedades_mes": 0, "cambios_mes": 0, "total_mes": 0,
        }

        def sumar_dotacion(dotacion: Optional[str]) -> None:
            valor = (dotacion or "").strip()
            if not valor or self._es_dia_semana(valor):
                return
            conteo_dotacion[valor] = conteo_dotacion.get(valor, 0) + 1

        with self.db.read_connection() as conn:
            filas_nov = conn.execute(
                "SELECT registrado_en, novedad, dotacion FROM novedades WHERE activo=1"
            ).fetchall()
            filas_cam = conn.execute(
                "SELECT registrado_en, dotacion_1, dotacion_2 FROM cambios_turno WHERE activo=1"
            ).fetchall()

        for fila in filas_nov:
            fecha = self.parsear_registrado_en(fila["registrado_en"])
            if fecha is None:
                continue
            if fecha.date() == hoy.date():
                resumen["novedades_hoy"] += 1
            if fecha.year == hoy.year and fecha.month == hoy.month:
                resumen["novedades_mes"] += 1

            if fecha >= desde:
                conteo_dia_semana[DIAS_SEMANA[fecha.weekday()]] += 1
                tipo = (fila["novedad"] or "").strip() or "Sin tipo"
                conteo_tipo[tipo] = conteo_tipo.get(tipo, 0) + 1
                sumar_dotacion(fila["dotacion"])
                clave = fecha.strftime("%Y-%m-%d")
                if clave in serie:
                    serie[clave]["novedades"] += 1

        for fila in filas_cam:
            fecha = self.parsear_registrado_en(fila["registrado_en"])
            if fecha is None:
                continue
            if fecha.date() == hoy.date():
                resumen["cambios_hoy"] += 1
            if fecha.year == hoy.year and fecha.month == hoy.month:
                resumen["cambios_mes"] += 1

            if fecha >= desde:
                conteo_dia_semana[DIAS_SEMANA[fecha.weekday()]] += 1
                sumar_dotacion(fila["dotacion_1"])
                sumar_dotacion(fila["dotacion_2"])
                clave = fecha.strftime("%Y-%m-%d")
                if clave in serie:
                    serie[clave]["cambios"] += 1

        resumen["total_mes"] = resumen["novedades_mes"] + resumen["cambios_mes"]

        return {
            "resumen": resumen,
            "por_tipo": sorted(conteo_tipo.items(), key=lambda item: item[1], reverse=True),
            "por_dotacion": sorted(conteo_dotacion.items(), key=lambda item: item[1], reverse=True)[:10],
            "tendencia": [(f, serie[f]["novedades"], serie[f]["cambios"]) for f in fechas],
            "por_dia_semana": list(conteo_dia_semana.items()),
        }

    def dashboard_resumen(self) -> Dict[str, int]:
        return self.obtener_metricas_dashboard(30)["resumen"]

    def dashboard_por_tipo(self, dias: int = 30) -> List[Tuple[str, int]]:
        return self.obtener_metricas_dashboard(dias)["por_tipo"]

    def dashboard_por_dotacion(self, dias: int = 30, limite: int = 10) -> List[Tuple[str, int]]:
        return self.obtener_metricas_dashboard(dias)["por_dotacion"][:max(1, int(limite))]

    def dashboard_tendencia(self, dias: int = 30) -> List[Tuple[str, int, int]]:
        return self.obtener_metricas_dashboard(dias)["tendencia"]

    def dashboard_por_dia_semana(self, dias: int = 30) -> List[Tuple[str, int]]:
        return self.obtener_metricas_dashboard(dias)["por_dia_semana"]

    # ---------------------------------------------------------
    # Itinerarios de Conducción Eléctrica y Buscador por Tren
    # ---------------------------------------------------------

    @staticmethod
    def normalizar_regimen_turno(valor: Any, default: str = "TODOS") -> str:
        """Normaliza día/régimen operativo de turno a 'H' (Hábil), 'S' (Sábado), 'D' (Domingo) o 'TODOS'."""
        raw = str(valor or "").strip()
        if not raw:
            return default
        t = normalizar_texto_estacion(raw).upper()
        if t in ("TODOS", "TODAS", "ALL", "CUALQUIERA", "SIN DISTINCION") or t.startswith("TODOS"):
            return "TODOS"
        if t in ("H", "HABIL", "LV", "LAV", "L-V", "L A V") or "HABIL" in t or "LUNES A VIERNES" in t:
            return "H"
        if t in ("S", "SAB", "SABADO") or "SABADO" in t or t.startswith("SAB"):
            return "S"
        if t in ("D", "DOM", "DOMINGO", "DF", "DOM / FERIADO") or "DOMINGO" in t or "FERIAD" in t or t.startswith("DOM"):
            return "D"
        return default

    @staticmethod
    def normalizar_regimen_parada(valor: Any, default: str = "H") -> str:
        """Normaliza régimen de paradas/ordenamientos convirtiendo alias de Hábil (LV/L-V/HABIL) a 'H'."""
        raw = str(valor or "").strip()
        if not raw:
            return default
        up = raw.upper()
        if up in ("H", "S", "D", "TODOS"):
            return up
        t = normalizar_texto_estacion(raw).upper()
        if t in ("H", "HABIL", "LV", "LAV", "L-V", "L A V") or "HABIL" in t or "LUNES A VIERNES" in t:
            return "H"
        if t in ("S", "SAB", "SABADO") or t.startswith("SAB") or "SABADO" in t:
            return "S"
        if t in ("D", "DOM", "DOMINGO", "DF", "DOM / FERIADO") or t.startswith("DOM") or "DOMINGO" in t or "FERIAD" in t:
            return "D"
        if t in ("TODOS", "TODAS"):
            return "TODOS"
        return up

    @staticmethod
    def formatear_regimen_turno(regimen: Any) -> str:
        """Devuelve representación legible para UI y reportes de H/S/D/TODOS."""
        raw = str(regimen or "TODOS").strip()
        t = normalizar_texto_estacion(raw).upper()
        if t in ("H", "HABIL", "LV", "LAV", "L-V", "L A V") or "HABIL" in t or "LUNES A VIERNES" in t:
            return "H (Hábil)"
        if t in ("S", "SAB", "SABADO") or t.startswith("SAB") or "SABADO" in t:
            return "S (Sábado)"
        if t in ("D", "DOM", "DOMINGO", "DF", "DOM / FERIADO") or t.startswith("DOM") or "DOMINGO" in t or "FERIAD" in t:
            return "D (Domingo)"
        if t in ("", "TODOS", "TODAS"):
            return "Todos"
        return raw.upper() if raw.upper() in ("DIAGRAMA_6D", "UNIPERSONAL", "PROGRAMA", "FRANQUERO") else raw

    @staticmethod
    def _formatear_hora_celda(val: Any) -> str:
        """Convierte celdas de horario Excel (time, datetime, timedelta o str) a HH:MM."""
        if val is None:
            return ""
        if isinstance(val, datetime):
            return val.strftime("%H:%M")
        if isinstance(val, time):
            return val.strftime("%H:%M")
        if isinstance(val, timedelta):
            total_mins = int(round(val.total_seconds() / 60.0)) % (24 * 60)
            return f"{total_mins // 60:02d}:{total_mins % 60:02d}"
        if isinstance(val, (int, float)) and not isinstance(val, bool) and 0 <= float(val) < 1:
            total_mins = int(round(float(val) * 24 * 60)) % (24 * 60)
            return f"{total_mins // 60:02d}:{total_mins % 60:02d}"
        s = str(val).strip()
        if not s or s.lower() == "none":
            return ""
        m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?(?:\.\d+)?$", s)
        if m:
            h_i = int(m.group(1))
            if 0 <= h_i <= 23:
                return f"{h_i:02d}:{m.group(2)}"
        return s

    @staticmethod
    def _formatear_texto_celda(val: Any, reemplazar_coma: bool = False) -> str:
        """Normaliza valores de texto/numéricos de Excel evitando sufijos '.0' espurios."""
        if val is None:
            return ""
        if isinstance(val, bool):
            return str(val)
        if isinstance(val, int):
            s = str(val)
        elif isinstance(val, float):
            s = str(int(val)) if val.is_integer() else str(val)
        else:
            s = str(val).strip()
        if s.lower() == "none":
            return ""
        if reemplazar_coma:
            s = s.replace(",", ".")
        return s

    @staticmethod
    def _norm_header_col(val: Any) -> str:
        if val is None:
            return ""
        txt = unicodedata.normalize("NFKD", str(val)).encode("ascii", "ignore").decode("ascii").lower().strip()
        txt = re.sub(r"[\s_]+", "_", txt)
        return txt.strip("_")

    @staticmethod
    def _deducir_deposito_turno(hoja: str, archivo: str = "") -> str:
        txt = f"{hoja or ''} {archivo or ''}".upper()
        tokens = set(re.split(r"[^A-ZÁÉÍÓÚÑ0-9]+", txt))
        if "LLV" in tokens or "LLAVALLOL" in tokens:
            return "LLV"
        if "TY" in tokens or "TEMPERLEY" in tokens or "TÉMPERLEY" in tokens:
            return "TY"
        if "KM5" in tokens or "K5" in tokens or "KM 5" in txt:
            return "KM5"
        if "LP" in tokens or "LA PLATA" in txt:
            return "LP"
        if "OA" in tokens or "TOLOSA" in tokens:
            return "OA"
        if "CÑ" in tokens or "CAÑUELAS" in tokens or "CANUELAS" in tokens:
            return "CÑ"
        if "AK" in tokens or "ALEJANDRO KORN" in txt or "KORN" in tokens:
            return "AK"
        if "RE" in tokens or "ESCALADA" in tokens:
            return "RE"
        if "PC" in tokens or "PLAZA CONSTITUCION" in txt or "PLAZA" in tokens:
            return "PC"
        if "LLV" in txt:
            return "LLV"
        if "TEMPERLEY" in txt:
            return "TY"
        if "KM5" in txt:
            return "KM5"
        if "LA PLATA" in txt:
            return "LP"
        if "TOLOSA" in txt:
            return "OA"
        return "PC"

    def importar_itinerario_excel(
        self,
        file_path: str,
        itinerario_nombre: Optional[str] = None,
        regimen_default: Optional[str] = None,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Importa turnos desde Excel en formato completo (turno, dia H/S/D, nombre_itinerario...) o legacy Indice_45."""
        import openpyxl

        wb = openpyxl.load_workbook(file_path, data_only=True)

        header_aliases = {
            "codigo_turno": {"turno", "codigo_turno", "codigo turno", "cod_turno", "cod turno"},
            "dia": {"dia", "regimen", "tipo_dia", "tipo dia", "servicio", "dia_servicio"},
            "toma": {"toma", "toma_servicio", "hora_toma"},
            "deja": {"deja", "deje", "deje_servicio", "deja_servicio", "hora_deja", "hora_deje"},
            "vuelta": {"vuelta", "nro_vuelta", "n_vuelta"},
            "ref": {"ref", "referencia"},
            "tren": {"tren", "nro_tren", "n_tren"},
            "origen": {"origen", "est_origen", "estacion_origen"},
            "sale": {"sale", "salida", "hora_salida", "h_salida"},
            "destino": {"destino", "est_destino", "estacion_destino"},
            "llega": {"llega", "llegada", "hora_llegada", "h_llegada"},
            "observaciones": {"observaciones", "observacion", "obs", "observaciones_tramo", "obs_tramo"},
            "nombre_itinerario": {"nombre_itinerario", "itinerario", "itinerario_vigente", "itin", "circ_iti"},
            "nombre_hoja": {"nombre_hoja", "hoja", "deposito", "deposito_base"},
            "nombre_archivo": {"nombre_archivo", "archivo"},
        }
        alias_to_field = {
            alias: field
            for field, aliases in header_aliases.items()
            for alias in aliases
        }

        def _detectar_mapeo_hoja(ws_cand) -> Optional[Tuple[int, Dict[str, int]]]:
            for r in range(1, min(4, ws_cand.max_row) + 1):
                cmap: Dict[str, int] = {}
                for c in range(1, ws_cand.max_column + 1):
                    h_norm = self._norm_header_col(ws_cand.cell(r, c).value)
                    field = alias_to_field.get(h_norm)
                    if field and field not in cmap:
                        cmap[field] = c
                if "codigo_turno" in cmap and len(
                    {"toma", "deja", "vuelta", "tren", "origen", "destino"} & set(cmap.keys())
                ) >= 2:
                    return r, cmap
            return None

        hojas_a_procesar: List[Tuple[Any, int, Optional[Dict[str, int]]]] = []
        if "Datos" in wb.sheetnames:
            ws_datos = wb["Datos"]
            det = _detectar_mapeo_hoja(ws_datos)
            if det:
                hojas_a_procesar.append((ws_datos, det[0] + 1, det[1]))
            else:
                hojas_a_procesar.append((ws_datos, 2, None))
        else:
            for ws_cand in wb.worksheets:
                det = _detectar_mapeo_hoja(ws_cand)
                if det:
                    hojas_a_procesar.append((ws_cand, det[0] + 1, det[1]))
            if not hojas_a_procesar:
                hojas_a_procesar.append((wb.active, 2, None))

        legacy_pos = {
            "codigo_turno": 1,
            "toma": 2,
            "deja": 3,
            "vuelta": 4,
            "ref": 5,
            "tren": 6,
            "origen": 7,
            "sale": 8,
            "destino": 9,
            "llega": 10,
            "observaciones": 11,
            "nombre_itinerario": 12,
            "nombre_hoja": 13,
        }

        filas_por_turno: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}

        for ws, start_row, col_map in hojas_a_procesar:
            cmap_use = col_map if col_map is not None else legacy_pos
            has_dia_col = col_map is not None and "dia" in col_map
            sheet_reg_hint = ""
            title_up = normalizar_texto_estacion(ws.title).upper()
            if "SAB" in title_up:
                sheet_reg_hint = "S"
            elif "DOM" in title_up or "FERIAD" in title_up:
                sheet_reg_hint = "D"
            elif "HAB" in title_up or "LAV" in title_up or "L-V" in title_up:
                sheet_reg_hint = "H"

            for row in range(start_row, ws.max_row + 1):
                c_turno = cmap_use.get("codigo_turno", 1)
                t_raw = ws.cell(row, c_turno).value
                codigo_turno = self._formatear_texto_celda(t_raw, reemplazar_coma=True)
                if not codigo_turno:
                    continue

                def _get_val(field_name: str):
                    col_i = cmap_use.get(field_name)
                    return ws.cell(row, col_i).value if col_i else None

                toma = self._formatear_hora_celda(_get_val("toma"))
                deja = self._formatear_hora_celda(_get_val("deja"))

                hoja = self._formatear_texto_celda(_get_val("nombre_hoja"))
                archivo = self._formatear_texto_celda(_get_val("nombre_archivo"))
                nom_itin = self._formatear_texto_celda(_get_val("nombre_itinerario"))
                itinerario_val = (itinerario_nombre or nom_itin or "Dic25").strip() or "Dic25"

                dia_raw = self._formatear_texto_celda(_get_val("dia"))
                if dia_raw:
                    regimen_val = self.normalizar_regimen_turno(dia_raw, default="H")
                elif has_dia_col:
                    # Si la hoja trae columna 'dia' pero la celda está vacía, deducir desde hoja/archivo o default H
                    hint_txt = normalizar_texto_estacion(f"{hoja} {archivo}").upper()
                    if "SAB" in hint_txt:
                        regimen_val = "S"
                    elif "DOM" in hint_txt or "FERIAD" in hint_txt:
                        regimen_val = "D"
                    elif sheet_reg_hint:
                        regimen_val = sheet_reg_hint
                    else:
                        regimen_val = self.normalizar_regimen_turno(regimen_default or "H", default="H")
                elif sheet_reg_hint:
                    regimen_val = sheet_reg_hint
                else:
                    regimen_val = self.normalizar_regimen_turno(regimen_default or "TODOS", default="TODOS")

                group_key = (codigo_turno, regimen_val, itinerario_val)

                nro_vuelta_raw = _get_val("vuelta")
                try:
                    if isinstance(nro_vuelta_raw, float) and nro_vuelta_raw.is_integer():
                        nro_vuelta = int(nro_vuelta_raw)
                    else:
                        nro_vuelta = int(str(nro_vuelta_raw).strip()) if nro_vuelta_raw not in (None, "") else len(filas_por_turno.get(group_key, [])) + 1
                except (ValueError, TypeError):
                    nro_vuelta = len(filas_por_turno.get(group_key, [])) + 1

                ref = self._formatear_texto_celda(_get_val("ref"))
                nro_tren_txt = self._formatear_texto_celda(_get_val("tren"))
                nro_tren = None if nro_tren_txt in ("", "-") else nro_tren_txt

                origen = self._formatear_texto_celda(_get_val("origen"))
                h_salida = self._formatear_hora_celda(_get_val("sale"))
                destino = self._formatear_texto_celda(_get_val("destino"))
                h_llegada = self._formatear_hora_celda(_get_val("llega"))
                obs_tramo = self._formatear_texto_celda(_get_val("observaciones"))

                item = {
                    "codigo_turno": codigo_turno,
                    "regimen": regimen_val,
                    "toma": toma,
                    "deja": deja,
                    "nro_vuelta": nro_vuelta,
                    "ref": ref,
                    "nro_tren": nro_tren,
                    "origen": origen,
                    "h_salida": h_salida,
                    "destino": destino,
                    "h_llegada": h_llegada,
                    "obs_tramo": obs_tramo,
                    "circ_iti": itinerario_val,
                    "hoja": hoja,
                    "archivo": archivo,
                }
                filas_por_turno.setdefault(group_key, []).append(item)

        turnos_guardados = 0
        vueltas_guardadas = 0

        with self.db.write_transaction() as conn:
            for (cod, regimen_val, itinerario), vueltas in filas_por_turno.items():
                v0 = vueltas[0]
                toma = v0["toma"]
                deja = v0["deja"]
                deposito = self._deducir_deposito_turno(v0.get("hoja", ""), v0.get("archivo", ""))

                m = re.match(r"^(\d+)\.(\d+)$", cod)
                if m:
                    base = m.group(1)
                    subindice = int(m.group(2))
                    tipo_turno = "DIAGRAMA_6D"
                elif cod.startswith("PROG") or "PROG" in cod:
                    base = cod
                    subindice = None
                    tipo_turno = "PROGRAMA"
                elif "ORD" in cod or "DH" in cod:
                    base = cod
                    subindice = None
                    tipo_turno = "FRANQUERO"
                else:
                    base = cod
                    subindice = None
                    tipo_turno = "UNIPERSONAL"

                duracion = ""
                try:
                    if len(toma) == 5 and len(deja) == 5 and ":" in toma and ":" in deja:
                        t_h, t_m = map(int, toma.split(":"))
                        d_h, d_m = map(int, deja.split(":"))
                        t_min = t_h * 60 + t_m
                        d_min = d_h * 60 + d_m
                        diff = (d_min - t_min) % (24 * 60)
                        duracion = f"{diff // 60:02d}:{diff % 60:02d}"
                except Exception:
                    duracion = ""

                conn.execute(
                    """INSERT INTO turnos_itinerario
                       (codigo_turno, regimen, turno_base, subindice, tipo_turno, deposito_base,
                        toma_servicio, deje_servicio, duracion_jornada, itinerario_vigente,
                        observaciones_turno, activo, sync_status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 1, 'pending', datetime('now', 'localtime'), datetime('now', 'localtime'))
                       ON CONFLICT(codigo_turno, regimen, itinerario_vigente) DO UPDATE SET
                        turno_base = excluded.turno_base,
                        subindice = excluded.subindice,
                        tipo_turno = excluded.tipo_turno,
                        deposito_base = excluded.deposito_base,
                        toma_servicio = excluded.toma_servicio,
                        deje_servicio = excluded.deje_servicio,
                        duracion_jornada = excluded.duracion_jornada,
                        activo = 1,
                        sync_status = 'pending',
                        updated_at = datetime('now', 'localtime');""",
                    (cod, regimen_val, base, subindice, tipo_turno, deposito, toma, deja, duracion, itinerario),
                )

                turno_row = conn.execute(
                    "SELECT id FROM turnos_itinerario WHERE codigo_turno = ? AND regimen = ? AND itinerario_vigente = ?;",
                    (cod, regimen_val, itinerario),
                ).fetchone()
                turno_id = turno_row[0]
                turnos_guardados += 1

                conn.execute("DELETE FROM turnos_vueltas WHERE turno_id = ?;", (turno_id,))
                for v in vueltas:
                    conn.execute(
                        """INSERT INTO turnos_vueltas
                           (turno_id, nro_vuelta, ref, nro_tren, origen, hora_salida,
                            destino, hora_llegada, observaciones_tramo, sync_status, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now', 'localtime'), datetime('now', 'localtime'));""",
                        (
                            turno_id,
                            v["nro_vuelta"],
                            v["ref"],
                            v["nro_tren"],
                            v["origen"],
                            v["h_salida"],
                            v["destino"],
                            v["h_llegada"],
                            v["obs_tramo"],
                        ),
                    )
                    vueltas_guardadas += 1

            self._audit(
                conn,
                user_id,
                windows_user,
                "importar",
                "turnos_itinerario",
                None,
                None,
                {
                    "archivo": Path(file_path).name,
                    "turnos": turnos_guardados,
                    "vueltas": vueltas_guardadas,
                },
            )

        logger.info("Importación de itinerario completada: %d turnos, %d vueltas.", turnos_guardados, vueltas_guardadas)
        return turnos_guardados, vueltas_guardadas

    def buscar_turnos_por_tren(self, nro_tren: str) -> List[Dict[str, Any]]:
        """Busca de forma instantánea todos los turnos que intervienen en un número de tren."""
        tren_clean = str(nro_tren or "").strip()
        if not tren_clean:
            return []

        with self.db.read_connection() as conn:
            rows = conn.execute(
                """SELECT
                    v.id AS vuelta_id,
                    v.nro_tren,
                    ti.codigo_turno,
                    COALESCE(NULLIF(TRIM(ti.regimen), ''), 'TODOS') AS regimen,
                    ti.turno_base,
                    ti.tipo_turno,
                    ti.deposito_base,
                    ti.toma_servicio,
                    ti.deje_servicio,
                    v.nro_vuelta,
                    v.ref,
                    v.origen,
                    v.hora_salida,
                    v.destino,
                    v.hora_llegada,
                    v.observaciones_tramo AS obs_vuelta,
                    ti.observaciones_turno AS obs_turno,
                    ti.itinerario_vigente
                   FROM turnos_vueltas v
                   JOIN turnos_itinerario ti ON v.turno_id = ti.id
                   WHERE v.nro_tren LIKE ? OR v.nro_tren = ?
                   ORDER BY v.hora_salida ASC, ti.codigo_turno ASC, ti.regimen ASC;""",
                (f"%{tren_clean}%", tren_clean),
            ).fetchall()
            res = []
            for r in rows:
                d = dict(r)
                d["regimen_label"] = self.formatear_regimen_turno(d.get("regimen"))
                res.append(d)
            return res

    def listar_turnos_itinerario_map(
        self,
        regimen: Optional[str] = None,
        itinerario: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Devuelve un mapa indexado por codigo_turno para resolución ultra-rápida."""
        reg_target = self.normalizar_regimen_turno(regimen, default="TODOS") if regimen else None

        with self.db.read_connection() as conn:
            query = """SELECT id, codigo_turno, COALESCE(NULLIF(TRIM(regimen), ''), 'TODOS') AS regimen,
                              turno_base, subindice, tipo_turno, deposito_base, toma_servicio,
                              deje_servicio, duracion_jornada, itinerario_vigente, observaciones_turno
                       FROM turnos_itinerario WHERE activo = 1"""
            params: List[Any] = []
            if itinerario and str(itinerario).strip() not in ("", "Todos", "Todas"):
                query += " AND itinerario_vigente = ?"
                params.append(str(itinerario).strip())
            query += " ORDER BY id DESC;"
            rows = [dict(r) for r in conn.execute(query, params).fetchall()]

        def _score_reg(r_val: str) -> int:
            rv = str(r_val or "TODOS").strip().upper()
            if reg_target in ("H", "S", "D"):
                if rv == reg_target:
                    return 0
                if rv in ("", "TODOS", "ALL"):
                    return 1
                if rv == "H":
                    return 2
                return 3
            if rv in ("", "TODOS", "ALL"):
                return 0
            if rv == "H":
                return 1
            if rv == "S":
                return 2
            return 3

        rows.sort(key=lambda r: _score_reg(r.get("regimen", "TODOS")))
        mapa: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            cod = r["codigo_turno"]
            if cod not in mapa:
                mapa[cod] = r
        return mapa

    def listar_itinerarios_disponibles(self) -> List[str]:
        """Devuelve la lista de códigos/nombres de itinerarios activos únicos cargados."""
        with self.db.read_connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT itinerario_vigente FROM turnos_itinerario
                   WHERE activo = 1 ORDER BY itinerario_vigente ASC;"""
            ).fetchall()
            return [r[0] for r in rows if r[0]]

    def listar_itinerarios_turnos(self, incluir_inactivos: bool = True) -> List[str]:
        """Devuelve los itinerarios únicos cargados en turnos_itinerario (opcionalmente incluyendo inactivos)."""
        with self.db.read_connection() as conn:
            sql = "SELECT DISTINCT TRIM(itinerario_vigente) FROM turnos_itinerario WHERE TRIM(itinerario_vigente) != ''"
            if not incluir_inactivos:
                sql += " AND activo = 1"
            sql += " ORDER BY 1 ASC;"
            return [r[0] for r in conn.execute(sql).fetchall() if r[0]]

    def listar_depositos_turnos(self) -> List[str]:
        """Devuelve los depósitos activos combinados con los presentes en turnos_itinerario."""
        with self.db.read_connection() as conn:
            dots = {
                str(r[0]).strip()
                for r in conn.execute("SELECT nombre FROM dotaciones WHERE activo = 1").fetchall()
                if r and str(r[0] or "").strip()
            }
            deps = {
                str(r[0]).strip()
                for r in conn.execute("SELECT DISTINCT deposito_base FROM turnos_itinerario WHERE TRIM(deposito_base) != ''").fetchall()
                if r and str(r[0] or "").strip()
            }
        return sorted(dots | deps)

    def obtener_vueltas_de_turno(self, turno_id: int) -> List[Dict[str, Any]]:
        """Devuelve la totalidad de vueltas de un turno ordenadas cronológicamente."""
        with self.db.read_connection() as conn:
            rows = conn.execute(
                """SELECT id, turno_id, nro_vuelta, ref, nro_tren, origen,
                          hora_salida, destino, hora_llegada, observaciones_tramo
                   FROM turnos_vueltas
                   WHERE turno_id = ?
                   ORDER BY nro_vuelta ASC, hora_salida ASC;""",
                (int(turno_id),),
            ).fetchall()
            return [dict(r) for r in rows]

    def obtener_turno_por_codigo(
        self,
        codigo_turno: str,
        itinerario: Optional[str] = None,
        regimen: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Busca un turno por su código (ej: '401.1' o '401') y opcionalmente por itinerario y día/régimen."""
        cod = str(codigo_turno or "").strip()
        if not cod:
            return None
        reg_norm = self.normalizar_regimen_turno(regimen, default="TODOS") if regimen else None

        with self.db.read_connection() as conn:
            query = """
                SELECT id, codigo_turno, COALESCE(NULLIF(TRIM(regimen), ''), 'TODOS') AS regimen,
                       turno_base, subindice, tipo_turno, deposito_base,
                       toma_servicio, deje_servicio, duracion_jornada, tiempo_toma, tiempo_deje,
                       itinerario_vigente, observaciones_turno, activo
                FROM turnos_itinerario
                WHERE activo = 1 AND (codigo_turno = ? OR turno_base = ?)
            """
            params: List[Any] = [cod, cod]
            if itinerario and str(itinerario).strip() not in ("Todos", "Todas", ""):
                query += " AND itinerario_vigente = ?"
                params.append(str(itinerario).strip())

            if reg_norm in ("H", "S", "D"):
                query += """
                    ORDER BY
                        CASE UPPER(TRIM(COALESCE(regimen, 'TODOS')))
                            WHEN ? THEN 0
                            WHEN 'TODOS' THEN 1
                            WHEN '' THEN 1
                            WHEN 'H' THEN 2
                            ELSE 3
                        END ASC,
                        itinerario_vigente DESC,
                        id DESC
                    LIMIT 1;
                """
                params.append(reg_norm)
            elif reg_norm == "TODOS":
                query += """
                    ORDER BY
                        CASE UPPER(TRIM(COALESCE(regimen, 'TODOS')))
                            WHEN 'TODOS' THEN 0
                            WHEN '' THEN 0
                            WHEN 'H' THEN 1
                            WHEN 'S' THEN 2
                            ELSE 3
                        END ASC,
                        itinerario_vigente DESC,
                        id DESC
                    LIMIT 1;
                """
            else:
                query += """
                    ORDER BY
                        CASE UPPER(TRIM(COALESCE(regimen, 'TODOS')))
                            WHEN 'H' THEN 0
                            WHEN 'TODOS' THEN 1
                            WHEN '' THEN 1
                            WHEN 'S' THEN 2
                            ELSE 3
                        END ASC,
                        itinerario_vigente DESC,
                        id DESC
                    LIMIT 1;
                """
            row = conn.execute(query, params).fetchone()
            if not row:
                return None
            res = dict(row)
            res["regimen_label"] = self.formatear_regimen_turno(res.get("regimen"))
            res["vueltas"] = self.obtener_vueltas_de_turno(res["id"])
            return res

    def buscar_turnos_con_cobertura(
        self,
        criterio: str,
        fecha_target_iso: str,
        tipo_dia: str = "HABIL",
        itinerarios: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Búsqueda dual (por tren o por turno) filtrando por día H/S/D y resolviendo titular/cobertura."""
        crit_clean = str(criterio or "").strip()
        if not crit_clean:
            return []

        f_obj = parsear_fecha(fecha_target_iso)
        if not f_obj:
            f_obj = date.today()
        dia_sem_nom = DIAS_SEMANA[f_obj.weekday()]
        reg_target = self.normalizar_regimen_turno(tipo_dia, default="TODOS")

        with self.db.read_connection() as conn:
            # 1. Identificar turnos candidatos según criterio dual + día/itinerario
            where_clauses = ["ti.activo = 1"]
            params: List[Any] = []

            if reg_target in ("H", "S", "D"):
                where_clauses.append(
                    "(UPPER(TRIM(COALESCE(ti.regimen, 'TODOS'))) = ? OR UPPER(TRIM(COALESCE(ti.regimen, 'TODOS'))) IN ('', 'TODOS', 'ALL'))"
                )
                params.append(reg_target)

            if itinerarios:
                placeholders = ",".join("?" for _ in itinerarios)
                where_clauses.append(f"ti.itinerario_vigente IN ({placeholders})")
                params.extend(itinerarios)

            if crit_clean:
                crit_normalizado = crit_clean.replace(",", ".")
                where_clauses.append("""(
                    ti.codigo_turno LIKE ? OR
                    ti.turno_base = ? OR
                    EXISTS (
                        SELECT 1 FROM turnos_vueltas tv
                        WHERE tv.turno_id = ti.id AND (tv.nro_tren = ? OR tv.nro_tren LIKE ?)
                    )
                )""")
                params.extend([f"%{crit_normalizado}%", crit_clean, crit_clean, f"%{crit_clean}%"])

            query = f"""
                SELECT DISTINCT
                    ti.id, ti.codigo_turno, COALESCE(NULLIF(TRIM(ti.regimen), ''), 'TODOS') AS regimen,
                    ti.turno_base, ti.subindice, ti.tipo_turno,
                    ti.deposito_base, ti.toma_servicio, ti.deje_servicio, ti.duracion_jornada,
                    ti.itinerario_vigente, ti.observaciones_turno
                FROM turnos_itinerario ti
                WHERE {" AND ".join(where_clauses)}
                ORDER BY ti.turno_base ASC, ti.subindice ASC, ti.codigo_turno ASC, ti.itinerario_vigente ASC, ti.regimen ASC
                LIMIT 300;
            """
            turnos_candidatos = [dict(r) for r in conn.execute(query, params).fetchall()]

            # 2. Precargar conductores en dotación para asignar titular
            conductores = [
                dict(r) for r in conn.execute(
                    """SELECT legajo, apellidos_nombres, dotacion, turnos, franco
                       FROM empleados WHERE activo = 1 AND LOWER(especialidad) LIKE '%conductor%';"""
                ).fetchall()
            ]

            # 3. Precargar novedades y reemplazos activos en la fecha evaluando rangos en Python
            raw_novedades = [
                dict(r) for r in conn.execute(
                    """SELECT n.id, n.legajo, n.apellidos_nombres, n.novedad, n.fecha_inicio, n.fecha_fin,
                              nr.id AS reemplazo_id, nr.legajo_reemplazo, nr.apellidos_nombres_reemplazo,
                              nr.turnos_reemplazo, nr.fecha_inicio AS r_fecha_inicio, nr.fecha_fin AS r_fecha_fin
                       FROM novedades n
                       LEFT JOIN novedad_reemplazos nr ON nr.novedad_id = n.id AND nr.activo = 1
                       WHERE n.activo = 1;"""
                ).fetchall()
            ]

            nov_map_por_legajo: Dict[int, Dict[str, Any]] = {}
            for n in raw_novedades:
                try:
                    d_ini = parsear_fecha(n.get("fecha_inicio"))
                except Exception:
                    continue
                if not d_ini or f_obj < d_ini:
                    continue
                f_fin_str = n.get("fecha_fin")
                if f_fin_str and str(f_fin_str).strip() not in ("", "-", "None"):
                    try:
                        d_fin = parsear_fecha(f_fin_str)
                    except Exception:
                        d_fin = None
                    if d_fin and f_obj > d_fin:
                        continue

                reemplazo_aplica = False
                leg_r = n.get("legajo_reemplazo")
                if leg_r:
                    try:
                        r_ini = parsear_fecha(n.get("r_fecha_inicio")) or d_ini
                    except Exception:
                        r_ini = d_ini
                    r_fin_str = n.get("r_fecha_fin")
                    if f_obj >= r_ini:
                        if not r_fin_str or str(r_fin_str).strip() in ("", "-", "None"):
                            reemplazo_aplica = True
                        else:
                            try:
                                r_fin = parsear_fecha(r_fin_str)
                            except Exception:
                                r_fin = None
                            if not r_fin or f_obj <= r_fin:
                                reemplazo_aplica = True

                leg_tit = int(n["legajo"])
                nov_entry = dict(n)
                if not reemplazo_aplica:
                    nov_entry["legajo_reemplazo"] = None
                    nov_entry["apellidos_nombres_reemplazo"] = None

                if leg_tit not in nov_map_por_legajo or reemplazo_aplica:
                    nov_map_por_legajo[leg_tit] = nov_entry

            # 3.b Precargar cambios de turno (permutas) activos para la fecha
            raw_cambios = [
                dict(r) for r in conn.execute(
                    """SELECT legajo_1, apellidos_nombres_1, turnos_1,
                              legajo_2, apellidos_nombres_2, turnos_2, fecha_cambio
                       FROM cambios_turno WHERE activo = 1;"""
                ).fetchall()
            ]
            cambios_por_legajo: Dict[int, Dict[str, Any]] = {}
            for c in raw_cambios:
                try:
                    d_c = parsear_fecha(c.get("fecha_cambio"))
                except Exception:
                    continue
                if d_c and d_c.date() == f_obj.date():
                    leg1 = int(c["legajo_1"])
                    leg2 = int(c["legajo_2"])
                    cambios_por_legajo[leg1] = {
                        "reemplazante_legajo": leg2,
                        "reemplazante_nombre": c["apellidos_nombres_2"],
                        "titular_cedente_legajo": leg1,
                        "titular_cedente_nombre": c["apellidos_nombres_1"],
                    }
                    cambios_por_legajo[leg2] = {
                        "reemplazante_legajo": leg1,
                        "reemplazante_nombre": c["apellidos_nombres_1"],
                        "titular_cedente_legajo": leg2,
                        "titular_cedente_nombre": c["apellidos_nombres_2"],
                    }

            # 3.c Precargar ordenamientos operativos activos para la fecha
            raw_ords = [
                dict(r) for r in conn.execute(
                    """SELECT o.id, o.nro_orden, o.fecha_emision, o.legajo_personal, o.nombre_personal,
                              o.deposito, o.ciclo_o_turno, o.motivo_orden, o.ref_pct,
                              GROUP_CONCAT(ot.nro_tren, ',') AS trenes_str
                       FROM ordenamientos o
                       LEFT JOIN ordenamiento_tramos ot ON ot.ordenamiento_id = o.id
                       WHERE o.activo = 1 AND o.estado = 'activo'
                       GROUP BY o.id;"""
                ).fetchall()
            ]
            ords_por_legajo: Dict[int, Dict[str, Any]] = {}
            ords_por_turno: Dict[str, Dict[str, Any]] = {}
            ords_por_tren: Dict[str, List[Dict[str, Any]]] = {}
            for o in raw_ords:
                try:
                    d_o = parsear_fecha(o.get("fecha_emision"))
                except Exception:
                    continue
                if d_o and d_o.date() == f_obj.date():
                    try:
                        ords_por_legajo[int(o["legajo_personal"])] = o
                    except Exception:
                        pass
                    ct = str(o.get("ciclo_o_turno") or "").strip()
                    if ct:
                        ords_por_turno[ct.upper()] = o
                    for tr_num in str(o.get("trenes_str") or "").split(","):
                        tr_c = tr_num.strip()
                        if tr_c:
                            ords_por_tren.setdefault(tr_c, []).append(o)

            # 4. Resolver titular y cobertura para cada turno
            from src.domain.validators import resolver_subindice_diagrama_6d

            resultados = []
            for t in turnos_candidatos:
                base = str(t["turno_base"])
                sub = t["subindice"]
                tipo = t["tipo_turno"]

                titular_encontrado: Optional[Dict[str, Any]] = None

                if tipo == "DIAGRAMA_6D" and sub is not None:
                    for c in conductores:
                        if c["turnos"] == base:
                            sub_calc, es_f = resolver_subindice_diagrama_6d(dia_sem_nom, c.get("franco") or "")
                            if not es_f and sub_calc == sub:
                                titular_encontrado = c
                                break
                    if not titular_encontrado:
                        for c in conductores:
                            if c["turnos"] == base:
                                titular_encontrado = c
                                break
                else:
                    for c in conductores:
                        if c["turnos"] == base or c["turnos"] == t["codigo_turno"]:
                            titular_encontrado = c
                            break

                titular_str = "-"
                reemplazo_str = "Normal (Titular en servicio)"
                estado_alerta = "OK"

                ord_match = (
                    ords_por_turno.get(str(t["codigo_turno"]).upper())
                    or ords_por_turno.get(base.upper())
                )
                if not ord_match and crit_clean in ords_por_tren:
                    ord_match = ords_por_tren[crit_clean][0]

                if titular_encontrado:
                    leg_tit = int(titular_encontrado["legajo"])
                    nom_tit = titular_encontrado["apellidos_nombres"]
                    if not ord_match and leg_tit in ords_por_legajo:
                        ord_match = ords_por_legajo[leg_tit]

                    if ord_match:
                        nro_ord = ord_match["nro_orden"]
                        nom_ord = ord_match["nombre_personal"]
                        leg_ord = ord_match["legajo_personal"]
                        mot_ord = ord_match["motivo_orden"]
                        titular_str = f"📄 {nom_ord} (Leg. {leg_ord})"
                        reemplazo_str = f"Ordenamiento {nro_ord}: {mot_ord}"
                        estado_alerta = "ORDENAMIENTO"
                    elif leg_tit in nov_map_por_legajo:
                        nov_info = nov_map_por_legajo[leg_tit]
                        motivo = nov_info["novedad"]
                        if nov_info.get("legajo_reemplazo"):
                            leg_r = nov_info["legajo_reemplazo"]
                            nom_r = nov_info["apellidos_nombres_reemplazo"]
                            titular_str = f"⚠️ {nom_r} (Relevo Leg. {leg_r})"
                            reemplazo_str = f"Reemplazo por {motivo}: {nom_tit} (Leg. {leg_tit})"
                            estado_alerta = "RELEVO"
                        else:
                            titular_str = f"{nom_tit} (Leg. {leg_tit})"
                            reemplazo_str = f"🔴 {motivo} (Turno DESCUBIERTO)"
                            estado_alerta = "DESCUBIERTO"
                    elif leg_tit in cambios_por_legajo:
                        cambio_info = cambios_por_legajo[leg_tit]
                        leg_c = cambio_info["reemplazante_legajo"]
                        nom_c = cambio_info["reemplazante_nombre"]
                        titular_str = f"🔄 {nom_c} (Leg. {leg_c})"
                        reemplazo_str = f"Cambio de Turno (Permuta con {nom_tit} Leg. {leg_tit})"
                        estado_alerta = "CAMBIO"
                    else:
                        titular_str = f"{nom_tit} (Leg. {leg_tit})"
                else:
                    if ord_match:
                        nro_ord = ord_match["nro_orden"]
                        nom_ord = ord_match["nombre_personal"]
                        leg_ord = ord_match["legajo_personal"]
                        mot_ord = ord_match["motivo_orden"]
                        titular_str = f"📄 {nom_ord} (Leg. {leg_ord})"
                        reemplazo_str = f"Ordenamiento {nro_ord}: {mot_ord}"
                        estado_alerta = "ORDENAMIENTO"
                    else:
                        titular_str = "Sin titular asignado"
                        reemplazo_str = "Vacante / A cubrir"
                        estado_alerta = "VACANTE"

                item_res = dict(t)
                item_res["regimen_label"] = self.formatear_regimen_turno(item_res.get("regimen"))
                item_res["titular"] = titular_str
                item_res["cobertura"] = reemplazo_str
                item_res["estado_alerta"] = estado_alerta
                item_res["fecha_consulta"] = fecha_target_iso
                item_res["dia_servicio"] = tipo_dia
                resultados.append(item_res)

            return resultados

    def obtener_vueltas_de_turnos(self, turno_ids: List[int]) -> List[Dict[str, Any]]:
        """Obtiene todas las vueltas de múltiples turnos ordenadas por turno y nro_vuelta."""
        if not turno_ids:
            return []
        placeholders = ",".join("?" for _ in turno_ids)
        with self.db.read_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    tv.id, tv.turno_id, tv.nro_vuelta, tv.ref, tv.nro_tren,
                    tv.origen, tv.hora_salida, tv.destino, tv.hora_llegada, tv.observaciones_tramo,
                    ti.codigo_turno, ti.turno_base,
                    COALESCE(NULLIF(TRIM(ti.regimen), ''), 'TODOS') AS regimen,
                    ti.itinerario_vigente
                FROM turnos_vueltas tv
                JOIN turnos_itinerario ti ON ti.id = tv.turno_id
                WHERE tv.turno_id IN ({placeholders})
                ORDER BY ti.turno_base ASC, ti.subindice ASC, ti.codigo_turno ASC, ti.itinerario_vigente ASC, ti.regimen ASC, tv.nro_vuelta ASC;
                """,
                turno_ids,
            ).fetchall()
            return [dict(r) for r in rows]

    def listar_codigos_turnos_base(self) -> List[str]:
        """Devuelve los turnos base únicos (ej: 401, 410, PROG.1CK5) para autocompletar."""
        with self.db.read_connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT turno_base FROM turnos_itinerario
                   WHERE activo = 1 ORDER BY turno_base ASC;"""
            ).fetchall()
            return [r[0] for r in rows]

    # ---------------------------------------------------------
    # CRUD de Turnos de Itinerario y Vueltas
    # ---------------------------------------------------------

    def _build_turnos_admin_where(
        self,
        criterio: str = "",
        deposito: Optional[str] = None,
        itinerario: Optional[str] = None,
        regimen: Optional[str] = None,
        incluir_inactivos: bool = True,
    ) -> Tuple[str, List[Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if not incluir_inactivos:
            clauses.append("ti.activo = 1")
        if deposito and str(deposito).strip() not in ("Todas", "Todos", ""):
            clauses.append("ti.deposito_base = ?")
            params.append(str(deposito).strip())
        if itinerario and str(itinerario).strip() not in ("Todas", "Todos", ""):
            clauses.append("ti.itinerario_vigente = ?")
            params.append(str(itinerario).strip())
        if regimen and str(regimen).strip() not in ("Todas", "Todos", ""):
            reg_raw = str(regimen).strip()
            reg_key = reg_raw.split("-")[0].strip().upper()
            reg_norm = "TODOS" if reg_key == "TODOS" else self.normalizar_regimen_turno(reg_key, default="TODOS")
            if reg_norm == "TODOS":
                clauses.append("UPPER(TRIM(COALESCE(ti.regimen, ''))) IN ('', 'TODOS')")
            else:
                clauses.append("UPPER(TRIM(COALESCE(ti.regimen, ''))) = ?")
                params.append(reg_norm)
        if criterio and criterio.strip():
            c_clean = criterio.strip()
            c_norm = c_clean.replace(",", ".")
            c_like = f"%{c_norm}%"
            clauses.append("(ti.codigo_turno LIKE ? OR ti.turno_base LIKE ? OR ti.observaciones_turno LIKE ?)")
            params.extend([c_like, f"%{c_clean}%", f"%{c_clean}%"])
        return " AND ".join(clauses), params

    def listar_turnos_admin(
        self,
        criterio: str = "",
        deposito: Optional[str] = None,
        itinerario: Optional[str] = None,
        regimen: Optional[str] = None,
        incluir_inactivos: bool = True,
        limite: int = 10000,
    ) -> List[Dict[str, Any]]:
        """Lista los turnos de itinerario con conteo de vueltas y filtros por depósito, itinerario y día H/S/D."""
        where_sql, params = self._build_turnos_admin_where(
            criterio=criterio,
            deposito=deposito,
            itinerario=itinerario,
            regimen=regimen,
            incluir_inactivos=incluir_inactivos,
        )
        query = f"""
            SELECT ti.id, ti.codigo_turno, COALESCE(NULLIF(TRIM(ti.regimen), ''), 'TODOS') AS regimen,
                   ti.turno_base, ti.subindice, ti.tipo_turno,
                   ti.deposito_base, ti.toma_servicio, ti.deje_servicio, ti.duracion_jornada,
                   ti.itinerario_vigente, ti.observaciones_turno, ti.activo,
                   (SELECT COUNT(*) FROM turnos_vueltas tv WHERE tv.turno_id = ti.id) AS cant_vueltas
            FROM turnos_itinerario ti
            WHERE {where_sql}
            ORDER BY ti.itinerario_vigente ASC, ti.regimen ASC, ti.turno_base ASC, ti.subindice ASC, ti.codigo_turno ASC
            LIMIT ?
        """
        params.append(int(limite))

        with self.db.read_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            res = []
            for r in rows:
                d = dict(r)
                d["regimen_label"] = self.formatear_regimen_turno(d.get("regimen"))
                res.append(d)
            return res

    def contar_turnos_por_filtros(
        self,
        criterio: str = "",
        deposito: Optional[str] = None,
        itinerario: Optional[str] = None,
        regimen: Optional[str] = None,
        incluir_inactivos: bool = True,
    ) -> int:
        """Devuelve la cantidad exacta de turnos que cumplen los filtros indicados."""
        where_sql, params = self._build_turnos_admin_where(
            criterio=criterio,
            deposito=deposito,
            itinerario=itinerario,
            regimen=regimen,
            incluir_inactivos=incluir_inactivos,
        )
        with self.db.read_connection() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM turnos_itinerario ti WHERE {where_sql}",
                params,
            ).fetchone()
            return int(row[0]) if row else 0

    def obtener_turno_completo(self, turno_id: int) -> Optional[Dict[str, Any]]:
        """Obtiene la ficha completa de un turno de itinerario y todas sus vueltas."""
        with self.db.read_connection() as conn:
            t = conn.execute("SELECT * FROM turnos_itinerario WHERE id = ?", (turno_id,)).fetchone()
            if not t:
                return None
            res = dict(t)
            res["regimen"] = res.get("regimen") or "TODOS"
            res["regimen_label"] = self.formatear_regimen_turno(res["regimen"])
            vueltas = conn.execute(
                "SELECT * FROM turnos_vueltas WHERE turno_id = ? ORDER BY nro_vuelta ASC",
                (turno_id,),
            ).fetchall()
            res["vueltas"] = [dict(v) for v in vueltas]
            return res

    def crear_turno_itinerario(
        self,
        payload: Dict[str, Any],
        vueltas: Optional[List[Dict[str, Any]]] = None,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        """Crea un nuevo turno de itinerario y sus vueltas asociadas."""
        codigo = str(payload.get("codigo_turno") or "").strip()
        base = str(payload.get("turno_base") or "").strip()
        if not codigo:
            raise ValueError("El código de turno es obligatorio.")
        if not base:
            base = codigo.split(".")[0]

        deposito = str(payload.get("deposito_base") or "PC").strip()
        tipo = str(payload.get("tipo_turno") or "DIAGRAMA_6D").strip()
        itinerario = str(payload.get("itinerario_vigente") or "45").strip()
        regimen = self.normalizar_regimen_turno(payload.get("regimen") or payload.get("dia") or "TODOS", default="TODOS")

        with self.db.write_transaction() as conn:
            exist = conn.execute(
                "SELECT id FROM turnos_itinerario WHERE codigo_turno = ? AND regimen = ? AND itinerario_vigente = ?",
                (codigo, regimen, itinerario),
            ).fetchone()
            if exist:
                raise ValueError(
                    f"Ya existe el turno '{codigo}' para el día '{self.formatear_regimen_turno(regimen)}' e itinerario '{itinerario}'."
                )

            cur = conn.execute(
                """INSERT INTO turnos_itinerario (
                    codigo_turno, regimen, turno_base, subindice, tipo_turno, deposito_base,
                    toma_servicio, deje_servicio, duracion_jornada, tiempo_toma, tiempo_deje,
                    itinerario_vigente, observaciones_turno, activo, sync_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now', 'localtime'), datetime('now', 'localtime'))""",
                (
                    codigo, regimen, base, payload.get("subindice"), tipo, deposito,
                    payload.get("toma_servicio"), payload.get("deje_servicio"),
                    payload.get("duracion_jornada"), payload.get("tiempo_toma"), payload.get("tiempo_deje"),
                    itinerario, payload.get("observaciones_turno"), int(payload.get("activo", 1)),
                ),
            )
            turno_id = cur.lastrowid

            if vueltas:
                for idx, v in enumerate(vueltas, start=1):
                    conn.execute(
                        """INSERT INTO turnos_vueltas (
                            turno_id, nro_vuelta, ref, nro_tren, origen, hora_salida,
                            destino, hora_llegada, observaciones_tramo, sync_status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now', 'localtime'), datetime('now', 'localtime'))""",
                        (
                            turno_id, v.get("nro_vuelta", idx), v.get("ref"), str(v.get("nro_tren") or ""),
                            v.get("origen"), v.get("hora_salida"), v.get("destino"), v.get("hora_llegada"),
                            v.get("observaciones_tramo"),
                        ),
                    )

            audit_payload = dict(payload)
            audit_payload["regimen"] = regimen
            audit_payload["vueltas_count"] = len(vueltas or [])
            self._audit(conn, user_id, windows_user, "crear", "turno_itinerario", turno_id, None, audit_payload)
            self._enqueue_sync(conn, "turnos_itinerario", turno_id, "INSERT", audit_payload)
            return turno_id

    def actualizar_turno_itinerario(
        self,
        turno_id: int,
        payload: Dict[str, Any],
        vueltas: Optional[List[Dict[str, Any]]] = None,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        """Actualiza un turno de itinerario existente y opcionalmente sus vueltas."""
        codigo = str(payload.get("codigo_turno") or "").strip()
        base = str(payload.get("turno_base") or "").strip()
        if not codigo:
            raise ValueError("El código de turno es obligatorio.")
        if not base:
            base = codigo.split(".")[0]

        deposito = str(payload.get("deposito_base") or "PC").strip()
        tipo = str(payload.get("tipo_turno") or "DIAGRAMA_6D").strip()
        itinerario = str(payload.get("itinerario_vigente") or "45").strip()

        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM turnos_itinerario WHERE id = ?", (turno_id,)).fetchone()
            if not before:
                raise ValueError("El turno no existe.")

            reg_fallback = dict(before).get("regimen") or "TODOS"
            reg_raw = payload.get("regimen") or payload.get("dia")
            regimen = self.normalizar_regimen_turno(reg_raw, default=reg_fallback) if reg_raw is not None else reg_fallback

            dup = conn.execute(
                "SELECT id FROM turnos_itinerario WHERE codigo_turno = ? AND regimen = ? AND itinerario_vigente = ? AND id != ?",
                (codigo, regimen, itinerario, turno_id),
            ).fetchone()
            if dup:
                raise ValueError(
                    f"Ya existe otro turno '{codigo}' para '{self.formatear_regimen_turno(regimen)}' e itinerario '{itinerario}'."
                )

            conn.execute(
                """UPDATE turnos_itinerario SET
                    codigo_turno = ?, regimen = ?, turno_base = ?, subindice = ?, tipo_turno = ?, deposito_base = ?,
                    toma_servicio = ?, deje_servicio = ?, duracion_jornada = ?, tiempo_toma = ?, tiempo_deje = ?,
                    itinerario_vigente = ?, observaciones_turno = ?, activo = ?,
                    sync_status = 'pending', updated_at = datetime('now', 'localtime')
                WHERE id = ?""",
                (
                    codigo, regimen, base, payload.get("subindice"), tipo, deposito,
                    payload.get("toma_servicio"), payload.get("deje_servicio"),
                    payload.get("duracion_jornada"), payload.get("tiempo_toma"), payload.get("tiempo_deje"),
                    itinerario, payload.get("observaciones_turno"), int(payload.get("activo", 1)),
                    turno_id,
                ),
            )

            if vueltas is not None:
                conn.execute("DELETE FROM turnos_vueltas WHERE turno_id = ?", (turno_id,))
                for idx, v in enumerate(vueltas, start=1):
                    conn.execute(
                        """INSERT INTO turnos_vueltas (
                            turno_id, nro_vuelta, ref, nro_tren, origen, hora_salida,
                            destino, hora_llegada, observaciones_tramo, sync_status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now', 'localtime'), datetime('now', 'localtime'))""",
                        (
                            turno_id, v.get("nro_vuelta", idx), v.get("ref"), str(v.get("nro_tren") or ""),
                            v.get("origen"), v.get("hora_salida"), v.get("destino"), v.get("hora_llegada"),
                            v.get("observaciones_tramo"),
                        ),
                    )

            after = dict(payload)
            after["regimen"] = regimen
            self._audit(conn, user_id, windows_user, "modificar", "turno_itinerario", turno_id, dict(before), after)
            self._enqueue_sync(conn, "turnos_itinerario", turno_id, "UPDATE", after)

    def cambiar_estado_turno(
        self,
        turno_id: int,
        activo: int,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        """Activa o desactiva (soft-delete) un turno de itinerario."""
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM turnos_itinerario WHERE id = ?", (turno_id,)).fetchone()
            if not before:
                raise ValueError("El turno no existe.")
            conn.execute(
                "UPDATE turnos_itinerario SET activo = ?, sync_status = 'pending', updated_at = datetime('now', 'localtime') WHERE id = ?",
                (int(activo), turno_id),
            )
            after = dict(before)
            after["activo"] = int(activo)
            self._audit(conn, user_id, windows_user, "cambiar_estado", "turno_itinerario", turno_id, dict(before), after)
            self._enqueue_sync(conn, "turnos_itinerario", turno_id, "UPDATE", after)

    def cambiar_estado_turnos_lote(
        self,
        turno_ids: Sequence[int],
        activo: Optional[int] = None,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        """Activa, desactiva o invierte el estado de múltiples turnos de itinerario en lote."""
        ids_clean = list(dict.fromkeys(int(tid) for tid in turno_ids if tid is not None))
        if not ids_clean:
            return 0
        actualizados = 0
        with self.db.write_transaction() as conn:
            for i in range(0, len(ids_clean), 400):
                chunk = ids_clean[i : i + 400]
                ph = ",".join("?" for _ in chunk)
                rows = [dict(r) for r in conn.execute(f"SELECT * FROM turnos_itinerario WHERE id IN ({ph})", chunk).fetchall()]
                for r in rows:
                    nuevo_act = (0 if r.get("activo") else 1) if activo is None else int(activo)
                    conn.execute(
                        "UPDATE turnos_itinerario SET activo = ?, sync_status = 'pending', updated_at = datetime('now', 'localtime') WHERE id = ?",
                        (nuevo_act, r["id"]),
                    )
                    after = dict(r)
                    after["activo"] = nuevo_act
                    self._enqueue_sync(conn, "turnos_itinerario", r["id"], "UPDATE", after)
                    actualizados += 1
            self._audit(
                conn,
                user_id,
                windows_user,
                "cambiar_estado_lote",
                "turno_itinerario",
                None,
                None,
                {"ids": ids_clean[:50], "total": actualizados, "activo": activo},
            )
        return actualizados

    def eliminar_turno_definitivo(
        self,
        turno_id: int,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        """Elimina permanentemente un turno de itinerario y sus vueltas."""
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM turnos_itinerario WHERE id = ?", (turno_id,)).fetchone()
            if not before:
                raise ValueError("El turno no existe.")
            conn.execute("DELETE FROM turnos_vueltas WHERE turno_id = ?", (turno_id,))
            conn.execute("DELETE FROM turnos_itinerario WHERE id = ?", (turno_id,))
            self._audit(conn, user_id, windows_user, "eliminar", "turno_itinerario", turno_id, dict(before), None)
            self._enqueue_sync(conn, "turnos_itinerario", turno_id, "DELETE", {"id": turno_id})

    def eliminar_turnos_lote(
        self,
        turno_ids: Sequence[int],
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        """Elimina permanentemente en lote una selección de turnos de itinerario y sus vueltas."""
        ids_clean = list(dict.fromkeys(int(tid) for tid in turno_ids if tid is not None))
        if not ids_clean:
            return 0

        eliminados = 0
        ejemplos: List[str] = []
        with self.db.write_transaction() as conn:
            for i in range(0, len(ids_clean), 400):
                chunk = ids_clean[i : i + 400]
                ph = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT id, codigo_turno, regimen, itinerario_vigente FROM turnos_itinerario WHERE id IN ({ph})",
                    chunk,
                ).fetchall()
                if not rows:
                    continue
                found_ids = [int(r[0]) for r in rows]
                for r in rows:
                    if len(ejemplos) < 25:
                        ejemplos.append(f"{r[1]} ({r[2]}/{r[3]})")
                ph_found = ",".join("?" for _ in found_ids)
                conn.execute(f"DELETE FROM turnos_vueltas WHERE turno_id IN ({ph_found})", found_ids)
                conn.execute(f"DELETE FROM turnos_itinerario WHERE id IN ({ph_found})", found_ids)
                for tid in found_ids:
                    self._enqueue_sync(conn, "turnos_itinerario", tid, "DELETE", {"id": tid})
                eliminados += len(found_ids)

            if eliminados > 0:
                self._audit(
                    conn,
                    user_id,
                    windows_user,
                    "eliminar_lote",
                    "turno_itinerario",
                    None,
                    {"eliminados": eliminados, "ejemplos": ejemplos},
                    None,
                )
        return eliminados

    def eliminar_turnos_por_filtros(
        self,
        criterio: str = "",
        deposito: Optional[str] = None,
        itinerario: Optional[str] = None,
        regimen: Optional[str] = None,
        incluir_inactivos: bool = True,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        """Elimina permanentemente todos los turnos (y sus vueltas) que coinciden con los filtros activos."""
        where_sql, params = self._build_turnos_admin_where(
            criterio=criterio,
            deposito=deposito,
            itinerario=itinerario,
            regimen=regimen,
            incluir_inactivos=incluir_inactivos,
        )
        with self.db.write_transaction() as conn:
            rows = conn.execute(
                f"SELECT ti.id, ti.codigo_turno FROM turnos_itinerario ti WHERE {where_sql}",
                params,
            ).fetchall()
            if not rows:
                return 0

            ids = [int(r[0]) for r in rows]
            ejemplos = [str(r[1]) for r in rows[:25]]
            for i in range(0, len(ids), 400):
                chunk = ids[i : i + 400]
                ph = ",".join("?" for _ in chunk)
                conn.execute(f"DELETE FROM turnos_vueltas WHERE turno_id IN ({ph})", chunk)
                conn.execute(f"DELETE FROM turnos_itinerario WHERE id IN ({ph})", chunk)
                for tid in chunk:
                    self._enqueue_sync(conn, "turnos_itinerario", tid, "DELETE", {"id": tid})

            self._audit(
                conn,
                user_id,
                windows_user,
                "eliminar_por_filtros",
                "turno_itinerario",
                None,
                {
                    "filtros": {
                        "criterio": criterio,
                        "deposito": deposito,
                        "itinerario": itinerario,
                        "regimen": regimen,
                        "incluir_inactivos": incluir_inactivos,
                    },
                    "eliminados": len(ids),
                    "ejemplos": ejemplos,
                },
                None,
            )
            return len(ids)

    # ---------------------------------------------------------
    # Catálogo de Estaciones, Siglas y Equivalencias
    # ---------------------------------------------------------

    def _obtener_indice_estaciones(
        self,
        conn: Optional[sqlite3.Connection] = None,
    ) -> CatalogoEstacionesIndex:
        """Construye un índice en memoria con el catálogo actual de estaciones activas."""
        def _cargar(c: sqlite3.Connection) -> List[Dict[str, Any]]:
            try:
                rows = c.execute(
                    """SELECT id, codigo_origen, nombre, sigla, aliases, activo
                       FROM estaciones
                       WHERE activo = 1
                       ORDER BY COALESCE(codigo_origen, 999999) ASC, nombre ASC"""
                ).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                return []

        if conn is not None:
            data = _cargar(conn)
        else:
            with self.db.read_connection() as read_conn:
                data = _cargar(read_conn)

        return CatalogoEstacionesIndex(data if data else ESTACIONES_BASE)

    def obtener_sugerencias_estaciones(self) -> List[str]:
        """Devuelve la lista de nombres, alias y siglas de estaciones para autocompletado."""
        return self._obtener_indice_estaciones().obtener_completado_estaciones()

    def listar_estaciones(
        self,
        criterio: str = "",
        incluir_inactivos: bool = False,
    ) -> List[Dict[str, Any]]:
        """Lista el catálogo de estaciones filtrado por nombre, sigla, alias o código."""
        query = """
            SELECT id, codigo_origen, nombre, sigla, aliases, activo, created_at, updated_at
            FROM estaciones
        """
        if not incluir_inactivos:
            query += " WHERE activo = 1"
        query += " ORDER BY COALESCE(codigo_origen, 999999) ASC, nombre ASC"

        with self.db.read_connection() as conn:
            rows = [dict(r) for r in conn.execute(query).fetchall()]

        crit = str(criterio or "").strip()
        if not crit:
            return rows

        crit_norm = normalizar_texto_estacion(crit)
        crit_up = crit.upper()
        idx = CatalogoEstacionesIndex([dict(r, activo=1) for r in rows] if rows else ESTACIONES_BASE)
        es_sigla_exacta = crit_up in idx._por_sigla_upper
        filtradas: List[Dict[str, Any]] = []
        for r in rows:
            cod_txt = str(r.get("codigo_origen") or "").strip()
            nom_norm = normalizar_texto_estacion(r.get("nombre"))
            sig_up = str(r.get("sigla") or "").strip().upper()
            als_norm = normalizar_texto_estacion(r.get("aliases"))
            if es_sigla_exacta:
                coincide = (
                    cod_txt == crit
                    or sig_up == crit_up
                    or idx.coincide_filtro_estacion(r.get("nombre"), crit)
                )
            else:
                coincide = (
                    cod_txt == crit
                    or (crit_norm and crit_norm in nom_norm)
                    or (crit_norm and crit_norm in als_norm)
                    or (sig_up and (crit_up == sig_up or crit_up in sig_up))
                    or idx.coincide_filtro_estacion(r.get("nombre"), crit)
                )
            if coincide:
                filtradas.append(r)
        return filtradas

    def guardar_estacion(
        self,
        datos: Dict[str, Any],
        estacion_id: Optional[int] = None,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        """Crea o actualiza una estación con su sigla y alias equivalentes."""
        nombre = str(datos.get("nombre") or "").strip()
        if not nombre:
            raise ValueError("El nombre de la estación es obligatorio.")

        sigla = str(datos.get("sigla") or "").strip().upper()
        aliases_tupla = parsear_aliases_estacion(datos.get("aliases"))
        aliases_str = ", ".join(aliases_tupla)
        activo = int(datos.get("activo", 1))
        cod_raw = datos.get("codigo_origen")
        try:
            codigo_origen = int(cod_raw) if cod_raw not in (None, "", "-") else None
        except (ValueError, TypeError):
            codigo_origen = None

        with self.db.write_transaction() as conn:
            dup = conn.execute(
                "SELECT id FROM estaciones WHERE LOWER(TRIM(nombre)) = LOWER(TRIM(?)) AND (? IS NULL OR id != ?)",
                (nombre, estacion_id, estacion_id),
            ).fetchone()
            if dup:
                raise ValueError(f"Ya existe una estación registrada con el nombre '{nombre}'.")

            if estacion_id is None:
                cur = conn.execute(
                    """INSERT INTO estaciones (codigo_origen, nombre, sigla, aliases, activo, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))""",
                    (codigo_origen, nombre, sigla, aliases_str, activo),
                )
                eid = int(cur.lastrowid)
                after = dict(conn.execute("SELECT * FROM estaciones WHERE id = ?", (eid,)).fetchone())
                self._audit(conn, user_id, windows_user, "crear", "estacion", eid, None, after)
                return eid

            before = conn.execute("SELECT * FROM estaciones WHERE id = ?", (estacion_id,)).fetchone()
            if not before:
                raise ValueError("La estación indicada no existe.")
            conn.execute(
                """UPDATE estaciones
                   SET codigo_origen = ?, nombre = ?, sigla = ?, aliases = ?, activo = ?,
                       updated_at = datetime('now', 'localtime')
                   WHERE id = ?""",
                (codigo_origen, nombre, sigla, aliases_str, activo, estacion_id),
            )
            after = dict(conn.execute("SELECT * FROM estaciones WHERE id = ?", (estacion_id,)).fetchone())
            self._audit(conn, user_id, windows_user, "modificar", "estacion", estacion_id, dict(before), after)
            return int(estacion_id)

    def cambiar_estado_estacion(
        self,
        estacion_id: int,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        """Activa o desactiva una estación del catálogo."""
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM estaciones WHERE id = ?", (estacion_id,)).fetchone()
            if not before:
                raise ValueError("La estación no existe.")
            nuevo = 0 if int(before["activo"]) == 1 else 1
            conn.execute(
                "UPDATE estaciones SET activo = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
                (nuevo, estacion_id),
            )
            after = dict(before)
            after["activo"] = nuevo
            self._audit(conn, user_id, windows_user, "cambiar_estado", "estacion", estacion_id, dict(before), after)

    def importar_estaciones_excel(
        self,
        file_path: str,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Importa o actualiza el catálogo de estaciones y siglas desde Excel (ej. estaciones.xlsx)."""
        import openpyxl

        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws_sel = None
        col_id = col_nom = col_sig = col_alias = None
        hdr_row = 1

        for ws in wb.worksheets:
            for r in range(1, min(6, ws.max_row + 1)):
                headers = [normalizar_texto_estacion(ws.cell(r, c).value) for c in range(1, ws.max_column + 1)]
                c_n = next((i + 1 for i, h in enumerate(headers) if h in ("nombre", "estacion", "nombre estacion") or "nombre" in h), None)
                c_s = next((i + 1 for i, h in enumerate(headers) if "sigla" in h or "abrev" in h), None)
                if c_n and c_s:
                    ws_sel = ws
                    hdr_row = r
                    col_nom = c_n
                    col_sig = c_s
                    col_id = next((i + 1 for i, h in enumerate(headers) if h in ("id", "codigo", "codigo origen", "nro")), None)
                    col_alias = next((i + 1 for i, h in enumerate(headers) if "alias" in h or "equival" in h), None)
                    break
            if ws_sel is not None:
                break

        if ws_sel is None or not col_nom or not col_sig:
            raise ValueError(
                "El archivo Excel no contiene columnas válidas de estaciones ('Nombre' y 'Sigla')."
            )

        insertados = 0
        actualizados = 0
        with self.db.write_transaction() as conn:
            existentes = [dict(r) for r in conn.execute("SELECT * FROM estaciones").fetchall()]
            por_codigo = {int(r["codigo_origen"]): r for r in existentes if r.get("codigo_origen") is not None}
            por_nombre_norm = {normalizar_texto_estacion(r["nombre"]): r for r in existentes}

            for r in range(hdr_row + 1, ws_sel.max_row + 1):
                nom_raw = str(ws_sel.cell(r, col_nom).value or "").strip()
                sig_raw = str(ws_sel.cell(r, col_sig).value or "").strip().upper()
                if not nom_raw:
                    continue

                cod_val = None
                if col_id and ws_sel.cell(r, col_id).value is not None:
                    try:
                        cod_val = int(ws_sel.cell(r, col_id).value)
                    except (ValueError, TypeError):
                        cod_val = None

                alias_excel = parsear_aliases_estacion(ws_sel.cell(r, col_alias).value if col_alias else "")
                nom_norm = normalizar_texto_estacion(nom_raw)

                prev = None
                if cod_val is not None and cod_val in por_codigo:
                    prev = por_codigo[cod_val]
                elif nom_norm in por_nombre_norm:
                    prev = por_nombre_norm[nom_norm]

                if prev:
                    aliases_prev = parsear_aliases_estacion(prev.get("aliases"))
                    extras = [prev["nombre"]] if normalizar_texto_estacion(prev["nombre"]) != nom_norm else []
                    aliases_merged = parsear_aliases_estacion([*aliases_prev, *alias_excel, *extras])
                    aliases_str = ", ".join(aliases_merged)
                    conn.execute(
                        """UPDATE estaciones
                           SET codigo_origen = COALESCE(?, codigo_origen),
                               nombre = ?, sigla = ?, aliases = ?, activo = 1,
                               updated_at = datetime('now', 'localtime')
                           WHERE id = ?""",
                        (cod_val, nom_raw, sig_raw, aliases_str, prev["id"]),
                    )
                    actualizados += 1
                else:
                    aliases_str = ", ".join(alias_excel)
                    cur = conn.execute(
                        """INSERT INTO estaciones (codigo_origen, nombre, sigla, aliases, activo, created_at, updated_at)
                           VALUES (?, ?, ?, ?, 1, datetime('now', 'localtime'), datetime('now', 'localtime'))""",
                        (cod_val, nom_raw, sig_raw, aliases_str),
                    )
                    nuevo_row = {"id": cur.lastrowid, "codigo_origen": cod_val, "nombre": nom_raw, "sigla": sig_raw, "aliases": aliases_str}
                    if cod_val is not None:
                        por_codigo[cod_val] = nuevo_row
                    por_nombre_norm[nom_norm] = nuevo_row
                    insertados += 1

            self._audit(
                conn,
                user_id,
                windows_user,
                "importar",
                "estaciones",
                None,
                None,
                {"insertados": insertados, "actualizados": actualizados, "archivo": Path(file_path).name},
            )
        return insertados, actualizados

    # ---------------------------------------------------------
    # Estaciones Intermedias y Horarios de Paso por Tren (tren_paradas)
    # ---------------------------------------------------------

    def listar_itinerarios_paradas(self) -> List[str]:
        """Devuelve la unión ordenada de itinerarios existentes en tren_paradas y turnos_itinerario."""
        with self.db.read_connection() as conn:
            r1 = conn.execute(
                "SELECT DISTINCT TRIM(itinerario) FROM tren_paradas WHERE TRIM(itinerario) != ''"
            ).fetchall()
            r2 = conn.execute(
                "SELECT DISTINCT TRIM(itinerario_vigente) FROM turnos_itinerario WHERE activo = 1 AND TRIM(itinerario_vigente) != ''"
            ).fetchall()
        vals = {str(r[0]).strip() for r in (*r1, *r2) if r and str(r[0] or "").strip()}
        return sorted(vals)

    @staticmethod
    def inferir_itinerario_desde_archivo(file_path: str) -> str:
        """Extrae el código de itinerario (ej. 'H5', '45') del nombre de archivo si está presente."""
        stem = Path(file_path).stem
        m = re.search(r"itinerario[_\s\-]+([a-zA-Z]?\d+[a-zA-Z]?)", stem, re.IGNORECASE)
        if m:
            return m.group(1).upper()
        return ""

    @staticmethod
    def _inferir_regimen_hoja(sheet_title: str, default_reg: str = "H") -> str:
        t = normalizar_texto_estacion(sheet_title)
        if not t:
            return default_reg
        tokens = [tok for tok in re.split(r"[\s_\-/]+", t) if tok]
        token_set = set(tokens)
        if "sab" in t or "sabado" in token_set or "s" in token_set or re.match(r"^s(?:\s|_|-|$)", t):
            return "S"
        if "dom" in t or "feriad" in t or "df" in token_set or "d" in token_set or re.match(r"^d(?:\s|_|-|$)", t):
            return "D"
        if "hab" in t or "lun" in t or "lv" in token_set or "lav" in token_set or "h" in token_set or re.match(r"^h(?:\s|_|-|$)", t):
            return "H"
        return default_reg

    def _enqueue_sync_delete_lote(
        self,
        conn: sqlite3.Connection,
        tabla: str,
        ids: Sequence[int],
    ) -> None:
        if not ids:
            return
        ahora = self.db.now()
        rows_sync = [
            (tabla, str(int(rid)), "DELETE", json.dumps({"id": int(rid)}, ensure_ascii=False), ahora)
            for rid in ids
        ]
        conn.executemany(
            """INSERT INTO sync_outbox (tabla, registro_pk, operacion, payload_json, creado_en)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(tabla, registro_pk) DO UPDATE SET
                   operacion = excluded.operacion,
                   payload_json = excluded.payload_json,
                   creado_en = excluded.creado_en""",
            rows_sync,
        )

    def _build_paradas_where(
        self,
        nro_tren: Optional[str] = None,
        itinerario: Optional[str] = None,
        regimen: Optional[str] = None,
    ) -> Tuple[str, List[Any]]:
        clauses: List[str] = ["1=1"]
        params: List[Any] = []
        if nro_tren and str(nro_tren).strip():
            nt = str(nro_tren).strip()
            clauses.append("(nro_tren = ? OR nro_tren LIKE ?)")
            params.extend([nt, f"%{nt}%"])
        if itinerario and str(itinerario).strip() not in ("", "Todos", "Todas"):
            clauses.append("itinerario = ?")
            params.append(str(itinerario).strip())
        if regimen and str(regimen).strip() not in ("", "Todos", "Todas"):
            reg_norm = self.normalizar_regimen_parada(regimen)
            if reg_norm == "H":
                clauses.append(
                    "UPPER(TRIM(COALESCE(regimen, ''))) IN ('', 'H', 'LV', 'L-V', 'LAV', 'L A V', 'HABIL', 'HÁBIL', 'DIA HABIL', 'DÍA HÁBIL', 'LUNES A VIERNES')"
                )
            elif reg_norm == "S":
                clauses.append("UPPER(TRIM(COALESCE(regimen, ''))) IN ('S', 'SAB', 'SABADO', 'SÁBADO')")
            elif reg_norm == "D":
                clauses.append(
                    "UPPER(TRIM(COALESCE(regimen, ''))) IN ('D', 'DOM', 'DOMINGO', 'DF', 'FERIADO', 'DOM / FERIADO')"
                )
            else:
                clauses.append("UPPER(TRIM(COALESCE(regimen, ''))) = UPPER(TRIM(?))")
                params.append(reg_norm)
        return " AND ".join(clauses), params

    def listar_paradas_tren(
        self,
        nro_tren: Optional[str] = None,
        itinerario: Optional[str] = None,
        estacion: Optional[str] = None,
        regimen: Optional[str] = None,
        limite: int = 5000,
    ) -> List[Dict[str, Any]]:
        """Lista las estaciones intermedias y horarios de paso por tren con filtro por nombre o sigla."""
        where_sql, params = self._build_paradas_where(
            nro_tren=nro_tren,
            itinerario=itinerario,
            regimen=regimen,
        )
        query = f"""
            SELECT id, nro_tren, itinerario, regimen, orden_parada, estacion, hora_paso, created_at, updated_at
            FROM tren_paradas
            WHERE {where_sql}
            ORDER BY
                CASE WHEN TRIM(nro_tren) GLOB '[0-9]*' THEN CAST(TRIM(nro_tren) AS INTEGER) ELSE 999999999 END ASC,
                nro_tren ASC,
                itinerario ASC,
                CASE UPPER(TRIM(regimen)) WHEN 'H' THEN 1 WHEN 'LV' THEN 1 WHEN 'S' THEN 2 WHEN 'D' THEN 3 ELSE 4 END ASC,
                orden_parada ASC,
                hora_paso ASC
        """
        est_filtro = str(estacion or "").strip()
        if not est_filtro and limite is not None:
            query += " LIMIT ?"
            params.append(int(limite))

        with self.db.read_connection() as conn:
            idx_est = self._obtener_indice_estaciones(conn)
            rows = conn.execute(query, params).fetchall()

        resultado: List[Dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            est_txt = str(item.get("estacion") or "").strip()
            if est_filtro and not idx_est.coincide_filtro_estacion(est_txt, est_filtro):
                continue
            reg_norm = self.normalizar_regimen_parada(item.get("regimen"), default="H")
            item["regimen"] = reg_norm
            item["regimen_label"] = self.formatear_regimen_turno(reg_norm)
            item["sigla"] = idx_est.obtener_sigla_estacion(est_txt)
            resultado.append(item)
            if limite is not None and len(resultado) >= limite:
                break
        return resultado

    def contar_paradas_tren_por_filtros(
        self,
        nro_tren: Optional[str] = None,
        itinerario: Optional[str] = None,
        estacion: Optional[str] = None,
        regimen: Optional[str] = None,
    ) -> int:
        """Devuelve la cantidad exacta en base de paradas por tren que coinciden con los filtros activos."""
        where_sql, params = self._build_paradas_where(
            nro_tren=nro_tren,
            itinerario=itinerario,
            regimen=regimen,
        )
        est_filtro = str(estacion or "").strip()
        with self.db.read_connection() as conn:
            if not est_filtro:
                row = conn.execute(f"SELECT COUNT(*) FROM tren_paradas WHERE {where_sql}", params).fetchone()
                return int(row[0]) if row else 0
            idx_est = self._obtener_indice_estaciones(conn)
            rows = conn.execute(f"SELECT estacion FROM tren_paradas WHERE {where_sql}", params).fetchall()
            return sum(1 for r in rows if idx_est.coincide_filtro_estacion(str(r[0] or "").strip(), est_filtro))

    def crear_parada_tren(
        self,
        datos: Dict[str, Any],
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        """Crea una parada intermedia / horario de paso para un tren."""
        nro_tren = str(datos.get("nro_tren") or "").strip()
        estacion = str(datos.get("estacion") or "").strip()
        hora_paso = str(datos.get("hora_paso") or "").strip()
        if not nro_tren:
            raise ValueError("El número de tren es obligatorio.")
        if not estacion:
            raise ValueError("La estación de parada es obligatoria.")
        if not hora_paso:
            raise ValueError("La hora de paso es obligatoria.")

        itinerario = str(datos.get("itinerario") or "").strip()
        regimen = self.normalizar_regimen_parada(datos.get("regimen"), default="H")
        try:
            orden_parada = int(datos.get("orden_parada") or 1)
        except (ValueError, TypeError):
            orden_parada = 1

        with self.db.write_transaction() as conn:
            cur = conn.execute(
                """INSERT INTO tren_paradas (nro_tren, itinerario, regimen, orden_parada, estacion, hora_paso, sync_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', datetime('now', 'localtime'), datetime('now', 'localtime'))""",
                (nro_tren, itinerario, regimen, orden_parada, estacion, hora_paso),
            )
            pid = cur.lastrowid
            after = dict(conn.execute("SELECT * FROM tren_paradas WHERE id = ?", (pid,)).fetchone())
            self._audit(conn, user_id, windows_user, "crear", "tren_parada", pid, None, after)
            self._enqueue_sync(conn, "tren_paradas", pid, "INSERT", after)
            return pid

    def actualizar_parada_tren(
        self,
        parada_id: int,
        datos: Dict[str, Any],
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        """Actualiza una parada intermedia de tren."""
        nro_tren = str(datos.get("nro_tren") or "").strip()
        estacion = str(datos.get("estacion") or "").strip()
        hora_paso = str(datos.get("hora_paso") or "").strip()
        if not nro_tren or not estacion or not hora_paso:
            raise ValueError("N° de Tren, Estación y Hora de Paso son obligatorios.")

        itinerario = str(datos.get("itinerario") or "").strip()
        regimen = self.normalizar_regimen_parada(datos.get("regimen"), default="H")
        try:
            orden_parada = int(datos.get("orden_parada") or 1)
        except (ValueError, TypeError):
            orden_parada = 1

        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM tren_paradas WHERE id = ?", (parada_id,)).fetchone()
            if not before:
                raise ValueError("La parada de tren no existe.")
            conn.execute(
                """UPDATE tren_paradas SET
                   nro_tren=?, itinerario=?, regimen=?, orden_parada=?, estacion=?, hora_paso=?,
                   sync_status='pending', updated_at=datetime('now', 'localtime')
                   WHERE id=?""",
                (nro_tren, itinerario, regimen, orden_parada, estacion, hora_paso, parada_id),
            )
            after = dict(conn.execute("SELECT * FROM tren_paradas WHERE id = ?", (parada_id,)).fetchone())
            self._audit(conn, user_id, windows_user, "modificar", "tren_parada", parada_id, dict(before), after)
            self._enqueue_sync(conn, "tren_paradas", parada_id, "UPDATE", after)

    def eliminar_parada_tren(
        self,
        parada_id: int,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> None:
        """Elimina una parada de tren."""
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM tren_paradas WHERE id = ?", (parada_id,)).fetchone()
            if not before:
                raise ValueError("La parada de tren no existe.")
            conn.execute("DELETE FROM tren_paradas WHERE id = ?", (parada_id,))
            self._audit(conn, user_id, windows_user, "eliminar", "tren_parada", parada_id, dict(before), None)
            self._enqueue_sync(conn, "tren_paradas", parada_id, "DELETE", {"id": parada_id})

    def eliminar_paradas_tren_lote(
        self,
        parada_ids: Sequence[int],
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        """Elimina permanentemente en lote una selección de paradas de tren."""
        ids_clean = list(dict.fromkeys(int(pid) for pid in parada_ids if pid is not None))
        if not ids_clean:
            return 0

        eliminados = 0
        ejemplos: List[str] = []
        with self.db.write_transaction() as conn:
            for i in range(0, len(ids_clean), 400):
                chunk = ids_clean[i : i + 400]
                ph = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT id, nro_tren, estacion, itinerario, regimen FROM tren_paradas WHERE id IN ({ph})",
                    chunk,
                ).fetchall()
                if not rows:
                    continue
                found_ids = [int(r[0]) for r in rows]
                for r in rows:
                    if len(ejemplos) < 25:
                        ejemplos.append(f"Tren {r[1]} - {r[2]} ({r[4]}/{r[3]})")
                ph_found = ",".join("?" for _ in found_ids)
                conn.execute(f"DELETE FROM tren_paradas WHERE id IN ({ph_found})", found_ids)
                self._enqueue_sync_delete_lote(conn, "tren_paradas", found_ids)
                eliminados += len(found_ids)

            if eliminados > 0:
                self._audit(
                    conn,
                    user_id,
                    windows_user,
                    "eliminar_lote",
                    "tren_parada",
                    None,
                    {"eliminados": eliminados, "ejemplos": ejemplos},
                    None,
                )
        return eliminados

    def eliminar_paradas_tren_por_filtros(
        self,
        nro_tren: Optional[str] = None,
        itinerario: Optional[str] = None,
        estacion: Optional[str] = None,
        regimen: Optional[str] = None,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        """Elimina permanentemente todas las paradas que coincidan con los filtros indicados."""
        where_sql, params = self._build_paradas_where(
            nro_tren=nro_tren,
            itinerario=itinerario,
            regimen=regimen,
        )
        est_filtro = str(estacion or "").strip()
        with self.db.write_transaction() as conn:
            rows = conn.execute(
                f"SELECT id, nro_tren, estacion, itinerario, regimen FROM tren_paradas WHERE {where_sql}",
                params,
            ).fetchall()
            if est_filtro:
                idx_est = self._obtener_indice_estaciones(conn)
                rows = [
                    r for r in rows
                    if idx_est.coincide_filtro_estacion(str(r[2] or "").strip(), est_filtro)
                ]
            if not rows:
                return 0

            ids = [int(r[0]) for r in rows]
            ejemplos = [f"Tren {r[1]} - {r[2]} ({r[4]}/{r[3]})" for r in rows[:25]]
            for i in range(0, len(ids), 400):
                chunk = ids[i : i + 400]
                ph = ",".join("?" for _ in chunk)
                conn.execute(f"DELETE FROM tren_paradas WHERE id IN ({ph})", chunk)
                self._enqueue_sync_delete_lote(conn, "tren_paradas", chunk)

            self._audit(
                conn,
                user_id,
                windows_user,
                "eliminar_por_filtros",
                "tren_parada",
                None,
                {
                    "filtros": {
                        "nro_tren": nro_tren,
                        "itinerario": itinerario,
                        "estacion": estacion,
                        "regimen": regimen,
                    },
                    "eliminados": len(ids),
                    "ejemplos": ejemplos,
                },
                None,
            )
            return len(ids)

    def eliminar_todas_paradas_tren(
        self,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        """Elimina permanentemente todas las paradas registradas en tren_paradas."""
        with self.db.write_transaction() as conn:
            ids = [int(r[0]) for r in conn.execute("SELECT id FROM tren_paradas").fetchall()]
            if not ids:
                return 0
            conn.execute("DELETE FROM tren_paradas")
            self._enqueue_sync_delete_lote(conn, "tren_paradas", ids)
            self._audit(
                conn,
                user_id,
                windows_user,
                "eliminar_todas",
                "tren_parada",
                None,
                {"eliminados": len(ids)},
                None,
            )
            return len(ids)

    def importar_paradas_tren_excel(
        self,
        file_path: str,
        itinerario_default: Optional[str] = None,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        """Importa estaciones intermedias y horarios de paso por tren desde un archivo Excel.

        Soporta:
        1) Libros con múltiples hojas por ramal/régimen (ej. Hab ASC CÑ, Sab DES CÑ, DOM ASC CÑ).
        2) Cuadros horarios matriciales SOFSE (columna KM + columna Estación/Tren + múltiples columnas numéricas de tren, excluyendo filas de pie como Formación, Locomotora, Sigue con y Sale) y matrices simples (Col 1=Estación, Col 2..N=Trenes).
        3) Planillas dedicadas con columnas estándar (TREN, ESTACION, HORA, ORDEN, ITINERARIO, REGIMEN).
        4) Planillas de itinerarios estándar (ORIGEN, HORA_SALIDA, DESTINO, HORA_LLEGADA por vuelta).
        5) Importación masiva idempotente en lote con deduplicación por tren, itinerario, régimen, estación equivalente y hora.
        """
        import openpyxl
        from datetime import time as dt_time, datetime as dt_datetime

        def _parse_hr(val: Any) -> Optional[str]:
            if val is None or isinstance(val, bool):
                return None
            if isinstance(val, (dt_time, dt_datetime)):
                return val.strftime("%H:%M")
            if isinstance(val, (int, float)):
                return None
            val_s = str(val).strip()
            if not val_s or val_s in ("None", "-", ".", "..", "--", "---"):
                return None
            m_hr = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", val_s)
            if m_hr:
                h, m = int(m_hr.group(1)), int(m_hr.group(2))
                if 0 <= h <= 23 and 0 <= m <= 59:
                    return f"{h:02d}:{m:02d}"
            return None

        def _parse_tren_header(val: Any) -> Optional[str]:
            if val is None or isinstance(val, bool):
                return None
            if isinstance(val, int):
                return str(val)
            if isinstance(val, float):
                return str(int(val)) if val.is_integer() else None
            val_s = str(val).strip()
            if not val_s:
                return None
            cleaned = re.sub(r"^(?:TREN|NRO\.?|N[°º]\.?|#)\s*", "", val_s, flags=re.IGNORECASE).strip()
            if re.fullmatch(r"\d+\.0+", cleaned):
                cleaned = cleaned.split(".", 1)[0]
            return cleaned if cleaned.isdigit() else None

        FOOTER_ROW_KEYS = {
            "estacion",
            "tipo",
            "tren",
            "trenes",
            "tren n",
            "tren nº",
            "tren nro",
            "kilometro",
            "kilometros",
            "km",
            "formacion",
            "locomotora",
            "sigue con",
            "vuelve con",
            "sale",
            "hora salida",
            "observaciones",
            "total",
            "desde",
            "hasta",
        }

        itin_archivo = self.inferir_itinerario_desde_archivo(file_path)
        itin_fallback_base = str(itinerario_default).strip() if itinerario_default and str(itinerario_default).strip() else itin_archivo

        wb = openpyxl.load_workbook(file_path, data_only=True)
        registros_a_insertar: List[Tuple[str, str, str, int, str, str]] = []
        orden_tracker: Dict[Tuple[str, str, str], int] = {}
        vio_catalogo_estaciones = False

        for ws in wb.worksheets:
            if ws.max_row < 2:
                continue

            # Detectar si la hoja corresponde únicamente a un catálogo de estaciones (Id, Nombre, Sigla)
            first_rows_headers = [
                {normalizar_texto_estacion(ws.cell(r, c).value) for c in range(1, ws.max_column + 1)}
                for r in range(1, min(4, ws.max_row + 1))
            ]
            has_cat_cols = any(
                ("nombre" in hs or "estacion" in hs) and any("sigla" in h or "abrev" in h for h in hs)
                for hs in first_rows_headers
            )

            # Buscar la fila cabecera dentro de las primeras 10 filas
            best_hdr = None
            max_r_scan = min(10, ws.max_row)
            for r in range(1, max_r_scan + 1):
                headers_raw = [str(ws.cell(r, c).value or "").strip().upper() for c in range(1, ws.max_column + 1)]
                col_map: Dict[str, int] = {}
                for idx_c, h in enumerate(headers_raw):
                    if h and h not in col_map:
                        col_map[h] = idx_c + 1

                def _find_col(*candidates: str) -> Optional[int]:
                    for cand in candidates:
                        for h_name, c_idx in col_map.items():
                            if cand in h_name:
                                return c_idx
                    return None

                c_tren = _find_col("NRO_TREN", "TREN", "N° TREN")
                c_est = _find_col("ESTACION", "ESTACIÓN", "PARADA")
                c_hora = _find_col("HORA_PASO", "HORA", "PASO", "HORARIO")
                c_orden = _find_col("ORDEN", "SECUENCIA")
                c_itin = _find_col("ITINERARIO", "CIRC", "VIGENCIA")
                c_reg = next(
                    (
                        c_idx
                        for h_name, c_idx in col_map.items()
                        if "REGIMEN" in h_name or "RÉGIMEN" in h_name or h_name in ("DIA", "DÍA", "SERVICIO")
                    ),
                    None,
                )
                c_origen = _find_col("ORIGEN", "DESDE", "SALE DE")
                c_h_sal = _find_col("SALIDA", "H_SALIDA", "HORA_SALIDA")
                c_destino = _find_col("DESTINO", "HASTA", "LLEGA A")
                c_h_lleg = _find_col("LLEGADA", "H_LLEGADA", "HORA_LLEGADA")

                matrix_tren_cols: List[Tuple[int, str]] = []
                if not c_hora and not c_h_sal:
                    for c in range(2, ws.max_column + 1):
                        nt_col = _parse_tren_header(ws.cell(r, c).value)
                        if nt_col:
                            matrix_tren_cols.append((c, nt_col))

                if len(matrix_tren_cols) >= 2:
                    best_hdr = (r, "matrix", matrix_tren_cols, c_tren, c_est, c_hora, c_orden, c_itin, c_reg, c_origen, c_h_sal, c_destino, c_h_lleg)
                    break
                if len(matrix_tren_cols) == 1 and not c_est and (c_tren or any(_parse_hr(ws.cell(rr, matrix_tren_cols[0][0]).value) for rr in range(r + 1, min(ws.max_row + 1, r + 40)))):
                    best_hdr = (r, "matrix", matrix_tren_cols, c_tren, c_est, c_hora, c_orden, c_itin, c_reg, c_origen, c_h_sal, c_destino, c_h_lleg)
                    break
                if c_tren and c_est and c_hora:
                    best_hdr = (r, "columnar", [], c_tren, c_est, c_hora, c_orden, c_itin, c_reg, c_origen, c_h_sal, c_destino, c_h_lleg)
                    break
                if c_tren and c_origen and c_h_sal and c_destino and c_h_lleg:
                    best_hdr = (r, "vueltas", [], c_tren, c_est, c_hora, c_orden, c_itin, c_reg, c_origen, c_h_sal, c_destino, c_h_lleg)
                    break

            if has_cat_cols and (best_hdr is None):
                vio_catalogo_estaciones = True
                continue

            sheet_reg_default = self._inferir_regimen_hoja(ws.title, default_reg="H")
            sheet_itin_default = itin_fallback_base or str(ws.title or "").strip()

            if best_hdr and best_hdr[1] == "matrix":
                hdr_row, _, matrix_tren_cols, _, c_est, _, _, _, _, _, _, _, _ = best_hdr
                first_tren_col = min(c for c, _ in matrix_tren_cols)

                # Detectar automáticamente la columna que contiene los nombres de estación
                if first_tren_col <= 2:
                    col_estacion = 1
                elif c_est and c_est < first_tren_col:
                    col_estacion = c_est
                else:
                    best_col = first_tren_col - 1
                    best_score = -1
                    for cand_c in range(1, first_tren_col):
                        score = 0
                        for rr in range(hdr_row + 1, min(ws.max_row + 1, hdr_row + 60)):
                            v = ws.cell(rr, cand_c).value
                            if isinstance(v, str):
                                s_clean = re.sub(r"\s+", " ", v).strip()
                                s_norm = normalizar_texto_estacion(s_clean)
                                if (
                                    len(s_clean) >= 3
                                    and s_norm not in FOOTER_ROW_KEYS
                                    and s_norm != "pc"
                                    and not re.fullmatch(r"[\d.,\-]+", s_clean)
                                ):
                                    score += 1
                        if score > best_score:
                            best_score = score
                            best_col = cand_c
                    col_estacion = best_col

                for r_idx in range(hdr_row + 1, ws.max_row + 1):
                    raw_est = ws.cell(r_idx, col_estacion).value
                    if raw_est is None or isinstance(raw_est, (int, float, dt_time, dt_datetime)):
                        continue
                    est_val = re.sub(r"\s+", " ", str(raw_est)).strip()
                    if not est_val:
                        continue
                    est_norm = normalizar_texto_estacion(est_val)
                    est_comp = _clave_compacta_estacion(est_val, quitar_parentesis=False)
                    if (
                        est_norm in FOOTER_ROW_KEYS
                        or est_comp in FOOTER_ROW_KEYS
                        or est_norm.startswith("formacion")
                        or est_norm.startswith("locomotora")
                        or est_norm.startswith("sigue con")
                        or est_norm.startswith("tren n")
                        or re.fullmatch(r"[\d.,\-]+", est_val)
                    ):
                        continue

                    for c_idx, nt in matrix_tren_cols:
                        hr = _parse_hr(ws.cell(r_idx, c_idx).value)
                        if hr:
                            ord_key = (nt, sheet_itin_default, sheet_reg_default)
                            orden_tracker[ord_key] = orden_tracker.get(ord_key, 0) + 1
                            registros_a_insertar.append(
                                (nt, sheet_itin_default, sheet_reg_default, orden_tracker[ord_key], est_val, hr)
                            )
                continue

            if best_hdr:
                hdr_row, mode, _, c_tren, c_est, c_hora, c_orden, c_itin, c_reg, c_origen, c_h_sal, c_destino, c_h_lleg = best_hdr
            else:
                hdr_row, mode = 1, "positional"
                c_tren = c_est = c_hora = c_orden = c_itin = c_reg = None
                c_origen = c_h_sal = c_destino = c_h_lleg = None

            for r_idx in range(hdr_row + 1, ws.max_row + 1):
                if mode == "columnar" and c_tren and c_est and c_hora:
                    nt = str(ws.cell(r_idx, c_tren).value or "").strip()
                    if nt.endswith(".0") and nt[:-2].isdigit():
                        nt = nt[:-2]
                    est = re.sub(r"\s+", " ", str(ws.cell(r_idx, c_est).value or "")).strip()
                    hr = _parse_hr(ws.cell(r_idx, c_hora).value)
                    if not nt or not est or not hr or nt in ("None", "-"):
                        continue
                    cell_itin = str(ws.cell(r_idx, c_itin).value or "").strip() if c_itin else ""
                    itin = cell_itin or sheet_itin_default
                    cell_reg = str(ws.cell(r_idx, c_reg).value or "").strip() if c_reg else ""
                    reg = self.normalizar_regimen_parada(cell_reg or sheet_reg_default, default=sheet_reg_default)
                    ord_key = (nt, itin, reg)
                    if c_orden and ws.cell(r_idx, c_orden).value is not None:
                        try:
                            ord_num = int(ws.cell(r_idx, c_orden).value)
                        except (ValueError, TypeError):
                            orden_tracker[ord_key] = orden_tracker.get(ord_key, 0) + 1
                            ord_num = orden_tracker[ord_key]
                    else:
                        orden_tracker[ord_key] = orden_tracker.get(ord_key, 0) + 1
                        ord_num = orden_tracker[ord_key]
                    registros_a_insertar.append((nt, itin, reg, ord_num, est, hr))
                elif mode == "vueltas" and c_tren and c_origen and c_h_sal and c_destino and c_h_lleg:
                    nt = str(ws.cell(r_idx, c_tren).value or "").strip()
                    if not nt or nt in ("None", "-"):
                        continue
                    cell_itin = str(ws.cell(r_idx, c_itin).value or "").strip() if c_itin else ""
                    itin = cell_itin or sheet_itin_default
                    cell_reg = str(ws.cell(r_idx, c_reg).value or "").strip() if c_reg else ""
                    reg = self.normalizar_regimen_parada(cell_reg or sheet_reg_default, default=sheet_reg_default)
                    orig = re.sub(r"\s+", " ", str(ws.cell(r_idx, c_origen).value or "")).strip()
                    hs = _parse_hr(ws.cell(r_idx, c_h_sal).value)
                    dest = re.sub(r"\s+", " ", str(ws.cell(r_idx, c_destino).value or "")).strip()
                    hl = _parse_hr(ws.cell(r_idx, c_h_lleg).value)
                    ord_key = (nt, itin, reg)
                    for est, hr in ((orig, hs), (dest, hl)):
                        if est and hr:
                            orden_tracker[ord_key] = orden_tracker.get(ord_key, 0) + 1
                            registros_a_insertar.append((nt, itin, reg, orden_tracker[ord_key], est, hr))
                else:
                    # Fallback posicional: col 1=Tren, col 2=Estacion, col 3=Hora, col 4=Orden, col 5=Itinerario
                    raw_nt = ws.cell(r_idx, 1).value
                    if isinstance(raw_nt, float) and not raw_nt.is_integer():
                        continue
                    nt = str(raw_nt or "").strip()
                    if nt.endswith(".0") and nt[:-2].isdigit():
                        nt = nt[:-2]
                    est = re.sub(r"\s+", " ", str(ws.cell(r_idx, 2).value or "")).strip()
                    hr = _parse_hr(ws.cell(r_idx, 3).value)
                    if (
                        not nt
                        or not est
                        or not hr
                        or nt in ("None", "-")
                        or normalizar_texto_estacion(est) in FOOTER_ROW_KEYS
                        or re.fullmatch(r"\d+[.,]\d+", nt)
                    ):
                        continue
                    itin = str(ws.cell(r_idx, 5).value or "").strip() if ws.max_column >= 5 else ""
                    itin = itin or sheet_itin_default
                    ord_key = (nt, itin, sheet_reg_default)
                    orden_tracker[ord_key] = orden_tracker.get(ord_key, 0) + 1
                    registros_a_insertar.append((nt, itin, sheet_reg_default, orden_tracker[ord_key], est, hr))

        if not registros_a_insertar and vio_catalogo_estaciones:
            raise ValueError(
                "El archivo seleccionado corresponde a un catálogo de estaciones (columnas Nombre/Sigla) y no a horarios de trenes.\n"
                "Utilice el botón '🏷️ Estaciones / Abrev.' para importar el catálogo de estaciones y abreviaturas."
            )

        to_insert: List[Tuple[str, str, str, int, str, str]] = []
        with self.db.write_transaction() as conn:
            idx_est = self._obtener_indice_estaciones(conn)
            existentes: Dict[Tuple[str, str, str, str], List[str]] = {}
            for r in conn.execute(
                "SELECT nro_tren, itinerario, regimen, estacion, hora_paso FROM tren_paradas"
            ).fetchall():
                k = (
                    str(r["nro_tren"] or "").strip(),
                    str(r["itinerario"] or "").strip().upper(),
                    self.normalizar_regimen_parada(r["regimen"], default="H"),
                    str(r["hora_paso"] or "").strip(),
                )
                existentes.setdefault(k, []).append(str(r["estacion"] or "").strip())

            for nt, itin, reg, ord_num, est, hr in registros_a_insertar:
                reg_norm = self.normalizar_regimen_parada(reg, default="H")
                k = (nt, itin.upper(), reg_norm, hr)
                ests_existentes = existentes.get(k, [])
                if any(e.upper() == est.upper() or idx_est.estaciones_equivalentes(e, est) for e in ests_existentes):
                    continue
                existentes.setdefault(k, []).append(est)
                to_insert.append((nt, itin, reg_norm, ord_num, est, hr))

            if to_insert:
                conn.executemany(
                    """INSERT INTO tren_paradas (nro_tren, itinerario, regimen, orden_parada, estacion, hora_paso, sync_status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'pending', datetime('now', 'localtime'), datetime('now', 'localtime'))""",
                    to_insert,
                )

            insertados = len(to_insert)
            self._audit(
                conn,
                user_id,
                windows_user,
                "importar",
                "tren_paradas",
                None,
                None,
                {"insertados": insertados, "archivo": Path(file_path).name},
            )
        return insertados

    def obtener_estaciones_y_horarios_por_tren(
        self,
        nro_tren: str,
        itinerario: Optional[str] = None,
        regimen: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Obtiene la secuencia de estaciones y horarios de paso para un N° de tren dado.

        Combina registros de `tren_paradas` y tramos de `turnos_vueltas` evitando duplicados
        entre nombres completos y abreviaturas, respetando el régimen y el orden de marcha
        (incluso en servicios que cruzan la medianoche).
        """
        nt = str(nro_tren or "").strip()
        if not nt:
            return []

        itin_clean = str(itinerario or "").strip()
        filtrar_itin = bool(itin_clean and itin_clean not in ("Todos", "Todas"))
        reg_raw = str(regimen or "").strip().upper()
        filtrar_reg = bool(reg_raw and reg_raw not in ("TODOS", "TODAS"))
        reg_clean = self.normalizar_regimen_parada(reg_raw, default="H") if filtrar_reg else ""

        def _min_of_day(hhmm: str) -> Optional[int]:
            parts = str(hhmm or "").strip().split(":")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                h, m = int(parts[0]), int(parts[1])
                if 0 <= h <= 23 and 0 <= m <= 59:
                    return h * 60 + m
            return None

        resultado: List[Dict[str, str]] = []
        offsets_resultado: List[Optional[int]] = []

        with self.db.read_connection() as conn:
            idx_est = self._obtener_indice_estaciones(conn)

            paradas_all = [
                dict(r)
                for r in conn.execute(
                    """SELECT estacion, hora_paso, orden_parada, itinerario, regimen
                       FROM tren_paradas
                       WHERE nro_tren = ?
                       ORDER BY orden_parada ASC, id ASC""",
                    (nt,),
                ).fetchall()
            ]

            paradas_sub = paradas_all
            if filtrar_itin:
                coinciden_itin = [
                    r for r in paradas_all if str(r.get("itinerario") or "").strip() in (itin_clean, "")
                ]
                if coinciden_itin:
                    paradas_sub = coinciden_itin

            if paradas_sub:
                regimenes_presentes = [
                    self.normalizar_regimen_parada(r.get("regimen"), default="H") for r in paradas_sub
                ]
                if filtrar_reg and reg_clean in regimenes_presentes:
                    reg_elegido = reg_clean
                else:
                    for prio in ("H", "S", "D"):
                        if prio in regimenes_presentes:
                            reg_elegido = prio
                            break
                    else:
                        reg_elegido = regimenes_presentes[0]

                paradas_sub = [
                    r for r in paradas_sub
                    if self.normalizar_regimen_parada(r.get("regimen"), default="H") == reg_elegido
                ]

            ref_min: Optional[int] = None
            prev_abs_min: Optional[int] = None
            wrap_days = 0
            for r in paradas_sub:
                est = str(r.get("estacion") or "").strip()
                hr = str(r.get("hora_paso") or "").strip()
                if not est:
                    continue
                if any(idx_est.estaciones_equivalentes(it["estacion"], est) or it["estacion"].upper() == est.upper() for it in resultado):
                    continue
                mod = _min_of_day(hr)
                off: Optional[int] = None
                if mod is not None:
                    if ref_min is None:
                        ref_min = mod
                        prev_abs_min = mod
                        off = 0
                    else:
                        cand_abs = mod + wrap_days * 1440
                        if prev_abs_min is not None and cand_abs + 60 < prev_abs_min:
                            wrap_days += 1
                            cand_abs = mod + wrap_days * 1440
                        prev_abs_min = cand_abs
                        off = cand_abs - ref_min
                resultado.append({"estacion": est, "hora": hr})
                offsets_resultado.append(off)

            # Complementar con turnos_vueltas
            q_vueltas = """
                SELECT v.origen, v.hora_salida, v.destino, v.hora_llegada, t.itinerario_vigente
                FROM turnos_vueltas v
                JOIN turnos_itinerario t ON t.id = v.turno_id
                WHERE v.nro_tren = ?
                ORDER BY v.hora_salida ASC
            """
            vueltas_rows = [dict(r) for r in conn.execute(q_vueltas, (nt,)).fetchall()]
            if filtrar_itin:
                vueltas_itin = [
                    r for r in vueltas_rows if str(r.get("itinerario_vigente") or "").strip() == itin_clean
                ]
                if vueltas_itin or not resultado:
                    vueltas_rows = vueltas_itin

            desde_vueltas_solo = len(resultado) == 0
            for r in vueltas_rows:
                for est_raw, hr_raw in ((r.get("origen"), r.get("hora_salida")), (r.get("destino"), r.get("hora_llegada"))):
                    est = str(est_raw or "").strip()
                    hr = str(hr_raw or "").strip()
                    if not est:
                        continue
                    if any(
                        idx_est.estaciones_equivalentes(it["estacion"], est) or it["estacion"].upper() == est.upper()
                        for it in resultado
                    ):
                        continue
                    nuevo_item = {"estacion": est, "hora": hr}
                    if not desde_vueltas_solo and ref_min is not None:
                        mod_v = _min_of_day(hr)
                        if mod_v is not None:
                            delta = (mod_v - ref_min) % 1440
                            if delta >= 720:
                                delta -= 1440
                            ins_pos = len(resultado)
                            for idx_p, off_p in enumerate(offsets_resultado):
                                if off_p is not None and delta < off_p:
                                    ins_pos = idx_p
                                    break
                            resultado.insert(ins_pos, nuevo_item)
                            offsets_resultado.insert(ins_pos, delta)
                            continue
                    resultado.append(nuevo_item)
                    offsets_resultado.append(None)

        if desde_vueltas_solo and resultado:
            mins = [m for m in (_min_of_day(x.get("hora", "")) for x in resultado) if m is not None]
            cruza_medianoche = bool(mins) and any(m >= 20 * 60 for m in mins) and any(m <= 4 * 60 for m in mins)

            def _vuelta_sort_key(item: Dict[str, str]):
                hr_s = item.get("hora") or "99:99"
                m = _min_of_day(hr_s)
                if m is None:
                    return (99999, hr_s)
                if cruza_medianoche and m <= 4 * 60:
                    return (m + 1440, hr_s)
                return (m, hr_s)

            resultado.sort(key=_vuelta_sort_key)
        return resultado

    # ---------------------------------------------------------
    # Módulo de Ordenamientos Operativos
    # ---------------------------------------------------------

    def generar_siguiente_nro_orden(self, deposito: str = "PC") -> str:
        """Genera el siguiente número correlativo de orden con formato '<DOTACION> 0000-XXXX'."""
        prefijo = (deposito or "PC").strip().upper() or "PC"
        with self.db.read_connection() as conn:
            rows = conn.execute(
                "SELECT nro_orden FROM ordenamientos WHERE UPPER(nro_orden) LIKE ?",
                (f"{prefijo} %",),
            ).fetchall()
            max_num = 0
            for r in rows:
                txt = str(r[0] or "").strip()
                if "-" in txt:
                    try:
                        num_part = int(txt.split("-")[-1].strip())
                        if num_part > max_num:
                            max_num = num_part
                    except ValueError:
                        pass
            sig = max_num + 1
            return f"{prefijo} 0000-{sig:04d}"

    def crear_ordenamiento(
        self,
        datos: Dict[str, Any],
        tramos: List[Dict[str, Any]],
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
    ) -> int:
        """Crea un nuevo ordenamiento operativo y sus tramos asociados."""
        es_valido, err = validar_datos_ordenamiento(datos, tramos)
        if not es_valido:
            raise ValueError(err)

        deposito = str(datos.get("deposito") or "PC").strip().upper()
        nro_orden = str(datos.get("nro_orden") or "").strip()
        if not nro_orden:
            nro_orden = self.generar_siguiente_nro_orden(deposito)

        win_usr = (windows_user or datos.get("usuario_windows") or "").strip() or obtener_usuario_windows()
        ahora_dt = datetime.now()
        fecha_emision = str(datos.get("fecha_emision") or ahora_dt.strftime("%d/%m/%Y")).strip()
        hora_emision = str(datos.get("hora_emision") or ahora_dt.strftime("%H:%M")).strip()

        with self.db.write_transaction() as conn:
            dup = conn.execute("SELECT id FROM ordenamientos WHERE nro_orden = ?", (nro_orden,)).fetchone()
            if dup:
                nro_orden = self.generar_siguiente_nro_orden(deposito)

            cur = conn.execute(
                """INSERT INTO ordenamientos (
                    nro_orden, fecha_emision, hora_emision, legajo_personal, nombre_personal,
                    deposito, itinerario, ciclo_o_turno, regimen, motivo_orden, observacion_pie,
                    ref_pct, emisor, usuario_id, usuario_windows, estado, activo, sync_status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'activo', 1, 'pending', datetime('now', 'localtime'), datetime('now', 'localtime'))""",
                (
                    nro_orden,
                    fecha_emision,
                    hora_emision,
                    int(str(datos["legajo_personal"]).strip()),
                    str(datos.get("nombre_personal") or "").strip(),
                    deposito,
                    str(datos.get("itinerario") or "").strip(),
                    str(datos.get("ciclo_o_turno") or "").strip(),
                    self.normalizar_regimen_parada(datos.get("regimen"), default="H"),
                    str(datos.get("motivo_orden") or "").strip(),
                    str(datos.get("observacion_pie") or "").strip(),
                    str(datos.get("ref_pct") or "").strip(),
                    str(datos.get("emisor") or "").strip(),
                    user_id,
                    win_usr,
                ),
            )
            ord_id = cur.lastrowid

            for idx, t in enumerate(tramos, start=1):
                conn.execute(
                    """INSERT INTO ordenamiento_tramos (
                        ordenamiento_id, nro_secuencia, ref, nro_tren, origen,
                        hora_salida, destino, hora_llegada, observaciones, sync_status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now', 'localtime'), datetime('now', 'localtime'))""",
                    (
                        ord_id,
                        int(t.get("nro_secuencia") or idx),
                        str(t.get("ref") or "").strip(),
                        str(t.get("nro_tren") or "").strip(),
                        str(t.get("origen") or "").strip(),
                        str(t.get("hora_salida") or "").strip(),
                        str(t.get("destino") or "").strip(),
                        str(t.get("hora_llegada") or "").strip(),
                        str(t.get("observaciones") or "").strip(),
                    ),
                )

            after = dict(conn.execute("SELECT * FROM ordenamientos WHERE id = ?", (ord_id,)).fetchone())
            audit_after = dict(after)
            audit_after["tramos_count"] = len(tramos)
            self._audit(conn, user_id, win_usr, "crear", "ordenamiento", ord_id, None, audit_after)
            self._enqueue_sync(conn, "ordenamientos", ord_id, "INSERT", after)
            return ord_id

    def actualizar_ordenamiento(
        self,
        ord_id: int,
        datos: Dict[str, Any],
        tramos: List[Dict[str, Any]],
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
        bypass_window: bool = False,
    ) -> None:
        """Actualiza un ordenamiento existente y reemplaza sus tramos."""
        es_valido, err = validar_datos_ordenamiento(datos, tramos)
        if not es_valido:
            raise ValueError(err)

        win_usr = (windows_user or datos.get("usuario_windows") or "").strip() or obtener_usuario_windows()
        deposito = str(datos.get("deposito") or "PC").strip().upper()

        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM ordenamientos WHERE id = ?", (ord_id,)).fetchone()
            if not before:
                raise ValueError("El ordenamiento no existe.")

            ref_time = before["created_at"] or f"{before['fecha_emision']} {before['hora_emision']}"
            if not bypass_window and not self.dentro_de_ventana(ref_time, "editar_horas"):
                raise ValueError("El ordenamiento superó el tiempo permitido para editar.")

            nro_orden = str(datos.get("nro_orden") or before["nro_orden"]).strip()
            conn.execute(
                """UPDATE ordenamientos SET
                    nro_orden=?, fecha_emision=?, hora_emision=?, legajo_personal=?, nombre_personal=?,
                    deposito=?, itinerario=?, ciclo_o_turno=?, regimen=?, motivo_orden=?, observacion_pie=?,
                    ref_pct=?, emisor=?, usuario_windows=?, sync_status='pending', updated_at=datetime('now', 'localtime')
                   WHERE id=?""",
                (
                    nro_orden,
                    str(datos.get("fecha_emision") or before["fecha_emision"]).strip(),
                    str(datos.get("hora_emision") or before["hora_emision"]).strip(),
                    int(str(datos["legajo_personal"]).strip()),
                    str(datos.get("nombre_personal") or "").strip(),
                    deposito,
                    str(datos.get("itinerario") or "").strip(),
                    str(datos.get("ciclo_o_turno") or "").strip(),
                    self.normalizar_regimen_parada(datos.get("regimen"), default="H"),
                    str(datos.get("motivo_orden") or "").strip(),
                    str(datos.get("observacion_pie") or "").strip(),
                    str(datos.get("ref_pct") or "").strip(),
                    str(datos.get("emisor") or before["emisor"] or "").strip(),
                    win_usr,
                    ord_id,
                ),
            )

            conn.execute("DELETE FROM ordenamiento_tramos WHERE ordenamiento_id = ?", (ord_id,))
            for idx, t in enumerate(tramos, start=1):
                conn.execute(
                    """INSERT INTO ordenamiento_tramos (
                        ordenamiento_id, nro_secuencia, ref, nro_tren, origen,
                        hora_salida, destino, hora_llegada, observaciones, sync_status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now', 'localtime'), datetime('now', 'localtime'))""",
                    (
                        ord_id,
                        int(t.get("nro_secuencia") or idx),
                        str(t.get("ref") or "").strip(),
                        str(t.get("nro_tren") or "").strip(),
                        str(t.get("origen") or "").strip(),
                        str(t.get("hora_salida") or "").strip(),
                        str(t.get("destino") or "").strip(),
                        str(t.get("hora_llegada") or "").strip(),
                        str(t.get("observaciones") or "").strip(),
                    ),
                )

            after = dict(conn.execute("SELECT * FROM ordenamientos WHERE id = ?", (ord_id,)).fetchone())
            audit_after = dict(after)
            audit_after["tramos_count"] = len(tramos)
            self._audit(conn, user_id, win_usr, "modificar", "ordenamiento", ord_id, dict(before), audit_after)
            self._enqueue_sync(conn, "ordenamientos", ord_id, "UPDATE", after)

    def eliminar_ordenamiento(
        self,
        ord_id: int,
        user_id: Optional[int] = None,
        windows_user: Optional[str] = None,
        bypass_window: bool = False,
    ) -> None:
        """Eliminación lógica (soft-delete) de un ordenamiento enviándolo a la papelera."""
        win_usr = (windows_user or "").strip() or obtener_usuario_windows()
        with self.db.write_transaction() as conn:
            before = conn.execute("SELECT * FROM ordenamientos WHERE id = ?", (ord_id,)).fetchone()
            if not before:
                raise ValueError("El ordenamiento no existe.")
            ref_time = before["created_at"] or f"{before['fecha_emision']} {before['hora_emision']}"
            if not bypass_window and not self.dentro_de_ventana(ref_time, "eliminar_horas"):
                raise ValueError("El ordenamiento superó el tiempo permitido para eliminar.")

            ahora = self.db.now()
            conn.execute(
                "UPDATE ordenamientos SET activo=0, estado='eliminado', deleted_at=?, updated_at=?, sync_status='pending' WHERE id=?",
                (ahora, ahora, ord_id),
            )
            after = {"activo": 0, "estado": "eliminado", "deleted_at": ahora}
            self._audit(conn, user_id, win_usr, "eliminar", "ordenamiento", ord_id, dict(before), after)
            self._enqueue_sync(conn, "ordenamientos", ord_id, "UPDATE", after)

    def obtener_ordenamiento_completo(self, ord_id: int) -> Optional[Dict[str, Any]]:
        """Obtiene la cabecera del ordenamiento y todos sus tramos ordenados por secuencia."""
        with self.db.read_connection() as conn:
            row = conn.execute("SELECT * FROM ordenamientos WHERE id = ?", (ord_id,)).fetchone()
            if not row:
                return None
            res = dict(row)
            res["regimen"] = self.normalizar_regimen_parada(res.get("regimen"), default="H")
            tramos = conn.execute(
                "SELECT * FROM ordenamiento_tramos WHERE ordenamiento_id = ? ORDER BY nro_secuencia ASC, id ASC",
                (ord_id,),
            ).fetchall()
            res["tramos"] = [dict(t) for t in tramos]
            return res

    def listar_ordenamientos(
        self,
        filtros: Optional[Dict[str, Any]] = None,
        incluir_inactivos: bool = False,
        orden_asc: bool = True,
    ) -> List[Dict[str, Any]]:
        """Lista los ordenamientos operativos con resumen de trenes involucrados."""
        with self.db.read_connection() as conn:
            query = """
                SELECT o.*,
                       COALESCE(GROUP_CONCAT(NULLIF(TRIM(ot.nro_tren), ''), ', '), '') AS trenes_involucrados,
                       COUNT(ot.id) AS cant_tramos
                FROM ordenamientos o
                LEFT JOIN ordenamiento_tramos ot ON ot.ordenamiento_id = o.id
            """
            conds = []
            params: List[Any] = []
            if not incluir_inactivos:
                conds.append("o.activo = 1 AND o.estado = 'activo'")

            if filtros:
                if filtros.get("deposito") and str(filtros["deposito"]).strip() not in ("", "Todas", "Todos"):
                    conds.append("UPPER(TRIM(o.deposito)) = UPPER(TRIM(?))")
                    params.append(str(filtros["deposito"]).strip())
                if filtros.get("legajo"):
                    conds.append("o.legajo_personal = ?")
                    params.append(int(filtros["legajo"]))
                if filtros.get("texto"):
                    like = f"%{str(filtros['texto']).strip()}%"
                    conds.append("""(
                        o.nro_orden LIKE ? OR
                        CAST(o.legajo_personal AS TEXT) LIKE ? OR
                        o.nombre_personal LIKE ? OR
                        o.ciclo_o_turno LIKE ? OR
                        o.motivo_orden LIKE ? OR
                        EXISTS (
                            SELECT 1 FROM ordenamiento_tramos ot2
                            WHERE ot2.ordenamiento_id = o.id AND ot2.nro_tren LIKE ?
                        )
                    )""")
                    params.extend([like, like, like, like, like, like])

            if conds:
                query += " WHERE " + " AND ".join(conds)
            query += " GROUP BY o.id"
            query += " ORDER BY o.id ASC" if orden_asc else " ORDER BY o.id DESC"

            rows = conn.execute(query, params).fetchall()
            resultado = []
            for r in rows:
                item = dict(r)
                item["regimen"] = self.normalizar_regimen_parada(item.get("regimen"), default="H")
                resultado.append(item)

            if filtros and filtros.get("fecha"):
                f_filtro = parsear_fecha(filtros["fecha"], obligatorio=False)
                if f_filtro:
                    filtrados_fecha = []
                    for item in resultado:
                        d_em = parsear_fecha(item.get("fecha_emision"), obligatorio=False)
                        if d_em and d_em.date() == f_filtro.date():
                            filtrados_fecha.append(item)
                    return filtrados_fecha

            return resultado

    def buscar_ordenamientos_activos(
        self,
        criterio: str,
        fecha_target_iso: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Busca ordenamientos activos cuyos tramos, turno, legajo o N° de orden coincidan con el criterio (ej. N° de tren)."""
        crit = str(criterio or "").strip()
        if not crit:
            return []

        f_obj = parsear_fecha(fecha_target_iso, obligatorio=False) if fecha_target_iso else None
        like = f"%{crit}%"

        with self.db.read_connection() as conn:
            rows = conn.execute(
                """
                SELECT o.*,
                       COALESCE(GROUP_CONCAT(NULLIF(TRIM(ot.nro_tren), ''), ', '), '') AS trenes_involucrados,
                       COUNT(ot.id) AS cant_tramos
                FROM ordenamientos o
                LEFT JOIN ordenamiento_tramos ot ON ot.ordenamiento_id = o.id
                WHERE o.activo = 1 AND o.estado = 'activo'
                  AND (
                      o.nro_orden LIKE ? OR
                      o.ciclo_o_turno LIKE ? OR
                      CAST(o.legajo_personal AS TEXT) = ? OR
                      EXISTS (
                          SELECT 1 FROM ordenamiento_tramos ot2
                          WHERE ot2.ordenamiento_id = o.id AND (ot2.nro_tren = ? OR ot2.nro_tren LIKE ?)
                      )
                  )
                GROUP BY o.id
                ORDER BY o.id ASC
                """,
                (like, like, crit, crit, like),
            ).fetchall()

            resultados: List[Dict[str, Any]] = []
            for r in rows:
                d_item = dict(r)
                d_item["regimen"] = self.normalizar_regimen_parada(d_item.get("regimen"), default="H")
                if f_obj:
                    d_em = parsear_fecha(d_item.get("fecha_emision"), obligatorio=False)
                    if d_em and d_em.date() != f_obj.date():
                        continue
                tramos = conn.execute(
                    "SELECT * FROM ordenamiento_tramos WHERE ordenamiento_id = ? ORDER BY nro_secuencia ASC, id ASC",
                    (d_item["id"],),
                ).fetchall()
                d_item["tramos"] = [dict(t) for t in tramos]
                resultados.append(d_item)
            return resultados


