"""Diálogos de Remociones semestrales e Historial de Turnos por Empleado."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.logger import get_logger
from src.services.excel_service import ExcelService
from src.services.records_service import RecordsService
from src.ui.views.dialogs.base_dialog import BaseDialog

logger = get_logger("RemocionesDialogs")


class AgregarHistorialTurnoDialog(BaseDialog):
    """Permite cargar manualmente un período histórico de turno/dotación para un empleado."""

    def __init__(
        self,
        records_service: RecordsService,
        empleado: Dict[str, Any],
        usuario_actual: Optional[Dict[str, Any]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.records_service = records_service
        self.empleado = dict(empleado)
        self.usuario_actual = usuario_actual or {}

        self.setWindowTitle(f"Agregar Registro Histórico — {self.empleado.get('apellidos_nombres', '')}")
        self.setMinimumWidth(450)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.txt_remocion = QLineEdit()
        self.txt_remocion.setPlaceholderText("Ej. Remoción 1° Semestre 2025")
        form.addRow("Remoción / Período:", self.txt_remocion)

        self.date_desde = QDateEdit()
        self.date_desde.setCalendarPopup(True)
        self.date_desde.setDisplayFormat("dd/MM/yyyy")
        self.date_desde.setDate(QDate.currentDate().addMonths(-6))
        form.addRow("Fecha desde:", self.date_desde)

        h_hasta = QHBoxLayout()
        self.chk_sin_fin = QCheckBox("Vigente (sin fecha hasta)")
        self.date_hasta = QDateEdit()
        self.date_hasta.setCalendarPopup(True)
        self.date_hasta.setDisplayFormat("dd/MM/yyyy")
        self.date_hasta.setDate(QDate.currentDate())
        self.chk_sin_fin.toggled.connect(lambda c: self.date_hasta.setEnabled(not c))
        h_hasta.addWidget(self.date_hasta)
        h_hasta.addWidget(self.chk_sin_fin)
        form.addRow("Fecha hasta:", h_hasta)

        self.txt_turno = QLineEdit(str(self.empleado.get("turnos") or ""))
        self.txt_turno.setPlaceholderText("Ej. 401.1, CICLO...")
        form.addRow("Turno:", self.txt_turno)

        self.combo_dot = QComboBox()
        self.combo_dot.setEditable(True)
        for d in self.records_service.listar_dotaciones(incluir_inactivos=False):
            self.combo_dot.addItem(d["nombre"])
        self.combo_dot.setCurrentText(str(self.empleado.get("dotacion") or ""))
        form.addRow("Dotación:", self.combo_dot)

        self.txt_franco = QLineEdit(str(self.empleado.get("franco") or ""))
        form.addRow("Franco:", self.txt_franco)

        self.txt_franco_hor = QLineEdit(str(self.empleado.get("franco_horario") or ""))
        self.txt_franco_hor.setPlaceholderText("HH:MM (opcional)")
        form.addRow("Horario Franco:", self.txt_franco_hor)

        self.txt_esp = QLineEdit(str(self.empleado.get("especialidad") or ""))
        form.addRow("Especialidad:", self.txt_esp)

        self.txt_obs = QLineEdit()
        self.txt_obs.setPlaceholderText("Observaciones opcionales...")
        form.addRow("Observaciones:", self.txt_obs)

        layout.addLayout(form)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setObjectName("btnSecondary")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("💾 Guardar")
        btn_save.clicked.connect(self._guardar)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_save)
        layout.addLayout(btns)

    def _guardar(self) -> None:
        try:
            self.records_service.agregar_historial_turno_manual(
                legajo=int(self.empleado["legajo"]),
                data={
                    "remocion_nombre": self.txt_remocion.text().strip(),
                    "fecha_desde": self.date_desde.date().toString("dd/MM/yyyy"),
                    "fecha_hasta": "" if self.chk_sin_fin.isChecked() else self.date_hasta.date().toString("dd/MM/yyyy"),
                    "turnos": self.txt_turno.text().strip(),
                    "dotacion": self.combo_dot.currentText().strip(),
                    "franco": self.txt_franco.text().strip(),
                    "franco_horario": self.txt_franco_hor.text().strip(),
                    "especialidad": self.txt_esp.text().strip(),
                    "motivo": "Histórico manual",
                    "observaciones": self.txt_obs.text().strip(),
                },
                user_id=self.usuario_actual.get("id"),
                windows_user=self.usuario_actual.get("username"),
            )
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "Historial de Turnos", str(e))


class HistorialTurnosEmpleadoDialog(BaseDialog):
    """Muestra la línea de tiempo completa de turnos y dotaciones de un empleado."""

    def __init__(
        self,
        records_service: RecordsService,
        excel_service: ExcelService,
        empleado: Dict[str, Any],
        usuario_actual: Optional[Dict[str, Any]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.records_service = records_service
        self.excel_service = excel_service
        self.empleado = dict(empleado)
        self.usuario_actual = usuario_actual or {}
        self._filas: List[Dict[str, Any]] = []
        self._mapa_emp: Dict[str, Dict[str, Any]] = {}

        self.setWindowTitle(f"Historial de Turnos — {self.empleado.get('apellidos_nombres', '')}")
        self.resize(1020, 560)
        self._init_ui()
        self._recargar()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Barra de cambio rápido de agente
        nav = QHBoxLayout()
        nav.addWidget(QLabel("Agente:"))
        self.combo_emp = QComboBox()
        self.combo_emp.setEditable(True)
        self.combo_emp.setInsertPolicy(QComboBox.NoInsert)
        self.combo_emp.setMinimumWidth(340)
        etiquetas = []
        for e in self.records_service.listar_empleados(incluir_inactivos=True):
            et = f"{e['apellidos_nombres']} (Leg. {e['legajo']})"
            self._mapa_emp[et] = dict(e)
            etiquetas.append(et)
            self.combo_emp.addItem(et, e["legajo"])
        comp = QCompleter(etiquetas, self)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        self.combo_emp.setCompleter(comp)

        et_act = f"{self.empleado.get('apellidos_nombres', '')} (Leg. {self.empleado.get('legajo', '')})"
        idx = self.combo_emp.findText(et_act)
        if idx >= 0:
            self.combo_emp.setCurrentIndex(idx)
        self.combo_emp.activated.connect(self._cambiar_agente)
        nav.addWidget(self.combo_emp)

        nav.addStretch()
        self.btn_agregar = QPushButton("➕ Agregar Histórico")
        self.btn_agregar.clicked.connect(self._agregar_manual)
        nav.addWidget(self.btn_agregar)

        self.btn_eliminar = QPushButton("🗑️ Eliminar")
        self.btn_eliminar.setObjectName("btnDanger")
        self.btn_eliminar.clicked.connect(self._eliminar_sel)
        nav.addWidget(self.btn_eliminar)

        self.btn_excel = QPushButton("📊 Exportar Excel")
        self.btn_excel.setObjectName("btnSecondary")
        self.btn_excel.clicked.connect(self._exportar_excel)
        nav.addWidget(self.btn_excel)
        layout.addLayout(nav)

        self.lbl_info = QLabel("")
        self.lbl_info.setStyleSheet("color: #6B7280;")
        layout.addWidget(self.lbl_info)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "DESDE", "HASTA", "REMOCIÓN", "TURNO", "DOTACIÓN",
            "FRANCO", "HOR. FRANCO", "ESPECIALIDAD", "MOTIVO", "OBSERVACIONES",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(9, QHeaderView.Stretch)
        for c, w in ((0, 90), (1, 90), (2, 150), (3, 95), (4, 80), (5, 85), (6, 85), (7, 145), (8, 120)):
            self.table.setColumnWidth(c, w)
        layout.addWidget(self.table, 1)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_close = QPushButton("Cerrar")
        btn_close.setObjectName("btnSecondary")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        layout.addLayout(btns)

    def _cambiar_agente(self, index: int) -> None:
        if index < 0:
            return
        et = self.combo_emp.itemText(index)
        emp = self._mapa_emp.get(et)
        if emp:
            self.empleado = dict(emp)
            self._recargar()

    def _recargar(self) -> None:
        leg = int(self.empleado["legajo"])
        self.setWindowTitle(f"Historial de Turnos — {self.empleado.get('apellidos_nombres', '')} (Leg. {leg})")
        self.lbl_info.setText(
            f"Actual: Turno <b>{self.empleado.get('turnos') or '-'}</b> | "
            f"Dotación <b>{self.empleado.get('dotacion') or '-'}</b> | "
            f"Franco <b>{self.empleado.get('franco') or '-'}</b> | "
            f"Especialidad <b>{self.empleado.get('especialidad') or '-'}</b>"
        )
        self._filas = self.records_service.listar_historial_turnos_empleado(leg)
        self.table.setRowCount(len(self._filas))

        from src.core.theme import obtener_tema_visual
        es_oscuro = obtener_tema_visual() == "oscuro"
        bg_vig = QBrush(QColor("#132E1F") if es_oscuro else QColor("#E8F5E9"))
        fg_vig = QBrush(QColor("#6EE7B7") if es_oscuro else QColor("#1B5E20"))

        for r, f in enumerate(self._filas):
            vals = [
                f.get("fecha_desde_display", ""),
                f.get("fecha_hasta_display", "Vigente"),
                f.get("remocion_nombre", "") or "-",
                f.get("turnos", "") or "-",
                f.get("dotacion", "") or "-",
                f.get("franco", "") or "-",
                f.get("franco_horario", "") or "-",
                f.get("especialidad", "") or "-",
                f.get("motivo", "") or "-",
                f.get("observaciones", "") or "",
            ]
            es_vig = bool(f.get("es_vigente"))
            for c, val in enumerate(vals):
                it = QTableWidgetItem(str(val))
                if c in (0, 1, 3, 4, 5, 6):
                    it.setTextAlignment(Qt.AlignCenter)
                if es_vig:
                    it.setBackground(bg_vig)
                    it.setForeground(fg_vig)
                    if c in (1, 3):
                        ft = QFont()
                        ft.setBold(True)
                        it.setFont(ft)
                self.table.setItem(r, c, it)

    def _agregar_manual(self) -> None:
        dlg = AgregarHistorialTurnoDialog(
            records_service=self.records_service,
            empleado=self.empleado,
            usuario_actual=self.usuario_actual,
            parent=self,
        )
        if dlg.exec():
            self._recargar()

    def _eliminar_sel(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Eliminar", "Seleccione un registro del historial.")
            return
        idx = rows[0].row()
        if not (0 <= idx < len(self._filas)):
            return
        fila = self._filas[idx]
        resp = QMessageBox.question(
            self,
            "Eliminar Registro Histórico",
            f"¿Desea eliminar el registro del turno <b>{fila.get('turnos')}</b> ({fila.get('fecha_desde_display')})?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        self.records_service.eliminar_historial_turno(
            int(fila["id"]),
            user_id=self.usuario_actual.get("id"),
            windows_user=self.usuario_actual.get("username"),
        )
        self._recargar()

    def _exportar_excel(self) -> None:
        if not self._filas:
            QMessageBox.information(self, "Exportar", "No hay registros históricos para exportar.")
            return
        leg = self.empleado.get("legajo", "")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar Historial de Turnos",
            f"Historial_Turnos_Leg_{leg}.xlsx",
            "Excel Files (*.xlsx)",
        )
        if not path:
            return
        try:
            out = self.excel_service.export_historial_turnos(path, self._filas, titulo=f"Historial Leg {leg}")
            QMessageBox.information(self, "Exportación Exitosa", f"Archivo exportado a:\n{out}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo exportar:\n{e}")


class RemocionesDialog(BaseDialog):
    """Gestión de Remociones semestrales (importación BASE con diff, snapshot y consulta histórica)."""

    def __init__(
        self,
        records_service: RecordsService,
        excel_service: ExcelService,
        usuario_actual: Optional[Dict[str, Any]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.records_service = records_service
        self.excel_service = excel_service
        self.usuario_actual = usuario_actual or {}
        self.hubo_cambios = False
        self._excel_path: str = ""
        self._remociones: List[Dict[str, Any]] = []
        self._cambios_rem_actual: List[Dict[str, Any]] = []

        self.setWindowTitle("Gestión de Remociones — Historial de Turnos y Dotaciones")
        self.resize(1040, 620)
        self._init_ui()
        self._recargar_remociones()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()

        # --- TAB 1: Nueva Remoción ---
        tab_nueva = QWidget()
        lay_n = QVBoxLayout(tab_nueva)
        lay_n.setSpacing(10)

        form = QFormLayout()
        sem = "1° Semestre" if datetime.now().month <= 6 else "2° Semestre"
        self.txt_nombre_rem = QLineEdit(f"Remoción {sem} {datetime.now().year}")
        form.addRow("Nombre de la Remoción:", self.txt_nombre_rem)

        self.date_vigencia = QDateEdit()
        self.date_vigencia.setCalendarPopup(True)
        self.date_vigencia.setDisplayFormat("dd/MM/yyyy")
        self.date_vigencia.setDate(QDate.currentDate())
        form.addRow("Fecha de vigencia:", self.date_vigencia)

        self.txt_obs_rem = QLineEdit()
        self.txt_obs_rem.setPlaceholderText("Observaciones opcionales...")
        form.addRow("Observaciones:", self.txt_obs_rem)
        lay_n.addLayout(form)

        # Opción A: Snapshot del padrón actual
        grp_snap = QGroupBox("Opción A — Guardar Foto (Snapshot) del Padrón Actual")
        lay_snap = QHBoxLayout(grp_snap)
        lay_snap.addWidget(
            QLabel("Registra el turno y dotación actuales de todo el personal activo bajo esta remoción.")
        )
        btn_snap = QPushButton("📸 Guardar Snapshot")
        btn_snap.setObjectName("btnSecondary")
        btn_snap.clicked.connect(self._guardar_snapshot)
        lay_snap.addWidget(btn_snap)
        lay_n.addWidget(grp_snap)

        # Opción B: Importar Excel BASE de Remoción con previsualización
        grp_xl = QGroupBox("Opción B — Aplicar Remoción desde Excel BASE (con vista previa de cambios)")
        lay_xl = QVBoxLayout(grp_xl)

        row_file = QHBoxLayout()
        self.txt_excel_path = QLineEdit()
        self.txt_excel_path.setReadOnly(True)
        self.txt_excel_path.setPlaceholderText("Seleccione el archivo Excel BASE actualizado de la remoción...")
        btn_browse = QPushButton("📂 Seleccionar Excel...")
        btn_browse.setObjectName("btnSecondary")
        btn_browse.clicked.connect(self._seleccionar_excel)
        self.chk_reg_todos = QCheckBox("Registrar también a quienes conservaron el mismo turno")
        row_file.addWidget(self.txt_excel_path, 1)
        row_file.addWidget(btn_browse)
        lay_xl.addLayout(row_file)
        lay_xl.addWidget(self.chk_reg_todos)

        self.lbl_prev = QLabel("Seleccione un Excel para previsualizar los cambios de turno y dotación.")
        self.lbl_prev.setStyleSheet("color: #6B7280;")
        lay_xl.addWidget(self.lbl_prev)

        self.tbl_prev = QTableWidget(0, 7)
        self.tbl_prev.setHorizontalHeaderLabels([
            "LEGAJO", "APELLIDOS Y NOMBRES", "TIPO", "TURNO ANTERIOR", "TURNO NUEVO",
            "DOT. ANTERIOR", "DOT. NUEVA",
        ])
        self.tbl_prev.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_prev.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for c, w in ((0, 80), (2, 110), (3, 115), (4, 115), (5, 95), (6, 95)):
            self.tbl_prev.setColumnWidth(c, w)
        lay_xl.addWidget(self.tbl_prev, 1)

        row_apply = QHBoxLayout()
        row_apply.addStretch()
        self.btn_aplicar_xl = QPushButton("🔄 Aplicar Remoción desde Excel")
        self.btn_aplicar_xl.setEnabled(False)
        self.btn_aplicar_xl.clicked.connect(self._aplicar_excel)
        row_apply.addWidget(self.btn_aplicar_xl)
        lay_xl.addLayout(row_apply)

        lay_n.addWidget(grp_xl, 1)
        self.tabs.addTab(tab_nueva, "➕ Nueva Remoción")

        # --- TAB 2: Remociones Registradas ---
        tab_hist = QWidget()
        lay_h = QVBoxLayout(tab_hist)

        splitter = QSplitter(Qt.Vertical)
        self.tbl_rems = QTableWidget(0, 5)
        self.tbl_rems.setHorizontalHeaderLabels(["ID", "REMOCIÓN", "VIGENCIA", "REGISTROS", "OBSERVACIONES"])
        self.tbl_rems.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl_rems.setSelectionMode(QTableWidget.SingleSelection)
        self.tbl_rems.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_rems.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl_rems.setColumnWidth(0, 55)
        self.tbl_rems.setColumnWidth(1, 240)
        self.tbl_rems.setColumnWidth(2, 100)
        self.tbl_rems.setColumnWidth(3, 95)
        self.tbl_rems.itemSelectionChanged.connect(self._on_rem_seleccionada)
        splitter.addWidget(self.tbl_rems)

        det_box = QWidget()
        det_lay = QVBoxLayout(det_box)
        det_lay.setContentsMargins(0, 4, 0, 0)
        det_bar = QHBoxLayout()
        self.lbl_det_rem = QLabel("Seleccione una remoción para ver su detalle.")
        self.lbl_det_rem.setStyleSheet("font-weight: 600;")
        btn_exp_rem = QPushButton("📊 Exportar Detalle a Excel")
        btn_exp_rem.setObjectName("btnSecondary")
        btn_exp_rem.clicked.connect(self._exportar_remocion_excel)
        det_bar.addWidget(self.lbl_det_rem)
        det_bar.addStretch()
        det_bar.addWidget(btn_exp_rem)
        det_lay.addLayout(det_bar)

        self.tbl_det_rem = QTableWidget(0, 8)
        self.tbl_det_rem.setHorizontalHeaderLabels([
            "LEGAJO", "APELLIDOS Y NOMBRES", "TURNO", "DOTACIÓN", "FRANCO",
            "DESDE", "HASTA", "OBSERVACIONES",
        ])
        self.tbl_det_rem.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_det_rem.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        for c, w in ((0, 75), (1, 210), (2, 95), (3, 80), (4, 85), (5, 90), (6, 90)):
            self.tbl_det_rem.setColumnWidth(c, w)
        det_lay.addWidget(self.tbl_det_rem)
        splitter.addWidget(det_box)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        lay_h.addWidget(splitter)
        self.tabs.addTab(tab_hist, "📜 Remociones Registradas")

        layout.addWidget(self.tabs, 1)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_close = QPushButton("Cerrar")
        btn_close.setObjectName("btnSecondary")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        layout.addLayout(btns)

    def _guardar_snapshot(self) -> None:
        nombre = self.txt_nombre_rem.text().strip()
        f_vig = self.date_vigencia.date().toString("dd/MM/yyyy")
        obs = self.txt_obs_rem.text().strip()
        if not nombre:
            QMessageBox.warning(self, "Remoción", "Ingrese un nombre para la remoción.")
            return
        resp = QMessageBox.question(
            self,
            "Confirmar Snapshot",
            f"¿Guardar la foto del padrón actual como <b>{nombre}</b> con fecha de vigencia <b>{f_vig}</b>?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        try:
            res = self.records_service.registrar_snapshot_remocion(
                nombre=nombre,
                fecha_vigencia=f_vig,
                observaciones=obs,
                user_id=self.usuario_actual.get("id"),
                windows_user=self.usuario_actual.get("username"),
            )
            self.hubo_cambios = True
            self._recargar_remociones()
            self.tabs.setCurrentIndex(1)
            QMessageBox.information(
                self,
                "Snapshot Guardado",
                f"Se registraron {res['registrados']} agentes bajo '{nombre}'.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo guardar el snapshot:\n{e}")

    def _seleccionar_excel(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar Excel BASE de Remoción",
            "",
            "Excel Files (*.xlsx)",
        )
        if not path:
            return
        self._excel_path = path
        self.txt_excel_path.setText(path)
        try:
            prev = self.records_service.previsualizar_remocion_desde_excel(path)
            cambios = prev["cambios"]
            altas = prev["altas"]
            self.lbl_prev.setText(
                f"Leídos: <b>{prev['total_excel']}</b>  |  "
                f"Cambios detectados: <b>{len(cambios)}</b>  |  "
                f"Altas nuevas: <b>{len(altas)}</b>  |  "
                f"Sin cambios: <b>{prev['sin_cambios']}</b>"
            )
            filas_prev = []
            for c in cambios:
                tipo_txt = []
                if c.get("cambio_turno"):
                    tipo_txt.append("Turno")
                if c.get("cambio_dotacion"):
                    tipo_txt.append("Dotación")
                filas_prev.append((
                    c["legajo"], c["apellidos_nombres"], " + ".join(tipo_txt) or "Datos",
                    c.get("turno_anterior", ""), c.get("turnos", ""),
                    c.get("dotacion_anterior", ""), c.get("dotacion", ""),
                ))
            for a in altas:
                filas_prev.append((
                    a["legajo"], a["apellidos_nombres"], "Alta nueva",
                    "-", a.get("turnos", ""), "-", a.get("dotacion", ""),
                ))
            self.tbl_prev.setRowCount(len(filas_prev))
            for r, vals in enumerate(filas_prev):
                for c_idx, v in enumerate(vals):
                    it = QTableWidgetItem(str(v))
                    if c_idx in (0, 2, 3, 4, 5, 6):
                        it.setTextAlignment(Qt.AlignCenter)
                    self.tbl_prev.setItem(r, c_idx, it)
            self.btn_aplicar_xl.setEnabled(prev["total_excel"] > 0)
        except Exception as e:
            self.btn_aplicar_xl.setEnabled(False)
            QMessageBox.critical(self, "Error leyendo Excel", str(e))

    def _aplicar_excel(self) -> None:
        if not self._excel_path:
            return
        nombre = self.txt_nombre_rem.text().strip()
        f_vig = self.date_vigencia.date().toString("dd/MM/yyyy")
        obs = self.txt_obs_rem.text().strip()
        if not nombre:
            QMessageBox.warning(self, "Remoción", "Ingrese un nombre para la remoción.")
            return
        resp = QMessageBox.question(
            self,
            "Aplicar Remoción",
            f"¿Aplicar <b>{nombre}</b> (vigencia {f_vig}) actualizando el padrón de empleados "
            f"y guardando el historial de turnos?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        try:
            res = self.records_service.aplicar_remocion_desde_excel(
                file_path=self._excel_path,
                nombre=nombre,
                fecha_vigencia=f_vig,
                observaciones=obs,
                registrar_todos=self.chk_reg_todos.isChecked(),
                user_id=self.usuario_actual.get("id"),
                windows_user=self.usuario_actual.get("username"),
            )
            self.hubo_cambios = True
            self._recargar_remociones()
            self.tabs.setCurrentIndex(1)
            QMessageBox.information(
                self,
                "Remoción Aplicada",
                f"Remoción '{nombre}' aplicada correctamente.\n"
                f"Cambios registrados: {res['cambios']}\n"
                f"Altas nuevas: {res['altas']}",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo aplicar la remoción:\n{e}")

    def _recargar_remociones(self) -> None:
        self._remociones = self.records_service.listar_remociones()
        self.tbl_rems.setRowCount(len(self._remociones))
        for r, rem in enumerate(self._remociones):
            vals = [
                str(rem["id"]),
                str(rem["nombre"]),
                str(rem.get("fecha_vigencia_display", "")),
                str(rem.get("total_registros", rem.get("cantidad_cambios", 0))),
                str(rem.get("observaciones") or ""),
            ]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c in (0, 2, 3):
                    it.setTextAlignment(Qt.AlignCenter)
                self.tbl_rems.setItem(r, c, it)

    def _on_rem_seleccionada(self) -> None:
        rows = self.tbl_rems.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        if not (0 <= idx < len(self._remociones)):
            return
        rem = self._remociones[idx]
        self._cambios_rem_actual = self.records_service.listar_cambios_remocion(int(rem["id"]))
        self.lbl_det_rem.setText(
            f"Remoción: {rem['nombre']} ({rem.get('fecha_vigencia_display', '')}) — "
            f"{len(self._cambios_rem_actual)} registro(s)"
        )
        self.tbl_det_rem.setRowCount(len(self._cambios_rem_actual))
        for r, c_item in enumerate(self._cambios_rem_actual):
            vals = [
                str(c_item.get("legajo", "")),
                str(c_item.get("apellidos_nombres") or ""),
                str(c_item.get("turnos") or ""),
                str(c_item.get("dotacion") or ""),
                str(c_item.get("franco") or ""),
                str(c_item.get("fecha_desde_display") or ""),
                str(c_item.get("fecha_hasta_display") or "Vigente"),
                str(c_item.get("observaciones") or ""),
            ]
            for c_idx, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c_idx in (0, 2, 3, 4, 5, 6):
                    it.setTextAlignment(Qt.AlignCenter)
                self.tbl_det_rem.setItem(r, c_idx, it)

    def _exportar_remocion_excel(self) -> None:
        if not self._cambios_rem_actual:
            QMessageBox.information(self, "Exportar", "Seleccione una remoción con registros.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar Detalle de Remoción",
            f"Remocion_{datetime.now().strftime('%Y%m%d')}.xlsx",
            "Excel Files (*.xlsx)",
        )
        if not path:
            return
        try:
            out = self.excel_service.export_historial_turnos(path, self._cambios_rem_actual, titulo="Detalle Remocion")
            QMessageBox.information(self, "Exportación Exitosa", f"Archivo exportado a:\n{out}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo exportar:\n{e}")
