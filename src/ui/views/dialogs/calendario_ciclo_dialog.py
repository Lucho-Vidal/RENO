"""Diálogo del Calendario Mensual de Personal de Ciclo (Maximizado).

Presenta el cronograma día por día del mes con cálculo de descansos reglamentarios CCT
(18/42 hs o 17/41 hs), horas trabajadas acumuladas de la semana y asignación de jornadas.
"""

from typing import Any, Dict, List, Optional
from datetime import date, datetime

from PySide6.QtCore import QDate, QTime, Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableView,
    QTimeEdit,
    QVBoxLayout,
)

from src.core.logger import get_logger
from src.services.excel_service import ExcelService
from src.services.pdf_service import PDFService
from src.services.records_service import RecordsService
from src.ui.models.base_table_model import BaseTableModel
from src.ui.views.dialogs.base_dialog import BaseDialog, BaseMaximizedDialog

logger = get_logger("CalendarioCicloDialog")


class AsignarJornadaDialog(BaseDialog):
    """Diálogo modal para registrar horario de toma/deja o franco anticipado."""

    def __init__(
        self,
        legajo: int,
        nombre: str,
        fecha_iso: str,
        dia_semana: str,
        jornada_existente: Optional[Dict[str, Any]] = None,
        franco_agente: str = "Domingo",
        records_service: Optional[RecordsService] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Asignar Jornada / Franco")
        self.setMinimumWidth(440)

        self.legajo = legajo
        self.fecha_iso = fecha_iso
        self.dia_semana = dia_semana
        self.franco_agente = franco_agente
        self.records_service = records_service
        self.datos_resultado: Optional[Dict[str, Any]] = None

        layout = QVBoxLayout(self)
        form = QGridLayout()

        form.addWidget(QLabel("Personal de Ciclo:"), 0, 0)
        lbl_nom = QLabel(f"<b>{nombre}</b> (Leg. {legajo})")
        form.addWidget(lbl_nom, 0, 1)

        form.addWidget(QLabel("Fecha:"), 1, 0)
        partes = fecha_iso.split("-")
        f_fmt = f"{partes[2]}/{partes[1]}/{partes[0]}" if len(partes) == 3 else fecha_iso
        lbl_f = QLabel(f"<b>{f_fmt} ({dia_semana})</b> | Franco: <b>{franco_agente}</b>")
        form.addWidget(lbl_f, 1, 1)

        # Selector Asistido de Turno Itinerario
        form.addWidget(QLabel("Turno Itinerario (Opcional):"), 2, 0)
        h_turno = QHBoxLayout()
        self.txt_turno_base = QLineEdit()
        self.txt_turno_base.setPlaceholderText("Ej. 401, 410, PROG.1CK5...")
        self.btn_resolver = QPushButton("⚡ Autocompletar")
        self.btn_resolver.setObjectName("btnSecondary")
        self.btn_resolver.clicked.connect(self._autocompletar_por_turno)
        self.txt_turno_base.returnPressed.connect(self._autocompletar_por_turno)
        h_turno.addWidget(self.txt_turno_base)
        h_turno.addWidget(self.btn_resolver)
        form.addLayout(h_turno, 2, 1)

        # Franco anticipado checkbox
        self.chk_fa = QCheckBox("Marcar como Franco Anticipado")
        form.addWidget(self.chk_fa, 3, 0, 1, 2)

        # Horarios Toma / Deja con QTimeEdit
        form.addWidget(QLabel("Horario de Toma (HH:mm):"), 4, 0)
        self.time_toma = QTimeEdit()
        self.time_toma.setDisplayFormat("HH:mm")
        self.time_toma.setTime(QTime(6, 0))
        form.addWidget(self.time_toma, 4, 1)

        form.addWidget(QLabel("Horario de Deja (HH:mm):"), 5, 0)
        self.time_deja = QTimeEdit()
        self.time_deja.setDisplayFormat("HH:mm")
        self.time_deja.setTime(QTime(12, 0))
        form.addWidget(self.time_deja, 5, 1)

        form.addWidget(QLabel("Observaciones:"), 6, 0)
        self.txt_obs = QPlainTextEdit()
        self.txt_obs.setMaximumHeight(75)
        self.txt_obs.setPlaceholderText("Observaciones o notas sobre la jornada...")
        # Aliases de compatibilidad
        self.txt_obs.setText = self.txt_obs.setPlainText  # type: ignore
        self.txt_obs.text = self.txt_obs.toPlainText  # type: ignore
        form.addWidget(self.txt_obs, 6, 1)

        self.chk_fa.toggled.connect(self._on_fa_toggled)
        layout.addLayout(form)

        if jornada_existente:
            if jornada_existente.get("franco_anticipado"):
                self.chk_fa.setChecked(True)
            if jornada_existente.get("toma") and jornada_existente["toma"] != "-":
                t = QTime.fromString(str(jornada_existente["toma"]).strip(), "HH:mm")
                if t.isValid():
                    self.time_toma.setTime(t)
            if jornada_existente.get("deja") and jornada_existente["deja"] != "-":
                d = QTime.fromString(str(jornada_existente["deja"]).strip(), "HH:mm")
                if d.isValid():
                    self.time_deja.setTime(d)
            if jornada_existente.get("observaciones"):
                self.txt_obs.setPlainText(str(jornada_existente["observaciones"]))

        # Botones
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setObjectName("btnSecondary")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("Guardar")
        btn_ok.clicked.connect(self._on_accept)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def _autocompletar_por_turno(self) -> None:
        base = self.txt_turno_base.text().strip()
        if not base:
            QMessageBox.information(self, "Itinerario", "Ingrese un código de turno base (ej. 401, 410, PROG.1CK5).")
            return

        if not self.records_service:
            return

        turnos_map = self.records_service.listar_turnos_itinerario_map()
        if not turnos_map:
            QMessageBox.warning(
                self,
                "Itinerarios Vacíos",
                "No hay base de itinerarios cargada en el sistema. Cargue 'Indice_45.xlsx' desde el Buscador de Trenes.",
            )
            return

        from src.domain.validators import resolver_jornada_itinerario
        try:
            res = resolver_jornada_itinerario(
                turno_base=base,
                fecha_target=self.fecha_iso,
                franco_agente=self.franco_agente,
                turnos_dict=turnos_map,
            )

            if res.get("es_franco"):
                self.chk_fa.setChecked(True)
                self.txt_obs.setPlainText(f"Turno {base} - FRANCO")
                QMessageBox.information(
                    self,
                    "Franco Detectado",
                    f"Para la fecha seleccionada ({self.dia_semana}), el turno {base} corresponde a FRANCO según el ciclo del agente.",
                )
            else:
                self.chk_fa.setChecked(False)
                if res.get("toma"):
                    t = QTime.fromString(res["toma"].strip(), "HH:mm")
                    if t.isValid():
                        self.time_toma.setTime(t)
                if res.get("deje"):
                    d = QTime.fromString(res["deje"].strip(), "HH:mm")
                    if d.isValid():
                        self.time_deja.setTime(d)

                detalles = f"Turno {res.get('codigo_turno')}"
                if res.get("deposito"):
                    detalles += f" ({res['deposito']})"
                self.txt_obs.setPlainText(detalles)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Fallo al resolver turno itinerario:\n{e}")

    def _on_fa_toggled(self, checked: bool) -> None:
        self.time_toma.setEnabled(not checked)
        self.time_deja.setEnabled(not checked)

    def _on_accept(self) -> None:
        fa = 1 if self.chk_fa.isChecked() else 0
        toma = self.time_toma.time().toString("HH:mm") if not fa else ""
        deja = self.time_deja.time().toString("HH:mm") if not fa else ""
        obs = self.txt_obs.toPlainText().strip()

        self.datos_resultado = {
            "toma": toma,
            "deja": deja,
            "franco_anticipado": fa,
            "observaciones": obs,
        }
        self.accept()


class CalendarioCicloDialog(BaseMaximizedDialog):
    """Ventana del Calendario Mensual de Personal de Ciclo (abierta maximizada)."""

    def __init__(
        self,
        empleado: Dict[str, Any],
        records_service: RecordsService,
        pdf_service: PDFService,
        excel_service: ExcelService,
        mes: Optional[int] = None,
        anio: Optional[int] = None,
        usuario_actual: Optional[Dict[str, Any]] = None,
        tiene_permiso_fn=None,
        empleados_lista: Optional[List[Dict[str, Any]]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.empleado = dict(empleado)
        self.records_service = records_service
        self.pdf_service = pdf_service
        self.excel_service = excel_service
        self.usuario_actual = usuario_actual or {}
        self.tiene_permiso_fn = tiene_permiso_fn

        hoy = date.today()
        self.mes = mes or hoy.month
        self.anio = anio or hoy.year
        self._dias_datos: List[Dict[str, Any]] = []

        self._todos_empleados_ciclo: List[Dict[str, Any]] = self._cargar_catalogo_ciclo()
        self._empleados_nav: List[Dict[str, Any]] = self._preparar_lista_navegacion(empleados_lista)
        self._idx_actual: int = self._buscar_indice_por_legajo(self._empleados_nav, self.empleado.get("legajo"))

        self.resize(1100, 700)

        self._init_ui()
        self._actualizar_cabecera_y_nav()
        self.cargar_mes()

    def _cargar_catalogo_ciclo(self) -> List[Dict[str, Any]]:
        try:
            return list(self.records_service.listar_empleados_ciclo() or [])
        except Exception as e:
            logger.warning("No se pudo cargar catálogo completo de ciclo: %s", e)
            return []

    def _preparar_lista_navegacion(self, empleados_lista: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        base = list(empleados_lista) if empleados_lista else list(self._todos_empleados_ciclo)
        vistos = set()
        resultado: List[Dict[str, Any]] = []
        for item in base:
            leg = item.get("legajo")
            if leg is None:
                continue
            try:
                leg_int = int(leg)
            except (TypeError, ValueError):
                continue
            if leg_int in vistos:
                continue
            vistos.add(leg_int)
            resultado.append(dict(item))

        try:
            leg_actual = int(self.empleado.get("legajo"))
        except (TypeError, ValueError):
            leg_actual = None

        if leg_actual is not None and leg_actual not in vistos:
            resultado.insert(0, dict(self.empleado))
        elif not resultado:
            resultado.append(dict(self.empleado))
        return resultado

    @staticmethod
    def _buscar_indice_por_legajo(lista: List[Dict[str, Any]], legajo: Any) -> int:
        try:
            objetivo = int(legajo)
        except (TypeError, ValueError):
            return 0
        for idx, item in enumerate(lista):
            try:
                if int(item.get("legajo")) == objetivo:
                    return idx
            except (TypeError, ValueError):
                continue
        return 0

    def _can(self, perm: str) -> bool:
        if self.tiene_permiso_fn:
            return bool(self.tiene_permiso_fn(perm))
        return True

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # 0. Barra de navegación de personal (Anterior / Siguiente / Legajo / Nombre)
        nav_frame = QFrame()
        nav_frame.setObjectName("filterBar")
        nav_layout = QHBoxLayout(nav_frame)
        nav_layout.setContentsMargins(8, 4, 8, 4)
        nav_layout.setSpacing(8)

        self.btn_prev_emp = QPushButton("◀ Anterior")
        self.btn_prev_emp.setObjectName("btnSecondary")
        self.btn_prev_emp.clicked.connect(lambda: self._navegar_empleado(-1))
        nav_layout.addWidget(self.btn_prev_emp)

        self.lbl_nav_pos = QLabel("1 / 1")
        self.lbl_nav_pos.setStyleSheet("font-weight: 600; min-width: 55px;")
        self.lbl_nav_pos.setAlignment(Qt.AlignCenter)
        nav_layout.addWidget(self.lbl_nav_pos)

        self.btn_next_emp = QPushButton("Siguiente ▶")
        self.btn_next_emp.setObjectName("btnSecondary")
        self.btn_next_emp.clicked.connect(lambda: self._navegar_empleado(1))
        nav_layout.addWidget(self.btn_next_emp)

        nav_layout.addSpacing(12)
        nav_layout.addWidget(QLabel("Legajo:"))
        self.txt_nav_legajo = QLineEdit()
        self.txt_nav_legajo.setPlaceholderText("Nro legajo...")
        self.txt_nav_legajo.setFixedWidth(105)
        self.txt_nav_legajo.returnPressed.connect(self._ir_por_legajo)
        nav_layout.addWidget(self.txt_nav_legajo)

        self.btn_ir_legajo = QPushButton("Ir")
        self.btn_ir_legajo.setObjectName("btnSecondary")
        self.btn_ir_legajo.clicked.connect(self._ir_por_legajo)
        nav_layout.addWidget(self.btn_ir_legajo)

        nav_layout.addSpacing(12)
        nav_layout.addWidget(QLabel("Buscar por nombre:"))
        self.combo_nav_nombre = QComboBox()
        self.combo_nav_nombre.setEditable(True)
        self.combo_nav_nombre.setInsertPolicy(QComboBox.NoInsert)
        self.combo_nav_nombre.setMinimumWidth(290)
        self.combo_nav_nombre.lineEdit().setPlaceholderText("Escriba apellido o nombre...")
        self.combo_nav_nombre.lineEdit().setClearButtonEnabled(True)

        self._etiquetas_busqueda: List[str] = []
        self._mapa_busqueda_emp: Dict[str, Dict[str, Any]] = {}
        fuente_busqueda = self._todos_empleados_ciclo or self._empleados_nav
        vistos_busq = set()
        for emp_item in fuente_busqueda:
            leg_v = emp_item.get("legajo")
            if leg_v in vistos_busq:
                continue
            vistos_busq.add(leg_v)
            etiqueta = f"{emp_item.get('apellidos_nombres', '')} (Leg. {leg_v})"
            self._etiquetas_busqueda.append(etiqueta)
            self._mapa_busqueda_emp[etiqueta] = dict(emp_item)
            self.combo_nav_nombre.addItem(etiqueta, leg_v)

        completer = QCompleter(self._etiquetas_busqueda, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.combo_nav_nombre.setCompleter(completer)
        self.combo_nav_nombre.activated.connect(self._on_seleccionar_nombre)
        self.combo_nav_nombre.lineEdit().returnPressed.connect(self._ir_por_nombre_texto)
        nav_layout.addWidget(self.combo_nav_nombre, 1)

        layout.addWidget(nav_frame)

        # 1. Cabecera con datos del agente y selector de Mes/Año
        header_frame = QFrame()
        header_frame.setObjectName("headerFrame")
        h_layout = QHBoxLayout(header_frame)

        # Info del empleado
        v_info = QVBoxLayout()
        v_info.setSpacing(3)
        self.lbl_nombre = QLabel("")
        self.lbl_nombre.setObjectName("lblHeaderTitle")
        self.lbl_detalles = QLabel("")
        self.lbl_detalles.setObjectName("lblHeaderSubtitle")
        v_info.addWidget(self.lbl_nombre)
        v_info.addWidget(self.lbl_detalles)
        h_layout.addLayout(v_info)

        h_layout.addStretch()

        # Selector de mes y año
        h_mes = QHBoxLayout()
        h_mes.addWidget(QLabel("Mes:"))
        self.combo_mes = QComboBox()
        meses = [
            "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
        ]
        self.combo_mes.addItems(meses)
        self.combo_mes.setCurrentIndex(self.mes - 1)
        self.combo_mes.currentIndexChanged.connect(self._on_mes_anio_changed)
        h_mes.addWidget(self.combo_mes)

        h_mes.addWidget(QLabel("Año:"))
        self.spin_anio = QSpinBox()
        self.spin_anio.setRange(2020, 2040)
        self.spin_anio.setValue(self.anio)
        self.spin_anio.valueChanged.connect(self._on_mes_anio_changed)
        h_mes.addWidget(self.spin_anio)

        h_layout.addLayout(h_mes)
        layout.addWidget(header_frame)

        # 2. Barra de acciones secundarias
        action_bar = QHBoxLayout()
        self.btn_asignar = QPushButton("⏱️ Asignar Jornada / Franco")
        self.btn_asignar.setEnabled(self._can("reemplazos.asignar"))
        self.btn_asignar.clicked.connect(self._asignar_jornada_seleccionada)
        action_bar.addWidget(self.btn_asignar)

        action_bar.addStretch()

        self.btn_excel = QPushButton("📊 Exportar Cronograma Excel")
        self.btn_excel.setObjectName("btnSecondary")
        self.btn_excel.clicked.connect(self._exportar_excel_individual)
        action_bar.addWidget(self.btn_excel)

        self.btn_excel_todos = QPushButton("📑 Exportar Todos los Ciclos (Excel)")
        self.btn_excel_todos.setObjectName("btnSecondary")
        self.btn_excel_todos.clicked.connect(self._exportar_excel_todos)
        action_bar.addWidget(self.btn_excel_todos)

        self.btn_pdf = QPushButton("📑 Exportar Cronograma PDF")
        self.btn_pdf.setObjectName("btnSecondary")
        self.btn_pdf.clicked.connect(self._exportar_pdf)
        action_bar.addWidget(self.btn_pdf)

        layout.addLayout(action_bar)

        # 3. Tabla de días
        headers = [
            "DÍA", "FECHA", "DÍA SEM.", "ESTADO", "HORA DISPONIBLE",
            "TOMA", "DEJA", "HS TRABAJADAS", "HS SEMANA", "REEMPLAZA A / MOTIVO", "OBSERVACIONES",
        ]
        keys = [
            "dia", "fecha", "dia_semana", "estado", "hora_disponible",
            "toma", "deja", "horas_trabajadas", "hs_semana", "reemplaza_a", "observaciones",
        ]

        def conditional_colors(row_item: Dict[str, Any], col_idx: int):
            estado = str(row_item.get("estado", ""))
            from src.core.theme import obtener_tema_visual
            es_oscuro = obtener_tema_visual() == "oscuro"
            if "Novedad" in estado:
                return (QColor("#3D1B1B"), QColor("#FCA5A5")) if es_oscuro else (QColor("#FFEBEE"), QColor("#B71C1C"))
            elif "Franco" in estado:
                return (QColor("#3D3214"), QColor("#FCD34D")) if es_oscuro else (QColor("#FFF9C4"), QColor("#B78103"))
            elif "Reemplazando" in estado or "Relevo" in estado:
                return (QColor("#132E1F"), QColor("#6EE7B7")) if es_oscuro else (QColor("#E8F5E9"), QColor("#2E7D32"))
            elif estado == "Disponible":
                return (QColor("#172554"), QColor("#93C5FD")) if es_oscuro else (QColor("#F0F7FF"), QColor("#1F497D"))
            return None

        self.table_model = BaseTableModel(headers, keys, conditional_colors=conditional_colors, parent=self)
        self.table_view = QTableView()
        self.table_view.setModel(self.table_model)
        self.table_view.setSelectionBehavior(QTableView.SelectRows)
        self.table_view.setSelectionMode(QTableView.SingleSelection)
        self.table_view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table_view.setColumnWidth(0, 45)   # DÍA
        self.table_view.setColumnWidth(1, 85)   # FECHA
        self.table_view.setColumnWidth(2, 75)   # DÍA SEM.
        self.table_view.setColumnWidth(3, 175)  # ESTADO
        self.table_view.setColumnWidth(4, 120)  # HORA DISPONIBLE
        self.table_view.setColumnWidth(5, 60)   # TOMA
        self.table_view.setColumnWidth(6, 60)   # DEJA
        self.table_view.setColumnWidth(7, 80)   # HS TRABAJADAS
        self.table_view.setColumnWidth(8, 80)   # HS SEMANA
        self.table_view.setColumnWidth(9, 190)  # REEMPLAZA A / MOTIVO
        self.table_view.horizontalHeader().setSectionResizeMode(10, QHeaderView.Stretch)  # OBSERVACIONES
        self.table_view.doubleClicked.connect(self._on_row_double_clicked)
        layout.addWidget(self.table_view)

        # 4. Pie de cierre
        btn_close = QPushButton("Cerrar")
        btn_close.setObjectName("btnSecondary")
        btn_close.clicked.connect(self.accept)
        btn_close_layout = QHBoxLayout()
        btn_close_layout.addStretch()
        btn_close_layout.addWidget(btn_close)
        layout.addLayout(btn_close_layout)

    def _actualizar_cabecera_y_nav(self) -> None:
        nombre = self.empleado.get("apellidos_nombres") or ""
        leg = self.empleado.get("legajo")
        self.setWindowTitle(f"Calendario Mensual de Ciclo - {nombre} (Leg. {leg})")
        self.lbl_nombre.setText(f"<b>{nombre}</b> (Legajo: {leg})")
        franco_hor = str(self.empleado.get("franco_horario") or "").strip()
        franco_txt = f"{self.empleado.get('franco') or '-'}" + (f" ({franco_hor})" if franco_hor else "")
        self.lbl_detalles.setText(
            f"Dotación: <b>{self.empleado.get('dotacion') or '-'}</b> | "
            f"Especialidad: <b>{self.empleado.get('especialidad') or '-'}</b> | "
            f"Turno: <b>{self.empleado.get('turnos') or '-'}</b> | "
            f"Franco CCT: <b>{franco_txt}</b> | "
            f"Jornada: <b>{self.empleado.get('jornada_horas', 6)} hs</b>"
        )

        total = len(self._empleados_nav)
        self.lbl_nav_pos.setText(f"{self._idx_actual + 1} / {total}" if total else "0 / 0")
        self.btn_prev_emp.setEnabled(total > 1 and self._idx_actual > 0)
        self.btn_next_emp.setEnabled(total > 1 and self._idx_actual < total - 1)

        self.txt_nav_legajo.setText(str(leg) if leg is not None else "")

        self.combo_nav_nombre.blockSignals(True)
        etiqueta_actual = f"{nombre} (Leg. {leg})"
        idx_combo = self.combo_nav_nombre.findText(etiqueta_actual)
        if idx_combo >= 0:
            self.combo_nav_nombre.setCurrentIndex(idx_combo)
        else:
            self.combo_nav_nombre.setEditText(etiqueta_actual)
        self.combo_nav_nombre.blockSignals(False)

    def _seleccionar_empleado(self, emp: Dict[str, Any]) -> None:
        self.empleado = dict(emp)
        leg = self.empleado.get("legajo")
        encontrado = False
        for i, item in enumerate(self._empleados_nav):
            try:
                if int(item.get("legajo")) == int(leg):
                    self._idx_actual = i
                    encontrado = True
                    break
            except (TypeError, ValueError):
                continue
        if not encontrado:
            self._empleados_nav.append(dict(self.empleado))
            self._idx_actual = len(self._empleados_nav) - 1

        self._actualizar_cabecera_y_nav()
        self.cargar_mes()

    def _navegar_empleado(self, delta: int) -> None:
        if not self._empleados_nav:
            return
        nuevo_idx = self._idx_actual + delta
        if 0 <= nuevo_idx < len(self._empleados_nav):
            self._idx_actual = nuevo_idx
            self.empleado = dict(self._empleados_nav[nuevo_idx])
            self._actualizar_cabecera_y_nav()
            self.cargar_mes()

    def _ir_por_legajo(self) -> None:
        raw = self.txt_nav_legajo.text().strip()
        if not raw:
            return
        try:
            leg_buscado = int(raw)
        except ValueError:
            QMessageBox.warning(self, "Legajo inválido", "Ingrese un número de legajo válido.")
            return

        for lista in (self._empleados_nav, self._todos_empleados_ciclo):
            for item in lista:
                try:
                    if int(item.get("legajo")) == leg_buscado:
                        self._seleccionar_empleado(item)
                        return
                except (TypeError, ValueError):
                    continue

        QMessageBox.information(
            self,
            "No encontrado",
            f"No se encontró personal de ciclo activo con legajo {leg_buscado}.",
        )

    def _on_seleccionar_nombre(self, index: int) -> None:
        if index < 0:
            return
        etiqueta = self.combo_nav_nombre.itemText(index)
        emp = self._mapa_busqueda_emp.get(etiqueta)
        if emp:
            self._seleccionar_empleado(emp)

    def _ir_por_nombre_texto(self) -> None:
        texto = self.combo_nav_nombre.currentText().strip()
        if not texto:
            return
        if texto in self._mapa_busqueda_emp:
            self._seleccionar_empleado(self._mapa_busqueda_emp[texto])
            return
        t_low = texto.lower()
        for etiqueta, emp in self._mapa_busqueda_emp.items():
            if t_low in etiqueta.lower():
                self._seleccionar_empleado(emp)
                return
        QMessageBox.information(
            self,
            "No encontrado",
            f"No se encontró personal de ciclo cuyo nombre coincida con '{texto}'.",
        )

    def cargar_mes(self) -> None:
        leg = int(self.empleado["legajo"])
        self._dias_datos = self.records_service.obtener_calendario_mensual_ciclo(leg, self.anio, self.mes)
        self.table_model.set_data(self._dias_datos)

    def _on_mes_anio_changed(self) -> None:
        self.mes = self.combo_mes.currentIndex() + 1
        self.anio = self.spin_anio.value()
        self.cargar_mes()

    def _obtener_dia_seleccionado(self) -> Optional[Dict[str, Any]]:
        indexes = self.table_view.selectionModel().selectedRows()
        if not indexes:
            return None
        return self.table_model.get_row(indexes[0].row())

    def _asignar_jornada_seleccionada(self) -> None:
        if not self._can("reemplazos.asignar"):
            QMessageBox.warning(self, "Permiso Denegado", "No posee permiso para asignar o modificar jornadas de ciclo.")
            return

        dia_info = self._obtener_dia_seleccionado()
        if not dia_info:
            QMessageBox.information(self, "Asignar", "Seleccione un día en la tabla.")
            return

        dlg = AsignarJornadaDialog(
            legajo=int(self.empleado["legajo"]),
            nombre=str(self.empleado["apellidos_nombres"]),
            fecha_iso=dia_info["fecha_iso"],
            dia_semana=dia_info["dia_semana"],
            jornada_existente=dia_info,
            franco_agente=str(self.empleado.get("franco") or "Domingo"),
            records_service=self.records_service,
            parent=self,
        )
        if dlg.exec():
            res = dlg.datos_resultado
            if res:
                try:
                    self.records_service.guardar_jornada_ciclo(
                        legajo=int(self.empleado["legajo"]),
                        fecha=dia_info["fecha_iso"],
                        toma=res["toma"],
                        deja=res["deja"],
                        franco_anticipado=res["franco_anticipado"],
                        observaciones=res["observaciones"],
                        user_id=self.usuario_actual.get("id"),
                        windows_user=self.usuario_actual.get("username"),
                    )
                    self.cargar_mes()
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"No se pudo guardar la jornada:\n{e}")

    def _on_row_double_clicked(self, index) -> None:
        dia_info = self.table_model.get_row(index.row())
        if not dia_info:
            return

        # Si el día tiene una novedad asociada, podemos informar
        if dia_info.get("novedad_id"):
            QMessageBox.information(
                self,
                f"Relevo Asignado - Novedad #{dia_info['novedad_id']}",
                f"Agente Titular: {dia_info.get('reemplaza_a')}\n"
                f"Turno cubierto: {dia_info.get('turno_cubierto')}\n"
                f"Período del relevo: {dia_info.get('periodo')}\n\n"
                f"Para modificar el turno u horas de toma/deja, utilice 'Asignar Jornada'.",
            )
        else:
            if not self._can("reemplazos.asignar"):
                return
            self._asignar_jornada_seleccionada()

    def _exportar_pdf(self) -> None:
        default_name = f"Calendario_Ciclo_{self.empleado.get('legajo')}_{self.anio}_{self.mes:02d}.pdf"
        file_path, _ = QFileDialog.getSaveFileName(self, "Guardar Cronograma Mensual PDF", default_name, "Archivo PDF (*.pdf)")
        if not file_path:
            return

        try:
            self.pdf_service.generar_calendario_ciclo_pdf(
                self.empleado,
                self._dias_datos,
                self.mes,
                self.anio,
                file_path,
            )
            QMessageBox.information(self, "Exportación", f"Cronograma PDF generado exitosamente en:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo generar el PDF:\n{e}")

    def _exportar_excel_individual(self) -> None:
        default_name = f"Calendario_Ciclo_{self.empleado.get('legajo')}_{self.anio}_{self.mes:02d}.xlsx"
        file_path, _ = QFileDialog.getSaveFileName(self, "Guardar Cronograma Mensual Excel", default_name, "Excel (*.xlsx)")
        if not file_path:
            return
        try:
            self.excel_service.export_calendario_ciclo_individual(
                file_path,
                self.empleado,
                self._dias_datos,
                self.mes,
                self.anio,
            )
            QMessageBox.information(self, "Exportación", f"Cronograma Excel generado exitosamente en:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo generar el archivo Excel:\n{e}")

    def _exportar_excel_todos(self) -> None:
        default_name = f"Calendario_Ciclo_Todos_{self.anio}_{self.mes:02d}.xlsx"
        file_path, _ = QFileDialog.getSaveFileName(self, "Guardar Consolidado de Todo el Personal de Ciclo", default_name, "Excel (*.xlsx)")
        if not file_path:
            return
        try:
            self.excel_service.export_calendario_ciclo_todos(
                file_path,
                self.records_service,
                self.mes,
                self.anio,
            )
            QMessageBox.information(self, "Exportación", f"Consolidado general de ciclo exportado exitosamente en:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo exportar el consolidado general:\n{e}")
