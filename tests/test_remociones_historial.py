"""Pruebas para Remociones semestrales e Historial de Turnos por Empleado."""

from pathlib import Path
import openpyxl

from src.data.database import DatabaseManager
from src.data.migrations import init_database_schema
from src.services.excel_service import ExcelService
from src.services.records_service import RecordsService


def test_historial_automatico_al_actualizar_empleado(tmp_path: Path):
    db = DatabaseManager(tmp_path / "rem_auto.sqlite")
    init_database_schema(db)
    svc = RecordsService(db)

    with db.write_transaction() as conn:
        cur = conn.execute(
            """INSERT INTO empleados
               (legajo, apellidos_nombres, especialidad, dotacion, turnos, franco, franco_horario, jornada_horas, activo, actualizado_en)
               VALUES (2001, 'Lopez Carlos', 'Conductor Electrico', 'TY', '401.1', 'Domingo', '', 6, 1, '2026-01-01 00:00:00')"""
        )
        emp_id = cur.lastrowid

    # Cambio de turno y dotación -> debe registrar estado anterior y el nuevo vigente
    svc.actualizar_empleado(
        emp_id,
        {
            "legajo": 2001,
            "apellidos_nombres": "Lopez Carlos",
            "especialidad": "Conductor Electrico",
            "dotacion": "PC",
            "turnos": "415.2",
            "franco": "Lunes",
            "franco_horario": "",
            "jornada_horas": 6,
        },
    )

    hist = svc.listar_historial_turnos_empleado(2001)
    assert len(hist) == 2
    assert hist[0]["turnos"] == "415.2"
    assert hist[0]["dotacion"] == "PC"
    assert hist[0]["es_vigente"] is True
    assert hist[1]["turnos"] == "401.1"
    assert hist[1]["dotacion"] == "TY"
    assert hist[1]["es_vigente"] is False


def test_remocion_excel_snapshot_manual_y_export(tmp_path: Path):
    db = DatabaseManager(tmp_path / "rem_excel.sqlite")
    init_database_schema(db)
    svc = RecordsService(db)
    excel = ExcelService(db)

    with db.write_transaction() as conn:
        conn.execute(
            """INSERT INTO empleados
               (legajo, apellidos_nombres, especialidad, dotacion, turnos, franco, franco_horario, jornada_horas, activo, actualizado_en)
               VALUES
               (3001, 'Martinez Ana', 'Conductor Electrico', 'TY', '401.1', 'Domingo', '', 6, 1, '2026-01-01 00:00:00'),
               (3002, 'Suarez Pedro', 'Guardatren Electrico', 'TY', '105', 'Martes', '', 7, 1, '2026-01-01 00:00:00')"""
        )

    # 1. Snapshot inicial
    snap = svc.registrar_snapshot_remocion("Remoción 1° Semestre 2026", "01/03/2026", "Foto inicial")
    assert snap["registrados"] == 2

    # 2. Excel de nueva remoción: 3001 cambia turno y dotación (TY -> PC, 401.1 -> 420.1), 3002 sin cambios, 3003 alta nueva
    xlsx_rem = tmp_path / "BASE_remocion_2026_2.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BASE"
    ws.append(["LEGAJO", "APELLIDOS Y NOMBRES", "ESPECIALIDAD", "DOTACION", "TURNOS", "FRANCO", "HORARIO FRANCO", "JORNADA HS"])
    ws.append([3001, "Martinez Ana", "Conductor Electrico", "PC", "420.1", "Lunes", "", 6])
    ws.append([3002, "Suarez Pedro", "Guardatren Electrico", "TY", "105", "Martes", "", 7])
    ws.append([3003, "Rojas Diego", "Conductor Diesel", "CÑ", "CICLO", "Miércoles", "06:00", 6])
    wb.save(xlsx_rem)

    prev = svc.previsualizar_remocion_desde_excel(str(xlsx_rem))
    assert prev["total_excel"] == 3
    assert len(prev["cambios"]) == 1 and prev["cambios"][0]["legajo"] == 3001
    assert prev["cambios"][0]["cambio_turno"] is True and prev["cambios"][0]["cambio_dotacion"] is True
    assert len(prev["altas"]) == 1 and prev["altas"][0]["legajo"] == 3003
    assert prev["sin_cambios"] == 1

    res = svc.aplicar_remocion_desde_excel(
        str(xlsx_rem),
        nombre="Remoción 2° Semestre 2026",
        fecha_vigencia="01/09/2026",
        observaciones="Remoción semestral",
    )
    assert res["cambios"] == 1
    assert res["altas"] == 1

    # Verificar historial de 3001
    hist_3001 = svc.listar_historial_turnos_empleado(3001)
    assert len(hist_3001) == 2
    assert hist_3001[0]["turnos"] == "420.1" and hist_3001[0]["dotacion"] == "PC"
    assert hist_3001[0]["remocion_nombre"] == "Remoción 2° Semestre 2026"
    assert hist_3001[1]["turnos"] == "401.1" and hist_3001[1]["fecha_hasta"] == "2026-08-31"

    # 3. Carga retroactiva manual + eliminación
    hid = svc.agregar_historial_turno_manual(
        3001,
        {
            "remocion_nombre": "Remoción 2025",
            "fecha_desde": "01/09/2025",
            "fecha_hasta": "28/02/2026",
            "turnos": "399.1",
            "dotacion": "TY",
            "franco": "Sábado",
            "especialidad": "Conductor Electrico",
        },
    )
    assert len(svc.listar_historial_turnos_empleado(3001)) == 3
    svc.eliminar_historial_turno(hid)
    assert len(svc.listar_historial_turnos_empleado(3001)) == 2

    # 4. Listado de remociones y exportación a Excel
    rems = svc.listar_remociones()
    assert len(rems) == 2
    det = svc.listar_cambios_remocion(res["remocion_id"])
    assert len(det) == 2
    out_xlsx = excel.export_historial_turnos(str(tmp_path / "hist_3001.xlsx"), hist_3001, "Historial 3001")
    assert Path(out_xlsx).exists() and Path(out_xlsx).stat().st_size > 1000
