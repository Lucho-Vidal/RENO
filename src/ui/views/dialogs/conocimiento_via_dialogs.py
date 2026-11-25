"""Diálogos operativos para el módulo de Conocimiento de Vía por Sector.

Incluye:
- EditarConocimientoDialog: carga/edición de fechas de conocimiento en vía ASC y DESC.
- AdminSectoresViaDialog: ABM del catálogo editable de sectores de vía.
- AutocompletarTurnoViaDialog: previsualización y aplicación de sectores según el turno asignado.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.core.logger import get_logger
from src.domain.conocimiento_via import (
    PLANTILLA_OBS_TURNO_DEFAULT,
    calcular_fecha_vencimiento,
    es_ayudante_conduccion,
    formatear_observacion_turno,
)
from src.services.records_service import RecordsService
from src.ui.views.dialogs.base_dialog import BaseDialog

logger = get_logger("ConocimientoViaDialogs")


class EditarConocimientoDialog(BaseDialog):
    """Diálogo para registrar o actualizar fechas de conocimiento en vía Ascendente y Descendente."""

    def __init__(
        self,
        records_service: RecordsService,
        empleado: Dict[str, Any],
        sector: Dict[str, Any],
        info_actual: Optional[Dict[str, Dict[str, Any]]] = None,
        usuario_actual: Optional[Dict[str, Any]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.records_service = records_service
        self.empleado = dict(empleado)
        self.sector = dict(sector)
        self.info_actual = info_actual or {}
        self.usuario_actual = usuario_actual or {}
        self.es_ayudante = es_ayudante_conduccion(self.empleado.get("especialidad"))

        self.setWindowTitle(f"Conocimiento de Vía — {self.sector.get('codigo', '')}")
        self.setMinimumWidth(520)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        ori = self.sector.get("origen_sigla", "")
        des = self.sector.get("destino_sigla", "")
        desc = self.sector.get("descripcion", "")

        lbl_emp = QLabel(
            f"<b>{self.empleado.get('apellidos_nombres', '')}</b> "
            f"(Leg. {self.empleado.get('legajo', '')}) — {self.empleado.get('especialidad', '')} "
            f"| Dotación: <b>{self.empleado.get('dotacion', '')}</b>"
        )
        lbl_emp.setWordWrap(True)
        layout.addWidget(lbl_emp)

        lbl_sec = QLabel(
            f"Sector: <b>{self.sector.get('codigo', '')}</b>"
            + (f" ({desc})" if desc else "")
            + f"<br><span style='color:#6B7280;'>Vía Ascendente (ASC): {ori} → {des} (se aleja de PC km 0) | "
            f"Vía Descendente (DESC): {des} → {ori} (hacia PC)</span>"
        )
        lbl_sec.setWordWrap(True)
        layout.addWidget(lbl_sec)

        if self.es_ayudante:
            lbl_ayu = QLabel("ℹ️ Personal Ayudante: se registra fecha histórica sin alertas de vencimiento.")
            lbl_ayu.setStyleSheet("color: #6B7280; font-style: italic;")
            layout.addWidget(lbl_ayu)

        # Grupo Ascendente
        grp_asc = QGroupBox(f"▲ Vía Ascendente ({ori} → {des})")
        lay_asc = QHBoxLayout(grp_asc)
        self.chk_asc = QCheckBox("Conocido")
        self.date_asc = QDateEdit()
        self.date_asc.setCalendarPopup(True)
        self.date_asc.setDisplayFormat("dd/MM/yyyy")
        self.date_asc.setDate(QDate.currentDate())
        self.lbl_venc_asc = QLabel("")
        self.lbl_venc_asc.setStyleSheet("color: #6B7280;")
        lay_asc.addWidget(self.chk_asc)
        lay_asc.addWidget(self.date_asc)
        lay_asc.addWidget(self.lbl_venc_asc, 1)
        layout.addWidget(grp_asc)

        # Grupo Descendente
        grp_desc = QGroupBox(f"▼ Vía Descendente ({des} → {ori})")
        lay_desc = QHBoxLayout(grp_desc)
        self.chk_desc = QCheckBox("Conocido")
        self.date_desc = QDateEdit()
        self.date_desc.setCalendarPopup(True)
        self.date_desc.setDisplayFormat("dd/MM/yyyy")
        self.date_desc.setDate(QDate.currentDate())
        self.lbl_venc_desc = QLabel("")
        self.lbl_venc_desc.setStyleSheet("color: #6B7280;")
        lay_desc.addWidget(self.chk_desc)
        lay_desc.addWidget(self.date_desc)
        lay_desc.addWidget(self.lbl_venc_desc, 1)
        layout.addWidget(grp_desc)

        btn_igualar = QPushButton("🔁 Marcar ambos sentidos con la fecha de Ascendente")
        btn_igualar.setObjectName("btnSecondary")
        btn_igualar.clicked.connect(self._igualar_ambos)
        layout.addWidget(btn_igualar)

        # Cargar valores existentes
        asc_info = self.info_actual.get("ASC") or {}
        desc_info = self.info_actual.get("DESC") or {}

        origen_reg = asc_info.get("origen_registro") or desc_info.get("origen_registro") or ""
        turno_ref = asc_info.get("turno_referencia") or desc_info.get("turno_referencia") or ""
        if origen_reg or turno_ref:
            etiquetas_origen = {
                "manual": "Carga manual",
                "turno": "Autocompletado por turno (individual)",
                "turno_masivo": "Cálculo masivo por turno",
            }
            partes_meta = []
            if origen_reg:
                partes_meta.append(f"Origen: <b>{etiquetas_origen.get(origen_reg, origen_reg)}</b>")
            if turno_ref:
                partes_meta.append(f"Turno de referencia: <b>{turno_ref}</b>")
            lbl_meta = QLabel(" | ".join(partes_meta))
            lbl_meta.setStyleSheet("color: #6B7280; font-size: 8.5pt;")
            layout.addWidget(lbl_meta)

        # Observaciones
        layout.addWidget(QLabel("Observaciones / Condiciones de conocimiento:"))
        self.txt_obs = QPlainTextEdit()
        self.txt_obs.setMaximumHeight(85)
        self.txt_obs.setPlaceholderText("Notas o condiciones (ej. examen de vía, viaje de reconocimiento, turno e itinerario)...")
        layout.addWidget(self.txt_obs)

        if asc_info.get("fecha_iso"):
            self.chk_asc.setChecked(True)
            qd = QDate.fromString(asc_info["fecha_iso"], "yyyy-MM-dd")
            if qd.isValid():
                self.date_asc.setDate(qd)
        if desc_info.get("fecha_iso"):
            self.chk_desc.setChecked(True)
            qd = QDate.fromString(desc_info["fecha_iso"], "yyyy-MM-dd")
            if qd.isValid():
                self.date_desc.setDate(qd)
        obs_ini = asc_info.get("observaciones") or desc_info.get("observaciones") or ""
        if obs_ini:
            self.txt_obs.setPlainText(obs_ini)

        self.chk_asc.toggled.connect(self._actualizar_preview_venc)
        self.chk_desc.toggled.connect(self._actualizar_preview_venc)
        self.date_asc.dateChanged.connect(self._actualizar_preview_venc)
        self.date_desc.dateChanged.connect(self._actualizar_preview_venc)
        self._actualizar_preview_venc()

        # Botones
        btns = QHBoxLayout()
        self.btn_borrar = QPushButton("🗑️ Quitar Conocimiento")
        self.btn_borrar.setObjectName("btnDanger")
        self.btn_borrar.clicked.connect(self._borrar_ambos)
        btns.addWidget(self.btn_borrar)
        btns.addStretch()

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setObjectName("btnSecondary")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("💾 Guardar")
        btn_save.clicked.connect(self._guardar)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_save)
        layout.addLayout(btns)

    def _igualar_ambos(self) -> None:
        self.chk_asc.setChecked(True)
        self.chk_desc.setChecked(True)
        self.date_desc.setDate(self.date_asc.date())
        self._actualizar_preview_venc()

    def _actualizar_preview_venc(self) -> None:
        self.date_asc.setEnabled(self.chk_asc.isChecked())
        self.date_desc.setEnabled(self.chk_desc.isChecked())

        for chk, dt_edit, lbl in (
            (self.chk_asc, self.date_asc, self.lbl_venc_asc),
            (self.chk_desc, self.date_desc, self.lbl_venc_desc),
        ):
            if not chk.isChecked():
                lbl.setText("Sin registro")
                continue
            qd = dt_edit.date()
            f_py = date(qd.year(), qd.month(), qd.day())
            f_venc = calcular_fecha_vencimiento(f_py, anios=2)
            lbl.setText(f"Vence (2 años): {f_venc.strftime('%d/%m/%Y')}")

    def _guardar(self) -> None:
        leg = int(self.empleado["legajo"])
        sid = int(self.sector["id"])
        obs = self.txt_obs.toPlainText().strip()
        uid = self.usuario_actual.get("id")
        win_u = self.usuario_actual.get("username")

        try:
            if self.chk_asc.isChecked():
                self.records_service.guardar_conocimiento_via(
                    legajo=leg,
                    sector_id=sid,
                    sentidos=["ASC"],
                    fecha_conocimiento=self.date_asc.date().toString("dd/MM/yyyy"),
                    origen_registro="manual",
                    observaciones=obs,
                    user_id=uid,
                    windows_user=win_u,
                )
            else:
                self.records_service.eliminar_conocimiento_via(leg, sid, sentido="ASC", user_id=uid, windows_user=win_u)

            if self.chk_desc.isChecked():
                self.records_service.guardar_conocimiento_via(
                    legajo=leg,
                    sector_id=sid,
                    sentidos=["DESC"],
                    fecha_conocimiento=self.date_desc.date().toString("dd/MM/yyyy"),
                    origen_registro="manual",
                    observaciones=obs,
                    user_id=uid,
                    windows_user=win_u,
                )
            else:
                self.records_service.eliminar_conocimiento_via(leg, sid, sentido="DESC", user_id=uid, windows_user=win_u)

            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo guardar el conocimiento de vía:\n{e}")

    def _borrar_ambos(self) -> None:
        resp = QMessageBox.question(
            self,
            "Quitar Conocimiento",
            f"¿Desea eliminar el registro de conocimiento del sector <b>{self.sector.get('codigo')}</b> "
            f"para <b>{self.empleado.get('apellidos_nombres')}</b>?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        try:
            self.records_service.eliminar_conocimiento_via(
                int(self.empleado["legajo"]),
                int(self.sector["id"]),
                sentido=None,
                user_id=self.usuario_actual.get("id"),
                windows_user=self.usuario_actual.get("username"),
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo eliminar:\n{e}")


class AdminSectoresViaDialog(BaseDialog):
    """ABM del catálogo editable de sectores de vía."""

    def __init__(
        self,
        records_service: RecordsService,
        usuario_actual: Optional[Dict[str, Any]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.records_service = records_service
        self.usuario_actual = usuario_actual or {}
        self._sector_edit_id: Optional[int] = None
        self._sectores: List[Dict[str, Any]] = []

        self.setWindowTitle("Administrar Sectores de Vía")
        self.resize(820, 560)
        self._init_ui()
        self._recargar()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        lbl_info = QLabel(
            "Configure los sectores de vía. La sigla <b>Origen</b> corresponde al extremo más cercano a PC (km 0) "
            "y <b>Destino</b> al extremo más alejado (Origen → Destino = Vía Ascendente)."
        )
        lbl_info.setWordWrap(True)
        layout.addWidget(lbl_info)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["ORDEN", "CÓDIGO", "ORIGEN", "DESTINO", "DESCRIPCIÓN", "ESTADO"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 65)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(3, 80)
        self.table.setColumnWidth(5, 85)
        self.table.itemSelectionChanged.connect(self._on_seleccion)
        layout.addWidget(self.table, 1)

        grp_form = QGroupBox("Alta / Edición de Sector")
        form = QFormLayout(grp_form)

        row_cod = QHBoxLayout()
        self.txt_codigo = QLineEdit()
        self.txt_codigo.setPlaceholderText("Ej. PC-TY")
        self.txt_origen = QLineEdit()
        self.txt_origen.setPlaceholderText("Sigla origen (ej. PC)")
        self.txt_origen.setFixedWidth(120)
        self.txt_destino = QLineEdit()
        self.txt_destino.setPlaceholderText("Sigla destino (ej. TY)")
        self.txt_destino.setFixedWidth(120)
        row_cod.addWidget(self.txt_codigo)
        row_cod.addWidget(QLabel("Origen:"))
        row_cod.addWidget(self.txt_origen)
        row_cod.addWidget(QLabel("Destino:"))
        row_cod.addWidget(self.txt_destino)
        form.addRow("Código:", row_cod)

        row_desc = QHBoxLayout()
        self.txt_desc = QLineEdit()
        self.txt_desc.setPlaceholderText("Descripción opcional (ej. Plaza Constitución - Témperley)")
        self.spin_orden = QSpinBox()
        self.spin_orden.setRange(0, 999)
        self.chk_activo = QCheckBox("Activo")
        self.chk_activo.setChecked(True)
        row_desc.addWidget(self.txt_desc, 1)
        row_desc.addWidget(QLabel("Orden:"))
        row_desc.addWidget(self.spin_orden)
        row_desc.addWidget(self.chk_activo)
        form.addRow("Descripción:", row_desc)

        btn_form = QHBoxLayout()
        self.btn_nuevo = QPushButton("➕ Nuevo Sector")
        self.btn_nuevo.setObjectName("btnSecondary")
        self.btn_nuevo.clicked.connect(self._limpiar_form)
        self.btn_guardar = QPushButton("💾 Guardar Sector")
        self.btn_guardar.clicked.connect(self._guardar)
        btn_form.addWidget(self.btn_nuevo)
        btn_form.addWidget(self.btn_guardar)
        btn_form.addStretch()
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.setObjectName("btnSecondary")
        btn_cerrar.clicked.connect(self.accept)
        btn_form.addWidget(btn_cerrar)
        form.addRow(btn_form)

        layout.addWidget(grp_form)

    def _recargar(self) -> None:
        self._sectores = self.records_service.listar_sectores_via(incluir_inactivos=True)
        self.table.setRowCount(len(self._sectores))
        for r, sec in enumerate(self._sectores):
            vals = [
                str(sec.get("orden", 0)),
                str(sec.get("codigo", "")),
                str(sec.get("origen_sigla", "")),
                str(sec.get("destino_sigla", "")),
                str(sec.get("descripcion", "")),
                "Activo" if sec.get("activo", 1) else "Inactivo",
            ]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if c in (0, 2, 3, 5):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(r, c, item)

    def _on_seleccion(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        if 0 <= idx < len(self._sectores):
            sec = self._sectores[idx]
            self._sector_edit_id = int(sec["id"])
            self.txt_codigo.setText(str(sec.get("codigo", "")))
            self.txt_origen.setText(str(sec.get("origen_sigla", "")))
            self.txt_destino.setText(str(sec.get("destino_sigla", "")))
            self.txt_desc.setText(str(sec.get("descripcion", "")))
            self.spin_orden.setValue(int(sec.get("orden", 0)))
            self.chk_activo.setChecked(bool(sec.get("activo", 1)))

    def _limpiar_form(self) -> None:
        self._sector_edit_id = None
        self.table.clearSelection()
        self.txt_codigo.clear()
        self.txt_origen.clear()
        self.txt_destino.clear()
        self.txt_desc.clear()
        self.spin_orden.setValue(len(self._sectores) + 1)
        self.chk_activo.setChecked(True)
        self.txt_codigo.setFocus()

    def _guardar(self) -> None:
        try:
            self.records_service.guardar_sector_via(
                {
                    "codigo": self.txt_codigo.text().strip(),
                    "origen_sigla": self.txt_origen.text().strip().upper(),
                    "destino_sigla": self.txt_destino.text().strip().upper(),
                    "descripcion": self.txt_desc.text().strip(),
                    "orden": self.spin_orden.value(),
                    "activo": 1 if self.chk_activo.isChecked() else 0,
                },
                sector_id=self._sector_edit_id,
                user_id=self.usuario_actual.get("id"),
                windows_user=self.usuario_actual.get("username"),
            )
            self._recargar()
            self._limpiar_form()
        except Exception as e:
            QMessageBox.warning(self, "Sectores de Vía", str(e))


class AutocompletarTurnoViaDialog(BaseDialog):
    """Previsualiza y aplica conocimientos de vía inferidos desde el turno asignado."""

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
        self._propuestas: List[Dict[str, Any]] = []
        self._itinerario_usado: str = ""

        self.setWindowTitle(f"Autocompletar Conocimiento por Turno — {self.empleado.get('apellidos_nombres', '')}")
        self.resize(800, 580)
        self._init_ui()
        self._analizar()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        lbl_emp = QLabel(
            f"<b>{self.empleado.get('apellidos_nombres', '')}</b> (Leg. {self.empleado.get('legajo', '')}) — "
            f"{self.empleado.get('especialidad', '')} | Dotación: <b>{self.empleado.get('dotacion', '')}</b>"
        )
        layout.addWidget(lbl_emp)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Turno:"))
        self.txt_turno = QLineEdit(str(self.empleado.get("turnos") or "").strip())
        self.txt_turno.setPlaceholderText("Ej. 401.1")
        self.txt_turno.setFixedWidth(115)
        self.txt_turno.returnPressed.connect(self._analizar)
        bar.addWidget(self.txt_turno)

        bar.addWidget(QLabel("Itinerario:"))
        self.combo_itinerario = QComboBox()
        self.combo_itinerario.addItem("Vigente (más reciente)")
        for it in self.records_service.listar_itinerarios_disponibles():
            self.combo_itinerario.addItem(str(it))
        bar.addWidget(self.combo_itinerario)

        bar.addWidget(QLabel("Régimen:"))
        self.combo_regimen = QComboBox()
        self.combo_regimen.addItems(["Todos", "H", "S", "D"])
        bar.addWidget(self.combo_regimen)

        btn_analizar = QPushButton("🔍 Analizar Turno")
        btn_analizar.setObjectName("btnSecondary")
        btn_analizar.clicked.connect(self._analizar)
        bar.addWidget(btn_analizar)

        bar.addStretch()
        bar.addWidget(QLabel("Fecha conocido:"))
        self.date_fecha = QDateEdit()
        self.date_fecha.setCalendarPopup(True)
        self.date_fecha.setDisplayFormat("dd/MM/yyyy")
        self.date_fecha.setDate(QDate.currentDate())
        bar.addWidget(self.date_fecha)
        layout.addLayout(bar)

        self.chk_no_sobrescribir = QCheckBox(
            "No sobrescribir conocimientos que ya tengan fecha igual o más reciente"
        )
        self.chk_no_sobrescribir.setChecked(True)
        layout.addWidget(self.chk_no_sobrescribir)

        # Panel de observación descriptiva
        grp_obs = QGroupBox("Observación / Condiciones de conocimiento")
        lay_obs = QVBoxLayout(grp_obs)
        lay_obs.setSpacing(4)
        self.txt_plantilla_obs = QLineEdit(PLANTILLA_OBS_TURNO_DEFAULT)
        self.txt_plantilla_obs.setPlaceholderText(PLANTILLA_OBS_TURNO_DEFAULT)
        self.txt_plantilla_obs.textChanged.connect(self._actualizar_preview_obs)
        lay_obs.addWidget(self.txt_plantilla_obs)
        self.lbl_obs_preview = QLabel("")
        self.lbl_obs_preview.setStyleSheet("color: #6B7280; font-size: 8.5pt;")
        self.lbl_obs_preview.setWordWrap(True)
        lay_obs.addWidget(self.lbl_obs_preview)
        layout.addWidget(grp_obs)

        self.lbl_resumen = QLabel("")
        self.lbl_resumen.setStyleSheet("color: #6B7280;")
        self.lbl_resumen.setWordWrap(True)
        layout.addWidget(self.lbl_resumen)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["APLICAR", "SECTOR", "SENTIDO", "RECORRIDO / TRENES DETECTADOS"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 75)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 130)
        layout.addWidget(self.table, 1)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setObjectName("btnSecondary")
        btn_cancel.clicked.connect(self.reject)
        self.btn_aplicar = QPushButton("⚡ Aplicar Seleccionados")
        self.btn_aplicar.clicked.connect(self._aplicar)
        btns.addWidget(btn_cancel)
        btns.addWidget(self.btn_aplicar)
        layout.addLayout(btns)

    def _actualizar_preview_obs(self) -> None:
        turno_str = self.txt_turno.text().strip() or "-"
        itin_str = self._itinerario_usado or self.combo_itinerario.currentText()
        if itin_str == "Vigente (más reciente)":
            itin_str = "-"
        trenes_ej = str(self._propuestas[0].get("detalle", "")) if self._propuestas else ""
        preview = formatear_observacion_turno(
            itinerario=itin_str,
            turno=turno_str,
            regimen=self.combo_regimen.currentText(),
            trenes=trenes_ej,
            plantilla=self.txt_plantilla_obs.text(),
        )
        self.lbl_obs_preview.setText(
            f"Vista previa: <i>{preview}</i>  |  Variables: <code>{{itinerario}}</code>, "
            f"<code>{{turno}}</code>, <code>{{regimen}}</code>, <code>{{trenes}}</code>"
        )

    def _analizar(self) -> None:
        turno_str = self.txt_turno.text().strip()
        reg_str = self.combo_regimen.currentText()
        itin_str = self.combo_itinerario.currentText()
        self.table.setRowCount(0)
        self._propuestas = []
        self._itinerario_usado = ""

        if not turno_str:
            self.lbl_resumen.setText("Ingrese un código de turno (ej. 401.1) y presione 'Analizar Turno'.")
            self.btn_aplicar.setEnabled(False)
            self._actualizar_preview_obs()
            return

        try:
            res = self.records_service.previsualizar_conocimientos_por_turno(
                int(self.empleado["legajo"]),
                codigo_turno=turno_str,
                regimen=None if reg_str == "Todos" else reg_str,
                itinerario=None if itin_str == "Vigente (más reciente)" else itin_str,
            )
        except Exception as e:
            self.lbl_resumen.setText(f"Error al analizar turno: {e}")
            self.btn_aplicar.setEnabled(False)
            self._actualizar_preview_obs()
            return

        propuestas = res.get("propuestas", [])
        vueltas = res.get("vueltas", [])
        self._propuestas = propuestas
        self._itinerario_usado = str(res.get("itinerario_usado") or "").strip()

        if not res.get("turnos_encontrados"):
            self.lbl_resumen.setText(
                f"No se encontró el turno '{turno_str}' en la base de itinerarios (turnos_itinerario)."
            )
            self.btn_aplicar.setEnabled(False)
            self._actualizar_preview_obs()
            return

        if not propuestas:
            self.lbl_resumen.setText(
                f"Se encontraron {len(vueltas)} vuelta(s) para '{turno_str}', pero no se pudieron mapear sectores de vía activos."
            )
            self.btn_aplicar.setEnabled(False)
            self._actualizar_preview_obs()
            return

        self.lbl_resumen.setText(
            f"Turno '{turno_str}' (Itinerario <b>{self._itinerario_usado or '-'}</b>): {len(vueltas)} vuelta(s) analizadas → "
            f"<b>{len(propuestas)}</b> sector(es)/sentido(s) detectados."
        )
        self.table.setRowCount(len(propuestas))
        for r, prop in enumerate(propuestas):
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk_item.setCheckState(Qt.Checked)
            self.table.setItem(r, 0, chk_item)

            sec_item = QTableWidgetItem(str(prop["codigo"]))
            sec_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 1, sec_item)

            sent_txt = "▲ Ascendente (ASC)" if prop["sentido"] == "ASC" else "▼ Descendente (DESC)"
            sent_item = QTableWidgetItem(sent_txt)
            sent_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 2, sent_item)

            self.table.setItem(r, 3, QTableWidgetItem(str(prop.get("detalle", ""))))

        self.btn_aplicar.setEnabled(True)
        self._actualizar_preview_obs()

    def _aplicar(self) -> None:
        seleccionados = []
        for r, prop in enumerate(self._propuestas):
            item_chk = self.table.item(r, 0)
            if item_chk and item_chk.checkState() == Qt.Checked:
                seleccionados.append(prop)
        if not seleccionados:
            QMessageBox.information(self, "Autocompletar", "Seleccione al menos un sector para aplicar.")
            return

        try:
            aplicados = self.records_service.aplicar_conocimientos_por_turno(
                legajo=int(self.empleado["legajo"]),
                items=seleccionados,
                fecha_conocimiento=self.date_fecha.date().toString("dd/MM/yyyy"),
                turno_referencia=self.txt_turno.text().strip(),
                itinerario=self._itinerario_usado,
                plantilla_observacion=self.txt_plantilla_obs.text().strip() or PLANTILLA_OBS_TURNO_DEFAULT,
                solo_si_mas_reciente=self.chk_no_sobrescribir.isChecked(),
                user_id=self.usuario_actual.get("id"),
                windows_user=self.usuario_actual.get("username"),
            )
            QMessageBox.information(
                self,
                "Conocimiento Actualizado",
                f"Se registraron {aplicados} conocimiento(s) de vía para {self.empleado.get('apellidos_nombres', '')}.",
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo aplicar el autocompletado:\n{e}")


class AutocompletarMasivoViaDialog(BaseDialog):
    """Previsualiza y aplica el cálculo masivo de conocimiento de vía para todo el personal de conducción."""

    def __init__(
        self,
        records_service: RecordsService,
        usuario_actual: Optional[Dict[str, Any]] = None,
        dotacion_actual: Optional[str] = None,
        especialidad_actual: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.records_service = records_service
        self.usuario_actual = usuario_actual or {}
        self.dotacion_actual = dotacion_actual
        self.especialidad_actual = especialidad_actual
        self._registros: List[Dict[str, Any]] = []
        self._registros_visibles: List[Dict[str, Any]] = []

        self.setWindowTitle("Cálculo Masivo de Conocimiento de Vía por Turno")
        self.resize(1060, 680)
        self._init_ui()
        self._previsualizar()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        lbl_intro = QLabel(
            "Calcula y marca como conocidas las vías de <b>todo el personal de conducción</b> en base a los turnos "
            "asignados en el padrón y el itinerario seleccionado, dejando constancia de las condiciones en las observaciones."
        )
        lbl_intro.setWordWrap(True)
        layout.addWidget(lbl_intro)

        # Fila 1 de parámetros
        bar1 = QHBoxLayout()
        bar1.addWidget(QLabel("Alcance:"))
        self.combo_alcance = QComboBox()
        self.combo_alcance.addItem("Todo el personal de conducción", "TODOS")
        if self.dotacion_actual or self.especialidad_actual:
            desc_filtro = " / ".join(
                p for p in (
                    f"Dotación: {self.dotacion_actual}" if self.dotacion_actual else "",
                    f"Esp.: {self.especialidad_actual}" if self.especialidad_actual else "",
                ) if p
            )
            self.combo_alcance.addItem(f"Solo filtro actual ({desc_filtro})", "FILTRO_ACTUAL")
        bar1.addWidget(self.combo_alcance)

        bar1.addWidget(QLabel("Itinerario:"))
        self.combo_itinerario = QComboBox()
        self.combo_itinerario.addItem("Vigente (más reciente)")
        for it in self.records_service.listar_itinerarios_disponibles():
            self.combo_itinerario.addItem(str(it))
        bar1.addWidget(self.combo_itinerario)

        bar1.addWidget(QLabel("Régimen:"))
        self.combo_regimen = QComboBox()
        self.combo_regimen.addItems(["Todos", "H", "S", "D"])
        bar1.addWidget(self.combo_regimen)

        bar1.addStretch()
        bar1.addWidget(QLabel("Fecha conocido:"))
        self.date_fecha = QDateEdit()
        self.date_fecha.setCalendarPopup(True)
        self.date_fecha.setDisplayFormat("dd/MM/yyyy")
        self.date_fecha.setDate(QDate.currentDate())
        bar1.addWidget(self.date_fecha)

        self.btn_previsualizar = QPushButton("🔍 Previsualizar")
        self.btn_previsualizar.setObjectName("btnSecondary")
        self.btn_previsualizar.clicked.connect(self._previsualizar)
        bar1.addWidget(self.btn_previsualizar)
        layout.addLayout(bar1)

        # Fila 2 de opciones
        bar2 = QHBoxLayout()
        self.chk_no_sobrescribir = QCheckBox(
            "No sobrescribir conocimientos que ya tengan fecha igual o más reciente"
        )
        self.chk_no_sobrescribir.setChecked(True)
        self.chk_no_sobrescribir.toggled.connect(self._previsualizar)
        bar2.addWidget(self.chk_no_sobrescribir)

        bar2.addStretch()
        self.chk_solo_listos = QCheckBox("Mostrar solo agentes listos para aplicar")
        self.chk_solo_listos.setChecked(False)
        self.chk_solo_listos.toggled.connect(self._poblar_tabla)
        bar2.addWidget(self.chk_solo_listos)
        layout.addLayout(bar2)

        # Panel de observación
        grp_obs = QGroupBox("Observación / Condiciones que se guardarán en cada conocimiento")
        lay_obs = QVBoxLayout(grp_obs)
        lay_obs.setSpacing(4)
        self.txt_plantilla_obs = QLineEdit(PLANTILLA_OBS_TURNO_DEFAULT)
        self.txt_plantilla_obs.setPlaceholderText(PLANTILLA_OBS_TURNO_DEFAULT)
        self.txt_plantilla_obs.textChanged.connect(self._actualizar_preview_obs)
        lay_obs.addWidget(self.txt_plantilla_obs)
        self.lbl_obs_preview = QLabel("")
        self.lbl_obs_preview.setStyleSheet("color: #6B7280; font-size: 8.5pt;")
        self.lbl_obs_preview.setWordWrap(True)
        lay_obs.addWidget(self.lbl_obs_preview)
        layout.addWidget(grp_obs)

        self.lbl_resumen = QLabel("")
        self.lbl_resumen.setStyleSheet("font-weight: 600;")
        self.lbl_resumen.setWordWrap(True)
        layout.addWidget(self.lbl_resumen)

        # Tabla de previsualización
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "APLICAR",
            "LEGAJO",
            "APELLIDOS Y NOMBRES",
            "DOTACIÓN",
            "TURNO",
            "ITINERARIO",
            "SECTORES",
            "ESTADO / MOTIVO",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 68)
        self.table.setColumnWidth(1, 75)
        self.table.setColumnWidth(2, 210)
        self.table.setColumnWidth(3, 85)
        self.table.setColumnWidth(4, 80)
        self.table.setColumnWidth(5, 85)
        self.table.setColumnWidth(6, 95)
        layout.addWidget(self.table, 1)

        # Botonera inferior
        btns = QHBoxLayout()
        btn_sel_todos = QPushButton("☑️ Seleccionar todos los listos")
        btn_sel_todos.setObjectName("btnSecondary")
        btn_sel_todos.clicked.connect(lambda: self._marcar_todos(True))
        btn_desel = QPushButton("☐ Deseleccionar todos")
        btn_desel.setObjectName("btnSecondary")
        btn_desel.clicked.connect(lambda: self._marcar_todos(False))
        btns.addWidget(btn_sel_todos)
        btns.addWidget(btn_desel)
        btns.addStretch()

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setObjectName("btnSecondary")
        btn_cancel.clicked.connect(self.reject)
        self.btn_aplicar = QPushButton("⚡⚡ Aplicar a Seleccionados")
        self.btn_aplicar.clicked.connect(self._aplicar)
        btns.addWidget(btn_cancel)
        btns.addWidget(self.btn_aplicar)
        layout.addLayout(btns)

    def _actualizar_preview_obs(self) -> None:
        ejemplo_reg = next((r for r in self._registros if r.get("estado") == "LISTO"), None)
        if not ejemplo_reg and self._registros:
            ejemplo_reg = next((r for r in self._registros if r.get("turno")), None)
        itin_ej = (ejemplo_reg or {}).get("itinerario") or "45"
        turno_ej = (ejemplo_reg or {}).get("turno") or "401.1"
        reg_ej = (ejemplo_reg or {}).get("regimen") or "H"
        props = (ejemplo_reg or {}).get("propuestas_aplicables") or []
        trenes_ej = str(props[0].get("detalle", "")) if props else "Tren 3555 (PC → GW)"
        preview = formatear_observacion_turno(
            itinerario=itin_ej,
            turno=turno_ej,
            regimen=reg_ej,
            trenes=trenes_ej,
            plantilla=self.txt_plantilla_obs.text(),
        )
        self.lbl_obs_preview.setText(
            f"Vista previa ejemplo: <i>{preview}</i>  |  Variables: <code>{{itinerario}}</code>, "
            f"<code>{{turno}}</code>, <code>{{regimen}}</code>, <code>{{trenes}}</code>"
        )

    def _previsualizar(self) -> None:
        alcance = self.combo_alcance.currentData()
        dot = self.dotacion_actual if alcance == "FILTRO_ACTUAL" else None
        esp = self.especialidad_actual if alcance == "FILTRO_ACTUAL" else None
        itin = self.combo_itinerario.currentText()
        reg = self.combo_regimen.currentText()

        try:
            res = self.records_service.previsualizar_conocimientos_masivo(
                dotacion=dot,
                especialidad=esp,
                itinerario=None if itin == "Vigente (más reciente)" else itin,
                regimen=None if reg == "Todos" else reg,
                fecha_conocimiento=self.date_fecha.date().toString("dd/MM/yyyy"),
                plantilla_observacion=self.txt_plantilla_obs.text().strip() or PLANTILLA_OBS_TURNO_DEFAULT,
                solo_si_mas_reciente=self.chk_no_sobrescribir.isChecked(),
            )
        except Exception as e:
            self._registros = []
            self.table.setRowCount(0)
            self.lbl_resumen.setText(f"Error al previsualizar cálculo masivo: {e}")
            self.btn_aplicar.setEnabled(False)
            self._actualizar_preview_obs()
            return

        self._registros = res.get("registros", [])
        self.lbl_resumen.setText(
            f"Personal evaluado: {res.get('total_personal', 0)}  |  "
            f"Listos para aplicar: {res.get('agentes_listos', 0)} "
            f"({res.get('total_conocimientos_a_aplicar', 0)} conocimientos)  |  "
            f"Sin cambios (fecha reciente): {res.get('agentes_sin_cambios', 0)}  |  "
            f"Omitidos: {res.get('agentes_omitidos', 0)}"
        )
        self._poblar_tabla()
        self._actualizar_preview_obs()

    def _poblar_tabla(self) -> None:
        solo_listos = self.chk_solo_listos.isChecked()
        self._registros_visibles = [
            r for r in self._registros if (not solo_listos or r.get("estado") == "LISTO")
        ]
        self.table.setRowCount(len(self._registros_visibles))

        hay_listos = False
        for row_idx, reg in enumerate(self._registros_visibles):
            es_listo = reg.get("estado") == "LISTO"
            if es_listo:
                hay_listos = True

            chk_item = QTableWidgetItem()
            if es_listo:
                chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                chk_item.setCheckState(Qt.Checked)
            else:
                chk_item.setFlags(Qt.ItemIsSelectable)
                chk_item.setCheckState(Qt.Unchecked)
            self.table.setItem(row_idx, 0, chk_item)

            it_leg = QTableWidgetItem(str(reg.get("legajo", "")))
            it_leg.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row_idx, 1, it_leg)

            self.table.setItem(row_idx, 2, QTableWidgetItem(str(reg.get("apellidos_nombres", ""))))

            it_dot = QTableWidgetItem(str(reg.get("dotacion", "") or "-"))
            it_dot.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row_idx, 3, it_dot)

            it_tur = QTableWidgetItem(str(reg.get("turno", "") or "-"))
            it_tur.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row_idx, 4, it_tur)

            it_iti = QTableWidgetItem(str(reg.get("itinerario", "") or "-"))
            it_iti.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row_idx, 5, it_iti)

            cant_ap = len(reg.get("propuestas_aplicables") or [])
            cant_tot = int(reg.get("total_detectados", 0))
            it_sec = QTableWidgetItem(f"{cant_ap} / {cant_tot}" if cant_tot else "0")
            it_sec.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row_idx, 6, it_sec)

            self.table.setItem(row_idx, 7, QTableWidgetItem(str(reg.get("motivo", ""))))

        self.btn_aplicar.setEnabled(hay_listos)

    def _marcar_todos(self, checked: bool) -> None:
        estado = Qt.Checked if checked else Qt.Unchecked
        for row_idx, reg in enumerate(self._registros_visibles):
            if reg.get("estado") != "LISTO":
                continue
            item = self.table.item(row_idx, 0)
            if item:
                item.setCheckState(estado)

    def _aplicar(self) -> None:
        seleccionados: List[Dict[str, Any]] = []
        total_conoc = 0
        for row_idx, reg in enumerate(self._registros_visibles):
            if reg.get("estado") != "LISTO":
                continue
            item = self.table.item(row_idx, 0)
            if item and item.checkState() == Qt.Checked:
                seleccionados.append(reg)
                total_conoc += len(reg.get("propuestas_aplicables") or [])

        if not seleccionados:
            QMessageBox.information(
                self,
                "Cálculo Masivo",
                "Seleccione al menos un agente listo para aplicar el cálculo masivo.",
            )
            return

        fecha_str = self.date_fecha.date().toString("dd/MM/yyyy")
        plantilla = self.txt_plantilla_obs.text().strip() or PLANTILLA_OBS_TURNO_DEFAULT
        resp = QMessageBox.question(
            self,
            "Confirmar Cálculo Masivo",
            f"Se actualizarán <b>{total_conoc}</b> conocimiento(s) de vía para <b>{len(seleccionados)}</b> "
            f"agente(s) con fecha <b>{fecha_str}</b>.<br><br>"
            f"¿Desea continuar?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if resp != QMessageBox.Yes:
            return

        try:
            res = self.records_service.aplicar_conocimientos_masivo(
                registros_seleccionados=seleccionados,
                fecha_conocimiento=fecha_str,
                plantilla_observacion=plantilla,
                solo_si_mas_reciente=self.chk_no_sobrescribir.isChecked(),
                user_id=self.usuario_actual.get("id"),
                windows_user=self.usuario_actual.get("username"),
            )
            QMessageBox.information(
                self,
                "Cálculo Masivo Completado",
                f"Agentes actualizados: {res['agentes_actualizados']}\n"
                f"Conocimientos registrados/actualizados: {res['conocimientos_guardados']}\n"
                f"Omitidos por fecha más reciente: {res['omitidos_fecha_reciente']}",
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo completar el cálculo masivo:\n{e}")

