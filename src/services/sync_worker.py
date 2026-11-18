"""Hilo en segundo plano (QThread) para sincronización periódica y bajo demanda."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QMutex, QMutexLocker, QThread, QWaitCondition, Signal

from .sync_service import MODO_LOCAL, SyncService

logger = logging.getLogger("reno.services.sync_worker")


class SyncBackgroundWorker(QThread):
    """Worker no bloqueante para ejecutar SyncService en segundo plano."""

    sync_started = Signal()
    sync_finished = Signal(dict)
    status_updated = Signal(dict)

    def __init__(
        self,
        sync_service: SyncService,
        interval_sec: int = 45,
        auto_enabled: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.sync_service = sync_service
        self._interval_sec = max(5, int(interval_sec or 45))
        self._auto_enabled = bool(auto_enabled)
        self._stop_requested = False
        self._trigger_requested = False
        self._force_full_requested = False
        self._mutex = QMutex()
        self._cond = QWaitCondition()

    def set_interval(self, interval_sec: int) -> None:
        with QMutexLocker(self._mutex):
            self._interval_sec = max(5, int(interval_sec or 45))
            self._cond.wakeAll()

    def set_auto_enabled(self, enabled: bool) -> None:
        with QMutexLocker(self._mutex):
            self._auto_enabled = bool(enabled)
            self._cond.wakeAll()

    def trigger_sync(self, forzar_todo: bool = False) -> None:
        """Despierta el hilo para ejecutar una sincronización inmediata."""
        with QMutexLocker(self._mutex):
            self._trigger_requested = True
            if forzar_todo:
                self._force_full_requested = True
            self._cond.wakeAll()

    def stop(self) -> None:
        """Solicita detención ordenada del hilo de sincronización."""
        with QMutexLocker(self._mutex):
            self._stop_requested = True
            self._cond.wakeAll()

    def _ejecutar_ciclo(self, forzar_todo: bool = False) -> None:
        try:
            estado_inicial = self.sync_service.get_estado_sincronizacion()
            if estado_inicial.get("modo") == MODO_LOCAL:
                self.status_updated.emit(estado_inicial)
                return

            self.sync_started.emit()
            res = self.sync_service.sincronizar_pendientes(forzar_todo=forzar_todo)
            self.sync_finished.emit(res)
            self.status_updated.emit(res)
        except Exception as ex:
            logger.warning("Excepción controlada en SyncBackgroundWorker: %s", ex)
            estado = self.sync_service.get_estado_sincronizacion()
            estado["ok"] = False
            estado["error"] = str(ex)
            self.sync_finished.emit(estado)
            self.status_updated.emit(estado)

    def run(self) -> None:
        # Emitir estado inicial inmediatamente al arrancar
        try:
            self.status_updated.emit(self.sync_service.get_estado_sincronizacion())
        except Exception:
            pass

        while True:
            with QMutexLocker(self._mutex):
                if self._stop_requested:
                    break

                debe_ejecutar = self._trigger_requested or self._auto_enabled
                forzar = self._force_full_requested
                self._trigger_requested = False
                self._force_full_requested = False

            if debe_ejecutar and not self._stop_requested:
                self._ejecutar_ciclo(forzar_todo=forzar)

            with QMutexLocker(self._mutex):
                if self._stop_requested:
                    break
                if self._trigger_requested:
                    continue
                if self._auto_enabled:
                    self._cond.wait(self._mutex, self._interval_sec * 1000)
                else:
                    # En espera indefinida hasta trigger_sync() o stop()
                    self._cond.wait(self._mutex)
