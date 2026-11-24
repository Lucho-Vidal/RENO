"""Suite de pruebas de seguridad, RBAC, sanitización y estabilidad (Auditoría RENO v2)."""

import json
import sqlite3
from pathlib import Path

import openpyxl
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from src.core.config import guardar_configuracion_app
from src.data.database import DatabaseManager
from src.data.migrations import init_database_schema
from src.data.sync.supabase_sync import SupabaseSyncEngine
from src.domain.enums import ESTRUCTURA_PERMISOS_PESTANAS, PERMISOS_BASE, PERMISOS_GRUPOS
from src.services.auth_service import AuthService, validar_politica_password
from src.services.backup_service import BackupService
from src.services.excel_service import ExcelService, _sanitizar_celda_excel
from src.services.mail_service import _sanitizar_cabecera, _sanitizar_destinatarios
from src.services.pdf_service import PDFService
from src.services.records_service import RecordsService
from src.services.updater_service import (
    DescargarActualizacionWorker,
    InfoActualizacion,
    _expandir_candidatos_url_custom,
)
from src.services.word_service import WordService


def _get_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _crear_entorno(tmp_path: Path):
    db = DatabaseManager(tmp_path / "sec_test.sqlite", journal_mode="WAL")
    init_database_schema(db)
    auth = AuthService(db)
    auth.inicializar_permisos(PERMISOS_BASE)
    records = RecordsService(db)
    excel = ExcelService(db)
    pdf = PDFService()
    word = WordService()
    backup = BackupService(db)
    return db, auth, records, excel, pdf, word, backup


def test_politica_passwords_y_timing_safe(tmp_path: Path):
    _, auth, *_ = _crear_entorno(tmp_path)

    with pytest.raises(ValueError):
        validar_politica_password("")
    with pytest.raises(ValueError):
        validar_politica_password("1234567")
    with pytest.raises(ValueError):
        validar_politica_password("a" * 73)

    validar_politica_password("ClaveSegura2026!")

    with pytest.raises(ValueError):
        auth.crear_usuario("corto", "1234", nombre="Corto", legajo=100)

    uid = auth.crear_usuario("valido", "ClaveSegura2026!", nombre="Valido", legajo=101, roles=("Operador",))
    assert uid > 0
    assert auth.autenticar("inexistente", "ClaveSegura2026!") is None
    assert auth.autenticar("valido", "ClaveSegura2026!") is not None


def test_permisos_conocimiento_via_en_grupos_ui():
    grupos = dict(PERMISOS_GRUPOS)
    assert "conocimiento_via" in grupos
    todos_en_pestanas = {
        perm
        for bloque in ESTRUCTURA_PERMISOS_PESTANAS
        for sub in bloque["subgrupos"]
        for perm in sub["permisos"]
    }
    assert "conocimiento_via.ver" in todos_en_pestanas
    assert "conocimiento_via.editar" in todos_en_pestanas
    assert "conocimiento_via.administrar" in todos_en_pestanas


def test_updater_rechaza_http_sin_sha256_y_path_traversal():
    _get_app()
    # 1. URL HTTP rechazada
    assert _expandir_candidatos_url_custom("http://insecure.example.com/version.json", "updates") == []

    # 2. Path traversal en instalador_nombre rechazado
    errores_pt: list[str] = []
    info_pt = InfoActualizacion(
        version="2.1.0",
        fecha="24/11/2026",
        titulo="Test PT",
        instalador_nombre="../../Startup/evil.exe",
        instalador_url="https://example.com/evil.exe",
        sha256="a" * 64,
    )
    w_pt = DescargarActualizacionWorker(info_pt)
    w_pt.error_descarga.connect(errores_pt.append)
    w_pt.run()
    assert errores_pt and "inválido" in errores_pt[0].lower()

    # 3. Manifiesto sin SHA-256 válido rechazado
    errores_sha: list[str] = []
    info_sha = InfoActualizacion(
        version="2.1.0",
        fecha="24/11/2026",
        titulo="Test SHA",
        instalador_nombre="RENO_Setup_v2.1.0.exe",
        instalador_url="https://example.com/RENO_Setup_v2.1.0.exe",
        sha256="",
    )
    w_sha = DescargarActualizacionWorker(info_sha)
    w_sha.error_descarga.connect(errores_sha.append)
    w_sha.run()
    assert errores_sha and "sha-256" in errores_sha[0].lower()

    # 4. URL de descarga HTTP rechazada
    errores_http: list[str] = []
    info_http = InfoActualizacion(
        version="2.1.0",
        fecha="24/11/2026",
        titulo="Test HTTP",
        instalador_nombre="RENO_Setup_v2.1.0.exe",
        instalador_url="http://example.com/RENO_Setup_v2.1.0.exe",
        sha256="a" * 64,
    )
    w_http = DescargarActualizacionWorker(info_http)
    w_http.error_descarga.connect(errores_http.append)
    w_http.run()
    assert errores_http and "https" in errores_http[0].lower()


def test_backup_service_bloquea_path_traversal_y_esquema_invalido(tmp_path: Path):
    _, _, _, _, _, _, backup = _crear_entorno(tmp_path)

    with pytest.raises(ValueError):
        backup.eliminar_backup("../escape.sqlite")
    with pytest.raises(ValueError):
        backup.restaurar_backup("/etc/passwd")
    with pytest.raises(ValueError):
        backup.restaurar_backup("archivo.txt")

    # SQLite válido pero sin tablas núcleo de RENO debe ser rechazado
    bdir = Path(backup.ruta_directorio_backups())
    falso = bdir / "backup_falso.sqlite"
    con = sqlite3.connect(falso)
    con.execute("CREATE TABLE ajena (id INTEGER PRIMARY KEY);")
    con.commit()
    con.close()

    with pytest.raises(ValueError, match="RENO"):
        backup.restaurar_backup("backup_falso.sqlite")


def test_excel_formula_injection_y_pdf_xml_escape(tmp_path: Path):
    db, _, _, excel, pdf, _, _ = _crear_entorno(tmp_path)

    assert _sanitizar_celda_excel("=CMD|' /C calc'!A0") == "'=CMD|' /C calc'!A0"
    assert _sanitizar_celda_excel("+1+1") == "'+1+1"
    assert _sanitizar_celda_excel("-2+3") == "'-2+3"
    assert _sanitizar_celda_excel("@SUM(A1)") == "'@SUM(A1)"
    assert _sanitizar_celda_excel("Texto normal") == "Texto normal"
    assert _sanitizar_celda_excel(12345) == 12345

    # Exportar auditoría con fórmula peligrosa y verificar apóstrofe en celda
    xlsx_out = tmp_path / "auditoria_safe.xlsx"
    excel.export_auditoria(
        xlsx_out,
        [{"id": 1, "creado_en": "2026-11-24", "usuario": "op", "accion": "crear", "entidad": "nov", "entidad_id": 1, "datos_anteriores": "", "datos_nuevos": "=1+1"}],
    )
    wb = openpyxl.load_workbook(xlsx_out, data_only=False)
    ws = wb.active
    assert ws["H4"].value == "'=1+1"
    wb.close()

    # PDF ReportLab con caracteres XML peligrosos (<, >, &) no debe fallar
    pdf_out = tmp_path / "constancia_escape.pdf"
    pdf.generar_constancia_novedad(
        {
            "id": 99,
            "registrado_en": "2026-11-24 10:00",
            "legajo": 1234,
            "apellidos_nombres": "Pérez & <García>",
            "especialidad": "Conductor",
            "dotacion": "PC",
            "turnos": "101",
            "franco": "Domingo",
            "novedad": "Licencia <Especial> & Otros",
            "fecha_inicio": "24/11/2026",
            "fecha_fin": "25/11/2026",
            "referencia_estacion": "PC & LLV",
            "supervisor": "Sup <A>",
            "observaciones": "Línea 1 & <tag>invalido\nLínea 2 > ok",
        },
        [],
        str(pdf_out),
    )
    assert pdf_out.exists() and pdf_out.stat().st_size > 500


def test_mail_header_y_destinatarios_sanitizacion():
    assert _sanitizar_cabecera("Asunto\r\nBcc: evil@attacker.com") == "Asunto Bcc: evil@attacker.com"
    limpios = _sanitizar_destinatarios(["ok@trenes.gob.ar", "malo\r\n@evil.com", "no-es-mail"])
    assert limpios == ["ok@trenes.gob.ar"]


def test_config_no_persiste_service_role_key(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "reno_config.json"
    monkeypatch.setattr("src.core.config.CONFIG_JSON_PATH", cfg_path)
    ok = guardar_configuracion_app({
        "supabase_url": "https://demo.supabase.co",
        "supabase_anon_key": "sb_publishable_demo",
        "supabase_service_role_key": "sb_secret_MUST_NOT_BE_SAVED",
    })
    assert ok is True
    contenido = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "supabase_service_role_key" not in contenido
    assert contenido["supabase_url"] == "https://demo.supabase.co"


def test_rbac_ui_solo_lectura_y_cambio_usuario_conocimiento_via(tmp_path: Path, monkeypatch):
    _get_app()
    db, auth, records, excel, pdf, word, backup = _crear_entorno(tmp_path)

    # Crear admin primero con todos los permisos (incluyendo conocimiento_via.ver)
    uid_adm = auth.crear_administrador_inicial("admin", "AdminClave2026!", legajo=2000)
    user_adm = auth.obtener_usuario(uid_adm)

    # Crear rol solo lectura con novedades.ver únicamente
    rol_id = auth.crear_rol("SoloLectura")
    auth.establecer_permisos_rol(rol_id, ["novedades.ver", "cambios_turno.ver", "ordenamientos.ver"])
    uid_ro = auth.crear_usuario("lectura", "ClaveSegura2026!", nombre="Lectura", legajo=2001, roles=("SoloLectura",))
    user_ro = auth.obtener_usuario(uid_ro)

    from src.ui.main_window import MainWindow
    from src.ui.views.empleados_view import EmpleadosView

    # 1. EmpleadosView fail-closed por defecto
    emp_view_ro = EmpleadosView(records, excel, usuario_actual=user_ro, tiene_permiso_fn=lambda p: auth.tiene_permiso(uid_ro, p))
    assert emp_view_ro.btn_nuevo.isEnabled() is False
    assert emp_view_ro.btn_editar.isEnabled() is False
    assert emp_view_ro.btn_toggle.isEnabled() is False
    assert emp_view_ro.btn_importar.isEnabled() is False
    assert emp_view_ro.btn_remociones.isEnabled() is False
    emp_view_ro.close()

    # 2. MainWindow: cambiar de Admin (con conocimiento_via) a SoloLectura no debe crashear
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    win = MainWindow(db, records, auth, excel, pdf, word, backup, usuario_actual=user_adm, supabase_sync=None)
    try:
        assert hasattr(win, "tab_conocimiento_via")
        assert win.btn_sync_status.isEnabled() is True

        # Simular cambio de usuario a SoloLectura
        win.usuario_actual = user_ro
        win._reconstruir_ui_sesion()
        assert not hasattr(win, "tab_conocimiento_via")
        assert win.btn_sync_status.isEnabled() is False
        # Llamar recargar_todo no debe lanzar RuntimeError por dangling pointer
        win.recargar_todo()
        # Verificar botones RBAC en sub-vistas de Novedades y Cambios
        assert win.tab_novedades_cambios.tab_novedades.btn_editar.isEnabled() is False
        assert win.tab_novedades_cambios.tab_novedades.btn_eliminar.isEnabled() is False
        assert win.tab_novedades_cambios.tab_cambios.btn_editar.isEnabled() is False
        assert win.tab_novedades_cambios.tab_cambios.btn_eliminar.isEnabled() is False
        assert win.tab_novedades_cambios.tab_ordenamientos.btn_editar.isEnabled() is False
        assert win.tab_novedades_cambios.tab_ordenamientos.btn_paradas.isEnabled() is False
    finally:
        win.close()


def test_actualizar_novedad_actualiza_updated_at_y_sync_status(tmp_path: Path):
    db, auth, records, *_ = _crear_entorno(tmp_path)
    uid = auth.crear_administrador_inicial("admin", "AdminClave2026!", legajo=3000)

    nov_id = records.crear_novedad(
        {
            "legajo": 3000,
            "apellidos_nombres": "Test Agent",
            "especialidad": "Conductor",
            "dotacion": "PC",
            "turnos": "101",
            "franco": "Domingo",
            "novedad": "Licencia",
            "fecha_inicio": "24/11/2026",
            "fecha_fin": "25/11/2026",
            "referencia_estacion": "PC",
            "supervisor": "Sup",
            "observaciones": "Inicial",
        },
        user_id=uid,
    )
    # Simular que ya fue sincronizada
    with db.write_transaction() as conn:
        conn.execute("UPDATE novedades SET sync_status='synced', updated_at='2020-01-01 00:00:00' WHERE id=?", (nov_id,))

    records.actualizar_novedad(
        nov_id,
        {
            "legajo": 3000,
            "apellidos_nombres": "Test Agent",
            "especialidad": "Conductor",
            "dotacion": "PC",
            "turnos": "101",
            "franco": "Domingo",
            "novedad": "Licencia",
            "fecha_inicio": "24/11/2026",
            "fecha_fin": "26/11/2026",
            "referencia_estacion": "PC",
            "supervisor": "Sup",
            "observaciones": "Actualizada",
        },
        user_id=uid,
    )
    with db.read_connection() as conn:
        row = conn.execute("SELECT sync_status, updated_at FROM novedades WHERE id=?", (nov_id,)).fetchone()
    assert row["sync_status"] == "pending"
    assert row["updated_at"] != "2020-01-01 00:00:00"


def test_supabase_pull_persiste_todas_las_tablas(tmp_path: Path, monkeypatch):
    db, *_ = _crear_entorno(tmp_path)
    engine = SupabaseSyncEngine(db, supabase_url="https://demo.supabase.co", supabase_key="sb_publishable_demo")

    def fake_request(config, url, method="GET", data=None, headers=None, timeout_sec=15):
        if "reno_dotaciones" in url:
            return 200, json.dumps([{"nombre": "DOT_REMOTA", "activo": True}]).encode("utf-8"), {}
        if "reno_empleados" in url:
            return 200, json.dumps([
                {
                    "legajo": 9999,
                    "apellidos_nombres": "Remoto Juan",
                    "especialidad": "Conductor",
                    "dotacion": "DOT_REMOTA",
                    "turnos": "101",
                    "franco": "Lunes",
                    "franco_horario": "00:00",
                    "jornada_horas": 6,
                    "activo": True,
                }
            ]).encode("utf-8"), {}
        return 200, b"[]", {}

    monkeypatch.setattr("src.data.sync.supabase_sync.ejecutar_request_http", fake_request)
    monkeypatch.setattr("src.data.sync.supabase_sync.cargar_configuracion_app", lambda: {
        "supabase_url": "https://demo.supabase.co",
        "supabase_anon_key": "sb_publishable_demo",
    })

    _env, rec = engine.sincronizar(solo_push=False)
    assert rec == 2
    with db.read_connection() as conn:
        dot = conn.execute("SELECT nombre FROM dotaciones WHERE nombre='DOT_REMOTA'").fetchone()
        emp = conn.execute("SELECT apellidos_nombres FROM empleados WHERE legajo=9999").fetchone()
    assert dot is not None
    assert emp is not None and emp["apellidos_nombres"] == "Remoto Juan"
