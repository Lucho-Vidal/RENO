"""Pruebas integrales para catálogo de estaciones/siglas, importador multi-hoja y flujo de UI Turnos/Paradas."""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton

from src.data.database import DatabaseManager
from src.data.migrations import init_database_schema
from src.domain.estaciones import (
    CatalogoEstacionesIndex,
    ESTACIONES_BASE,
    coincide_filtro_estacion,
    estaciones_equivalentes,
    obtener_sigla_estacion,
    resolver_estacion,
    resolver_estaciones_por_sigla,
)
from src.services.excel_service import ExcelService
from src.services.records_service import RecordsService
from src.ui.views.buscador_turnos_view import BuscadorTurnosView
from src.ui.views.dialogs.admin_dialogs import AdminCatalogosDialog
from src.ui.views.dialogs.admin_tren_paradas_dialog import (
    AdminEstacionesDialog,
    AdminTrenParadasDialog,
)
from src.ui.views.dialogs.admin_turnos_dialog import AdminTurnosDialog
from src.ui.views.dialogs.ordenamiento_dialog import OrdenamientoDialog
from src.ui.views.dialogs.turno_dialog import VueltaItemDialog

REAL_ESTACIONES_XLSX = Path("/home/antigravity/estaciones.xlsx")
REAL_ITIN_H5_XLSX = Path(
    "/home/antigravity/Itinerario_H5_MAYO_2025_SUP._H5-01_-_ZZ-CÑ_+_Htal._Ezeiza.xlsx"
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def svc_env(tmp_path: Path):
    db = DatabaseManager(tmp_path / "test_paradas_est.sqlite", journal_mode="DELETE")
    init_database_schema(db)
    svc = RecordsService(db)
    excel = ExcelService(db)
    return {"db": db, "svc": svc, "excel": excel}


def test_equivalencias_estaciones_siglas_y_alias():
    """Verifica equivalencia bidireccional entre nombres completos, alias y siglas."""
    assert estaciones_equivalentes("TY", "Temperley")
    assert estaciones_equivalentes("TY", "Témperley")
    assert estaciones_equivalentes("PC", "Plaza Constitución")
    assert estaciones_equivalentes("PC", "P. Constitución")
    assert estaciones_equivalentes("PC", "Plaza C.")
    assert estaciones_equivalentes("P.C.", "Plaza Constitución")
    assert estaciones_equivalentes("ALL", "Avellaneda")
    assert estaciones_equivalentes("ALL", "Kosteki y Santillán")
    assert estaciones_equivalentes("Kosteki y Santillan", "Avellaneda")
    assert estaciones_equivalentes("HY", "H. Yrigoyen (Pda.)")
    assert estaciones_equivalentes("HY", "Yrigoyen")
    assert estaciones_equivalentes("LV", "Dr. R. Levene (Ap.)")
    assert estaciones_equivalentes("PT", "A. Petión (Ap.)")
    assert estaciones_equivalentes("HZZ", "Htal. Ezeiza (Ap.)")
    assert estaciones_equivalentes("CÑ", "Cañuelas")
    assert estaciones_equivalentes("Canuelas", "Cañuelas")

    assert obtener_sigla_estacion("Temperley") == "TY"
    assert obtener_sigla_estacion("Kosteki y Santillán") == "ALL"
    assert obtener_sigla_estacion("Htal. Ezeiza (Ap.)") == "HZZ"
    assert obtener_sigla_estacion("TY") == "TY"

    # Siglas compartidas / 1 carácter
    gl_cands = [s["nombre"] for s in resolver_estaciones_por_sigla("GL")]
    assert "Gerli" in gl_cands and "Gral.Alvear" in gl_cands
    br_cands = [s["nombre"] for s in resolver_estaciones_por_sigla("BR")]
    assert "Ing.Brian" in br_cands and "La Barrancosa" in br_cands
    assert resolver_estacion("D")["nombre"] == "Domselaar"
    assert resolver_estacion("E")["nombre"] == "Emp.Lobos"
    assert resolver_estacion("G")["nombre"] == "Guerrero"

    # Filtro tolerante a sigla, alias o subcadena (sin falsos positivos de siglas dentro de otras palabras)
    assert coincide_filtro_estacion("Kosteki y Santillán", "ALL")
    assert coincide_filtro_estacion("Temperley", "ty")
    assert coincide_filtro_estacion("Htal. Ezeiza (Ap.)", "HZZ")
    assert coincide_filtro_estacion("TY", "Temperley")
    assert coincide_filtro_estacion("C. Spegazzini (Pda.)", "Speg")
    assert not coincide_filtro_estacion("Lanús", "TY")
    assert not coincide_filtro_estacion("C. Spegazzini (Pda.)", "ZZ")
    assert not coincide_filtro_estacion("City Bell", "TY")
    assert not coincide_filtro_estacion("Dr. R. Levene (Ap.)", "D")


def test_catalogo_estaciones_crud_y_excel(svc_env, tmp_path: Path):
    """Verifica precarga base, CRUD, importación desde Excel y preservación de alias."""
    svc: RecordsService = svc_env["svc"]

    estaciones_init = svc.listar_estaciones()
    assert len(estaciones_init) >= 118

    por_sigla = svc.listar_estaciones(criterio="TY")
    assert any("mperley" in e["nombre"] and e["sigla"] == "TY" for e in por_sigla)

    # Crear estación personalizada
    new_id = svc.guardar_estacion({
        "codigo_origen": 999,
        "nombre": "Parada Prueba Sur",
        "sigla": "PPS",
        "aliases": "Pda. Prueba, P. Sur",
    })
    assert new_id > 0

    # Desactivar y filtrar
    svc.cambiar_estado_estacion(new_id)
    assert all(e["id"] != new_id for e in svc.listar_estaciones(incluir_inactivos=False))
    assert any(e["id"] == new_id and e["activo"] == 0 for e in svc.listar_estaciones(incluir_inactivos=True))

    # Importar Excel de catálogo (actualiza existente preservando alias previos e inserta nueva)
    cat_xlsx = tmp_path / "catalogo_estaciones.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Hoja1"
    ws.append(["Id", "Nombre", "Sigla"])
    ws.append([9, "Témperley", "TY"])
    ws.append([998, "Parada Nueva Oeste", "PNO"])
    wb.save(cat_xlsx)

    ins, upd = svc.importar_estaciones_excel(str(cat_xlsx))
    assert ins == 1
    assert upd >= 1

    idx = svc._obtener_indice_estaciones()
    assert idx.estaciones_equivalentes("Temperley", "TY")
    assert idx.resolver_estacion("PNO")["nombre"] == "Parada Nueva Oeste"

    # Subir catálogo de estaciones al importador de paradas debe dar error descriptivo
    with pytest.raises(ValueError, match="catálogo de estaciones"):
        svc.importar_paradas_tren_excel(str(cat_xlsx))


def test_importar_cuadro_horario_multihoja_y_dedup_vueltas(svc_env, tmp_path: Path):
    """Simula libro SOFSE multi-hoja (Hab/Sab/DOM, pies, medianoche) e integración con vueltas abreviadas."""
    svc: RecordsService = svc_env["svc"]

    xlsx_path = tmp_path / "Itinerario_H5_MAYO_TEST.xlsx"
    wb = openpyxl.Workbook()

    # Hoja 1: Hab ASC CÑ
    ws_hab = wb.active
    ws_hab.title = "Hab ASC CÑ"
    ws_hab.append(["Desde", "Tren Nº ", 4603, 4693])
    ws_hab.append(["PC", "Tipo", "P", "P"])
    ws_hab.append([0, " Plaza Constitución", datetime.time(6, 0, 30), datetime.time(7, 0, 0)])
    ws_hab.append([2.8, " H. Yrigoyen (Pda.)", "---", ". ."])
    ws_hab.append([3.8, " Kosteki y Santillán", datetime.time(6, 6, 0), datetime.time(7, 6, 0)])
    ws_hab.append([16.8, " Temperley", datetime.time(6, 25, 0), datetime.time(7, 25, 0)])
    ws_hab.append([31.5, " Ezeiza", datetime.time(6, 48, 0), datetime.time(7, 48, 0)])
    ws_hab.append([None, "Formación", 1, 2])
    ws_hab.append([None, "Locomotora", 5, 7])
    ws_hab.append([None, "Sigue con", 4604, 4694])
    ws_hab.append([None, "Sale", datetime.time(6, 55, 0), datetime.time(7, 55, 0)])

    # Hoja 2: Sab DES CÑ (incluye columna separadora combinada E1:E12 y tren 8044 cruzando medianoche)
    ws_sab = wb.create_sheet(title="Sab DES CÑ")
    ws_sab.append(["Hasta", "Tren", 8044, 4693, "TRENES DEL DIA ANTERIOR", 4604])
    ws_sab.append(["PC", "Tipo", "LS", "P", None, "P"])
    ws_sab.append([64, " Cañuelas", None, None, None, datetime.time(8, 0, 0)])
    ws_sab.append([16.8, " Temperley", datetime.time(23, 55, 0), datetime.time(9, 10, 0), None, datetime.time(8, 45, 0)])
    ws_sab.append([11.2, " R. de Escalada", datetime.time(0, 2, 0), datetime.time(9, 18, 0), None, "---"])
    ws_sab.append([0, " Plaza Constitución", None, datetime.time(9, 35, 0), None, datetime.time(9, 10, 0)])
    ws_sab.append([None, "Sale", datetime.time(0, 10, 0), datetime.time(9, 40, 0), None, None])
    ws_sab.merge_cells("E1:E8")

    # Hoja 3: DOM ASC CÑ
    ws_dom = wb.create_sheet(title="DOM ASC CÑ")
    ws_dom.append(["Desde", "Tren Nº ", 4693])
    ws_dom.append(["PC", "Tipo", "P"])
    ws_dom.append([0, " Plaza Constitución", datetime.time(10, 0, 0)])
    ws_dom.append([16.8, " Temperley", datetime.time(10, 28, 0)])
    ws_dom.append([None, "Sale", datetime.time(10, 30, 0)])

    wb.save(xlsx_path)

    assert svc.inferir_itinerario_desde_archivo(str(xlsx_path)) == "H5"
    insertados = svc.importar_paradas_tren_excel(str(xlsx_path), itinerario_default="H5")
    # Hab: 4603(4)+4693(4)=8; Sab: 8044(2)+4693(3)+4604(3)=8; DOM: 4693(2)=2 -> total 18
    assert insertados == 18

    # Idempotencia al reimportar
    assert svc.importar_paradas_tren_excel(str(xlsx_path), itinerario_default="H5") == 0

    # Nunca deben importarse filas de pie como Sale o Formación
    todas = svc.listar_paradas_tren(itinerario="H5", limite=500)
    est_names_lower = {p["estacion"].lower() for p in todas}
    assert "sale" not in est_names_lower
    assert "formación" not in est_names_lower
    assert "locomotora" not in est_names_lower
    assert "sigue con" not in est_names_lower

    # Verificar separación por régimen en tren 4693 (Hábil = H, alias legacy 'LV' compatible)
    p_hab = svc.obtener_estaciones_y_horarios_por_tren("4693", "H5", regimen="H")
    assert [p["hora"] for p in p_hab] == ["07:00", "07:06", "07:25", "07:48"]
    assert svc.obtener_estaciones_y_horarios_por_tren("4693", "H5", regimen="LV") == p_hab
    p_sab = svc.obtener_estaciones_y_horarios_por_tren("4693", "H5", regimen="S")
    assert [p["hora"] for p in p_sab] == ["09:10", "09:18", "09:35"]
    p_dom = svc.obtener_estaciones_y_horarios_por_tren("4693", "H5", regimen="D")
    assert [p["hora"] for p in p_dom] == ["10:00", "10:28"]

    # Verificar cruce de medianoche del tren 8044 en sábado
    p_8044 = svc.obtener_estaciones_y_horarios_por_tren("8044", "H5", regimen="S")
    assert len(p_8044) == 2
    assert p_8044[0] == {"estacion": "Temperley", "hora": "23:55"}
    assert p_8044[1] == {"estacion": "R. de Escalada", "hora": "00:02"}

    # Búsqueda por sigla en listar_paradas_tren
    filtradas_all = svc.listar_paradas_tren(estacion="ALL")
    assert len(filtradas_all) >= 2
    assert all(p["sigla"] == "ALL" for p in filtradas_all)

    # Deduplicación y fusión cronológica con vueltas de turno abreviadas
    svc.crear_turno_itinerario(
        payload={
            "codigo_turno": "501.1",
            "turno_base": "501",
            "subindice": 1,
            "deposito_base": "PC",
            "toma_servicio": "05:50",
            "deje_servicio": "11:50",
            "itinerario_vigente": "H5",
        },
        vueltas=[
            {
                "nro_vuelta": 1,
                "ref": "A",
                "nro_tren": "4603",
                "origen": "PC",
                "hora_salida": "06:00",
                "destino": "TY",
                "hora_llegada": "06:25",
            },
            {
                "nro_vuelta": 2,
                "ref": "B",
                "nro_tren": "8044",
                "origen": "CÑ",
                "hora_salida": "23:10",
                "destino": "PC",
                "hora_llegada": "00:15",
            },
        ],
    )
    # 4603 ya tiene Plaza Constitución y Temperley en tren_paradas -> no duplica PC ni TY
    p_4603 = svc.obtener_estaciones_y_horarios_por_tren("4603", "H5", regimen="H")
    assert len(p_4603) == 4
    assert [p["estacion"] for p in p_4603] == [
        "Plaza Constitución",
        "Kosteki y Santillán",
        "Temperley",
        "Ezeiza",
    ]
    # 8044 toma CÑ (23:10) antes de Temperley (23:55) y PC (00:15) después de R. de Escalada (00:02)
    p_8044_merged = svc.obtener_estaciones_y_horarios_por_tren("8044", "H5", regimen="S")
    assert [(p["estacion"], p["hora"]) for p in p_8044_merged] == [
        ("CÑ", "23:10"),
        ("Temperley", "23:55"),
        ("R. de Escalada", "00:02"),
        ("PC", "00:15"),
    ]


@pytest.mark.skipif(
    not (REAL_ESTACIONES_XLSX.exists() and REAL_ITIN_H5_XLSX.exists()),
    reason="Archivos adjuntos reales no presentes en el entorno.",
)
def test_importacion_archivos_reales_adjuntos(svc_env):
    """Verifica importación real completa sobre estaciones.xlsx e Itinerario_H5."""
    svc: RecordsService = svc_env["svc"]

    ins_est, upd_est = svc.importar_estaciones_excel(str(REAL_ESTACIONES_XLSX))
    assert ins_est + upd_est == 118

    assert svc.inferir_itinerario_desde_archivo(str(REAL_ITIN_H5_XLSX)) == "H5"
    ins_paradas = svc.importar_paradas_tren_excel(str(REAL_ITIN_H5_XLSX), itinerario_default="H5")
    assert ins_paradas == 1980
    assert len(svc.listar_paradas_tren(itinerario="H5", regimen="H", limite=5000)) == 772
    assert len(svc.listar_paradas_tren(itinerario="H5", regimen="S", limite=5000)) == 628
    assert len(svc.listar_paradas_tren(itinerario="H5", regimen="D", limite=5000)) == 580

    # Idempotente
    assert svc.importar_paradas_tren_excel(str(REAL_ITIN_H5_XLSX), itinerario_default="H5") == 0

    todas = svc.listar_paradas_tren(itinerario="H5", limite=10000)
    nombres_norm = {p["estacion"].strip().lower() for p in todas}
    for prohibido in ("sale", "formación", "locomotora", "sigue con", "trenes del dia anterior"):
        assert prohibido not in nombres_norm

    ty_rows = svc.listar_paradas_tren(estacion="TY", itinerario="H5", limite=5000)
    assert len(ty_rows) == 36
    assert all(r["sigla"] == "TY" for r in ty_rows)

    zz_rows = svc.listar_paradas_tren(estacion="ZZ", itinerario="H5", limite=5000)
    assert len(zz_rows) == 149
    assert all(r["sigla"] == "ZZ" for r in zz_rows)

    cn_rows = svc.listar_paradas_tren(estacion="CÑ", itinerario="H5", limite=5000)
    assert len(cn_rows) == 170
    assert all(r["sigla"] == "CÑ" for r in cn_rows)

    hzz_rows = svc.listar_paradas_tren(estacion="HZZ", itinerario="H5", limite=5000)
    assert len(hzz_rows) == 143
    assert all(r["sigla"] == "HZZ" for r in hzz_rows)


def test_ui_importar_turnos_reubicado_y_refresh(qapp, svc_env, tmp_path: Path):
    """Verifica ubicación de 'Importar Turnos' y actualización de BuscadorTurnosView."""
    svc: RecordsService = svc_env["svc"]
    excel: ExcelService = svc_env["excel"]

    view = BuscadorTurnosView(svc, excel)
    try:
        # En BuscadorTurnosView ya NO debe existir el botón importar itinerario
        assert not hasattr(view, "btn_importar")
        textos_botones = [b.text() for b in view.findChildren(QPushButton)]
        assert not any("Importar Itinerario" in t for t in textos_botones)
        assert any("Administrar Turnos" in t for t in textos_botones)
        assert any("Paradas por Tren" in t for t in textos_botones)

        # En AdminTurnosDialog sí debe existir "📥 Importar Turnos"
        dlg_turnos = AdminTurnosDialog(svc)
        try:
            assert hasattr(dlg_turnos.widget, "btn_importar_turnos")
            assert dlg_turnos.widget.btn_importar_turnos.text() == "📥 Importar Turnos"

            # Crear archivo Excel de turnos mínimo para probar importación desde AdminTurnosWidget
            itin_xlsx = tmp_path / "turnos_min.xlsx"
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Datos"
            ws.append([
                "Turno", "Toma", "Deja", "Vuelta", "Ref", "Tren",
                "Origen", "Salida", "Destino", "Llegada", "Obs", "Itin", "Hoja",
            ])
            ws.append([
                "701.1", "05:00", "11:00", 1, "A", "4603",
                "PC", "06:00", "TY", "06:25", "", "H5", "PC",
            ])
            wb.save(itin_xlsx)

            with (
                patch("src.ui.views.dialogs.admin_turnos_dialog.QFileDialog.getOpenFileName", return_value=(str(itin_xlsx), "")),
                patch("src.ui.views.dialogs.admin_turnos_dialog.QMessageBox.information"),
            ):
                dlg_turnos.widget._importar_turnos()

            assert dlg_turnos.widget.hubo_cambios is True
            assert len(dlg_turnos.widget._turnos) == 1
        finally:
            dlg_turnos.deleteLater()

        # También visible en Administración > Catálogos > Turnos de Itinerario
        dlg_cat = AdminCatalogosDialog(svc, excel)
        try:
            assert dlg_cat.widget.widget_turnos.btn_importar_turnos.text() == "📥 Importar Turnos"
        finally:
            dlg_cat.deleteLater()

        # Verificar que cerrar AdminTurnosDialog con X tras hubo_cambios=True igual refresca BuscadorTurnosView
        view.txt_criterio.setText("4603")

        def _simular_cambio_y_cerrar_x(dlg_inst):
            dlg_inst.widget.hubo_cambios = True
            QTimer.singleShot(0, lambda: QApplication.sendEvent(dlg_inst, QCloseEvent()))
            return QDialog.Rejected

        with patch("src.ui.views.dialogs.admin_turnos_dialog.AdminTurnosDialog.exec", new=_simular_cambio_y_cerrar_x):
            view._abrir_admin_turnos()

        assert view.model_turnos.rowCount() == 1
        assert view.model_vueltas.rowCount() == 1
        assert view.model_vueltas.get_row(0)["origen"] == "PC"
        assert view.model_vueltas.get_row(0)["destino"] == "TY"
    finally:
        view.close()
        view.deleteLater()


def test_ui_paradas_estaciones_y_ordenamiento_abreviaturas(qapp, svc_env):
    """Verifica filtros por sigla, régimen H, columna SIGLA, catálogo UI y resolución en OrdenamientoDialog/VueltaItemDialog."""
    svc: RecordsService = svc_env["svc"]

    svc.crear_parada_tren({
        "nro_tren": "4605",
        "itinerario": "H5",
        "regimen": "H",
        "orden_parada": 1,
        "estacion": "Plaza Constitución",
        "hora_paso": "06:10",
    })
    svc.crear_parada_tren({
        "nro_tren": "4605",
        "itinerario": "H5",
        "regimen": "H",
        "orden_parada": 2,
        "estacion": "Temperley",
        "hora_paso": "06:36",
    })
    svc.crear_parada_tren({
        "nro_tren": "4605",
        "itinerario": "H5",
        "regimen": "S",
        "orden_parada": 1,
        "estacion": "Temperley",
        "hora_paso": "07:15",
    })

    # 1. AdminTrenParadasDialog (sin LV en combos, muestra H - Hábil)
    dlg_paradas = AdminTrenParadasDialog(svc, tren_inicial="4605")
    try:
        assert dlg_paradas.tabla.columnCount() == 8
        assert dlg_paradas.tabla.horizontalHeaderItem(4).text() == "SIGLA"
        assert dlg_paradas.tabla.rowCount() == 3
        assert dlg_paradas.combo_filtro_regimen.findData("H") >= 0
        assert dlg_paradas.combo_filtro_regimen.findText("LV") == -1
        assert dlg_paradas.combo_regimen.findData("H") >= 0
        assert dlg_paradas.combo_regimen.findText("LV") == -1
        assert dlg_paradas.tabla.item(0, 7).text() == "H (Hábil)"

        # Filtro por sigla TY
        dlg_paradas.txt_filtro_estacion.setText("TY")
        assert dlg_paradas.tabla.rowCount() == 2
        assert dlg_paradas.tabla.item(0, 4).text() == "TY"

        # Filtro adicional por régimen S (vía data)
        idx_s = dlg_paradas.combo_filtro_regimen.findData("S")
        assert idx_s >= 0
        dlg_paradas.combo_filtro_regimen.setCurrentIndex(idx_s)
        assert dlg_paradas.tabla.rowCount() == 1
        assert dlg_paradas.tabla.item(0, 5).text() == "07:15"
        assert dlg_paradas.tabla.item(0, 7).text() == "S (Sábado)"

        # Vista previa de equivalencia en formulario
        dlg_paradas.txt_estacion.setText("ALL")
        assert "Avellaneda" in dlg_paradas.lbl_estacion_resuelta.text()
        assert dlg_paradas.txt_estacion.completer() is not None
        assert dlg_paradas.txt_filtro_estacion.completer() is not None
    finally:
        dlg_paradas.deleteLater()

    # 2. AdminEstacionesDialog
    dlg_est = AdminEstacionesDialog(svc)
    try:
        dlg_est.txt_buscar.setText("HZZ")
        assert dlg_est.tabla.rowCount() >= 1
        assert dlg_est.tabla.item(0, 3).text() == "HZZ"
    finally:
        dlg_est.deleteLater()

    # 3. OrdenamientoDialog usa H/S/D y resuelve abreviaturas a las horas reales del tren según régimen
    dlg_ord = OrdenamientoDialog(svc)
    try:
        assert dlg_ord.combo_regimen.findData("H") >= 0
        assert dlg_ord.combo_regimen.findText("LV") == -1
        dlg_ord.combo_itinerario.setCurrentText("H5")
        dlg_ord.combo_regimen.setCurrentIndex(dlg_ord.combo_regimen.findData("H"))
        dlg_ord.txt_tramo_tren.setText("4605")
        assert dlg_ord.txt_tramo_h_sal.text() == "06:10"
        assert dlg_ord.txt_tramo_h_lleg.text() == "06:36"

        # Escribir sigla TY en Origen o Destino resuelve automáticamente la hora de Temperley
        dlg_ord.combo_tramo_origen.setCurrentText("TY")
        assert dlg_ord.txt_tramo_h_sal.text() == "06:36"
        dlg_ord.combo_tramo_destino.setCurrentText("PC")
        assert dlg_ord.txt_tramo_h_lleg.text() == "06:10"

        # Cambiar régimen a S actualiza automáticamente los horarios del tren
        dlg_ord.combo_regimen.setCurrentIndex(dlg_ord.combo_regimen.findData("S"))
        assert dlg_ord.txt_tramo_h_sal.text() == "07:15"
    finally:
        dlg_ord.deleteLater()

    # 4. OrdenamientoDialog cargando registro existente con legacy 'LV' lo mapea a 'H'
    dlg_ord_leg = OrdenamientoDialog(
        svc,
        ordenamiento_existente={"id": 99, "regimen": "LV", "deposito": "PC", "tramos": []},
    )
    try:
        assert dlg_ord_leg.combo_regimen.currentData() == "H"
    finally:
        dlg_ord_leg.deleteLater()

    # 5. TurnoDialog y DetalleTurnoDialog normalizan alias legacy 'LV' a 'H'
    from src.ui.views.dialogs.turno_dialog import TurnoDialog
    from src.ui.views.dialogs.detalle_turno_dialog import DetalleTurnoDialog

    with svc.db.write_transaction() as conn:
        cur_t = conn.execute(
            """INSERT INTO turnos_itinerario
               (codigo_turno, regimen, turno_base, subindice, tipo_turno, deposito_base, itinerario_vigente)
               VALUES ('499.1', 'LV', '499', 1, 'DIAGRAMA_6D', 'PC', 'H5')"""
        )
        tid_leg = int(cur_t.lastrowid)

    dlg_t_leg = TurnoDialog(svc, turno_id=tid_leg)
    try:
        assert dlg_t_leg.combo_regimen.currentData() == "H"
    finally:
        dlg_t_leg.deleteLater()

    svc.eliminar_turno_definitivo(tid_leg)

    dlg_det_leg = DetalleTurnoDialog(svc, codigo_turno="499.1", itinerario_inicial="H5", regimen_inicial="LV")
    try:
        assert dlg_det_leg.combo_regimen.currentData() == "H"
    finally:
        dlg_det_leg.deleteLater()

    # 6. VueltaItemDialog 0-arg y con completer
    dlg_v = VueltaItemDialog()
    try:
        assert dlg_v.txt_origen.completer() is not None
        assert dlg_v.txt_destino.completer() is not None
        assert dlg_v.txt_origen.completer().filterMode() == Qt.MatchContains
    finally:
        dlg_v.deleteLater()


def test_admin_paradas_tren_eliminacion_multiple_filtro_y_todas(qapp, svc_env):
    """Verifica selección múltiple, 'Eliminar Seleccionadas', 'Eliminar por Filtro' y 'Eliminar Todas' en Paradas."""
    svc: RecordsService = svc_env["svc"]

    datos_paradas = [
        ("4601", "H5", "H", 1, "Plaza Constitución", "06:00"),
        ("4601", "H5", "H", 2, "Temperley", "06:25"),
        ("4601", "H5", "S", 1, "Plaza Constitución", "07:00"),
        ("4602", "H5", "H", 1, "Temperley", "08:00"),
        ("4602", "H5", "D", 1, "Ezeiza", "09:00"),
        ("1299", "Abril26", "H", 1, "Plaza Constitución", "10:00"),
        ("1299", "Abril26", "D", 1, "Cañuelas", "11:30"),
    ]
    for nt, iti, reg, ord_p, est, hr in datos_paradas:
        svc.crear_parada_tren({
            "nro_tren": nt,
            "itinerario": iti,
            "regimen": reg,
            "orden_parada": ord_p,
            "estacion": est,
            "hora_paso": hr,
        })

    dlg = AdminTrenParadasDialog(svc)
    try:
        assert dlg.tabla.selectionMode() == dlg.tabla.SelectionMode.ExtendedSelection
        assert dlg.tabla.rowCount() == 7

        # 1. Filtro H5 + régimen H y selección múltiple con 'Seleccionar Visibles'
        dlg.combo_filtro_itin.setCurrentText("H5")
        dlg.combo_filtro_regimen.setCurrentIndex(dlg.combo_filtro_regimen.findData("H"))
        assert dlg.tabla.rowCount() == 3  # 4601 PC, 4601 TY, 4602 TY

        dlg._seleccionar_visibles()
        assert len(dlg._obtener_filas_seleccionadas()) == 3
        assert "3 seleccionada(s)" in dlg.lbl_contador.text()
        assert dlg._parada_editando_id is None
        assert dlg.btn_eliminar.isEnabled()

        # Cancelar borrado múltiple no modifica DB
        with patch("src.ui.views.dialogs.admin_tren_paradas_dialog.QMessageBox.warning", return_value=QMessageBox.No):
            dlg._eliminar_paradas_seleccionadas()
        assert dlg.records_service.contar_paradas_tren_por_filtros() == 7

        # Seleccionar solo las 2 primeras filas y eliminarlas en lote
        dlg.tabla.clearSelection()
        dlg.tabla.selectRow(0)
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        sel_range = QItemSelection(dlg.tabla.model().index(0, 0), dlg.tabla.model().index(1, 7))
        dlg.tabla.selectionModel().select(sel_range, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        assert len(dlg._obtener_filas_seleccionadas()) == 2

        with (
            patch("src.ui.views.dialogs.admin_tren_paradas_dialog.QMessageBox.warning", return_value=QMessageBox.Yes),
            patch("src.ui.views.dialogs.admin_tren_paradas_dialog.QMessageBox.information") as mock_info,
        ):
            dlg._eliminar_paradas_seleccionadas()
            mock_info.assert_called_once()

        assert svc.contar_paradas_tren_por_filtros() == 5
        assert dlg.tabla.rowCount() == 1  # Solo queda 4602 TY (H5 / H)

        # 2. 'Eliminar por Filtro' sin filtros activos advierte y NO borra nada
        dlg._limpiar_filtros()
        assert dlg.tabla.rowCount() == 5
        with patch("src.ui.views.dialogs.admin_tren_paradas_dialog.QMessageBox.information") as mock_info:
            dlg._eliminar_por_filtros()
            mock_info.assert_called_once()
        assert svc.contar_paradas_tren_por_filtros() == 5

        # 3. 'Eliminar por Filtro' con itinerario H5 (borra 4601 S, 4602 H, 4602 D -> deja solo Abril26)
        dlg.combo_filtro_itin.setCurrentText("H5")
        assert dlg.tabla.rowCount() == 3
        with patch("src.ui.views.dialogs.admin_tren_paradas_dialog.QMessageBox.warning", return_value=QMessageBox.No):
            dlg._eliminar_por_filtros()
        assert svc.contar_paradas_tren_por_filtros() == 5

        with (
            patch("src.ui.views.dialogs.admin_tren_paradas_dialog.QMessageBox.warning", return_value=QMessageBox.Yes),
            patch("src.ui.views.dialogs.admin_tren_paradas_dialog.QMessageBox.information") as mock_info,
        ):
            dlg._eliminar_por_filtros()
            mock_info.assert_called_once()

        assert svc.contar_paradas_tren_por_filtros() == 2
        assert svc.contar_paradas_tren_por_filtros(itinerario="H5") == 0
        assert svc.contar_paradas_tren_por_filtros(itinerario="Abril26") == 2

        # 4. 'Eliminar por Filtro' por sigla de estación ('CÑ') borra solo Cañuelas de Abril26
        dlg._limpiar_filtros()
        dlg.txt_filtro_estacion.setText("CÑ")
        assert dlg.tabla.rowCount() == 1
        with (
            patch("src.ui.views.dialogs.admin_tren_paradas_dialog.QMessageBox.warning", return_value=QMessageBox.Yes),
            patch("src.ui.views.dialogs.admin_tren_paradas_dialog.QMessageBox.information"),
        ):
            dlg._eliminar_por_filtros()
        assert svc.contar_paradas_tren_por_filtros() == 1

        # 5. 'Eliminar Todas' (cancelar vs confirmar) vacía toda la tabla y encola DELETE en sync_outbox
        svc.crear_parada_tren({
            "nro_tren": "9991", "itinerario": "Mayo26", "regimen": "S",
            "orden_parada": 1, "estacion": "TY", "hora_paso": "12:00",
        })
        dlg._limpiar_filtros()
        assert dlg.tabla.rowCount() == 2

        with patch("src.ui.views.dialogs.admin_tren_paradas_dialog.QMessageBox.warning", return_value=QMessageBox.No):
            dlg._eliminar_todas_paradas()
        assert svc.contar_paradas_tren_por_filtros() == 2

        with (
            patch("src.ui.views.dialogs.admin_tren_paradas_dialog.QMessageBox.warning", return_value=QMessageBox.Yes),
            patch("src.ui.views.dialogs.admin_tren_paradas_dialog.QMessageBox.information") as mock_info,
        ):
            dlg._eliminar_todas_paradas()
            mock_info.assert_called_once()

        assert svc.contar_paradas_tren_por_filtros() == 0
        assert dlg.tabla.rowCount() == 0
        assert not dlg.btn_sel_todas.isEnabled()

        with svc.db.read_connection() as conn:
            del_outbox = conn.execute(
                "SELECT COUNT(*) FROM sync_outbox WHERE tabla = 'tren_paradas' AND operacion = 'DELETE'"
            ).fetchone()[0]
            assert del_outbox == 8  # 7 originales + 1 extra
    finally:
        dlg.deleteLater()


def test_migracion_legacy_lv_a_h_en_base_existente(tmp_path: Path):
    """Verifica que init_database_schema migre automáticamente registros legacy 'LV' a 'H' en paradas, ordenamientos y turnos."""
    import sqlite3

    db_file = tmp_path / "legacy_lv.sqlite"
    raw = sqlite3.connect(db_file)
    raw.executescript(
        """
        CREATE TABLE tren_paradas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nro_tren TEXT NOT NULL,
            itinerario TEXT NOT NULL DEFAULT '',
            regimen TEXT NOT NULL DEFAULT 'LV',
            orden_parada INTEGER NOT NULL DEFAULT 1,
            estacion TEXT NOT NULL,
            hora_paso TEXT NOT NULL,
            sync_status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE ordenamientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nro_orden TEXT UNIQUE NOT NULL,
            fecha_emision TEXT NOT NULL,
            hora_emision TEXT NOT NULL,
            legajo_personal INTEGER NOT NULL,
            nombre_personal TEXT NOT NULL,
            deposito TEXT NOT NULL DEFAULT 'PC',
            itinerario TEXT NOT NULL DEFAULT '',
            ciclo_o_turno TEXT NOT NULL DEFAULT '',
            regimen TEXT NOT NULL DEFAULT 'LV',
            motivo_orden TEXT NOT NULL DEFAULT '',
            observacion_pie TEXT NOT NULL DEFAULT '',
            ref_pct TEXT NOT NULL DEFAULT '',
            emisor TEXT NOT NULL DEFAULT '',
            usuario_id INTEGER,
            usuario_windows TEXT NOT NULL DEFAULT '',
            estado TEXT NOT NULL DEFAULT 'activo',
            activo INTEGER NOT NULL DEFAULT 1,
            deleted_at TEXT,
            sync_status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT,
            updated_at TEXT
        );
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
        INSERT INTO tren_paradas (nro_tren, itinerario, regimen, orden_parada, estacion, hora_paso)
        VALUES ('4603', 'H5', 'LV', 1, 'PC', '06:00'),
               ('4603', 'H5', 'S', 1, 'PC', '07:00');
        INSERT INTO ordenamientos (nro_orden, fecha_emision, hora_emision, legajo_personal, nombre_personal, regimen)
        VALUES ('PC 0000-0001', '01/05/2026', '08:00', 1001, 'Test Agent', 'LV');
        INSERT INTO turnos_itinerario (codigo_turno, regimen, turno_base, subindice, tipo_turno, deposito_base, itinerario_vigente)
        VALUES ('401.1', 'LV', '401', 1, 'DIAGRAMA_6D', 'PC', 'H5');
        """
    )
    raw.commit()
    raw.close()

    db = DatabaseManager(db_file, journal_mode="DELETE")
    init_database_schema(db)
    svc = RecordsService(db)

    paradas = svc.listar_paradas_tren(nro_tren="4603")
    assert [p["regimen"] for p in paradas] == ["H", "S"]
    assert svc.obtener_ordenamiento_completo(1)["regimen"] == "H"
    assert svc.listar_ordenamientos()[0]["regimen"] == "H"
    assert svc.obtener_turno_por_codigo("401.1", itinerario="H5")["regimen"] == "H"
