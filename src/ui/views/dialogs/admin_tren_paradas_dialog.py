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
        self.combo_filtro_regimen.addItem("Todos", "Todos")
        self.combo_filtro_regimen.addItem("H - Hábil", "H")
        self.combo_filtro_regimen.addItem("S - Sábado", "S")
        self.combo_filtro_regimen.addItem("D - Domingo", "D")
        self.combo_filtro_regimen.addItem("DIAGRAMA_6D", "DIAGRAMA_6D")
        self.combo_filtro_regimen.addItem("UNIPERSONAL", "UNIPERSONAL")
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

        # Panel Izquierdo: Tabla y barra de eliminación múltiple/filtro/total
        left_widget = QWidget()
        left_lay = QVBoxLayout(left_widget)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)

        self.lbl_contador = QLabel("0 paradas registradas")
        self.lbl_contador.setStyleSheet("font-weight: bold;")
        left_lay.addWidget(self.lbl_contador)

        bar_acciones_tabla = QHBoxLayout()
        bar_acciones_tabla.setSpacing(6)

        self.btn_sel_todas = QPushButton("☑️ Seleccionar Visibles")
        self.btn_sel_todas.setObjectName("btnSecondary")
        self.btn_sel_todas.setToolTip("Selecciona todas las filas actualmente visibles en la tabla")
        self.btn_sel_todas.clicked.connect(self._seleccionar_visibles)
        bar_acciones_tabla.addWidget(self.btn_sel_todas)

        self.btn_eliminar = QPushButton("🗑️ Eliminar Seleccionadas")
        self.btn_eliminar.setObjectName("btnDanger")
        self.btn_eliminar.setEnabled(False)
        self.btn_eliminar.setToolTip("Elimina una o múltiples paradas seleccionadas en la tabla (Ctrl+Clic / Shift+Clic)")
        self.btn_eliminar.clicked.connect(self._eliminar_paradas_seleccionadas)
        bar_acciones_tabla.addWidget(self.btn_eliminar)

        self.btn_eliminar_filtro = QPushButton("🗑️ Eliminar por Filtro")
        self.btn_eliminar_filtro.setObjectName("btnDanger")
        self.btn_eliminar_filtro.setToolTip("Elimina permanentemente todas las paradas que coincidan con los filtros aplicados")
        self.btn_eliminar_filtro.clicked.connect(self._eliminar_por_filtros)
        bar_acciones_tabla.addWidget(self.btn_eliminar_filtro)

        self.btn_eliminar_todas = QPushButton("🗑️ Eliminar Todas")
        self.btn_eliminar_todas.setObjectName("btnDanger")
        self.btn_eliminar_todas.setToolTip("Vacía por completo el catálogo de paradas por tren")
        self.btn_eliminar_todas.clicked.connect(self._eliminar_todas_paradas)
        bar_acciones_tabla.addWidget(self.btn_eliminar_todas)

        bar_acciones_tabla.addStretch()
        left_lay.addLayout(bar_acciones_tabla)

        self.tabla = QTableWidget(0, 8)
        self.tabla.setHorizontalHeaderLabels([
            "ID", "N° TREN", "ORDEN", "ESTACIÓN / PARADA", "SIGLA", "HORA PASO", "ITINERARIO", "DÍA / RÉGIMEN"
        ])
        self.tabla.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.tabla.setColumnWidth(0, 55)
        self.tabla.setColumnWidth(1, 75)
        self.tabla.setColumnWidth(2, 60)
        self.tabla.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tabla.setColumnWidth(4, 65)
        self.tabla.setColumnWidth(5, 80)
        self.tabla.setColumnWidth(6, 85)
        self.tabla.setColumnWidth(7, 95)
        self.tabla.setSelectionBehavior(QTableWidget.SelectRows)
        self.tabla.setSelectionMode(QTableWidget.ExtendedSelection)
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
        self.combo_regimen.addItem("H - Hábil", "H")
        self.combo_regimen.addItem("S - Sábado", "S")
        self.combo_regimen.addItem("D - Domingo", "D")
        self.combo_regimen.addItem("DIAGRAMA_6D", "DIAGRAMA_6D")
        self.combo_regimen.addItem("UNIPERSONAL", "UNIPERSONAL")
        f_grid.addRow("Régimen:", self.combo_regimen)

        form_lay.addLayout(f_grid)
        form_lay.addSpacing(10)

        self.lbl_modo_form = QLabel("Modo: Crear nueva parada")
        self.lbl_modo_form.setStyleSheet("font-weight: bold; color: #1F497D;")
        self.lbl_modo_form.setWordWrap(True)
        form_lay.addWidget(self.lbl_modo_form)

        btn_guardar = QPushButton("💾 Guardar Parada")
        btn_guardar.clicked.connect(self._guardar_parada)
        form_lay.addWidget(btn_guardar)

        btn_nuevo = QPushButton("➕ Limpiar / Nueva Parada")
        btn_nuevo.setObjectName("btnSecondary")
        btn_nuevo.clicked.connect(lambda: self._limpiar_formulario(limpiar_seleccion=True))
        form_lay.addWidget(btn_nuevo)

        form_lay.addStretch()

        lbl_ayuda = QLabel(
            "Nota: Soporta libros Excel con múltiples hojas por ramal/régimen\n"
            "(ej. Hab ASC/DES, Sab, DOM -> H/S/D), selección múltiple con Ctrl/Shift\n"
            "y búsqueda o eliminación filtrada por tren, estación/sigla o itinerario."
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

    def _filtros_actuales(self) -> Dict[str, Optional[str]]:
        nt = self.txt_filtro_tren.text().strip()
        est = self.txt_filtro_estacion.text().strip()
        itin = self.combo_filtro_itin.currentText().strip()
        reg_data = self.combo_filtro_regimen.currentData()
        reg = str(reg_data if reg_data is not None else self.combo_filtro_regimen.currentText()).strip()
        return {
            "nro_tren": nt or None,
            "itinerario": None if itin in ("", "Todos", "Todas") else itin,
            "estacion": est or None,
            "regimen": None if reg in ("", "Todos", "Todas") else reg,
        }

    def _obtener_filas_seleccionadas(self) -> List[Dict[str, Any]]:
        sel_model = self.tabla.selectionModel()
        if not sel_model:
            return []
        filas_idx = sorted({idx.row() for idx in sel_model.selectedIndexes()})
        return [self._datos_cache[r] for r in filas_idx if 0 <= r < len(self._datos_cache)]

    def _actualizar_contador(self) -> None:
        mostradas = len(self._datos_cache)
        total_filt = getattr(self, "_total_filtrado", mostradas)
        total_global = getattr(self, "_total_global", mostradas)
        sel_cnt = len(self._obtener_filas_seleccionadas())
        hay_filtro = any(self._filtros_actuales().values())

        if mostradas < total_filt:
            txt = f"Mostrando {mostradas} de {total_filt} paradas coincidentes (total en base: {total_global})"
        elif hay_filtro:
            txt = f"{total_filt} parada(s) coincidente(s) (total en base: {total_global})"
        else:
            txt = f"{total_global} paradas registradas"

        if sel_cnt > 0:
            txt += f" | {sel_cnt} seleccionada(s)"
        self.lbl_contador.setText(txt)

    def refrescar_tabla(self) -> None:
        filtros = self._filtros_actuales()
        self._total_filtrado = self.records_service.contar_paradas_tren_por_filtros(**filtros)
        if any(filtros.values()):
            self._total_global = self.records_service.contar_paradas_tren_por_filtros()
        else:
            self._total_global = self._total_filtrado

        self._datos_cache = self.records_service.listar_paradas_tren(
            **filtros,
            limite=5000,
        )

        self.tabla.blockSignals(True)
        self.tabla.setRowCount(len(self._datos_cache))
        for r, item in enumerate(self._datos_cache):
            self.tabla.setItem(r, 0, QTableWidgetItem(str(item["id"])))
            self.tabla.setItem(r, 1, QTableWidgetItem(str(item["nro_tren"])))
            self.tabla.setItem(r, 2, QTableWidgetItem(str(item["orden_parada"])))
            self.tabla.setItem(r, 3, QTableWidgetItem(str(item["estacion"])))
            self.tabla.setItem(r, 4, QTableWidgetItem(str(item.get("sigla") or "")))
            self.tabla.setItem(r, 5, QTableWidgetItem(str(item["hora_paso"])))
            self.tabla.setItem(r, 6, QTableWidgetItem(str(item.get("itinerario") or "")))
            reg_lbl = str(
                item.get("regimen_label")
                or self.records_service.formatear_regimen_turno(item.get("regimen") or "H")
            )
            self.tabla.setItem(r, 7, QTableWidgetItem(reg_lbl))
        self.tabla.blockSignals(False)

        self.btn_sel_todas.setEnabled(len(self._datos_cache) > 0)
        self._on_seleccion_tabla()

    def _seleccionar_visibles(self) -> None:
        if self.tabla.rowCount() > 0:
            self.tabla.selectAll()

    def _on_seleccion_tabla(self) -> None:
        sels = self._obtener_filas_seleccionadas()
        self._actualizar_contador()
        if not sels:
            self._limpiar_formulario(limpiar_seleccion=False)
            return
        if len(sels) > 1:
            self._limpiar_formulario(limpiar_seleccion=False)
            self.lbl_modo_form.setText(f"Modo: {len(sels)} paradas seleccionadas (eliminación en lote)")
            self.btn_eliminar.setEnabled(True)
            self.btn_eliminar.setText(f"🗑️ Eliminar Seleccionadas ({len(sels)})")
            return

        item = sels[0]
        self._parada_editando_id = int(item["id"])
        self.txt_nro_tren.setText(str(item["nro_tren"]))
        self.spin_orden.setValue(int(item.get("orden_parada") or 1))
        self.txt_estacion.setText(str(item["estacion"]))
        self.txt_hora_paso.setText(str(item["hora_paso"]))
        self.combo_itin.setCurrentText(str(item.get("itinerario") or ""))
        reg = self.records_service.normalizar_regimen_parada(item.get("regimen"), default="H")
        idx_r = self.combo_regimen.findData(reg)
        if idx_r < 0:
            idx_r = self.combo_regimen.findText(reg)
        if idx_r >= 0:
            self.combo_regimen.setCurrentIndex(idx_r)
        self.lbl_modo_form.setText(f"Modo: Editando parada ID #{self._parada_editando_id}")
        self.btn_eliminar.setEnabled(True)
        self.btn_eliminar.setText("🗑️ Eliminar Seleccionada (1)")

    def _limpiar_formulario(self, limpiar_seleccion: bool = True) -> None:
        if limpiar_seleccion and self.tabla.selectionModel() is not None:
            self.tabla.blockSignals(True)
            self.tabla.clearSelection()
            self.tabla.blockSignals(False)
            self._actualizar_contador()
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
        self.btn_eliminar.setText("🗑️ Eliminar Seleccionadas")

    def _guardar_parada(self) -> None:
        reg_val = self.combo_regimen.currentData() or self.records_service.normalizar_regimen_parada(
            self.combo_regimen.currentText(), default="H"
        )
        datos = {
            "nro_tren": self.txt_nro_tren.text().strip(),
            "orden_parada": self.spin_orden.value(),
            "estacion": self.txt_estacion.text().strip(),
            "hora_paso": self.txt_hora_paso.text().strip(),
            "itinerario": self.combo_itin.currentText().strip(),
            "regimen": str(reg_val).strip(),
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
            self._limpiar_formulario(limpiar_seleccion=False)
            self.refrescar_tabla()
        except Exception as e:
            QMessageBox.warning(self, "Validación", str(e))

    def _eliminar_paradas_seleccionadas(self) -> None:
        sels = self._obtener_filas_seleccionadas()
        if not sels:
            QMessageBox.information(
                self, "Selección", "Seleccione al menos una parada de la tabla para eliminar."
            )
            return

        if len(sels) == 1:
            p0 = sels[0]
            msg = (
                f"¿Está seguro que desea eliminar la parada ID #{p0['id']} "
                f"(Tren {p0['nro_tren']} - {p0['estacion']} {p0['hora_paso']})?"
            )
            resp = QMessageBox.question(
                self,
                "Confirmar Eliminación",
                msg,
                QMessageBox.Yes | QMessageBox.No,
            )
        else:
            muestra = ", ".join(f"Tren {x['nro_tren']} ({x['estacion']})" for x in sels[:6])
            if len(sels) > 6:
                muestra += f", ... (+{len(sels) - 6} más)"
            resp = QMessageBox.warning(
                self,
                "Confirmar Eliminación Múltiple",
                (
                    f"¿Está seguro que desea eliminar permanentemente las {len(sels)} paradas seleccionadas?\n\n"
                    f"{muestra}\n\nEsta acción no se puede deshacer."
                ),
                QMessageBox.Yes | QMessageBox.No,
            )
        if resp != QMessageBox.Yes:
            return

        try:
            win_usr = obtener_usuario_windows()
            uid = self.usuario_actual.get("id")
            if len(sels) == 1:
                self.records_service.eliminar_parada_tren(
                    int(sels[0]["id"]),
                    user_id=uid,
                    windows_user=win_usr,
                )
            else:
                eliminados = self.records_service.eliminar_paradas_tren_lote(
                    [int(x["id"]) for x in sels],
                    user_id=uid,
                    windows_user=win_usr,
                )
                QMessageBox.information(
                    self,
                    "Eliminación Completada",
                    f"Se eliminaron {eliminados} paradas seleccionadas correctamente.",
                )
            self._refrescar_combos_itinerarios()
            self._limpiar_formulario(limpiar_seleccion=False)
            self.refrescar_tabla()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _eliminar_parada(self) -> None:
        """Alias compatible hacia atrás que delega en la eliminación simple/múltiple."""
        self._eliminar_paradas_seleccionadas()

    def _eliminar_por_filtros(self) -> None:
        filtros = self._filtros_actuales()
        if not any(filtros.values()):
            QMessageBox.information(
                self,
                "Filtro Requerido",
                "No hay filtros aplicados (N° Tren, Estación/Sigla, Itinerario o Régimen).\n\n"
                "Aplique al menos un filtro o utilice el botón '🗑️ Eliminar Todas' si desea vaciar todas las paradas.",
            )
            return

        total = self.records_service.contar_paradas_tren_por_filtros(**filtros)
        if total == 0:
            QMessageBox.information(
                self,
                "Sin Paradas",
                "No existen paradas que coincidan con los filtros seleccionados para eliminar.",
            )
            return

        detalles: List[str] = []
        if filtros["nro_tren"]:
            detalles.append(f"• N° Tren: {filtros['nro_tren']}")
        if filtros["estacion"]:
            detalles.append(f"• Estación / Sigla: {filtros['estacion']}")
        if filtros["itinerario"]:
            detalles.append(f"• Itinerario: {filtros['itinerario']}")
        if filtros["regimen"]:
            detalles.append(f"• Régimen: {self.records_service.formatear_regimen_turno(filtros['regimen'])}")

        resp = QMessageBox.warning(
            self,
            "Eliminar Paradas por Filtro",
            (
                f"Se eliminarán permanentemente {total} parada(s) que coinciden con los filtros aplicados:\n\n"
                f"{chr(10).join(detalles)}\n\nEsta acción no se puede deshacer. ¿Desea continuar?"
            ),
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return

        try:
            eliminados = self.records_service.eliminar_paradas_tren_por_filtros(
                user_id=self.usuario_actual.get("id"),
                windows_user=obtener_usuario_windows(),
                **filtros,
            )
            self._refrescar_combos_itinerarios()
            self._limpiar_formulario(limpiar_seleccion=False)
            self.refrescar_tabla()
            QMessageBox.information(
                self,
                "Eliminación Completada",
                f"Se eliminaron {eliminados} parada(s) según los filtros seleccionados.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error al Eliminar", f"No se pudieron eliminar las paradas filtradas:\n{e}")

    def _eliminar_todas_paradas(self) -> None:
        total = self.records_service.contar_paradas_tren_por_filtros()
        if total == 0:
            QMessageBox.information(
                self,
                "Sin Paradas",
                "No hay paradas de tren registradas para eliminar.",
            )
            return

        resp = QMessageBox.warning(
            self,
            "Eliminar Todas las Paradas",
            (
                f"¿Está seguro que desea eliminar permanentemente TODAS las {total} paradas de tren registradas "
                f"(todos los trenes, itinerarios y regímenes)?\n\nEsta acción no se puede deshacer."
            ),
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return

        try:
            eliminados = self.records_service.eliminar_todas_paradas_tren(
                user_id=self.usuario_actual.get("id"),
                windows_user=obtener_usuario_windows(),
            )
            self._refrescar_combos_itinerarios()
            self._limpiar_formulario(limpiar_seleccion=False)
            self.refrescar_tabla()
            QMessageBox.information(
                self,
                "Eliminación Completada",
                f"Se eliminaron todas las {eliminados} paradas de tren registradas.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error al Eliminar", f"No se pudieron eliminar todas las paradas:\n{e}")

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
