"""Pruebas para modal no bloqueante de CalendarioCicloDialog, retiro de personal de ciclo con novedad activa y cierre de duplicadas."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from scripts.cerrar_novedades_duplicadas import analizar_y_cerrar_novedades_duplicadas
from src.data.database import DatabaseManager
from src.data.migrations import init_database_schema
from src.services.excel_service import ExcelService
from src.services.pdf_service import PDFService
from src.services.records_service import RecordsService
from src.ui.views.personal_ciclo_view import PersonalCicloView


def test_personal_ciclo_retira_con_novedad_activa_y_calendario_no_modal(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    db = DatabaseManager(tmp_path / "test_ciclo_nov.sqlite", journal_mode="DELETE")
    init_database_schema(db)

    svc = RecordsService(db)
    excel_svc = ExcelService(db)
    pdf_svc = PDFService()

    with db.write_transaction() as conn:
        conn.execute(
            """INSERT INTO empleados (legajo, apellidos_nombres, especialidad, dotacion, turnos, franco, activo, actualizado_en)
               VALUES (12479, 'ALEGRE Maximiliano Nicolas', 'Conductor Electrico', 'PC', 'CICLO 11', 'Domingo', 1, '2026-11-01 00:00:00'),
                      (12480, 'GOMEZ Carlos', 'Conductor Electrico', 'PC', 'CICLO 12', 'Lunes', 1, '2026-11-01 00:00:00');"""
        )

    # Inicialmente ambos están libres en 10/11/2026
    est_ini = svc.obtener_estado_personal_ciclo("10/11/2026", dotacion="PC")
    assert {e["legajo"] for e in est_ini["libres"]} == {12479, 12480}

    # Cargar novedad activa abierta para 12479 desde 05/11/2026
    nov_id = svc.crear_novedad(
        {
            "legajo": 12479,
            "apellidos_nombres": "ALEGRE Maximiliano Nicolas",
            "especialidad": "Conductor Electrico",
            "dotacion": "PC",
            "turnos": "CICLO 11",
            "franco": "Domingo",
            "novedad": "Enfermo",
            "fecha_inicio": "05/11/2026",
            "fecha_fin": "-",
            "referencia_estacion": "PC",
            "supervisor": "ADMIN",
            "observaciones": "",
        }
    )
    assert nov_id > 0

    # Debe retirarse a 12479 de 'libres' y 'ocupados' mientras la novedad esté activa
    est_con_nov = svc.obtener_estado_personal_ciclo("10/11/2026", dotacion="PC")
    assert [e["legajo"] for e in est_con_nov["libres"]] == [12480]
    assert est_con_nov["ocupados"] == []
    assert [e["legajo"] for e in est_con_nov["con_novedad"]] == [12479]

    # Antes de la fecha de inicio (03/11/2026) sigue disponible
    est_antes = svc.obtener_estado_personal_ciclo("03/11/2026", dotacion="PC")
    assert {e["legajo"] for e in est_antes["libres"]} == {12479, 12480}

    # Cerrar novedad al 08/11/2026 -> en 10/11/2026 vuelve a estar libre
    svc.actualizar_fecha_fin_novedad(nov_id, "08/11/2026")
    est_post = svc.obtener_estado_personal_ciclo("10/11/2026", dotacion="PC")
    assert {e["legajo"] for e in est_post["libres"]} == {12479, 12480}

    # Verificar que PersonalCicloView abre CalendarioCicloDialog como NO MODAL (permite seguir usando la ventana principal)
    view = PersonalCicloView(svc, pdf_svc, excel_svc)
    try:
        emp_12480 = next(e for e in est_post["libres"] if e["legajo"] == 12480)
        view._abrir_calendario(emp_12480)
        assert len(view._calendarios_abiertos) == 1
        dlg = view._calendarios_abiertos[0]
        assert dlg.isModal() is False
        assert dlg.windowModality() == Qt.NonModal
        assert dlg.isVisible() is True

        # Abrir el mismo legajo reutiliza la ventana abierta sin duplicar
        view._abrir_calendario(emp_12480)
        assert len(view._calendarios_abiertos) == 1

        dlg.close()
        app.processEvents()
        assert len(view._calendarios_abiertos) == 0
    finally:
        view.close()


def test_script_cerrar_novedades_duplicadas(tmp_path: Path):
    db = DatabaseManager(tmp_path / "test_dups.sqlite", journal_mode="DELETE")
    init_database_schema(db)

    with db.write_transaction() as conn:
        # Maxi (12479) tiene 3 novedades 'Enfermo' abiertas sin fecha fin en distintas fechas
        conn.execute(
            """INSERT INTO novedades
               (id, registrado_en, legajo, apellidos_nombres, especialidad, dotacion, turnos, franco,
                novedad, fecha_inicio, fecha_fin, referencia_estacion, supervisor, observaciones, activo)
               VALUES
               (10, '01/10/2026 08:00', 12479, 'ALEGRE Maximiliano', 'Conductor Electrico', 'PC', 'CICLO 11', 'Domingo',
                'Enfermo', '01/10/2026', '', 'PC', 'SUP', '', 1),
               (25, '15/10/2026 08:00', 12479, 'ALEGRE Maximiliano', 'Conductor Electrico', 'PC', 'CICLO 11', 'Domingo',
                'Enfermo', '15/10/2026', '-', 'PC', 'SUP', 'Repite parte', 1),
               (42, '05/11/2026 08:00', 12479, 'ALEGRE Maximiliano', 'Conductor Electrico', 'PC', 'CICLO 11', 'Domingo',
                'Enfermo', '05/11/2026', NULL, 'PC', 'SUP', '', 1),
               -- Mismo legajo pero distinto tipo ('Licencia') única abierta: no debe cerrarse
               (43, '06/11/2026 08:00', 12479, 'ALEGRE Maximiliano', 'Conductor Electrico', 'PC', 'CICLO 11', 'Domingo',
                'Licencia', '06/11/2026', '', 'PC', 'SUP', '', 1),
               -- Otro legajo con única novedad abierta: no debe cerrarse
               (50, '01/11/2026 08:00', 12480, 'GOMEZ Carlos', 'Conductor Electrico', 'PC', 'CICLO 12', 'Lunes',
                'Enfermo', '01/11/2026', '', 'PC', 'SUP', '', 1);"""
        )

    # Dry-run no modifica nada
    res_dry = analizar_y_cerrar_novedades_duplicadas(db, aplicar=False)
    assert res_dry["total_abiertas"] == 5
    assert res_dry["grupos_duplicados"] == 1
    assert res_dry["a_cerrar"] == 2
    assert res_dry["cerradas"] == 0

    # Aplicar cambios
    res_app = analizar_y_cerrar_novedades_duplicadas(db, aplicar=True)
    assert res_app["cerradas"] == 2

    with db.read_connection() as conn:
        n10 = dict(conn.execute("SELECT * FROM novedades WHERE id = 10").fetchone())
        n25 = dict(conn.execute("SELECT * FROM novedades WHERE id = 25").fetchone())
        n42 = dict(conn.execute("SELECT * FROM novedades WHERE id = 42").fetchone())
        n43 = dict(conn.execute("SELECT * FROM novedades WHERE id = 43").fetchone())
        n50 = dict(conn.execute("SELECT * FROM novedades WHERE id = 50").fetchone())
        outbox_cnt = conn.execute(
            "SELECT COUNT(*) FROM sync_outbox WHERE tabla = 'novedades' AND operacion = 'UPDATE'"
        ).fetchone()[0]

    assert n10["fecha_fin"] == "01/10/2026"
    assert "#42" in n10["observaciones"]
    assert "No se cuenta con fecha fin" in n10["observaciones"]

    assert n25["fecha_fin"] == "15/10/2026"
    assert n25["observaciones"].startswith("Repite parte | ")
    assert "#42" in n25["observaciones"]

    # La más reciente (#42), la de otro tipo (#43) y la de otro legajo (#50) siguen abiertas
    assert n42["fecha_fin"] is None
    assert n43["fecha_fin"] == ""
    assert n50["fecha_fin"] == ""
    assert outbox_cnt == 2

    # Idempotente: segunda ejecución ya no encuentra duplicados abiertos
    res_re = analizar_y_cerrar_novedades_duplicadas(db, aplicar=False)
    assert res_re["a_cerrar"] == 0
