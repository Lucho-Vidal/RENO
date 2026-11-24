"""Servicio despachador multimodo de sincronización (LOCAL / SHARED_SQLITE / SUPABASE)."""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Any

from ..core.config import cargar_configuracion_app
from ..data.database import DatabaseManager
from ..data.sync.outbox_queue import OutboxQueue
from ..data.sync.supabase_sync import SupabaseSyncEngine
from .shared_sqlite_sync import SharedSQLiteSyncEngine

logger = logging.getLogger("reno.services.sync_service")

MODO_LOCAL = "LOCAL"
MODO_SHARED_SQLITE = "SHARED_SQLITE"
MODO_SUPABASE = "SUPABASE"
MODOS_VALIDOS = (MODO_LOCAL, MODO_SHARED_SQLITE, MODO_SUPABASE)


class SyncService:
    """Coordina la sincronización Local-First según el modo activo configurado."""

    def __init__(
        self,
        db: DatabaseManager,
        outbox: OutboxQueue | None = None,
        supabase_sync: SupabaseSyncEngine | None = None,
        config_provider: Any = None,
    ) -> None:
        self.db = db
        self.outbox = outbox if isinstance(outbox, OutboxQueue) else OutboxQueue(db)
        self.supabase_sync = supabase_sync
        self._config_provider = config_provider or cargar_configuracion_app
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._conectado: bool = False
        self._ultimo_sync: str | None = None
        self._ultimo_error: str = ""
        self._ultimo_mensaje: str = ""

    def _leer_config(self) -> dict[str, Any]:
        cfg = self._config_provider() if callable(self._config_provider) else {}
        return dict(cfg or {})

    def get_mode(self) -> str:
        cfg = self._leer_config()
        modo = str(cfg.get("sync_mode") or "").strip().upper()
        if modo in MODOS_VALIDOS:
            return modo

        # Compatibilidad hacia atrás: si hay credenciales Supabase configuradas, usar SUPABASE
        sb_url = (os.environ.get("SUPABASE_URL") or cfg.get("supabase_url") or "").strip()
        sb_key = (
            os.environ.get("SUPABASE_KEY")
            or cfg.get("supabase_anon_key")
            or cfg.get("supabase_key")
            or ""
        ).strip()
        if self.supabase_sync is not None or (sb_url and sb_key):
            return MODO_SUPABASE
        return MODO_LOCAL

    def is_configured(self) -> bool:
        modo = self.get_mode()
        cfg = self._leer_config()
        if modo == MODO_LOCAL:
            return True
        if modo == MODO_SHARED_SQLITE:
            return bool(str(cfg.get("shared_sqlite_path") or "").strip())
        if modo == MODO_SUPABASE:
            if self.supabase_sync is not None:
                return True
            sb_url = (os.environ.get("SUPABASE_URL") or cfg.get("supabase_url") or "").strip()
            sb_key = (
                os.environ.get("SUPABASE_KEY")
                or cfg.get("supabase_anon_key")
                or cfg.get("supabase_key")
                or ""
            ).strip()
            return bool(sb_url and sb_key)
        return False

    def is_connected(self) -> bool:
        if self.get_mode() == MODO_LOCAL:
            return False
        return self._conectado

    def contar_pendientes(self) -> int:
        try:
            return self.outbox.contar_pendientes()
        except Exception as ex:
            logger.debug("No se pudo contar pendientes en sync_outbox: %s", ex)
            return 0

    def _obtener_engine_shared(self, cfg: dict[str, Any]) -> SharedSQLiteSyncEngine:
        ruta = str(cfg.get("shared_sqlite_path") or "").strip()
        return SharedSQLiteSyncEngine(self.db, outbox=self.outbox, shared_path=ruta)

    def _obtener_engine_supabase(self, cfg: dict[str, Any]) -> SupabaseSyncEngine:
        if self.supabase_sync is not None:
            return self.supabase_sync
        sb_url = (os.environ.get("SUPABASE_URL") or cfg.get("supabase_url") or "").strip()
        sb_key = (
            os.environ.get("SUPABASE_KEY")
            or cfg.get("supabase_anon_key")
            or cfg.get("supabase_key")
            or ""
        ).strip()
        return SupabaseSyncEngine(self.db, outbox=self.outbox, supabase_url=sb_url, supabase_key=sb_key)

    def sincronizar_pendientes(self, forzar_todo: bool = False) -> dict[str, Any]:
        """Ejecuta un ciclo de sincronización seguro sin lanzar excepciones fatales."""
        if not self._lock.acquire(blocking=False):
            return self.get_estado_sincronizacion()

        try:
            cfg = self._leer_config()
            modo = self.get_mode()

            if forzar_todo and modo != MODO_LOCAL:
                try:
                    self.outbox.forzar_resincronizacion_total()
                except Exception as ex_force:
                    logger.warning("Error al encolar resincronización total: %s", ex_force)

            if modo == MODO_LOCAL:
                self._conectado = False
                self._ultimo_error = ""
                self._ultimo_mensaje = "Modo Local"
                return {
                    **self.get_estado_sincronizacion(),
                    "ok": True,
                    "enviados": 0,
                    "recibidos": 0,
                }

            if not self.is_configured():
                self._conectado = False
                self._ultimo_error = "Sincronización sin ruta o credenciales configuradas."
                self._ultimo_mensaje = self._ultimo_error
                return {
                    **self.get_estado_sincronizacion(),
                    "ok": False,
                    "enviados": 0,
                    "recibidos": 0,
                }

            enviados = 0
            recibidos = 0
            try:
                if modo == MODO_SHARED_SQLITE:
                    engine = self._obtener_engine_shared(cfg)
                    enviados, recibidos = engine.sincronizar_completo()
                elif modo == MODO_SUPABASE:
                    engine_sb = self._obtener_engine_supabase(cfg)
                    enviados, recibidos = engine_sb.sincronizar_completo()

                self._conectado = True
                self._ultimo_error = ""
                self._ultimo_sync = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                pend = self.contar_pendientes()
                if modo == MODO_SHARED_SQLITE:
                    self._ultimo_mensaje = (
                        "Red Compartida Conectada | Al día"
                        if pend == 0
                        else f"Red Compartida ({pend} pend.)"
                    )
                else:
                    self._ultimo_mensaje = (
                        "Supabase Conectado"
                        if pend == 0
                        else f"Supabase ({pend} pend.)"
                    )
                return {
                    **self.get_estado_sincronizacion(),
                    "ok": True,
                    "enviados": enviados,
                    "recibidos": recibidos,
                }
            except Exception as ex:
                # Degradación silenciosa a modo local/offline ante corte de red, VPN o SMB caído
                self._conectado = False
                self._ultimo_error = str(ex)
                pend = self.contar_pendientes()
                if modo == MODO_SHARED_SQLITE:
                    self._ultimo_mensaje = f"Red Desconectada ({pend} pend.)"
                else:
                    self._ultimo_mensaje = f"Supabase Offline ({pend} pend.)"
                logger.warning("Sincronización (%s) en modo offline tolerante a fallos: %s", modo, ex)
                return {
                    **self.get_estado_sincronizacion(),
                    "ok": False,
                    "enviados": 0,
                    "recibidos": 0,
                    "error": str(ex),
                }
        finally:
            self._lock.release()

    def get_estado_sincronizacion(self) -> dict[str, Any]:
        modo = self.get_mode()
        pendientes = self.contar_pendientes()
        with self._state_lock:
            conectado = self._conectado if modo != MODO_LOCAL else False
            ultimo_sync = self._ultimo_sync
            ultimo_error = self._ultimo_error
            ultimo_mensaje = self._ultimo_mensaje
        return {
            "modo": modo,
            "configurado": self.is_configured(),
            "conectado": conectado,
            "pendientes": pendientes,
            "ultimo_sync": ultimo_sync,
            "error": ultimo_error,
            "mensaje": ultimo_mensaje,
        }
