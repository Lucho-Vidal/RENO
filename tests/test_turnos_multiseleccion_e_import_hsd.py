"""Pruebas integrales para turnos H/S/D, importación formato imagen_2, migración legacy y borrado múltiple/por filtro."""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pytest
from PySide6.QtCore import QDate, QItemSelection, QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QTableView

from src.data.database import DatabaseManager
from src.data.migrations import init_database_schema
from src.services.excel_service import ExcelService
from src.services.records_service import RecordsService
from src.ui.views.buscador_turnos_view import BuscadorTurnosView
from src.ui.views.dialogs.admin_turnos_dialog import AdminTurnosDialog
from src.ui.views.dialogs.detalle_turno_dialog import DetalleTurnoDialog
from src.ui.views.dialogs.turno_dialog import TurnoDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def env_db(tmp_path: Path):
    db = DatabaseManager(tmp_path / "turnos_hsd.sqlite", journal_mode="DELETE")
    init_database_schema(db)
    svc = RecordsService(db)
    excel = ExcelService(db)
    return {"db": db, "svc": svc, "excel": excel, "tmp": tmp_path}


def test_migracion_legacy_turnos_itinerario_y_unicidad_compuesta(tmp_path: Path):
    """Migra una DB legacy con UNIQUE(codigo_turno) sin regimen preservando vueltas e IDs."""
    db_file = tmp_path / "legacy_turnos.sqlite"
    raw = sqlite3.connect(db_file)
    raw.executescript(
        """
        CREATE TABLE turnos_itinerario (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo_turno TEXT UNIQUE NOT NULL,
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
            updated_at TEXT
        );
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
        INSERT INTO turnos_itinerario (
            id, codigo_turno, turno_base, subindice, tipo_turno, deposito_base,
            toma_servicio, deje_servicio, duracion_jornada, itinerario_vigente, activo
        ) VALUES (
            7, '401.1', '401', 1, 'DIAGRAMA_6D', 'PC',
            '18:55', '00:20', '05:25', 'Dic25', 1
        );
        INSERT INTO turnos_vueltas (turno_id, nro_vuelta, ref, nro_tren, origen, hora_salida, destino, hora_llegada)
        VALUES (7, 1, '', '2303', 'PC', '19:15', 'BQ', '20:05');
        """
    )
    raw.commit()
    raw.close()

    db = DatabaseManager(db_file, journal_mode="DELETE")
    init_database_schema(db)
    svc = RecordsService(db)

    # El turno legacy #7 y sus vueltas deben seguir intactos con regimen='TODOS'
    t_legacy = svc.obtener_turno_completo(7)
    assert t_legacy is not None
    assert t_legacy["codigo_turno"] == "401.1"
    assert t_legacy["regimen"] == "TODOS"
    assert len(t_legacy["vueltas"]) == 1
    assert t_legacy["vueltas"][0]["nro_tren"] == "2303"

    # Ahora deben coexistir 401.1 en H, S, D y otro itinerario sin colisión
    id_h = svc.crear_turno_itinerario(
        {"codigo_turno": "401.1", "regimen": "H", "deposito_base": "PC", "toma_servicio": "19:20", "deje_servicio": "00:45", "itinerario_vigente": "Abril26"},
        [{"nro_vuelta": 1, "nro_tren": "1313", "origen": "PC", "hora_salida": "19:44", "destino": "BQ", "hora_llegada": "20:38"}],
    )
    id_s = svc.crear_turno_itinerario(
        {"codigo_turno": "401.1", "regimen": "S", "deposito_base": "PC", "toma_servicio": "20:00", "deje_servicio": "01:00", "itinerario_vigente": "Abril26"},
        [{"nro_vuelta": 1, "nro_tren": "1313", "origen": "PC", "hora_salida": "20:15", "destino": "BQ", "hora_llegada": "21:05"}],
    )
    id_d = svc.crear_turno_itinerario(
        {"codigo_turno": "401.1", "regimen": "D", "deposito_base": "PC", "toma_servicio": "09:00", "deje_servicio": "14:00", "itinerario_vigente": "Abril26"},
        [{"nro_vuelta": 1, "nro_tren": "1313", "origen": "PC", "hora_salida": "09:20", "destino": "BQ", "hora_llegada": "10:10"}],
    )
    assert len({7, id_h, id_s, id_d}) == 4

    # Duplicado exacto (codigo + regimen + itinerario) sí debe rechazarse
    with pytest.raises(ValueError, match="Ya existe el turno"):
        svc.crear_turno_itinerario(
            {"codigo_turno": "401.1", "regimen": "H", "itinerario_vigente": "Abril26"},
            [],
        )


def _crear_excel_formato_imagen(xlsx_path: Path) -> None:
    """Genera un libro con las 15 columnas exactas de image_2.png (turno, dia H/S/D, ..., nombre_itinerario, nombre_hoja, nombre_archivo)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Turnos"
    ws.append([
        "turno", "dia", "toma", "deja", "vuelta", "ref", "tren",
        "origen", "sale", "destino", "llega", "observaciones",
        "nombre_itinerario", "nombre_hoja", "nombre_archivo",
    ])
    # 401,1 Hábil Abril26 (con coma decimal, PAS., int/str y observaciones X)
    rows = [
        ["401,1", "H", "19:20", "00:45", "1", "", 1313, "PC", "19:44", "BQ", "20:38", "", "Abril26", "PC COND ELEC", "Tur.COND.ELEC. LaV. ITI.5 ABR.2026 C-F.xls"],
        ["401,1", "H", "19:20", "00:45", "2", "", "1318", "BQ", "20:46", "PC", "21:42", "", "Abril26", "PC COND ELEC", "Tur.COND.ELEC. LaV. ITI.5 ABR.2026 C-F.xls"],
        ["401,1", "H", "19:20", "00:45", "3", "", "7753", "PC", "22:30", "QL", "22:51", "X", "Abril26", "PC COND ELEC", "Tur.COND.ELEC. LaV. ITI.5 ABR.2026 C-F.xls"],
        ["401,1", "H", "19:20", "00:45", "4", "PAS.", "4356", "QL", "23:06", "PC", "23:35", "", "Abril26", "PC COND ELEC", "Tur.COND.ELEC. LaV. ITI.5 ABR.2026 C-F.xls"],
        # Mismo código 401,1 en Sábado (S) con datetime.time y fracción serial Excel
        ["401,1", "S", datetime.time(20, 0), datetime.time(1, 0), 1, "", "1313", "PC", 0.875, "BQ", datetime.time(21, 52), "PD", "Abril26", "TY COND SAB", "Tur.COND.ELEC. Sab. ITI.5 ABR.2026.xls"],
        # Mismo código 401,1 en Domingo (D)
        ["401,1", "D", "08:00", "14:00", 1, "", "1313", "PC", "08:30", "BQ", "09:20", "XX", "Abril26", "PC COND DOM", "Tur.COND.ELEC. Dom. ITI.5 ABR.2026.xls"],
        # Turno 401,2 de otro itinerario Marzo26
        ["401,2", "H", "18:45", "00:15", 1, "", "1301", "PC", "19:11", "BQ", "20:01", "PD", "Marzo26", "PC COND ELEC", "Tur.COND.ELEC. LaV. MAR.2026.xls"],
    ]
    for r in rows:
        ws.append(r)
    wb.save(xlsx_path)


def test_importar_turnos_formato_imagen_hsd_y_multiitinerario(env_db):
    """Importa planilla de 15 columnas con dia H/S/D, coma decimal e itinerarios múltiples sin colisión."""
    svc: RecordsService = env_db["svc"]
    xlsx_path: Path = env_db["tmp"] / "turnos_abril26.xlsx"
    _crear_excel_formato_imagen(xlsx_path)

    turnos, vueltas = svc.importar_itinerario_excel(str(xlsx_path))
    assert turnos == 4
    assert vueltas == 7

    lista = svc.listar_turnos_admin(criterio="401")
    assert len(lista) == 4

    t_h = svc.obtener_turno_por_codigo("401.1", itinerario="Abril26", regimen="H")
    assert t_h is not None
    assert t_h["regimen"] == "H"
    assert t_h["regimen_label"] == "H (Hábil)"
    assert t_h["toma_servicio"] == "19:20"
    assert t_h["deje_servicio"] == "00:45"
    assert t_h["duracion_jornada"] == "05:25"
    assert t_h["deposito_base"] == "PC"
    assert t_h["subindice"] == 1
    assert len(t_h["vueltas"]) == 4
    assert t_h["vueltas"][0]["nro_tren"] == "1313"
    assert t_h["vueltas"][2]["observaciones_tramo"] == "X"
    assert t_h["vueltas"][3]["ref"] == "PAS."

    t_s = svc.obtener_turno_por_codigo("401.1", itinerario="Abril26", regimen="S")
    assert t_s is not None
    assert t_s["regimen"] == "S"
    assert t_s["toma_servicio"] == "20:00"
    assert t_s["deje_servicio"] == "01:00"
    assert t_s["duracion_jornada"] == "05:00"
    assert t_s["deposito_base"] == "TY"
    assert t_s["vueltas"][0]["hora_salida"] == "21:00"
    assert t_s["vueltas"][0]["hora_llegada"] == "21:52"

    t_d = svc.obtener_turno_por_codigo("401.1", itinerario="Abril26", regimen="D")
    assert t_d is not None
    assert t_d["regimen"] == "D"
    assert t_d["toma_servicio"] == "08:00"
    assert t_d["deje_servicio"] == "14:00"

    # Reimportación es idempotente y mantiene 4 turnos / 7 vueltas
    turnos2, vueltas2 = svc.importar_itinerario_excel(str(xlsx_path))
    assert (turnos2, vueltas2) == (4, 7)
    assert len(svc.listar_turnos_admin()) == 4


def test_buscador_turnos_filtrado_hsd_y_calendario_ciclo(qapp, env_db):
    """Filtra por H/S/D y por itinerario en BuscadorTurnosView, exporta Día y calcula calendario de ciclo."""
    svc: RecordsService = env_db["svc"]
    excel: ExcelService = env_db["excel"]
    xlsx_path: Path = env_db["tmp"] / "turnos_abril26.xlsx"
    _crear_excel_formato_imagen(xlsx_path)
    svc.importar_itinerario_excel(str(xlsx_path))

    # Titular 1 (franco Lunes -> Martes 07/04 es sub=1 Hábil H) y Titular 2 (franco Viernes -> Sábado 04/04 es sub=1 Sábado S)
    svc.crear_empleado({
        "legajo": 1001, "apellidos_nombres": "TITULAR HABIL",
        "especialidad": "Conductor Electrico", "dotacion": "PC", "turnos": "401", "franco": "Lunes",
    })
    svc.crear_empleado({
        "legajo": 1002, "apellidos_nombres": "TITULAR SABADO",
        "especialidad": "Conductor Electrico", "dotacion": "PC", "turnos": "401", "franco": "Viernes",
    })
    svc.crear_empleado({
        "legajo": 2001, "apellidos_nombres": "CICLO PRUEBA",
        "especialidad": "Conductor Electrico", "dotacion": "PC", "turnos": "CICLO 1", "franco": "Domingo",
    })
    with svc.db.write_transaction() as conn:
        conn.execute(
            """INSERT INTO novedades (id, registrado_en, legajo, apellidos_nombres, especialidad, dotacion, turnos, franco, novedad, fecha_inicio, fecha_fin, referencia_estacion, supervisor, observaciones, activo)
               VALUES (901, '01/04/2026 08:00', 1001, 'TITULAR HABIL', 'Conductor Electrico', 'PC', '401', 'Lunes', 'Enfermo', '06/04/2026', '10/04/2026', 'PC', 'ADMIN', '', 1),
                      (902, '01/04/2026 08:00', 1002, 'TITULAR SABADO', 'Conductor Electrico', 'PC', '401', 'Viernes', 'Enfermo', '04/04/2026', '04/04/2026', 'PC', 'ADMIN', '', 1);"""
        )
        conn.execute(
            """INSERT INTO novedad_reemplazos (id, novedad_id, legajo_reemplazo, apellidos_nombres_reemplazo, turnos_reemplazo, franco_reemplazo, fecha_inicio, fecha_fin, creado_en, activo)
               VALUES (901, 901, 2001, 'CICLO PRUEBA', 'CICLO 1', 'Domingo', '06/04/2026', '10/04/2026', '01/04/2026 08:05', 1),
                      (902, 902, 2001, 'CICLO PRUEBA', 'CICLO 1', 'Domingo', '04/04/2026', '04/04/2026', '01/04/2026 08:05', 1);"""
        )

    # En calendario de ciclo de Abril 2026:
    # Sábado 04/04/2026 (sub=1 -> 401.1 S): toma 20:00 / 01:00
    # Martes 07/04/2026 (sub=1 -> 401.1 H): toma 19:20 / 00:45
    cal = svc.obtener_calendario_mensual_ciclo(2001, 2026, 4)
    d_sab = next(d for d in cal if d["fecha"] == "04/04/2026")
    assert "401.1" in d_sab["reemplaza_a"]
    assert d_sab["toma"] == "20:00"
    assert d_sab["deja"] == "01:00"

    d_mar = next(d for d in cal if d["fecha"] == "07/04/2026")
    assert "401.1" in d_mar["reemplaza_a"]
    assert d_mar["toma"] == "19:20"
    assert d_mar["deja"] == "00:45"

    # Ahora probar UI BuscadorTurnosView
    view = BuscadorTurnosView(svc, excel)
    try:
        view.txt_criterio.setText("401")
        # Sábado 04/04/2026 -> combo_servicio se cambia automáticamente a "Sábado"
        view.date_picker.setDate(QDate(2026, 4, 4))
        assert view.combo_servicio.currentIndex() == 1
        view.consultar()
        assert view.model_turnos.rowCount() == 1
        assert view.model_turnos.get_row(0)["regimen"] == "S"
        assert view.model_turnos.get_row(0)["regimen_label"] == "S (Sábado)"

        # Domingo 05/04/2026 -> "Domingo / Feriado"
        view.date_picker.setDate(QDate(2026, 4, 5))
        assert view.combo_servicio.currentIndex() == 2
        view.consultar()
        assert view.model_turnos.rowCount() == 1
        assert view.model_turnos.get_row(0)["regimen"] == "D"

        # Día hábil -> vuelve H de Abril26 (401.1) y H de Marzo26 (401.2)
        view.date_picker.setDate(QDate(2026, 4, 7))
        assert view.combo_servicio.currentIndex() == 0
        view.consultar()
        assert view.model_turnos.rowCount() == 2

        # Filtrar itinerario en CheckableComboBox solo por "Abril26"
        for r in range(1, view.combo_itinerarios._model.rowCount()):
            it = view.combo_itinerarios._model.item(r)
            it.setData(Qt.Checked if it.text() == "Abril26" else Qt.Unchecked, Qt.CheckStateRole)
        view.consultar()
        assert view.model_turnos.rowCount() == 1
        assert view.model_turnos.get_row(0)["itinerario_vigente"] == "Abril26"

        # Verificar exportación a Excel con columna "Día"
        out_xlsx = env_db["tmp"] / "export_buscador.xlsx"
        with (
            patch("src.ui.views.buscador_turnos_view.QFileDialog.getSaveFileName", return_value=(str(out_xlsx), "")),
            patch("src.ui.views.buscador_turnos_view.QMessageBox.information"),
        ):
            view._exportar_grilla_excel()
        wb_out = openpyxl.load_workbook(out_xlsx)
        assert wb_out["Turnos Encontrados"].cell(1, 2).value == "Día"
        assert wb_out["Turnos Encontrados"].cell(2, 2).value == "H (Hábil)"
        assert wb_out["Todas las Vueltas"].cell(1, 2).value == "Día"
        assert wb_out["Todas las Vueltas"].cell(2, 2).value == "H (Hábil)"
    finally:
        view.close()
        view.deleteLater()


def test_admin_turnos_seleccion_multiple_y_eliminar_por_filtros(qapp, env_db):
    """Verifica selección múltiple, activación/borrado en lote y 'Eliminar Todos (Filtro)' por itinerario/día."""
    svc: RecordsService = env_db["svc"]
    xlsx_path: Path = env_db["tmp"] / "turnos_abril26.xlsx"
    _crear_excel_formato_imagen(xlsx_path)
    svc.importar_itinerario_excel(str(xlsx_path))
    svc.crear_turno_itinerario(
        {"codigo_turno": "502.1", "regimen": "H", "deposito_base": "PC", "toma_servicio": "06:00", "deje_servicio": "12:00", "itinerario_vigente": "Mayo26"},
        [{"nro_vuelta": 1, "nro_tren": "2001", "origen": "PC", "hora_salida": "06:30", "destino": "TY", "hora_llegada": "07:00"}],
    )
    svc.crear_turno_itinerario(
        {"codigo_turno": "502.1", "regimen": "S", "deposito_base": "TY", "toma_servicio": "07:00", "deje_servicio": "13:00", "itinerario_vigente": "Mayo26"},
        [{"nro_vuelta": 1, "nro_tren": "2002", "origen": "TY", "hora_salida": "07:30", "destino": "PC", "hora_llegada": "08:00"}],
    )

    dlg = AdminTurnosDialog(svc)
    w = dlg.widget
    try:
        assert w.table.selectionMode() == QTableView.ExtendedSelection
        assert w.model.rowCount() == 6  # Abril26 (H,S,D) + Marzo26 (H) + Mayo26 (H,S)

        # 1. Filtrar por Día = S -> debe mostrar S de Abril26 y S de Mayo26
        idx_s = w.combo_regimen.findData("S")
        w.combo_regimen.setCurrentIndex(idx_s)
        assert w.model.rowCount() == 2
        assert all(t["regimen"] == "S" for t in w._turnos)

        # 2. Seleccionar múltiples filas (las 2 filas S) y alternar estado en lote
        sel_model = w.table.selectionModel()
        sel_all = QItemSelection(w.model.index(0, 0), w.model.index(1, w.model.columnCount() - 1))
        sel_model.select(sel_all, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        assert len(w._obtener_seleccionados()) == 2
        assert "2 seleccionado(s)" in w.lbl_conteo.text()

        w._toggle_estado()
        assert w.hubo_cambios is True
        assert all(t["activo"] == 0 for t in w._turnos)

        # 3. Volver a seleccionar las 2 filas y eliminarlas en lote con btn_eliminar
        w.hubo_cambios = False
        sel_all = QItemSelection(w.model.index(0, 0), w.model.index(1, w.model.columnCount() - 1))
        w.table.selectionModel().select(sel_all, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        with patch("src.ui.views.dialogs.admin_turnos_dialog.QMessageBox.warning", return_value=QMessageBox.Yes):
            w._eliminar_definitivo()
        assert w.hubo_cambios is True
        assert w.model.rowCount() == 0

        # 4. Limpiar filtros -> quedan 4 turnos (Abril26 H, Abril26 D, Marzo26 H, Mayo26 H)
        w._limpiar_filtros()
        assert w.model.rowCount() == 4

        # 5. Filtrar por Itinerario = "Abril26" y probar cancelar vs confirmar en "Eliminar Todos (Filtro)"
        idx_abr = w.combo_itinerario.findText("Abril26")
        assert idx_abr >= 1
        w.combo_itinerario.setCurrentIndex(idx_abr)
        assert w.model.rowCount() == 2

        with patch("src.ui.views.dialogs.admin_turnos_dialog.QMessageBox.warning", return_value=QMessageBox.No):
            w._eliminar_todos_filtrados()
        assert w.model.rowCount() == 2  # Cancelado: no borró nada

        with (
            patch("src.ui.views.dialogs.admin_turnos_dialog.QMessageBox.warning", return_value=QMessageBox.Yes),
            patch("src.ui.views.dialogs.admin_turnos_dialog.QMessageBox.information") as mock_info,
        ):
            w._eliminar_todos_filtrados()
            mock_info.assert_called_once()

        # Al eliminarse todo Abril26, se actualiza combo_itinerario y permanecen Marzo26 y Mayo26 intactos
        assert w.combo_itinerario.findText("Abril26") == -1
        restantes = svc.listar_turnos_admin()
        assert len(restantes) == 2
        assert {r["itinerario_vigente"] for r in restantes} == {"Marzo26", "Mayo26"}

        # Verificar que turnos_vueltas huérfanas no existan en DB
        with svc.db.read_connection() as conn:
            huerfanas = conn.execute(
                "SELECT COUNT(*) FROM turnos_vueltas WHERE turno_id NOT IN (SELECT id FROM turnos_itinerario)"
            ).fetchone()[0]
            assert huerfanas == 0
    finally:
        dlg.deleteLater()

    # 6. Verificar bloqueo RBAC al intentar borrado masivo o selección sin permisos
    dlg_ro = AdminTurnosDialog(svc, tiene_permiso_fn=lambda _: False)
    try:
        with patch("src.ui.views.dialogs.admin_turnos_dialog.QMessageBox.warning"):
            dlg_ro.widget._eliminar_todos_filtrados()
            dlg_ro.widget._eliminar_definitivo()
        assert len(svc.listar_turnos_admin()) == 2
    finally:
        dlg_ro.deleteLater()


def test_turno_dialog_y_detalle_turno_dialog_con_regimen(qapp, env_db):
    """Verifica selección de Día H/S/D en TurnoDialog y visualización/exportación en DetalleTurnoDialog."""
    svc: RecordsService = env_db["svc"]

    dlg_new = TurnoDialog(svc)
    try:
        dlg_new.txt_codigo.setText("610.1")
        dlg_new.txt_base.setText("610")
        dlg_new.spin_subindice.setValue(1)
        dlg_new.txt_itinerario.setText("Abril26")
        dlg_new.combo_regimen.setCurrentIndex(dlg_new.combo_regimen.findData("S"))
        dlg_new._vueltas = [{
            "nro_vuelta": 1, "ref": "", "nro_tren": "3100",
            "origen": "PC", "hora_salida": "10:00", "destino": "TY", "hora_llegada": "10:30",
            "observaciones_tramo": "",
        }]
        dlg_new._on_guardar()
    finally:
        dlg_new.deleteLater()

    # Crear otra versión Hábil con igual código e itinerario para verificar selector en DetalleTurnoDialog
    svc.crear_turno_itinerario(
        {"codigo_turno": "610.1", "regimen": "H", "deposito_base": "PC", "toma_servicio": "06:00", "deje_servicio": "12:00", "itinerario_vigente": "Abril26"},
        [{"nro_vuelta": 1, "nro_tren": "3101", "origen": "PC", "hora_salida": "06:15", "destino": "TY", "hora_llegada": "06:45"}],
    )

    dlg_det = DetalleTurnoDialog(svc, codigo_turno="610.1", itinerario_inicial="Abril26", regimen_inicial="S")
    try:
        assert "S (Sábado)" in dlg_det.lbl_regimen.text()
        assert dlg_det.model.get_row(0)["nro_tren"] == "3100"

        # Cambiar a Hábil dentro del diálogo
        dlg_det.combo_regimen.setCurrentIndex(dlg_det.combo_regimen.findData("H"))
        assert "H (Hábil)" in dlg_det.lbl_regimen.text()
        assert dlg_det.model.get_row(0)["nro_tren"] == "3101"

        out_det = env_db["tmp"] / "detalle_610.xlsx"
        with (
            patch("src.ui.views.dialogs.detalle_turno_dialog.QFileDialog.getSaveFileName", return_value=(str(out_det), "")),
            patch("src.ui.views.dialogs.detalle_turno_dialog.QMessageBox.information"),
        ):
            dlg_det._exportar_excel()
        wb_det = openpyxl.load_workbook(out_det)
        assert wb_det.active.cell(1, 2).value == "Día"
        assert wb_det.active.cell(2, 2).value == "H (Hábil)"
    finally:
        dlg_det.deleteLater()


def test_turnos_sin_cubrir_pasa_regimen_a_detalle(qapp, env_db):
    """Verifica que TurnosSinCubrirView pasa el régimen H/S/D correspondiente al hacer clic en un turno."""
    from src.ui.views.turnos_sin_cubrir_view import TurnosSinCubrirView

    svc: RecordsService = env_db["svc"]
    excel: ExcelService = env_db["excel"]
    svc.crear_tipo("Enfermo", dias=1, exportar_descubierto=1)
    svc.crear_turno_itinerario(
        {"codigo_turno": "620.1", "regimen": "S", "deposito_base": "PC", "toma_servicio": "10:00", "deje_servicio": "16:00", "itinerario_vigente": "Abril26"},
        [{"nro_vuelta": 1, "nro_tren": "3200", "origen": "PC", "hora_salida": "10:15", "destino": "TY", "hora_llegada": "10:45"}],
    )
    svc.crear_empleado({
        "legajo": 3001, "apellidos_nombres": "TITULAR 620",
        "especialidad": "Conductor Electrico", "dotacion": "PC", "turnos": "620.1", "franco": "Domingo",
    })
    with svc.db.write_transaction() as conn:
        conn.execute(
            """INSERT INTO novedades (id, registrado_en, legajo, apellidos_nombres, especialidad, dotacion, turnos, franco, novedad, fecha_inicio, fecha_fin, referencia_estacion, supervisor, observaciones, activo)
               VALUES (950, '04/04/2026 07:00', 3001, 'TITULAR 620', 'Conductor Electrico', 'PC', '620.1', 'Domingo', 'Enfermo', '04/04/2026', '04/04/2026', 'PC', 'ADMIN', '', 1);"""
        )

    vista = TurnosSinCubrirView(svc, excel)
    try:
        vista.date_picker.setDate(QDate(2026, 4, 4))  # Sábado
        vista.consultar()
        assert vista.proxy.rowCount() >= 1
        captured_reg = []
        with patch(
            "src.ui.views.dialogs.detalle_turno_dialog.DetalleTurnoDialog",
            side_effect=lambda *args, **kwargs: (captured_reg.append(kwargs.get("regimen_inicial")), type("Dummy", (), {"exec": lambda s: 0})())[1],
        ):
            vista._abrir_detalle_turno_index(vista.proxy.index(0, 6))
        assert captured_reg == ["S"]
    finally:
        vista.close()
        vista.deleteLater()
