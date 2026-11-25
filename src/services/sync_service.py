"""Servicio despachador multimodo de sincronización (LOCAL / SHARED_SQLITE / SUPABASE / RING)."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Any

from ..core.config import cargar_configuracion_app
from ..core.logger import get_logger
from ..data.database import DatabaseManager
from ..data.sync.outbox_queue import OutboxQueue
from ..data.sync.supabase_sync import SupabaseSyncEngine
from .shared_sqlite_sync import SharedSQLiteSyncEngine

logger = get_logger("SyncService")

MODO_LOCAL = "LOCAL"
MODO_SHARED_SQLITE = "SHARED_SQLITE"
MODO_SUPABASE = "SUPABASE"
MODO_ANILLO = "RING"
MODOS_VALIDOS = (MODO_LOCAL, MODO_SHARED_SQLITE, MODO_SUPABASE, MODO_ANILLO)

CIRCUIT_BREAKER_FALLOS = 3
CIRCUIT_BREAKER_COOLDOWN_SEG = 300.0  # 5 minutos de pausa tras 3 fallos seguidos


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
        self._degradado: bool = False
        self._ultimo_sync: str | None = None
        self._ultimo_error: str = ""
        self._ultimo_mensaje: str = ""
        # Estado granular por canal para MODO_ANILLO + Circuit Breaker
        self._canal_ok: dict[str, bool] = {"smb": False, "supabase": False}
        self._canal_error: dict[str, str] = {"smb": "", "supabase": ""}
        self._fallos_consecutivos: dict[str, int] = {"smb": 0, "supabase": 0}
        self._cooldown_hasta: dict[str, float] = {"smb": 0.0, "supabase": 0.0}

    def _leer_config(self) -> dict[str, Any]:
        cfg = self._config_provider() if callable(self._config_provider) else {}
        return dict(cfg or {})

    def _tiene_smb_configurado(self, cfg: dict[str, Any]) -> bool:
        return bool(str(cfg.get("shared_sqlite_path") or "").strip())

    def _tiene_supabase_configurado(self, cfg: dict[str, Any]) -> bool:
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

    def get_mode(self) -> str:
        cfg = self._leer_config()
        modo = str(cfg.get("sync_mode") or "").strip().upper()
        if modo in MODOS_VALIDOS:
            return modo

        # Compatibilidad hacia atrás: si hay credenciales Supabase configuradas, usar SUPABASE
        if self._tiene_supabase_configurado(cfg):
            return MODO_SUPABASE
        return MODO_LOCAL

    def is_configured(self) -> bool:
        modo = self.get_mode()
        cfg = self._leer_config()
        if modo == MODO_LOCAL:
            return True
        if modo == MODO_SHARED_SQLITE:
            return self._tiene_smb_configurado(cfg)
        if modo == MODO_SUPABASE:
            return self._tiene_supabase_configurado(cfg)
        if modo == MODO_ANILLO:
            return self._tiene_smb_configurado(cfg) or self._tiene_supabase_configurado(cfg)
        return False

    def is_connected(self) -> bool:
        if self.get_mode() == MODO_LOCAL:
            return False
        with self._state_lock:
            return self._conectado

    def contar_pendientes(self, canal: str | None = None) -> int:
        try:
            return self.outbox.contar_pendientes(canal=canal)
        except Exception as ex:
            logger.debug("No se pudo contar pendientes en sync_outbox: %s", ex)
            return 0

    def resetear_circuit_breaker(self, canal: str | None = None) -> None:
        """Reinicia contadores de fallos y cooldowns (útil tras guardar config o clic manual)."""
        canales = [canal] if canal in ("smb", "supabase") else ["smb", "supabase"]
        with self._state_lock:
            for c in canales:
                self._fallos_consecutivos[c] = 0
                self._cooldown_hasta[c] = 0.0

    def _en_cooldown(self, canal: str, ignorar_cooldown: bool) -> bool:
        if ignorar_cooldown:
            return False
        with self._state_lock:
            return time.monotonic() < self._cooldown_hasta.get(canal, 0.0)

    def _registrar_exito_canal(self, canal: str) -> None:
        with self._state_lock:
            self._canal_ok[canal] = True
            self._canal_error[canal] = ""
            self._fallos_consecutivos[canal] = 0
            self._cooldown_hasta[canal] = 0.0

    def _registrar_fallo_canal(self, canal: str, error_msg: str) -> None:
        with self._state_lock:
            self._canal_ok[canal] = False
            self._canal_error[canal] = error_msg
            self._fallos_consecutivos[canal] = self._fallos_consecutivos.get(canal, 0) + 1
            if self._fallos_consecutivos[canal] >= CIRCUIT_BREAKER_FALLOS:
                self._cooldown_hasta[canal] = time.monotonic() + CIRCUIT_BREAKER_COOLDOWN_SEG
                logger.info(
                    "Circuit breaker activado para canal '%s' (%d fallos seguidos; pausa %.0fs).",
                    canal,
                    self._fallos_consecutivos[canal],
                    CIRCUIT_BREAKER_COOLDOWN_SEG,
                )

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

    def _sincronizar_anillo(
        self,
        cfg: dict[str, Any],
        ignorar_cooldown: bool = False,
    ) -> dict[str, Any]:
        """Orquesta la sincronización en anillo (SMB ↔ Supabase) con failover y puenteo."""
        has_smb = self._tiene_smb_configurado(cfg)
        has_sb = self._tiene_supabase_configurado(cfg)
        dual = has_smb and has_sb

        if ignorar_cooldown:
            self.resetear_circuit_breaker()

        primario = str(cfg.get("sync_ring_primary") or MODO_SHARED_SQLITE).strip().upper()
        if primario == MODO_SUPABASE:
            orden = [c for c, activo in (("supabase", has_sb), ("smb", has_smb)) if activo]
        else:
            orden = [c for c, activo in (("smb", has_smb), ("supabase", has_sb)) if activo]

        enviados_total = 0
        recibidos_total = 0
        resultados_canal: dict[str, bool] = {}
        errores_canal: dict[str, str] = {}

        engine_smb = self._obtener_engine_shared(cfg) if has_smb else None
        engine_sb = self._obtener_engine_supabase(cfg) if has_sb else None

        for idx, canal in enumerate(orden):
            if self._en_cooldown(canal, ignorar_cooldown):
                resultados_canal[canal] = False
                with self._state_lock:
                    errores_canal[canal] = self._canal_error.get(canal) or "En pausa temporal (circuit breaker)"
                continue
            try:
                if canal == "smb" and engine_smb is not None:
                    env, rec = engine_smb.sincronizar_completo(
                        modo_anillo=True,
                        propagar_a_canal_opuesto=dual,
                    )
                elif canal == "supabase" and engine_sb is not None:
                    env, rec = engine_sb.sincronizar_completo(
                        modo_anillo=True,
                        propagar_a_canal_opuesto=dual,
                    )
                else:
                    continue
                enviados_total += env
                recibidos_total += rec
                resultados_canal[canal] = True
                self._registrar_exito_canal(canal)

                # Si el canal secundario trajo registros puenteados para el primario y el primario está sano,
                # cerrar el anillo con un push rápido al primario en el mismo ciclo.
                if dual and idx == 1 and rec > 0 and resultados_canal.get(orden[0]):
                    try:
                        if orden[0] == "smb" and engine_smb is not None:
                            enviados_total += engine_smb.push_changes(modo_anillo=True)
                        elif orden[0] == "supabase" and engine_sb is not None:
                            env_extra, _ = engine_sb.sincronizar(solo_push=True, modo_anillo=True)
                            enviados_total += env_extra
                    except Exception as ex_close:
                        logger.debug("No se pudo cerrar puente hacia %s en el mismo ciclo: %s", orden[0], ex_close)
            except Exception as ex:
                resultados_canal[canal] = False
                errores_canal[canal] = str(ex)
                self._registrar_fallo_canal(canal, str(ex))
                logger.warning("Canal '%s' del anillo en modo offline tolerante a fallos: %s", canal, ex)

        # Purgar únicamente las filas entregadas a todos los canales configurados
        try:
            self.outbox.purgar_entregados(requiere_smb=has_smb, requiere_supabase=has_sb)
        except Exception as ex_purge:
            logger.debug("Error purgando sync_outbox en modo anillo: %s", ex_purge)

        alguno_ok = any(resultados_canal.get(c, False) for c in orden)
        todos_ok = all(resultados_canal.get(c, False) for c in orden) and bool(orden)
        degradado = dual and alguno_ok and not todos_ok

        pend_smb = self.contar_pendientes("smb") if has_smb else 0
        pend_sb = self.contar_pendientes("supabase") if has_sb else 0
        pend_total = self.contar_pendientes()

        if todos_ok:
            if dual:
                msg = (
                    "Anillo Activo (Red + Nube)"
                    if pend_total == 0
                    else f"Anillo Activo ({pend_total} pend.)"
                )
            elif has_smb:
                msg = "Anillo: Red OK" if pend_smb == 0 else f"Anillo: Red ({pend_smb} pend.)"
            else:
                msg = "Anillo: Nube OK" if pend_sb == 0 else f"Anillo: Nube ({pend_sb} pend.)"
            err_resumen = ""
        elif degradado:
            if resultados_canal.get("smb"):
                msg = f"Anillo: Red OK | Nube Offline ({pend_sb} pend.)"
                err_resumen = errores_canal.get("supabase", "")
            else:
                msg = f"Anillo: Nube OK | Red Offline ({pend_smb} pend.)"
                err_resumen = errores_canal.get("smb", "")
        else:
            msg = f"Anillo Offline ({pend_total} pend.)"
            err_resumen = " | ".join(f"{k}: {v}" for k, v in errores_canal.items() if v)

        with self._state_lock:
            self._conectado = alguno_ok
            self._degradado = degradado
            self._ultimo_error = err_resumen
            self._ultimo_mensaje = msg
            if alguno_ok:
                self._ultimo_sync = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return {
            **self.get_estado_sincronizacion(),
            "ok": alguno_ok,
            "enviados": enviados_total,
            "recibidos": recibidos_total,
            **({"error": err_resumen} if err_resumen else {}),
        }

    def sincronizar_pendientes(
        self,
        forzar_todo: bool = False,
        ignorar_cooldown: bool = False,
    ) -> dict[str, Any]:
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
                with self._state_lock:
                    self._conectado = False
                    self._degradado = False
                    self._ultimo_error = ""
                    self._ultimo_mensaje = "Modo Local"
                return {
                    **self.get_estado_sincronizacion(),
                    "ok": True,
                    "enviados": 0,
                    "recibidos": 0,
                }

            if not self.is_configured():
                with self._state_lock:
                    self._conectado = False
                    self._degradado = False
                    self._ultimo_error = "Sincronización sin ruta o credenciales configuradas."
                    self._ultimo_mensaje = self._ultimo_error
                return {
                    **self.get_estado_sincronizacion(),
                    "ok": False,
                    "enviados": 0,
                    "recibidos": 0,
                }

            if modo == MODO_ANILLO:
                return self._sincronizar_anillo(cfg, ignorar_cooldown=ignorar_cooldown or forzar_todo)

            enviados = 0
            recibidos = 0
            try:
                if modo == MODO_SHARED_SQLITE:
                    engine = self._obtener_engine_shared(cfg)
                    enviados, recibidos = engine.sincronizar_completo()
                elif modo == MODO_SUPABASE:
                    engine_sb = self._obtener_engine_supabase(cfg)
                    enviados, recibidos = engine_sb.sincronizar_completo()

                pend = self.contar_pendientes()
                if modo == MODO_SHARED_SQLITE:
                    msg_ok = (
                        "Red Compartida Conectada | Al día"
                        if pend == 0
                        else f"Red Compartida ({pend} pend.)"
                    )
                else:
                    msg_ok = (
                        "Supabase Conectado"
                        if pend == 0
                        else f"Supabase ({pend} pend.)"
                    )
                with self._state_lock:
                    self._conectado = True
                    self._degradado = False
                    self._ultimo_error = ""
                    self._ultimo_sync = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self._ultimo_mensaje = msg_ok
                return {
                    **self.get_estado_sincronizacion(),
                    "ok": True,
                    "enviados": enviados,
                    "recibidos": recibidos,
                }
            except Exception as ex:
                # Degradación silenciosa a modo local/offline ante corte de red, VPN o SMB caído
                pend = self.contar_pendientes()
                msg_err = (
                    f"Red Desconectada ({pend} pend.)"
                    if modo == MODO_SHARED_SQLITE
                    else f"Supabase Offline ({pend} pend.)"
                )
                with self._state_lock:
                    self._conectado = False
                    self._degradado = False
                    self._ultimo_error = str(ex)
                    self._ultimo_mensaje = msg_err
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
        cfg = self._leer_config()
        modo = self.get_mode()
        has_smb = self._tiene_smb_configurado(cfg)
        has_sb = self._tiene_supabase_configurado(cfg)
        pendientes = self.contar_pendientes()
        pend_smb = self.contar_pendientes("smb") if (modo == MODO_ANILLO and has_smb) else 0
        pend_sb = self.contar_pendientes("supabase") if (modo == MODO_ANILLO and has_sb) else 0
        with self._state_lock:
            conectado = self._conectado if modo != MODO_LOCAL else False
            degradado = self._degradado if modo == MODO_ANILLO else False
            ultimo_sync = self._ultimo_sync
            ultimo_error = self._ultimo_error
            ultimo_mensaje = self._ultimo_mensaje
            smb_ok = self._canal_ok.get("smb", False)
            sb_ok = self._canal_ok.get("supabase", False)
            err_smb = self._canal_error.get("smb", "")
            err_sb = self._canal_error.get("supabase", "")
        return {
            "modo": modo,
            "configurado": self.is_configured(),
            "conectado": conectado,
            "degradado": degradado,
            "pendientes": pendientes,
            "pendientes_smb": pend_smb,
            "pendientes_supabase": pend_sb,
            "smb_configurado": has_smb,
            "supabase_configurado": has_sb,
            "smb_ok": smb_ok,
            "supabase_ok": sb_ok,
            "error_smb": err_smb,
            "error_supabase": err_sb,
            "ultimo_sync": ultimo_sync,
            "error": ultimo_error,
            "mensaje": ultimo_mensaje,
        }
