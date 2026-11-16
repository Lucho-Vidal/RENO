"""Pruebas de cierre nativo (botón X), flags de ventana, maximización y limpieza de hilos."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QTimer, Qt, Signal, QObject
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from src.core.theme import (
    DIALOG_LOCKED_FLAGS,
    DIALOG_MAXIMIZED_FLAGS,
    DIALOG_STANDARD_FLAGS,
    _WORKERS_DESVINCULADOS,
    aplicar_icono_aplicacion,
    desvincular_worker_seguro,
)
from src.data.database import DatabaseManager
from src.data.migrations import init_database_schema
from src.domain.enums import PERMISOS_BASE
from src.services.auth_service import AuthService
from src.services.backup_service import BackupService
from src.services.excel_service import ExcelService
from src.services.pdf_service import PDFService
from src.services.records_service import RecordsService
from src.services.updater_service import InfoActualizacion
from src.services.word_service import WordService
from src.ui.main_window import MainWindow
from src.ui.views.dialogs.acerca_dialog import AcercaDialog
from src.ui.views.dialogs.actualizacion_dialog import ActualizacionDialog
from src.ui.views.dialogs.admin_dialogs import (
    AdminAuditoriaDialog,
    AdminBackupsDialog,
    AdminCatalogosDialog,
    AdminConfigDialog,
    AdminDestinatariosDialog,
    AdminEliminadosDialog,
    AdminEmpleadosDialog,
    AdminRolesDialog,
    AdminUsuariosDialog,
)
from src.ui.views.dialogs.admin_tren_paradas_dialog import (
    AdminEstacionesDialog,
    AdminTrenParadasDialog,
)
from src.ui.views.dialogs.admin_turnos_dialog import AdminTurnosDialog
from src.ui.views.dialogs.base_dialog import BaseDialog, BaseMaximizedDialog
from src.ui.views.dialogs.buscador_tren_dialog import BuscadorTrenDialog
from src.ui.views.dialogs.calendario_ciclo_dialog import (
    AsignarJornadaDialog,
    CalendarioCicloDialog,
)
from src.ui.views.dialogs.cambiar_password_dialog import CambiarMiPasswordDialog
from src.ui.views.dialogs.cambio_turno_dialog import CambioTurnoDialog
from src.ui.views.dialogs.detalle_turno_dialog import DetalleTurnoDialog
from src.ui.views.dialogs.legajo_search_dialog import LegajoSearchDialog
from src.ui.views.dialogs.login_dialog import (
    CambioPasswordObligatorioDialog,
    LoginDialog,
)
from src.ui.views.dialogs.novedad_dialog import NovedadDialog, TramoEditDialog
from src.ui.views.dialogs.nuevos_registros_dialog import NuevosRegistrosDialog
from src.ui.views.dialogs.ordenamiento_dialog import OrdenamientoDialog
from src.ui.views.dialogs.turno_dialog import TurnoDialog, VueltaItemDialog
from src.ui.views.novedades_view import TurnosSinCubrirDialog


def _get_app() -> QApplication:
    app = QApplication.instance() or QApplication([])
    aplicar_icono_aplicacion(app)
    return app


def _build_env(tmp_path: Path) -> dict:
    db = DatabaseManager(tmp_path / "test_close_buttons.sqlite", journal_mode="WAL")
    init_database_schema(db)

    auth = AuthService(db)
    auth.inicializar_permisos(PERMISOS_BASE)
    admin_id = auth.crear_administrador_inicial("admin", "Admin123!", legajo=1001)
    usuario = auth.autenticar("admin", "Admin123!")

    with db.write_transaction() as conn:
        conn.execute(
            """
            INSERT INTO empleados (
                legajo, apellidos_nombres, especialidad, dotacion, turnos, franco,
                franco_horario, jornada_horas, activo, actualizado_en
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, '2026-11-16 09:00:00')
            """,
            (1001, "Perez Juan", "Conductor", "PC", "CICLO M", "Domingo", "00:00", 6),
        )
        conn.execute(
            """
            INSERT INTO empleados (
                legajo, apellidos_nombres, especialidad, dotacion, turnos, franco,
                franco_horario, jornada_horas, activo, actualizado_en
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, '2026-11-16 09:00:00')
            """,
            (1002, "Gomez Luis", "Conductor", "PC", "CICLO M", "Sábado", "00:00", 6),
        )
        conn.execute(
            """
            INSERT INTO turnos_itinerario (
                codigo_turno, turno_base, tipo_turno, deposito_base, toma_servicio, deje_servicio, itinerario_vigente, activo
            ) VALUES ('T100', 'T100', 'Electrico', 'PC', '06:00', '12:00', 'Habil', 1)
            """
        )

    records = RecordsService(db)
    excel = ExcelService(db)
    pdf = PDFService()
    word = WordService()
    backup = BackupService(db)
    empleados = records.listar_empleados()

    info_upd = InfoActualizacion(
        version="2.9.9",
        fecha="16/11/2026",
        titulo="Actualización de prueba",
        novedades=["Prueba de cierre"],
        instalador_nombre="RENO_Setup_v2.9.9.exe",
        instalador_url="https://example.com/setup.exe",
        sha256="0" * 64,
        tamano_bytes=1024,
        obligatoria=False,
    )

    return {
        "db": db,
        "auth": auth,
        "records": records,
        "excel": excel,
        "pdf": pdf,
        "word": word,
        "backup": backup,
        "admin_id": admin_id,
        "usuario": usuario,
        "empleados": empleados,
        "info_upd": info_upd,
    }


def _assert_close_button_flags(dlg: QDialog, expected_maximizable: bool = False) -> None:
    flags = dlg.windowFlags()
    assert bool(flags & Qt.Window), f"{type(dlg).__name__} debe incluir Qt.Window"
    assert not (
        (flags & Qt.WindowType_Mask) == Qt.Dialog
    ), f"{type(dlg).__name__} no debe usar Qt.Dialog puro"
    assert bool(flags & Qt.WindowTitleHint), f"{type(dlg).__name__} requiere WindowTitleHint"
    assert bool(flags & Qt.WindowSystemMenuHint), f"{type(dlg).__name__} requiere WindowSystemMenuHint"
    assert bool(flags & Qt.WindowCloseButtonHint), f"{type(dlg).__name__} requiere WindowCloseButtonHint"
    assert not bool(flags & Qt.WindowContextHelpButtonHint), (
        f"{type(dlg).__name__} no debe incluir WindowContextHelpButtonHint"
    )
    if expected_maximizable:
        assert bool(flags & Qt.WindowMinimizeButtonHint)
        assert bool(flags & Qt.WindowMaximizeButtonHint)
        assert (flags & (Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)) == DIALOG_MAXIMIZED_FLAGS
    else:
        assert (flags & (Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowCloseButtonHint)) == DIALOG_STANDARD_FLAGS
    assert not dlg.windowIcon().isNull(), f"{type(dlg).__name__} debe tener icono corporativo"


def _exec_and_send_native_close(dlg: QDialog, assert_maximized: bool = False) -> int:
    """Ejecuta exec() y envía un QCloseEvent simulando el botón nativo X."""
    app = _get_app()
    observado = {"maximized": None, "event_accepted": None}

    def _trigger_close() -> None:
        observado["maximized"] = dlg.isMaximized()
        ev = QCloseEvent()
        QApplication.sendEvent(dlg, ev)
        observado["event_accepted"] = ev.isAccepted()
        if dlg.isVisible():
            # Fallback de seguridad para que nunca quede bloqueado pytest si falla el contrato
            dlg.done(QDialog.Rejected)

    QTimer.singleShot(0, _trigger_close)
    res = dlg.exec()
    app.processEvents()
    assert observado["event_accepted"] is True, f"{type(dlg).__name__} debe aceptar QCloseEvent"
    if assert_maximized:
        assert observado["maximized"] is True, f"{type(dlg).__name__} debe abrirse maximizado"
    assert dlg.isVisible() is False, f"{type(dlg).__name__} debe ocultarse tras cerrar con X"
    return res


def test_all_dialogs_close_button_flags_and_exec_close(tmp_path: Path):
    """Verifica todos los diálogos estándar y maximizados con QCloseEvent nativo durante exec()."""
    _get_app()
    env = _build_env(tmp_path)

    standard_factories = [
        ("BaseDialog", lambda: BaseDialog()),
        ("AcercaDialog", lambda: AcercaDialog()),
        ("ActualizacionDialog(opcional)", lambda: ActualizacionDialog(env["info_upd"], es_obligatoria=False)),
        ("AdminEstacionesDialog", lambda: AdminEstacionesDialog(env["records"], usuario_actual=env["usuario"])),
        ("BuscadorTrenDialog", lambda: BuscadorTrenDialog(env["records"], env["excel"])),
        (
            "AsignarJornadaDialog",
            lambda: AsignarJornadaDialog(
                1001,
                "Perez Juan",
                "2026-11-16",
                "Lunes",
                franco_agente="Domingo",
                records_service=env["records"],
            ),
        ),
        ("CambiarMiPasswordDialog", lambda: CambiarMiPasswordDialog(env["auth"], env["usuario"])),
        ("CambioTurnoDialog", lambda: CambioTurnoDialog(env["records"], usuario_actual=env["usuario"])),
        ("DetalleTurnoDialog", lambda: DetalleTurnoDialog(env["records"], "T100")),
        ("LegajoSearchDialog", lambda: LegajoSearchDialog(env["empleados"])),
        ("CambioPasswordObligatorioDialog", lambda: CambioPasswordObligatorioDialog(env["admin_id"], env["auth"])),
        ("LoginDialog", lambda: LoginDialog(env["auth"])),
        ("TramoEditDialog", lambda: TramoEditDialog(env["empleados"], date(2026, 11, 16))),
        ("NovedadDialog", lambda: NovedadDialog(env["records"], usuario_actual=env["usuario"])),
        (
            "NuevosRegistrosDialog",
            lambda: NuevosRegistrosDialog([], [], lambda _: None, lambda _: None),
        ),
        ("VueltaItemDialog", lambda: VueltaItemDialog()),
        ("TurnoDialog", lambda: TurnoDialog(env["records"], usuario_actual=env["usuario"])),
        ("TurnosSinCubrirDialog", lambda: TurnosSinCubrirDialog(env["records"], env["excel"], ["PC"])),
    ]

    maximized_factories = [
        ("BaseMaximizedDialog", lambda: BaseMaximizedDialog()),
        ("AdminUsuariosDialog", lambda: AdminUsuariosDialog(env["auth"])),
        ("AdminRolesDialog", lambda: AdminRolesDialog(env["auth"])),
        ("AdminEmpleadosDialog", lambda: AdminEmpleadosDialog(env["records"], env["excel"])),
        ("AdminCatalogosDialog", lambda: AdminCatalogosDialog(env["records"], env["excel"], usuario_actual=env["usuario"])),
        ("AdminDestinatariosDialog", lambda: AdminDestinatariosDialog(env["records"], usuario_actual=env["usuario"])),
        ("AdminConfigDialog", lambda: AdminConfigDialog(env["db"], records_service=env["records"], usuario_actual=env["usuario"])),
        ("AdminBackupsDialog", lambda: AdminBackupsDialog(env["backup"])),
        ("AdminEliminadosDialog", lambda: AdminEliminadosDialog(env["records"], usuario_actual=env["usuario"])),
        ("AdminAuditoriaDialog", lambda: AdminAuditoriaDialog(env["records"], excel_service=env["excel"])),
        ("AdminTrenParadasDialog", lambda: AdminTrenParadasDialog(env["records"], usuario_actual=env["usuario"])),
        ("AdminTurnosDialog", lambda: AdminTurnosDialog(env["records"], usuario_actual=env["usuario"])),
        (
            "CalendarioCicloDialog",
            lambda: CalendarioCicloDialog(
                env["empleados"][0],
                env["records"],
                env["pdf"],
                env["excel"],
                usuario_actual=env["usuario"],
            ),
        ),
        ("OrdenamientoDialog", lambda: OrdenamientoDialog(env["records"], pdf_service=env["pdf"], usuario_actual=env["usuario"])),
    ]

    assert len(standard_factories) == 18
    assert len(maximized_factories) == 14

    for nombre, factory in standard_factories:
        dlg = factory()
        try:
            _assert_close_button_flags(dlg, expected_maximizable=False)
            res = _exec_and_send_native_close(dlg, assert_maximized=False)
            assert res == QDialog.Rejected, f"{nombre} debe devolver Rejected tras cerrarse con X"
        finally:
            dlg.deleteLater()

    for nombre, factory in maximized_factories:
        dlg = factory()
        try:
            _assert_close_button_flags(dlg, expected_maximizable=True)
            # Dos ciclos consecutivos show/exec -> close para verificar que no haya recursión ni bloqueo de estado
            for ciclo in range(2):
                res = _exec_and_send_native_close(dlg, assert_maximized=True)
                assert res == QDialog.Rejected, f"{nombre} (ciclo {ciclo + 1}) debe devolver Rejected"
        finally:
            dlg.deleteLater()


def test_actualizacion_obligatoria_bloquea_cierre_hasta_descarga(tmp_path: Path):
    """Verifica el candado de cierre obligatorio antes de descargar y la liberación al completar."""
    _get_app()
    info = InfoActualizacion(
        version="2.9.9",
        fecha="16/11/2026",
        titulo="Obligatoria",
        novedades=["Cambio crítico"],
        instalador_nombre="RENO_Setup_v2.9.9.exe",
        instalador_url="https://example.com/setup.exe",
    )

    dlg_obl = ActualizacionDialog(info, es_obligatoria=True)
    try:
        assert (dlg_obl.windowFlags() & DIALOG_LOCKED_FLAGS) == DIALOG_LOCKED_FLAGS
        assert not bool(dlg_obl.windowFlags() & Qt.WindowCloseButtonHint)

        with patch("src.ui.views.dialogs.actualizacion_dialog.QMessageBox.warning") as mock_warn:
            dlg_obl.show()
            ev_blocked = QCloseEvent()
            QApplication.sendEvent(dlg_obl, ev_blocked)
            assert ev_blocked.isAccepted() is False
            assert dlg_obl.isVisible() is True
            mock_warn.assert_called()

            mock_warn.reset_mock()
            dlg_obl.reject()
            assert dlg_obl.isVisible() is True
            mock_warn.assert_called_once()

        # Una vez descargado el instalador, sí permite cerrarse limpiamente
        dlg_obl._on_descarga_completada(str(tmp_path / "RENO_Setup_v2.9.9.exe"))
        assert dlg_obl._permitir_cierre_nativo is True

        ev_allowed = QCloseEvent()
        QApplication.sendEvent(dlg_obl, ev_allowed)
        assert ev_allowed.isAccepted() is True
        assert dlg_obl.isVisible() is False
    finally:
        dlg_obl.deleteLater()


def test_actualizacion_opcional_con_worker_activo_cierra_con_x(tmp_path: Path):
    """Verifica que ActualizacionDialog opcional con descarga activa cierre con X sin errores."""
    _get_app()
    info = InfoActualizacion(
        version="2.9.9",
        fecha="16/11/2026",
        titulo="Opcional",
        instalador_url="https://example.com/setup.exe",
    )
    dlg = ActualizacionDialog(info, es_obligatoria=False)
    mock_worker = MagicMock()
    mock_worker.isRunning.return_value = True
    mock_worker.wait.return_value = True
    mock_worker.isFinished.return_value = True

    try:
        with patch(
            "src.ui.views.dialogs.actualizacion_dialog.DescargarActualizacionWorker",
            return_value=mock_worker,
        ):
            dlg._iniciar_descarga()

        res = _exec_and_send_native_close(dlg)
        assert res == QDialog.Rejected
        mock_worker.cancelar.assert_called()
        mock_worker.requestInterruption.assert_called()
        assert dlg.worker_descarga is None
    finally:
        dlg.deleteLater()


def test_legajo_focusout_al_cerrar_no_dispara_alerta_fantasma(tmp_path: Path):
    """Evita que editingFinished dispare QMessageBox.warning cuando se cierra con X."""
    app = _get_app()
    env = _build_env(tmp_path)

    with patch("src.ui.views.dialogs.novedad_dialog.QMessageBox.warning") as warn_nov:
        dlg_nov = NovedadDialog(env["records"], usuario_actual=env["usuario"])
        try:
            dlg_nov.show()
            dlg_nov.txt_legajo.setText("999999")
            dlg_nov.txt_legajo.setFocus()
            ev = QCloseEvent()
            QApplication.sendEvent(dlg_nov, ev)
            dlg_nov._on_legajo_changed()
            app.processEvents()
            assert ev.isAccepted() is True
            warn_nov.assert_not_called()
        finally:
            dlg_nov.deleteLater()

    with patch("src.ui.views.dialogs.cambio_turno_dialog.QMessageBox.warning") as warn_cam:
        dlg_cam = CambioTurnoDialog(env["records"], usuario_actual=env["usuario"])
        try:
            dlg_cam.show()
            dlg_cam.txt_legajo_1.setText("999999")
            dlg_cam.txt_legajo_1.setFocus()
            ev = QCloseEvent()
            QApplication.sendEvent(dlg_cam, ev)
            dlg_cam._on_legajo_changed(1)
            app.processEvents()
            assert ev.isAccepted() is True
            warn_cam.assert_not_called()
        finally:
            dlg_cam.deleteLater()

    with patch("src.ui.views.dialogs.ordenamiento_dialog.QMessageBox.warning") as warn_ord:
        dlg_ord = OrdenamientoDialog(env["records"], pdf_service=env["pdf"], usuario_actual=env["usuario"])
        try:
            dlg_ord.show()
            dlg_ord.txt_legajo.setText("999999")
            dlg_ord.txt_legajo.setFocus()
            ev = QCloseEvent()
            QApplication.sendEvent(dlg_ord, ev)
            dlg_ord._on_legajo_changed()
            app.processEvents()
            assert ev.isAccepted() is True
            warn_ord.assert_not_called()
        finally:
            dlg_ord.deleteLater()


def test_mainwindow_y_desvincular_worker_cierran_limpiamente(tmp_path: Path):
    """Verifica cierre con X de MainWindow con toast activo, timers y workers en curso."""
    app = _get_app()
    env = _build_env(tmp_path)

    class _DummySignals(QObject):
        finished = Signal()

    class _SlowWorker(_DummySignals):
        def __init__(self, finish_during_wait: bool = False) -> None:
            super().__init__()
            self.cancelado = False
            self.interrumpido = False
            self.deleted = False
            self._finished_flag = False
            self._finish_during_wait = finish_during_wait

        def isRunning(self) -> bool:
            return not self._finished_flag

        def cancelar(self) -> None:
            self.cancelado = True

        def requestInterruption(self) -> None:
            self.interrumpido = True

        def quit(self) -> None:
            pass

        def wait(self, _ms: int) -> bool:
            if self._finish_during_wait:
                self._finished_flag = True
            return False

        def setParent(self, _p) -> None:
            pass

        def isFinished(self) -> bool:
            return self._finished_flag

        def deleteLater(self) -> None:
            self.deleted = True

    worker_lento = _SlowWorker()
    win = MainWindow(
        db=env["db"],
        records_service=env["records"],
        auth_service=env["auth"],
        excel_service=env["excel"],
        pdf_service=env["pdf"],
        word_service=env["word"],
        backup_service=env["backup"],
        usuario_actual=env["usuario"],
        supabase_sync=None,
    )
    try:
        win._worker_updater = worker_lento
        win.toast.show_message("Aviso", "Prueba de cierre", duration_sec=5)
        assert win.toast.isVisible() is True

        ev = QCloseEvent()
        QApplication.sendEvent(win, ev)
        app.processEvents()

        assert ev.isAccepted() is True
        assert win._cerrando is True
        assert win.timer_refresh.isActive() is False
        assert win.timer_sync.isActive() is False
        assert win.timer_inactividad.isActive() is False
        assert win.timer_updater_inicial.isActive() is False
        assert win.toast.isVisible() is False
        assert worker_lento.cancelado is True
        assert worker_lento.interrumpido is True
        assert worker_lento in _WORKERS_DESVINCULADOS

        # Cuando el hilo lento finalmente emite finished, se libera sin fuga
        worker_lento._finished_flag = True
        worker_lento.finished.emit()
        assert worker_lento not in _WORKERS_DESVINCULADOS
        assert worker_lento.deleted is True
    finally:
        _WORKERS_DESVINCULADOS.discard(worker_lento)
        win.deleteLater()

    # Caso borde: race donde worker termina entre wait()==False e isFinished()
    worker_race = _SlowWorker(finish_during_wait=True)
    desvincular_worker_seguro(worker_race, timeout_ms=50, cancelar=True)
    assert worker_race.cancelado is True
    assert worker_race.deleted is True
    assert worker_race not in _WORKERS_DESVINCULADOS


def test_admin_config_dialog_limpia_worker_al_cerrar(tmp_path: Path):
    """Verifica que AdminConfigDialog detenga el worker del updater al cerrar con X."""
    _get_app()
    env = _build_env(tmp_path)
    dlg = AdminConfigDialog(
        env["db"],
        records_service=env["records"],
        usuario_actual=env["usuario"],
    )
    mock_worker = MagicMock()
    mock_worker.isRunning.return_value = True
    mock_worker.wait.return_value = True

    try:
        dlg.widget._worker_cfg_updater = mock_worker
        res = _exec_and_send_native_close(dlg, assert_maximized=True)
        assert res == QDialog.Rejected
        assert dlg.widget._cerrando is True
        assert dlg.widget._worker_cfg_updater is None
        mock_worker.cancelar.assert_called()
        mock_worker.requestInterruption.assert_called()
    finally:
        dlg.deleteLater()
