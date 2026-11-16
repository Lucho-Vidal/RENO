"""Diálogo CRUD e Importador Excel para Estaciones Intermedias, Horarios por Tren y Catálogo de Estaciones."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.config import obtener_usuario_windows
from src.services.records_service import RecordsService
from src.ui.views.dialogs.base_dialog import BaseDialog, BaseMaximizedDialog


class AdminEstacionesDialog(BaseDialog):
    """Administración CRUD e importación Excel del catálogo de estaciones y sus abreviaturas/siglas."""

    def __init__(
        self,
        records_service: RecordsService,
        usuario_actual: Optional[Dict[str, Any]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.records_service = records_service
        self.usuario_actual = usuario_actual or {}
        self._estacion_editando_id: Optional[int] = None
        self._datos_cache: List[Dict[str, Any]] = []
        self.hubo_cambios: bool = False

        self.setWindowTitle("Catálogo de Estaciones, Siglas y Abreviaturas")
        self.resize(980, 580)

        self._init_ui()
        self.refrescar_tabla()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        bar_top = QHBoxLayout()
        bar_top.addWidget(QLabel("Buscar:"))
        self.txt_buscar = QLineEdit()
        self.txt_buscar.setPlaceholderText("Nombre, sigla (TY, PC, ALL), alias o código...")
        self.txt_buscar.textChanged.connect(self.refrescar_tabla)
        bar_top.addWidget(self.txt_buscar, 1)

        self.chk_inactivos = QCheckBox("Incluir Inactivas")
        self.chk_inactivos.setChecked(True)
        self.chk_inactivos.stateChanged.connect(self.refrescar_tabla)
        bar_top.addWidget(self.chk_inactivos)

        self.btn_importar_catalogo = QPushButton("📥 Importar Catálogo (Excel)")
        self.btn_importar_catalogo.setObjectName("btnSecondary")
        self.btn_importar_catalogo.clicked.connect(self._importar_catalogo_excel)
        bar_top.addWidget(self.btn_importar_catalogo)

        layout.addLayout(bar_top)

        splitter = QSplitter(Qt.Horizontal)

        left_w = QWidget()
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 0, 0)

        self.lbl_contador = QLabel("0 estaciones registradas")
        self.lbl_contador.setStyleSheet("font-weight: bold;")
        left_lay.addWidget(self.lbl_contador)

        self.tabla = QTableWidget(0, 6)
        self.tabla.setHorizontalHeaderLabels([
            "ID", "CÓD.", "ESTACIÓN", "SIGLA", "ALIAS / ABREVIATURAS", "ESTADO",
        ])
        self.tabla.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.tabla.setColumnWidth(0, 48)
        self.tabla.setColumnWidth(1, 52)
        self.tabla.setColumnWidth(2, 170)
        self.tabla.setColumnWidth(3, 65)
        self.tabla.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tabla.setColumnWidth(5, 75)
        self.tabla.setSelectionBehavior(QTableWidget.SelectRows)
        self.tabla.setSelectionMode(QTableWidget.SingleSelection)
        self.tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabla.itemSelectionChanged.connect(self._on_seleccion_tabla)
        left_lay.addWidget(self.tabla)

        splitter.addWidget(left_w)

        grp_form = QGroupBox("Datos de la Estación")
        grp_form.setMaximumWidth(360)
        form_lay = QVBoxLayout(grp_form)

        f_grid = QFormLayout()
        self.txt_codigo = QLineEdit()
        self.txt_codigo.setPlaceholderText("Opcional (ej. 9)")
        self.txt_codigo.setMaximumWidth(110)
        f_grid.addRow("Cód. Origen:", self.txt_codigo)

        self.txt_nombre = QLineEdit()
        self.txt_nombre.setPlaceholderText("Ej: Témperley")
        f_grid.addRow("Nombre (*):", self.txt_nombre)

        self.txt_sigla = QLineEdit()
        self.txt_sigla.setPlaceholderText("Ej: TY")
        self.txt_sigla.setMaximumWidth(110)
        f_grid.addRow("Sigla / Abrev.:", self.txt_sigla)

        self.txt_aliases = QLineEdit()
        self.txt_aliases.setPlaceholderText("Ej: Temperley, Temp.")
        f_grid.addRow("Alias (coma):", self.txt_aliases)

        form_lay.addLayout(f_grid)
        form_lay.addSpacing(8)

        self.lbl_modo = QLabel("Modo: Crear nueva estación")
        self.lbl_modo.setStyleSheet("font-weight: bold; color: #1F497D;")
        form_lay.addWidget(self.lbl_modo)

        self.btn_guardar = QPushButton("💾 Guardar Estación")
        self.btn_guardar.clicked.connect(self._guardar_estacion)
        form_lay.addWidget(self.btn_guardar)

        self.btn_nueva = QPushButton("➕ Nueva / Limpiar")
        self.btn_nueva.setObjectName("btnSecondary")
        self.btn_nueva.clicked.connect(self._limpiar_formulario)
        form_lay.addWidget(self.btn_nueva)

        self.btn_toggle = QPushButton("🔄 Activar / Desactivar")
        self.btn_toggle.setObjectName("btnSecondary")
        self.btn_toggle.setEnabled(False)
        self.btn_toggle.clicked.connect(self._toggle_estado)
        form_lay.addWidget(self.btn_toggle)

        form_lay.addStretch()

        lbl_ayuda = QLabel(
            "Importe un Excel con columnas Id, Nombre, Sigla (opcional Alias)\n"
            "o agregue alias manualmente separados por coma para vincular\n"
            "nombres completos de itinerarios con siglas de turnos."
        )
        lbl_ayuda.setWordWrap(True)
        lbl_ayuda.setStyleSheet("color: #666; font-size: 11px;")
        form_lay.addWidget(lbl_ayuda)

        splitter.addWidget(grp_form)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        bottom_lay = QHBoxLayout()
        bottom_lay.addStretch()
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.setObjectName("btnSecondary")
        btn_cerrar.clicked.connect(self.accept)
        bottom_lay.addWidget(btn_cerrar)
        layout.addLayout(bottom_lay)

    def refrescar_tabla(self) -> None:
        criterio = self.txt_buscar.text().strip()
        inc = self.chk_inactivos.isChecked()
        self._datos_cache = self.records_service.listar_estaciones(
            criterio=criterio,
            incluir_inactivos=inc,
        )
        self.tabla.setRowCount(len(self._datos_cache))
        for r, item in enumerate(self._datos_cache):
            self.tabla.setItem(r, 0, QTableWidgetItem(str(item["id"])))
            cod = item.get("codigo_origen")
            self.tabla.setItem(r, 1, QTableWidgetItem(str(cod) if cod is not None else "-"))
            self.tabla.setItem(r, 2, QTableWidgetItem(str(item.get("nombre") or "")))
            self.tabla.setItem(r, 3, QTableWidgetItem(str(item.get("sigla") or "")))
            self.tabla.setItem(r, 4, QTableWidgetItem(str(item.get("aliases") or "")))
            self.tabla.setItem(r, 5, QTableWidgetItem("Activa" if item.get("activo") else "Inactiva"))

        self.lbl_contador.setText(f"{len(self._datos_cache)} estaciones mostradas")

    def _on_seleccion_tabla(self) -> None:
        sel = self.tabla.selectionModel().selectedRows()
        if not sel:
            self._limpiar_formulario()
            return
        idx = sel[0].row()
        if idx < 0 or idx >= len(self._datos_cache):
            return
        item = self._datos_cache[idx]
        self._estacion_editando_id = int(item["id"])
        cod = item.get("codigo_origen")
        self.txt_codigo.setText(str(cod) if cod is not None else "")
        self.txt_nombre.setText(str(item.get("nombre") or ""))
        self.txt_sigla.setText(str(item.get("sigla") or ""))
        self.txt_aliases.setText(str(item.get("aliases") or ""))
        self.lbl_modo.setText(f"Modo: Editando estación #{self._estacion_editando_id}")
        self.btn_toggle.setEnabled(True)

    def _limpiar_formulario(self) -> None:
        self._estacion_editando_id = None
        self.txt_codigo.clear()
        self.txt_nombre.clear()
        self.txt_sigla.clear()
        self.txt_aliases.clear()
        self.lbl_modo.setText("Modo: Crear nueva estación")
        self.btn_toggle.setEnabled(False)

    def _guardar_estacion(self) -> None:
        datos = {
            "codigo_origen": self.txt_codigo.text().strip(),
            "nombre": self.txt_nombre.text().strip(),
            "sigla": self.txt_sigla.text().strip().upper(),
            "aliases": self.txt_aliases.text().strip(),
        }
        try:
            self.records_service.guardar_estacion(
                datos,
                estacion_id=self._estacion_editando_id,
                user_id=self.usuario_actual.get("id"),
                windows_user=obtener_usuario_windows(),
            )
            self.hubo_cambios = True
            self._limpiar_formulario()
            self.refrescar_tabla()
        except Exception as e:
            QMessageBox.warning(self, "Validación", str(e))

    def _toggle_estado(self) -> None:
        if self._estacion_editando_id is None:
            return
        try:
            self.records_service.cambiar_estado_estacion(
                self._estacion_editando_id,
                user_id=self.usuario_actual.get("id"),
                windows_user=obtener_usuario_windows(),
            )
            self.hubo_cambios = True
            self.refrescar_tabla()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _importar_catalogo_excel(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar Catálogo de Estaciones (Excel)",
            "",
            "Archivos Excel (*.xlsx *.xlsm)",
        )
        if not file_path:
            return
        try:
            ins, upd = self.records_service.importar_estaciones_excel(
                file_path,
                user_id=self.usuario_actual.get("id"),
                windows_user=obtener_usuario_windows(),
            )
            self.hubo_cambios = True
            self.refrescar_tabla()
            QMessageBox.information(
                self,
                "Catálogo Importado",
                f"Catálogo actualizado: {ins} nuevas estaciones y {upd} actualizadas.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error al Importar", f"No se pudo importar el catálogo:\n{e}")


class AdminTrenParadasDialog(BaseMaximizedDialog):
    """Administración CRUD e importación desde Excel de estaciones intermedias por tren (`tren_paradas`)."""

    def __init__(
        self,
        records_service: RecordsService,
        usuario_actual: Optional[Dict[str, Any]] = None,
        tren_inicial: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.records_service = records_service
        self.usuario_actual = usuario_actual or {}
        self._parada_editando_id: Optional[int] = None
        self._datos_cache: List[Dict[str, Any]] = []

        self.setWindowTitle("Gestión de Estaciones Intermedias y Horarios de Paso por Tren")
        self.resize(1140, 700)

        self._init_ui(tren_inicial)
        self._recargar_sugerencias_estaciones()
        self.refrescar_tabla()

    def _init_ui(self, tren_inicial: str) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Barra de Filtros y Acciones Superiores
        grp_filtros = QGroupBox("Filtros de Búsqueda e Importación Masiva")
        lay_f = QHBoxLayout(grp_filtros)

        lay_f.addWidget(QLabel("N° Tren:"))
        self.txt_filtro_tren = QLineEdit(tren_inicial)
        self.txt_filtro_tren.setPlaceholderText("Ej: 1293, 4603...")
        self.txt_filtro_tren.setMaximumWidth(120)
        self.txt_filtro_tren.textChanged.connect(self.refrescar_tabla)
        lay_f.addWidget(self.txt_filtro_tren)

        lay_f.addWidget(QLabel("Estación / Sigla:"))
        self.txt_filtro_estacion = QLineEdit()
        self.txt_filtro_estacion.setPlaceholderText("Ej: Temperley, TY, ALL, PC...")
        self.txt_filtro_estacion.setMinimumWidth(180)
        self.txt_filtro_estacion.setMaximumWidth(230)
        self.txt_filtro_estacion.textChanged.connect(self.refrescar_tabla)
        lay_f.addWidget(self.txt_filtro_estacion)

        lay_f.addWidget(QLabel("Itinerario:"))
        self.combo_filtro_itin = QComboBox()
        self.combo_filtro_itin.addItem("Todos")
        for it in self.records_service.listar_itinerarios_paradas():
            self.combo_filtro_itin.addItem(it)
        self.combo_filtro_itin.currentIndexChanged.connect(self.refrescar_tabla)
        lay_f.addWidget(self.combo_filtro_itin)

        lay_f.addWidget(QLabel("Régimen:"))
        self.combo_filtro_regimen = QComboBox()
        self.combo_filtro_regimen.addItems(["Todos", "LV", "S", "D", "DIAGRAMA_6D", "UNIPERSONAL"])
        self.combo_filtro_regimen.currentIndexChanged.connect(self.refrescar_tabla)
        lay_f.addWidget(self.combo_filtro_regimen)

        self.btn_limpiar_filtros = QPushButton("Limpiar Filtros")
        self.btn_limpiar_filtros.setObjectName("btnSecondary")
        self.btn_limpiar_filtros.clicked.connect(self._limpiar_filtros)
        lay_f.addWidget(self.btn_limpiar_filtros)

        lay_f.addStretch()

        self.btn_estaciones = QPushButton("🏷️ Estaciones / Abrev.")
        self.btn_estaciones.setObjectName("btnSecondary")
        self.btn_estaciones.clicked.connect(self._abrir_catalogo_estaciones)
        lay_f.addWidget(self.btn_estaciones)

        self.btn_importar_excel = QPushButton("📥 Importar Paradas desde Excel")
        self.btn_importar_excel.clicked.connect(self._importar_excel)
        lay_f.addWidget(self.btn_importar_excel)

        layout.addWidget(grp_filtros)

        # Splitter Principal: Tabla a la izquierda + Formulario CRUD a la derecha
        splitter = QSplitter(Qt.Horizontal)

        # Panel Izquierdo: Tabla
        left_widget = QWidget()
        left_lay = QVBoxLayout(left_widget)
        left_lay.setContentsMargins(0, 0, 0, 0)

        self.lbl_contador = QLabel("0 paradas registradas")
        self.lbl_contador.setStyleSheet("font-weight: bold;")
        left_lay.addWidget(self.lbl_contador)

        self.tabla = QTableWidget(0, 8)
        self.tabla.setHorizontalHeaderLabels([
            "ID", "N° TREN", "ORDEN", "ESTACIÓN / PARADA", "SIGLA", "HORA PASO", "ITINERARIO", "RÉGIMEN"
        ])
        self.tabla.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.tabla.setColumnWidth(0, 55)
        self.tabla.setColumnWidth(1, 75)
        self.tabla.setColumnWidth(2, 60)
        self.tabla.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tabla.setColumnWidth(4, 65)
        self.tabla.setColumnWidth(5, 80)
        self.tabla.setColumnWidth(6, 85)
        self.tabla.setColumnWidth(7, 75)
        self.tabla.setSelectionBehavior(QTableWidget.SelectRows)
        self.tabla.setSelectionMode(QTableWidget.SingleSelection)
        self.tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabla.itemSelectionChanged.connect(self._on_seleccion_tabla)
        left_lay.addWidget(self.tabla)

        splitter.addWidget(left_widget)

        # Panel Derecho: Formulario CRUD
        grp_form = QGroupBox("Formulario de Parada / Horario de Paso")
        grp_form.setMaximumWidth(380)
        form_lay = QVBoxLayout(grp_form)

        f_grid = QFormLayout()
        self.txt_nro_tren = QLineEdit()
        self.txt_nro_tren.setPlaceholderText("Ej: 1293")
        f_grid.addRow("N° de Tren (*):", self.txt_nro_tren)

        self.spin_orden = QSpinBox()
        self.spin_orden.setRange(1, 100)
        self.spin_orden.setValue(1)
        f_grid.addRow("Orden de Parada:", self.spin_orden)

        self.txt_estacion = QLineEdit()
        self.txt_estacion.setPlaceholderText("Ej: P. Constitución, TY, Temperley...")
        self.txt_estacion.textChanged.connect(self._on_estacion_form_changed)
        f_grid.addRow("Estación / Sigla (*):", self.txt_estacion)

        self.lbl_estacion_resuelta = QLabel("Acepta nombre completo, alias o sigla (ej: TY, PC, ALL)")
        self.lbl_estacion_resuelta.setWordWrap(True)
        self.lbl_estacion_resuelta.setStyleSheet("color: #64748B; font-size: 11px;")
        f_grid.addRow("", self.lbl_estacion_resuelta)

        self.txt_hora_paso = QLineEdit()
        self.txt_hora_paso.setPlaceholderText("HH:MM (Ej: 08:15)")
        f_grid.addRow("Hora de Paso (*):", self.txt_hora_paso)

        self.combo_itin = QComboBox()
        self.combo_itin.setEditable(True)
        for it in self.records_service.listar_itinerarios_paradas():
            self.combo_itin.addItem(it)
        if self.combo_itin.count() == 0:
            self.combo_itin.addItem("H45")
        f_grid.addRow("Itinerario:", self.combo_itin)

        self.combo_regimen = QComboBox()
        self.combo_regimen.addItems(["LV", "S", "D", "DIAGRAMA_6D", "UNIPERSONAL"])
        f_grid.addRow("Régimen:", self.combo_regimen)

        form_lay.addLayout(f_grid)
        form_lay.addSpacing(10)

        self.lbl_modo_form = QLabel("Modo: Crear nueva parada")
        self.lbl_modo_form.setStyleSheet("font-weight: bold; color: #1F497D;")
        form_lay.addWidget(self.lbl_modo_form)

        btn_guardar = QPushButton("💾 Guardar Parada")
        btn_guardar.clicked.connect(self._guardar_parada)
        form_lay.addWidget(btn_guardar)

        btn_nuevo = QPushButton("➕ Limpiar / Nueva Parada")
        btn_nuevo.setObjectName("btnSecondary")
        btn_nuevo.clicked.connect(self._limpiar_formulario)
        form_lay.addWidget(btn_nuevo)

        self.btn_eliminar = QPushButton("🗑️ Eliminar Parada Seleccionada")
        self.btn_eliminar.setObjectName("btnDanger")
        self.btn_eliminar.setEnabled(False)
        self.btn_eliminar.clicked.connect(self._eliminar_parada)
        form_lay.addWidget(self.btn_eliminar)

        form_lay.addStretch()

        lbl_ayuda = QLabel(
            "Nota: Soporta libros Excel con múltiples hojas por ramal/régimen\n"
            "(ej. Hab ASC/DES, Sab, DOM), matrices con múltiples trenes,\n"
            "planillas TREN/ESTACION/HORA y búsqueda por abreviatura o sigla."
        )
        lbl_ayuda.setWordWrap(True)
        lbl_ayuda.setStyleSheet("color: #666; font-size: 11px;")
        form_lay.addWidget(lbl_ayuda)

        splitter.addWidget(grp_form)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        # Pie de cierre
        bottom_lay = QHBoxLayout()
        bottom_lay.addStretch()
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.setObjectName("btnSecondary")
        btn_cerrar.clicked.connect(self.accept)
        bottom_lay.addWidget(btn_cerrar)
        layout.addLayout(bottom_lay)

    def _recargar_sugerencias_estaciones(self) -> None:
        sugerencias = self.records_service.obtener_sugerencias_estaciones()
        for line_edit in (self.txt_filtro_estacion, self.txt_estacion):
            comp = QCompleter(sugerencias, line_edit)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setFilterMode(Qt.MatchContains)
            comp.setCompletionMode(QCompleter.PopupCompletion)
            line_edit.setCompleter(comp)
        self._on_estacion_form_changed(self.txt_estacion.text())

    def _on_estacion_form_changed(self, texto: str) -> None:
        raw = str(texto or "").strip()
        if not raw:
            self.lbl_estacion_resuelta.setText("Acepta nombre completo, alias o sigla (ej: TY, PC, ALL)")
            self.lbl_estacion_resuelta.setStyleSheet("color: #64748B; font-size: 11px;")
            return

        idx = self.records_service._obtener_indice_estaciones()
        cands = idx.resolver_estaciones_candidatas(raw)
        if not cands:
            self.lbl_estacion_resuelta.setText("Sin coincidencia en catálogo (se guardará el texto ingresado)")
            self.lbl_estacion_resuelta.setStyleSheet("color: #64748B; font-size: 11px;")
        elif len(cands) == 1:
            st = cands[0]
            sig = f" · Sigla: {st['sigla']}" if st.get("sigla") else ""
            if st["nombre"].lower() != raw.lower():
                self.lbl_estacion_resuelta.setText(f"Equivale a: {st['nombre']}{sig}")
            else:
                self.lbl_estacion_resuelta.setText(f"Estación: {st['nombre']}{sig}")
            self.lbl_estacion_resuelta.setStyleSheet("color: #1D4ED8; font-size: 11px;")
        else:
            desc = ", ".join(f"{s['nombre']} ({s['sigla']})" if s.get("sigla") else s["nombre"] for s in cands[:3])
            self.lbl_estacion_resuelta.setText(f"Sigla compartida: {desc}")
            self.lbl_estacion_resuelta.setStyleSheet("color: #1D4ED8; font-size: 11px;")

    def _refrescar_combos_itinerarios(self) -> None:
        itis = self.records_service.listar_itinerarios_paradas()

        prev_f = self.combo_filtro_itin.currentText()
        self.combo_filtro_itin.blockSignals(True)
        self.combo_filtro_itin.clear()
        self.combo_filtro_itin.addItem("Todos")
        for it in itis:
            self.combo_filtro_itin.addItem(it)
        idx_f = self.combo_filtro_itin.findText(prev_f)
        self.combo_filtro_itin.setCurrentIndex(idx_f if idx_f >= 0 else 0)
        self.combo_filtro_itin.blockSignals(False)

        prev_form = self.combo_itin.currentText().strip()
        self.combo_itin.blockSignals(True)
        self.combo_itin.clear()
        for it in itis:
            self.combo_itin.addItem(it)
        if self.combo_itin.count() == 0:
            self.combo_itin.addItem("H45")
        if prev_form:
            self.combo_itin.setCurrentText(prev_form)
        self.combo_itin.blockSignals(False)

    def _limpiar_filtros(self) -> None:
        self.txt_filtro_tren.blockSignals(True)
        self.txt_filtro_estacion.blockSignals(True)
        self.combo_filtro_itin.blockSignals(True)
        self.combo_filtro_regimen.blockSignals(True)

        self.txt_filtro_tren.clear()
        self.txt_filtro_estacion.clear()
        self.combo_filtro_itin.setCurrentIndex(0)
        self.combo_filtro_regimen.setCurrentIndex(0)

        self.txt_filtro_tren.blockSignals(False)
        self.txt_filtro_estacion.blockSignals(False)
        self.combo_filtro_itin.blockSignals(False)
        self.combo_filtro_regimen.blockSignals(False)
        self.refrescar_tabla()

    def refrescar_tabla(self) -> None:
        nt = self.txt_filtro_tren.text().strip()
        est = self.txt_filtro_estacion.text().strip()
        itin = self.combo_filtro_itin.currentText().strip()
        reg = self.combo_filtro_regimen.currentText().strip()

        self._datos_cache = self.records_service.listar_paradas_tren(
            nro_tren=nt or None,
            itinerario=itin if itin != "Todos" else None,
            estacion=est or None,
            regimen=reg if reg != "Todos" else None,
            limite=5000,
        )

        self.tabla.setRowCount(len(self._datos_cache))
        for r, item in enumerate(self._datos_cache):
            self.tabla.setItem(r, 0, QTableWidgetItem(str(item["id"])))
            self.tabla.setItem(r, 1, QTableWidgetItem(str(item["nro_tren"])))
            self.tabla.setItem(r, 2, QTableWidgetItem(str(item["orden_parada"])))
            self.tabla.setItem(r, 3, QTableWidgetItem(str(item["estacion"])))
            self.tabla.setItem(r, 4, QTableWidgetItem(str(item.get("sigla") or "")))
            self.tabla.setItem(r, 5, QTableWidgetItem(str(item["hora_paso"])))
            self.tabla.setItem(r, 6, QTableWidgetItem(str(item.get("itinerario") or "")))
            self.tabla.setItem(r, 7, QTableWidgetItem(str(item.get("regimen") or "LV")))

        self.lbl_contador.setText(f"{len(self._datos_cache)} paradas mostradas")

    def _on_seleccion_tabla(self) -> None:
        sel = self.tabla.selectionModel().selectedRows()
        if not sel:
            self._limpiar_formulario()
            return
        idx = sel[0].row()
        if idx < 0 or idx >= len(self._datos_cache):
            return
        item = self._datos_cache[idx]
        self._parada_editando_id = int(item["id"])
        self.txt_nro_tren.setText(str(item["nro_tren"]))
        self.spin_orden.setValue(int(item.get("orden_parada") or 1))
        self.txt_estacion.setText(str(item["estacion"]))
        self.txt_hora_paso.setText(str(item["hora_paso"]))
        self.combo_itin.setCurrentText(str(item.get("itinerario") or ""))
        reg = str(item.get("regimen") or "LV").strip()
        idx_r = self.combo_regimen.findText(reg)
        if idx_r >= 0:
            self.combo_regimen.setCurrentIndex(idx_r)
        self.lbl_modo_form.setText(f"Modo: Editando parada ID #{self._parada_editando_id}")
        self.btn_eliminar.setEnabled(True)

    def _limpiar_formulario(self) -> None:
        self._parada_editando_id = None
        if not self.txt_filtro_tren.text().strip():
            self.txt_nro_tren.clear()
        else:
            self.txt_nro_tren.setText(self.txt_filtro_tren.text().strip())
        self.spin_orden.setValue(1)
        self.txt_estacion.clear()
        self.txt_hora_paso.clear()
        self.lbl_modo_form.setText("Modo: Crear nueva parada")
        self.btn_eliminar.setEnabled(False)

    def _guardar_parada(self) -> None:
        datos = {
            "nro_tren": self.txt_nro_tren.text().strip(),
            "orden_parada": self.spin_orden.value(),
            "estacion": self.txt_estacion.text().strip(),
            "hora_paso": self.txt_hora_paso.text().strip(),
            "itinerario": self.combo_itin.currentText().strip(),
            "regimen": self.combo_regimen.currentText().strip(),
        }
        try:
            win_usr = obtener_usuario_windows()
            uid = self.usuario_actual.get("id")
            if self._parada_editando_id is not None:
                self.records_service.actualizar_parada_tren(
                    self._parada_editando_id, datos, user_id=uid, windows_user=win_usr
                )
            else:
                self.records_service.crear_parada_tren(
                    datos, user_id=uid, windows_user=win_usr
                )
            self._refrescar_combos_itinerarios()
            self._limpiar_formulario()
            self.refrescar_tabla()
        except Exception as e:
            QMessageBox.warning(self, "Validación", str(e))

    def _eliminar_parada(self) -> None:
        if self._parada_editando_id is None:
            return
        resp = QMessageBox.question(
            self,
            "Confirmar Eliminación",
            f"¿Está seguro que desea eliminar la parada ID #{self._parada_editando_id}?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        try:
            self.records_service.eliminar_parada_tren(
                self._parada_editando_id,
                user_id=self.usuario_actual.get("id"),
                windows_user=obtener_usuario_windows(),
            )
            self._limpiar_formulario()
            self.refrescar_tabla()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _abrir_catalogo_estaciones(self) -> None:
        dlg = AdminEstacionesDialog(
            self.records_service,
            usuario_actual=self.usuario_actual,
            parent=self,
        )
        dlg.exec()
        if dlg.hubo_cambios:
            self._recargar_sugerencias_estaciones()
            self.refrescar_tabla()

    def _importar_excel(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar Planilla Excel de Paradas / Horarios por Tren",
            "",
            "Archivos Excel (*.xlsx *.xlsm)",
        )
        if not file_path:
            return
        try:
            itin_inferido = self.records_service.inferir_itinerario_desde_archivo(file_path)
            itin_def = itin_inferido or self.combo_itin.currentText().strip() or "H45"
            insertados = self.records_service.importar_paradas_tren_excel(
                file_path,
                itinerario_default=itin_def,
                user_id=self.usuario_actual.get("id"),
                windows_user=obtener_usuario_windows(),
            )
            self._refrescar_combos_itinerarios()
            self.refrescar_tabla()
            QMessageBox.information(
                self,
                "Importación Completada",
                f"Se importaron {insertados} nuevas paradas intermedias desde el archivo Excel.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error al Importar", f"No se pudo importar el archivo:\n{e}")
