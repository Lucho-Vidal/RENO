"""Servicio de generación de documentos PDF con ReportLab.

Genera constancias formales de Novedades, Cambios de Turno,
Calendarios Mensuales de Personal de Ciclo e Informes Ejecutivos del Dashboard.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch, cm
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.core.logger import get_logger

logger = get_logger("PDFService")

# Paleta corporativa unificada con CRONO-PCT
PRIMARY = colors.HexColor("#1F497D")
SECONDARY = colors.HexColor("#4682B4")
ACCENT = colors.HexColor("#3CB371")
BG_LIGHT = colors.HexColor("#F4F6F9")
TEXT_DARK = colors.HexColor("#2C3E50")
BORDER_COLOR = colors.HexColor("#BDC3C7")


def _esc(val: Any, default: str = "") -> str:
    """Escapa texto para inserción segura en párrafos XML de ReportLab."""
    raw = default if val is None else str(val)
    return escape(raw)


def _esc_nl(val: Any, default: str = "") -> str:
    """Escapa texto XML y convierte saltos de línea en <br/>."""
    return _esc(val, default).replace("\n", "<br/>")


class PDFService:
    """Generador de documentos PDF para RENO v2.0.0."""

    @staticmethod
    def _crear_estilos() -> Dict[str, ParagraphStyle]:
        styles = getSampleStyleSheet()
        custom = {
            "Titulo": ParagraphStyle(
                "DocTitulo",
                parent=styles["Normal"],
                fontName="Helvetica-Bold",
                fontSize=16,
                leading=20,
                textColor=PRIMARY,
                alignment=1,  # Centrado
            ),
            "Subtitulo": ParagraphStyle(
                "DocSubtitulo",
                parent=styles["Normal"],
                fontName="Helvetica",
                fontSize=10,
                leading=14,
                textColor=TEXT_DARK,
                alignment=1,
            ),
            "Seccion": ParagraphStyle(
                "DocSeccion",
                parent=styles["Normal"],
                fontName="Helvetica-Bold",
                fontSize=12,
                leading=16,
                textColor=PRIMARY,
                spaceBefore=10,
                spaceAfter=4,
            ),
            "NormalCustom": ParagraphStyle(
                "DocNormal",
                parent=styles["Normal"],
                fontName="Helvetica",
                fontSize=9,
                leading=12,
                textColor=TEXT_DARK,
            ),
            "TablaHeader": ParagraphStyle(
                "DocTablaHeader",
                parent=styles["Normal"],
                fontName="Helvetica-Bold",
                fontSize=9,
                leading=11,
                textColor=colors.white,
                alignment=1,
            ),
            "TablaCell": ParagraphStyle(
                "DocTablaCell",
                parent=styles["Normal"],
                fontName="Helvetica",
                fontSize=8,
                leading=10,
                textColor=TEXT_DARK,
            ),
            "TablaCellCenter": ParagraphStyle(
                "DocTablaCellCenter",
                parent=styles["Normal"],
                fontName="Helvetica",
                fontSize=8,
                leading=10,
                textColor=TEXT_DARK,
                alignment=1,
            ),
        }
        return custom

    # ---------------------------------------------------------
    # Constancia de Novedad Operativa
    # ---------------------------------------------------------

    def generar_constancia_novedad(
        self,
        novedad: Dict[str, Any],
        tramos: List[Dict[str, Any]],
        output_path: str,
    ) -> str:
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(
            str(out_file),
            pagesize=A4,
            leftMargin=1.5 * cm,
            rightMargin=1.5 * cm,
            topMargin=1.5 * cm,
            bottomMargin=1.5 * cm,
        )

        estilos = self._crear_estilos()
        story = []

        # Cabecera
        story.append(Paragraph("TRENES ARGENTINOS OPERACIONES", estilos["Subtitulo"]))
        story.append(Paragraph("CONSTANCIA DE NOVEDAD OPERATIVA", estilos["Titulo"]))
        story.append(Paragraph(f"Registro N°: {_esc(novedad.get('id', '-'))} | Fecha: {_esc(novedad.get('registrado_en', '-'))}", estilos["Subtitulo"]))
        story.append(Spacer(1, 10))
        story.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY, spaceBefore=0, spaceAfter=15))

        # Datos del Titular
        story.append(Paragraph("1. DATOS DEL TITULAR Y NOVEDAD", estilos["Seccion"]))
        datos_titular = [
            [
                Paragraph("<b>Legajo:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(novedad.get("legajo", "-")), estilos["NormalCustom"]),
                Paragraph("<b>Apellidos y Nombres:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(novedad.get("apellidos_nombres", "-")), estilos["NormalCustom"]),
            ],
            [
                Paragraph("<b>Especialidad:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(novedad.get("especialidad", "-")), estilos["NormalCustom"]),
                Paragraph("<b>Dotación:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(novedad.get("dotacion", "-")), estilos["NormalCustom"]),
            ],
            [
                Paragraph("<b>Turno Habitual:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(novedad.get("turnos", "-")), estilos["NormalCustom"]),
                Paragraph("<b>Franco:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(novedad.get("franco", "-")), estilos["NormalCustom"]),
            ],
            [
                Paragraph("<b>Tipo de Novedad:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(novedad.get("novedad", "-")), estilos["NormalCustom"]),
                Paragraph("<b>Período:</b>", estilos["NormalCustom"]),
                Paragraph(f"{_esc(novedad.get('fecha_inicio', '-'))} al {_esc(novedad.get('fecha_fin') or 'Abierto')}", estilos["NormalCustom"]),
            ],
            [
                Paragraph("<b>Ref. Estación:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(novedad.get("referencia_estacion", "-")), estilos["NormalCustom"]),
                Paragraph("<b>Supervisor:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(novedad.get("supervisor", "-")), estilos["NormalCustom"]),
            ],
        ]
        t_titular = Table(datos_titular, colWidths=[3.2 * cm, 5.5 * cm, 3.8 * cm, 5.5 * cm])
        t_titular.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t_titular)
        story.append(Spacer(1, 15))

        # Tramos de Cobertura / Relevos
        story.append(Paragraph("2. COBERTURA ASIGNADA (PERSONAL DE CICLO)", estilos["Seccion"]))
        if tramos:
            encabezados = [
                Paragraph("Legajo", estilos["TablaHeader"]),
                Paragraph("Personal de Reemplazo", estilos["TablaHeader"]),
                Paragraph("Turno Asignado", estilos["TablaHeader"]),
                Paragraph("Desde", estilos["TablaHeader"]),
                Paragraph("Hasta", estilos["TablaHeader"]),
            ]
            filas_tramos = [encabezados]
            for t in tramos:
                filas_tramos.append([
                    Paragraph(_esc(t.get("legajo_reemplazo", "-")), estilos["TablaCellCenter"]),
                    Paragraph(_esc(t.get("apellidos_nombres_reemplazo", "-")), estilos["TablaCell"]),
                    Paragraph(_esc(t.get("turnos_reemplazo", "-")), estilos["TablaCellCenter"]),
                    Paragraph(_esc(t.get("fecha_inicio", "-")), estilos["TablaCellCenter"]),
                    Paragraph(_esc(t.get("fecha_fin") or "Abierto"), estilos["TablaCellCenter"]),
                ])
            t_reemplazos = Table(filas_tramos, colWidths=[2.2 * cm, 7.5 * cm, 3.5 * cm, 2.4 * cm, 2.4 * cm])
            t_reemplazos.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(t_reemplazos)
        else:
            story.append(Paragraph("<i>Sin personal de relevo asignado (Turno descubierto o no aplica).</i>", estilos["NormalCustom"]))

        story.append(Spacer(1, 15))

        # Observaciones
        story.append(Paragraph("3. OBSERVACIONES", estilos["Seccion"]))
        obs_text = _esc_nl(novedad.get("observaciones") or "Sin observaciones registradas.")
        t_obs = Table([[Paragraph(obs_text, estilos["NormalCustom"])]], colWidths=[18 * cm])
        t_obs.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(t_obs)
        story.append(Spacer(1, 40))

        # Firmas
        firmas = [
            [
                Paragraph("_______________________________<br/>Firma del Agente", estilos["TablaCellCenter"]),
                Paragraph("_______________________________<br/>Firma y Sello del Supervisor", estilos["TablaCellCenter"]),
            ]
        ]
        t_firmas = Table(firmas, colWidths=[9 * cm, 9 * cm])
        t_firmas.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(KeepTogether(t_firmas))

        doc.build(story)
        return str(out_file)

    # ---------------------------------------------------------
    # Constancia de Cambio de Turno
    # ---------------------------------------------------------

    def generar_constancia_cambio(
        self,
        cambio: Dict[str, Any],
        output_path: str,
    ) -> str:
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(
            str(out_file),
            pagesize=A4,
            leftMargin=1.5 * cm,
            rightMargin=1.5 * cm,
            topMargin=1.5 * cm,
            bottomMargin=1.5 * cm,
        )

        estilos = self._crear_estilos()
        story = []

        # Cabecera
        story.append(Paragraph("TRENES ARGENTINOS OPERACIONES", estilos["Subtitulo"]))
        story.append(Paragraph("SOLICITUD / CONSTANCIA DE CAMBIO DE TURNO", estilos["Titulo"]))
        story.append(Paragraph(f"Registro N°: {_esc(cambio.get('id', '-'))} | Fecha de Registro: {_esc(cambio.get('registrado_en', '-'))}", estilos["Subtitulo"]))
        story.append(Spacer(1, 10))
        story.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY, spaceBefore=0, spaceAfter=15))

        # Agente 1
        story.append(Paragraph("1. DATOS DEL PRIMER INTERVINIENTE (SOLICITANTE)", estilos["Seccion"]))
        datos_agente1 = [
            [
                Paragraph("<b>Legajo:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(cambio.get("legajo_1", "-")), estilos["NormalCustom"]),
                Paragraph("<b>Apellidos y Nombres:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(cambio.get("apellidos_nombres_1", "-")), estilos["NormalCustom"]),
            ],
            [
                Paragraph("<b>Especialidad:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(cambio.get("especialidad_1", "-")), estilos["NormalCustom"]),
                Paragraph("<b>Dotación:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(cambio.get("dotacion_1", "-")), estilos["NormalCustom"]),
            ],
            [
                Paragraph("<b>Turno Original:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(cambio.get("turnos_1", "-")), estilos["NormalCustom"]),
                Paragraph("<b>Franco:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(cambio.get("franco_1", "-")), estilos["NormalCustom"]),
            ],
        ]
        t1 = Table(datos_agente1, colWidths=[3.2 * cm, 5.5 * cm, 3.8 * cm, 5.5 * cm])
        t1.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t1)
        story.append(Spacer(1, 12))

        # Agente 2
        story.append(Paragraph("2. DATOS DEL SEGUNDO INTERVINIENTE (RECEPTOR)", estilos["Seccion"]))
        datos_agente2 = [
            [
                Paragraph("<b>Legajo:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(cambio.get("legajo_2", "-")), estilos["NormalCustom"]),
                Paragraph("<b>Apellidos y Nombres:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(cambio.get("apellidos_nombres_2", "-")), estilos["NormalCustom"]),
            ],
            [
                Paragraph("<b>Especialidad:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(cambio.get("especialidad_2", "-")), estilos["NormalCustom"]),
                Paragraph("<b>Dotación:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(cambio.get("dotacion_2", "-")), estilos["NormalCustom"]),
            ],
            [
                Paragraph("<b>Turno Original:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(cambio.get("turnos_2", "-")), estilos["NormalCustom"]),
                Paragraph("<b>Franco:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(cambio.get("franco_2", "-")), estilos["NormalCustom"]),
            ],
        ]
        t2 = Table(datos_agente2, colWidths=[3.2 * cm, 5.5 * cm, 3.8 * cm, 5.5 * cm])
        t2.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t2)
        story.append(Spacer(1, 12))

        # Fecha de Cambio y Autorizaciones
        story.append(Paragraph("3. DETALLE DE LA PERMUTA Y REFERENCIAS", estilos["Seccion"]))
        datos_permuta = [
            [
                Paragraph("<b>Fecha del Cambio:</b>", estilos["NormalCustom"]),
                Paragraph(f"<b>{_esc(cambio.get('fecha_cambio', '-'))}</b>", estilos["NormalCustom"]),
                Paragraph("<b>Ref. Estación:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(cambio.get("referencia_estacion", "-")), estilos["NormalCustom"]),
            ],
            [
                Paragraph("<b>Supervisor:</b>", estilos["NormalCustom"]),
                Paragraph(_esc(cambio.get("supervisor", "-")), estilos["NormalCustom"]),
                Paragraph("<b>Observaciones:</b>", estilos["NormalCustom"]),
                Paragraph(_esc_nl(cambio.get("observaciones") or "Sin observaciones"), estilos["NormalCustom"]),
            ],
        ]
        t_permuta = Table(datos_permuta, colWidths=[3.2 * cm, 5.5 * cm, 3.8 * cm, 5.5 * cm])
        t_permuta.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t_permuta)
        story.append(Spacer(1, 40))

        # Firmas Tripartitas
        firmas = [
            [
                Paragraph("________________________<br/>Firma Agente 1", estilos["TablaCellCenter"]),
                Paragraph("________________________<br/>Firma Agente 2", estilos["TablaCellCenter"]),
                Paragraph("________________________<br/>Firma Supervisor", estilos["TablaCellCenter"]),
            ]
        ]
        t_firmas = Table(firmas, colWidths=[6 * cm, 6 * cm, 6 * cm])
        t_firmas.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(KeepTogether(t_firmas))

        doc.build(story)
        return str(out_file)

    # ---------------------------------------------------------
    # Calendario Mensual de Personal de Ciclo
    # ---------------------------------------------------------

    def generar_calendario_ciclo_pdf(
        self,
        empleado: Dict[str, Any],
        dias_mes: List[Dict[str, Any]],
        mes: int,
        anio: int,
        output_path: str,
    ) -> str:
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(
            str(out_file),
            pagesize=A4,
            leftMargin=1.0 * cm,
            rightMargin=1.0 * cm,
            topMargin=1.0 * cm,
            bottomMargin=1.0 * cm,
        )

        estilos = self._crear_estilos()
        story = []

        nombre_meses = [
            "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
        ]
        mes_txt = f"{nombre_meses[mes]} {anio}"

        # Cabecera
        story.append(Paragraph(f"CRONOGRAMA MENSUAL - PERSONAL DE CICLO: {mes_txt.upper()}", estilos["Titulo"]))
        sub = (
            f"Agente: <b>{_esc(empleado.get('apellidos_nombres'))}</b> (Leg. {_esc(empleado.get('legajo'))}) | "
            f"Dotación: {_esc(empleado.get('dotacion'))} | Esp: {_esc(empleado.get('especialidad'))} | "
            f"Jornada CCT: {_esc(empleado.get('jornada_horas', 6))} hs"
        )
        story.append(Paragraph(sub, estilos["Subtitulo"]))
        story.append(Spacer(1, 8))

        # Tabla mensual
        headers = [
            Paragraph("Día", estilos["TablaHeader"]),
            Paragraph("Fecha", estilos["TablaHeader"]),
            Paragraph("Día Sem", estilos["TablaHeader"]),
            Paragraph("Estado", estilos["TablaHeader"]),
            Paragraph("Disp.", estilos["TablaHeader"]),
            Paragraph("Toma", estilos["TablaHeader"]),
            Paragraph("Deja", estilos["TablaHeader"]),
            Paragraph("Hs Trab", estilos["TablaHeader"]),
            Paragraph("Hs Sem", estilos["TablaHeader"]),
            Paragraph("Reemplaza a / Motivo", estilos["TablaHeader"]),
        ]
        filas = [headers]

        for d in dias_mes:
            estado = _esc(d.get("estado", ""))
            est_style = estilos["TablaCellCenter"]
            filas.append([
                Paragraph(_esc(d.get("dia")), estilos["TablaCellCenter"]),
                Paragraph(_esc(d.get("fecha")), estilos["TablaCellCenter"]),
                Paragraph(_esc(str(d.get("dia_semana", ""))[:3]), estilos["TablaCellCenter"]),
                Paragraph(estado, est_style),
                Paragraph(_esc(d.get("hora_disponible", "-")), estilos["TablaCellCenter"]),
                Paragraph(_esc(d.get("toma", "-")), estilos["TablaCellCenter"]),
                Paragraph(_esc(d.get("deja", "-")), estilos["TablaCellCenter"]),
                Paragraph(_esc(d.get("horas_trabajadas", "-")), estilos["TablaCellCenter"]),
                Paragraph(_esc(d.get("hs_semana", "-")), estilos["TablaCellCenter"]),
                Paragraph(_esc(d.get("reemplaza_a") if d.get("reemplaza_a") != "-" else d.get("observaciones", "-")), estilos["TablaCell"]),
            ])

        col_w = [1.0 * cm, 1.8 * cm, 1.4 * cm, 2.8 * cm, 1.4 * cm, 1.4 * cm, 1.4 * cm, 1.5 * cm, 1.5 * cm, 4.8 * cm]
        t = Table(filas, colWidths=col_w, repeatRows=1)
        t_style = [
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]

        # Resaltado de fines de semana o francos
        for idx, d in enumerate(dias_mes, start=1):
            if "Franco" in str(d.get("estado")):
                t_style.append(("BACKGROUND", (0, idx), (-1, idx), colors.HexColor("#FFF2CC")))
            elif str(d.get("estado")) == "Reemplazando":
                t_style.append(("BACKGROUND", (0, idx), (-1, idx), colors.HexColor("#E2EFDA")))

        t.setStyle(TableStyle(t_style))
        story.append(t)

        doc.build(story)
        return str(out_file)

    def generar_servicio_ciclo_rango_pdf(
        self,
        dias_datos: List[Dict[str, Any]],
        fecha_desde: str,
        fecha_hasta: str,
        output_path: str,
        dotacion: Optional[str] = None,
        especialidad: Optional[str] = None,
    ) -> str:
        """Genera un PDF en hoja horizontal (landscape A4) del Personal de Ciclo
        filtrado por rango de fechas, dotación y especialidad."""
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        estilos = self._crear_estilos()
        st_day = ParagraphStyle(
            "CicloRangoDay",
            parent=estilos["Subtitulo"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=PRIMARY,
            spaceBefore=6,
            spaceAfter=3,
        )
        st_sec = ParagraphStyle(
            "CicloRangoSec",
            parent=estilos["TablaCell"],
            fontName="Helvetica-Bold",
            fontSize=8.2,
            leading=10.5,
            textColor=TEXT_DARK,
            spaceBefore=2,
            spaceAfter=2,
        )
        st_empty = ParagraphStyle(
            "CicloRangoEmpty",
            parent=estilos["TablaCell"],
            fontName="Helvetica-Oblique",
            fontSize=7.8,
            leading=10,
            textColor=colors.HexColor("#64748B"),
            spaceAfter=3,
        )
        st_th = ParagraphStyle(
            "CicloRangoTH",
            parent=estilos["TablaHeader"],
            fontSize=7.2,
            leading=9,
        )
        st_td = ParagraphStyle(
            "CicloRangoTD",
            parent=estilos["TablaCell"],
            fontSize=7.2,
            leading=9.2,
        )
        st_td_c = ParagraphStyle(
            "CicloRangoTDC",
            parent=estilos["TablaCellCenter"],
            fontSize=7.2,
            leading=9.2,
        )

        def _p(txt: Any, st: ParagraphStyle) -> Paragraph:
            return Paragraph(escape(str(txt if txt is not None else "")), st)

        dot_label = dotacion if (dotacion and dotacion != "Todas") else "Todas"
        esp_label = especialidad if (especialidad and especialidad != "Todas") else "Todas"
        generado_str = datetime.now().strftime("%d/%m/%Y %H:%M")

        story: List[Any] = [
            _p("INFORME DE SERVICIO — PERSONAL DE CICLO", estilos["Titulo"]),
            _p(
                f"Período: {fecha_desde} al {fecha_hasta}   |   "
                f"Dotación: {dot_label}   |   Especialidad: {esp_label}   |   Generado: {generado_str}",
                estilos["Subtitulo"],
            ),
            HRFlowable(width="100%", thickness=1, color=PRIMARY, spaceAfter=6),
        ]

        # A4 horizontal (29.7 cm - 2.0 cm márgenes = 27.7 cm útiles)
        anchos_libres = [1.7 * cm, 6.0 * cm, 3.8 * cm, 2.2 * cm, 2.8 * cm, 2.5 * cm, 2.3 * cm, 6.4 * cm]
        anchos_ocup = [1.6 * cm, 4.8 * cm, 3.2 * cm, 1.9 * cm, 5.2 * cm, 2.6 * cm, 4.0 * cm, 2.2 * cm, 2.2 * cm]

        estilo_base = [
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, BORDER_COLOR),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BG_LIGHT]),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]

        for idx_dia, dia in enumerate(dias_datos):
            libres = dia.get("libres", [])
            ocupados = dia.get("ocupados", [])
            con_nov = dia.get("con_novedad", [])

            dia_titulo = _p(
                f"Fecha: {dia['fecha']} ({dia['dia_semana']}) — "
                f"Disponibles: {len(libres)}   |   Asignados: {len(ocupados)}   |   Con novedad: {len(con_nov)}",
                st_day,
            )

            # Tabla 1: Disponibles
            sec_lib = _p("Personal de Ciclo DISPONIBLE", st_sec)
            if libres:
                rows_lib = [[
                    _p("LEGAJO", st_th), _p("APELLIDOS Y NOMBRES", st_th), _p("ESPECIALIDAD", st_th),
                    _p("DOTACIÓN", st_th), _p("TURNO", st_th), _p("FRANCO", st_th),
                    _p("HOR. FRANCO", st_th), _p("ESTADO EN FECHA", st_th),
                ]]
                for emp in libres:
                    rows_lib.append([
                        _p(emp.get("legajo", ""), st_td_c),
                        _p(emp.get("apellidos_nombres", ""), st_td),
                        _p(emp.get("especialidad", ""), st_td_c),
                        _p(emp.get("dotacion", ""), st_td_c),
                        _p(emp.get("turnos", ""), st_td_c),
                        _p(emp.get("franco", ""), st_td_c),
                        _p(emp.get("franco_horario", "") or "-", st_td_c),
                        _p(emp.get("estado_dia") or "Disponible", st_td),
                    ])
                tbl_lib = Table(rows_lib, colWidths=anchos_libres, repeatRows=1)
                tbl_lib.setStyle(TableStyle(estilo_base))
                story.append(KeepTogether([dia_titulo, sec_lib, tbl_lib]))
            else:
                story.append(KeepTogether([dia_titulo, sec_lib, _p("Sin personal de ciclo disponible en esta fecha.", st_empty)]))

            story.append(Spacer(1, 0.15 * cm))

            # Tabla 2: Asignados + Con novedad propia activa
            sec_oc = _p("Personal de Ciclo ASIGNADO / CON NOVEDAD ACTIVA", st_sec)
            combinados = []
            for emp in ocupados:
                combinados.append((False, emp))
            for emp in con_nov:
                combinados.append((True, emp))

            if combinados:
                rows_oc = [[
                    _p("LEGAJO", st_th), _p("APELLIDOS Y NOMBRES", st_th), _p("ESPECIALIDAD", st_th),
                    _p("DOTACIÓN", st_th), _p("REEMPLAZA A / NOVEDAD", st_th), _p("TURNO CUB.", st_th),
                    _p("MOTIVO", st_th), _p("DESDE", st_th), _p("HASTA", st_th),
                ]]
                t_style_oc = list(estilo_base)
                for row_i, (es_nov, emp) in enumerate(combinados, start=1):
                    if es_nov:
                        nid = emp.get("novedad_propia_id", "")
                        reemp_txt = f"NOVEDAD PROPIA #{nid}" if nid else "NOVEDAD PROPIA"
                        turno_txt = "-"
                        motivo_txt = emp.get("novedad_propia") or "-"
                        desde_txt = emp.get("novedad_fecha_inicio") or "-"
                        hasta_txt = emp.get("novedad_fecha_fin") or "Sin fecha fin"
                        t_style_oc.append(("BACKGROUND", (0, row_i), (-1, row_i), colors.HexColor("#FFEBEE")))
                        t_style_oc.append(("TEXTCOLOR", (0, row_i), (-1, row_i), colors.HexColor("#B71C1C")))
                    else:
                        reemp_txt = emp.get("reemplaza_a") or "-"
                        turno_txt = emp.get("turno_titular") or "-"
                        motivo_txt = emp.get("tipo_novedad") or "-"
                        desde_txt = emp.get("fecha_inicio_relevo") or "-"
                        hasta_txt = emp.get("fecha_fin_relevo") or "Sin fecha fin"

                    rows_oc.append([
                        _p(emp.get("legajo", ""), st_td_c),
                        _p(emp.get("apellidos_nombres", ""), st_td),
                        _p(emp.get("especialidad", ""), st_td_c),
                        _p(emp.get("dotacion", ""), st_td_c),
                        _p(reemp_txt, st_td),
                        _p(turno_txt, st_td_c),
                        _p(motivo_txt, st_td),
                        _p(desde_txt, st_td_c),
                        _p(hasta_txt, st_td_c),
                    ])
                tbl_oc = Table(rows_oc, colWidths=anchos_ocup, repeatRows=1)
                tbl_oc.setStyle(TableStyle(t_style_oc))
                story.append(KeepTogether([sec_oc, tbl_oc]))
            else:
                story.append(KeepTogether([sec_oc, _p("Sin personal de ciclo asignado ni con novedad en esta fecha.", st_empty)]))

            if idx_dia < len(dias_datos) - 1:
                story.append(Spacer(1, 0.22 * cm))
                story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER_COLOR, spaceAfter=4))

        def _footer(canvas_obj, doc_obj):
            canvas_obj.saveState()
            canvas_obj.setFont("Helvetica", 7.5)
            canvas_obj.setFillColor(colors.HexColor("#64748B"))
            canvas_obj.drawString(1.0 * cm, 0.55 * cm, f"RENO — Personal de Ciclo ({dot_label}) | Generado: {generado_str}")
            canvas_obj.drawRightString(landscape(A4)[0] - 1.0 * cm, 0.55 * cm, f"Página {doc_obj.page}")
            canvas_obj.restoreState()

        doc = SimpleDocTemplate(
            str(out_file),
            pagesize=landscape(A4),
            leftMargin=1.0 * cm,
            rightMargin=1.0 * cm,
            topMargin=1.0 * cm,
            bottomMargin=1.05 * cm,
            title=f"Personal de Ciclo ({fecha_desde} al {fecha_hasta})",
        )
        doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
        return str(out_file)

    # ---------------------------------------------------------
    # Informe Ejecutivo del Dashboard (Requerimiento Usuario)
    # ---------------------------------------------------------

    def generar_informe_dashboard_pdf(
        self,
        metricas: Dict[str, Any],
        output_path: str,
        periodo_dias: int = 30,
        chart_image_paths: Optional[List[str]] = None,
    ) -> str:
        """Genera un informe ejecutivo del Panel de Control con KPIs, tablas detalladas y gráficos."""
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(
            str(out_file),
            pagesize=A4,
            leftMargin=1.5 * cm,
            rightMargin=1.5 * cm,
            topMargin=1.5 * cm,
            bottomMargin=1.5 * cm,
        )

        estilos = self._crear_estilos()
        story = []

        # Título
        story.append(Paragraph("TRENES ARGENTINOS OPERACIONES", estilos["Subtitulo"]))
        story.append(Paragraph("INFORME EJECUTIVO - PANEL DE CONTROL (RENO v2.0)", estilos["Titulo"]))
        story.append(Paragraph(
            f"Período de Análisis: Últimos {periodo_dias} días | Generado el {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
            estilos["Subtitulo"]
        ))
        story.append(Spacer(1, 10))
        story.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY, spaceBefore=0, spaceAfter=15))

        # 1. Resumen de KPIs
        story.append(Paragraph("1. RESUMEN DE INDICADORES CLAVE (KPIS)", estilos["Seccion"]))
        resumen = metricas.get("resumen", {})
        kpis = [
            [
                Paragraph("<b>Novedades Hoy:</b>", estilos["NormalCustom"]),
                Paragraph(str(resumen.get("novedades_hoy", 0)), estilos["NormalCustom"]),
                Paragraph("<b>Cambios Hoy:</b>", estilos["NormalCustom"]),
                Paragraph(str(resumen.get("cambios_hoy", 0)), estilos["NormalCustom"]),
            ],
            [
                Paragraph("<b>Total Novedades Mes:</b>", estilos["NormalCustom"]),
                Paragraph(str(resumen.get("novedades_mes", 0)), estilos["NormalCustom"]),
                Paragraph("<b>Total Cambios Mes:</b>", estilos["NormalCustom"]),
                Paragraph(str(resumen.get("cambios_mes", 0)), estilos["NormalCustom"]),
            ],
            [
                Paragraph("<b>Volumen Total Mensual:</b>", estilos["NormalCustom"]),
                Paragraph(f"<b>{resumen.get('total_mes', 0)} registros</b>", estilos["NormalCustom"]),
                Paragraph("<b>Estado del Sistema:</b>", estilos["NormalCustom"]),
                Paragraph("Operativo / Sincronizado", estilos["NormalCustom"]),
            ],
        ]
        t_kpi = Table(kpis, colWidths=[4.5 * cm, 4.5 * cm, 4.5 * cm, 4.5 * cm])
        t_kpi.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(t_kpi)
        story.append(Spacer(1, 15))

        # Si se suministraron capturas de gráficos PNG desde la UI, incrustarlas
        if chart_image_paths:
            story.append(Paragraph("GRÁFICOS OPERATIVOS DEL SISTEMA", estilos["Seccion"]))
            for img_p in chart_image_paths:
                if Path(img_p).exists():
                    story.append(Image(str(img_p), width=17 * cm, height=6.5 * cm))
                    story.append(Spacer(1, 10))

        # 2. Desglose de Novedades por Tipo
        story.append(Paragraph("2. NOVEDADES REGISTRADAS POR TIPO (TOP)", estilos["Seccion"]))
        por_tipo = metricas.get("por_tipo", [])
        total_tipo = sum(v for _, v in por_tipo) or 1
        filas_tipo = [
            [Paragraph("Tipo de Novedad", estilos["TablaHeader"]), Paragraph("Cantidad", estilos["TablaHeader"]), Paragraph("Participación", estilos["TablaHeader"])]
        ]
        for t, c in por_tipo[:12]:
            filas_tipo.append([
                Paragraph(_esc(t), estilos["TablaCell"]),
                Paragraph(_esc(c), estilos["TablaCellCenter"]),
                Paragraph(f"{(c / total_tipo) * 100:.1f}%", estilos["TablaCellCenter"]),
            ])
        t_tipo = Table(filas_tipo, colWidths=[10 * cm, 4 * cm, 4 * cm])
        t_tipo.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t_tipo)
        story.append(Spacer(1, 15))

        # 3. Registros por Dotación
        story.append(Paragraph("3. DISTRIBUCIÓN POR DOTACIÓN", estilos["Seccion"]))
        por_dot = metricas.get("por_dotacion", [])
        total_dot = sum(v for _, v in por_dot) or 1
        filas_dot = [
            [Paragraph("Dotación", estilos["TablaHeader"]), Paragraph("Cantidad", estilos["TablaHeader"]), Paragraph("Participación", estilos["TablaHeader"])]
        ]
        for d, c in por_dot[:10]:
            filas_dot.append([
                Paragraph(_esc(d), estilos["TablaCell"]),
                Paragraph(_esc(c), estilos["TablaCellCenter"]),
                Paragraph(f"{(c / total_dot) * 100:.1f}%", estilos["TablaCellCenter"]),
            ])
        t_dot = Table(filas_dot, colWidths=[10 * cm, 4 * cm, 4 * cm])
        t_dot.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), SECONDARY),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t_dot)
        story.append(Spacer(1, 15))

        # 4. Registros por Día de Semana
        story.append(Paragraph("4. DISTRIBUCIÓN POR DÍA DE LA SEMANA", estilos["Seccion"]))
        por_dia = metricas.get("por_dia_semana", [])
        filas_dia = [
            [Paragraph("Día", estilos["TablaHeader"]), Paragraph("Cantidad de Registros", estilos["TablaHeader"])]
        ]
        for dia, cant in por_dia:
            filas_dia.append([
                Paragraph(_esc(dia), estilos["TablaCell"]),
                Paragraph(_esc(cant), estilos["TablaCellCenter"]),
            ])
        t_dia = Table(filas_dia, colWidths=[9 * cm, 9 * cm])
        t_dia.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t_dia)

        doc.build(story)
        return str(out_file)

    # ---------------------------------------------------------
    # Ordenamiento Operativo (Orden de Servicio por Duplicado en A4)
    # ---------------------------------------------------------

    def generar_orden_servicio_duplicado(
        self,
        ordenamiento: Dict[str, Any],
        tramos: List[Dict[str, Any]],
        output_path: str,
    ) -> str:
        """Genera la Orden de Servicio por duplicado (Original + Duplicado en la misma hoja A4 vertical)."""
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(
            str(out_file),
            pagesize=A4,
            leftMargin=1.0 * cm,
            rightMargin=1.0 * cm,
            topMargin=0.8 * cm,
            bottomMargin=0.8 * cm,
        )

        estilos = self._crear_estilos()
        st_header_box = ParagraphStyle(
            "OrdHeaderBox",
            parent=estilos["NormalCustom"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=13,
            textColor=PRIMARY,
        )
        st_small = ParagraphStyle(
            "OrdSmall",
            parent=estilos["NormalCustom"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=9.5,
            textColor=TEXT_DARK,
        )
        st_small_bold = ParagraphStyle(
            "OrdSmallBold",
            parent=st_small,
            fontName="Helvetica-Bold",
        )
        st_legal = ParagraphStyle(
            "OrdLegal",
            parent=st_small,
            fontName="Helvetica-Oblique",
            fontSize=6.8,
            leading=8.5,
        )

        deposito = str(ordenamiento.get("deposito") or "PC").strip().upper()
        nro_orden = str(ordenamiento.get("nro_orden") or f"{deposito} 0000-0001")
        fecha_em = str(ordenamiento.get("fecha_emision") or "-")
        hora_em = str(ordenamiento.get("hora_emision") or "-")
        legajo = str(ordenamiento.get("legajo_personal") or "-")
        nombre = str(ordenamiento.get("nombre_personal") or "-")
        itinerario = str(ordenamiento.get("itinerario") or "-")
        ciclo_turno = str(ordenamiento.get("ciclo_o_turno") or "-")
        reg_raw = str(ordenamiento.get("regimen") or "H").strip()
        if reg_raw.upper() in ("LV", "L-V", "LAV", "L A V", "HABIL", "HÁBIL", "DIA HABIL", "DÍA HÁBIL", "LUNES A VIERNES"):
            regimen = "H"
        else:
            regimen = reg_raw
        motivo = str(ordenamiento.get("motivo_orden") or "-")
        obs_pie = str(ordenamiento.get("observacion_pie") or "")
        ref_pct = str(ordenamiento.get("ref_pct") or "-")
        emisor = str(ordenamiento.get("emisor") or "-")

        def _construir_mitad(etiqueta_copia: str) -> List[Any]:
            bloques: List[Any] = []

            # 1. Encabezado institucional + Nro de Orden
            cab_data = [
                [
                    Paragraph(
                        f"<b>TRENES ARGENTINOS OPERACIONES</b><br/>"
                        f"<font size=8>Gerencia Operativa Línea Roca - Base {_esc(deposito)} ({_esc(etiqueta_copia)})</font>",
                        st_small,
                    ),
                    Paragraph(f"<b>ORDEN DE SERVICIO</b><br/>N° {_esc(nro_orden)}", st_header_box),
                ]
            ]
            t_cab = Table(cab_data, colWidths=[12.5 * cm, 6.5 * cm])
            t_cab.setStyle(TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.8, PRIMARY),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
                ("BACKGROUND", (1, 0), (1, 0), BG_LIGHT),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            bloques.append(t_cab)
            bloques.append(Spacer(1, 3))

            # 2. Datos del Personal y Referencias Operativas
            meta_data = [
                [
                    Paragraph(f"<b>Legajo:</b> {_esc(legajo)}", st_small),
                    Paragraph(f"<b>Apellido y Nombre:</b> {_esc(nombre)}", st_small),
                    Paragraph(f"<b>Depósito:</b> {_esc(deposito)}", st_small),
                    Paragraph(f"<b>Fecha/Hora:</b> {_esc(fecha_em)} {_esc(hora_em)}", st_small),
                ],
                [
                    Paragraph(f"<b>Itinerario:</b> {_esc(itinerario)}", st_small),
                    Paragraph(f"<b>Turno / Ciclo:</b> {_esc(ciclo_turno)} ({_esc(regimen)})", st_small),
                    Paragraph(f"<b>REF de {_esc(deposito)}:</b> {_esc(ref_pct)}", st_small),
                    Paragraph(f"<b>Emisor:</b> {_esc(emisor)}", st_small),
                ],
                [
                    Paragraph(f"<b>Por orden de / Motivo:</b> {_esc(motivo)}", st_small_bold),
                    "",
                    "",
                    "",
                ],
            ]
            t_meta = Table(meta_data, colWidths=[3.5 * cm, 6.5 * cm, 4.2 * cm, 4.8 * cm])
            t_meta.setStyle(TableStyle([
                ("SPAN", (0, 2), (3, 2)),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER_COLOR),
                ("BACKGROUND", (0, 2), (3, 2), BG_LIGHT),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            bloques.append(t_meta)
            bloques.append(Spacer(1, 3))

            # 3. Grilla de Tramos / Diagrama de Trenes
            hdr_tramos = [
                Paragraph("<b>#</b>", estilos["TablaHeader"]),
                Paragraph("<b>Ref</b>", estilos["TablaHeader"]),
                Paragraph("<b>Tren</b>", estilos["TablaHeader"]),
                Paragraph("<b>Sale de (Origen)</b>", estilos["TablaHeader"]),
                Paragraph("<b>Hora Sal.</b>", estilos["TablaHeader"]),
                Paragraph("<b>Llega a (Destino)</b>", estilos["TablaHeader"]),
                Paragraph("<b>Hora Lleg.</b>", estilos["TablaHeader"]),
                Paragraph("<b>Observaciones del Tramo</b>", estilos["TablaHeader"]),
            ]
            filas_t = [hdr_tramos]
            max_filas = 10
            for idx, tr in enumerate(tramos[:max_filas], start=1):
                filas_t.append([
                    Paragraph(_esc(tr.get("nro_secuencia") or idx), estilos["TablaCellCenter"]),
                    Paragraph(_esc(tr.get("ref") or ""), estilos["TablaCellCenter"]),
                    Paragraph(f"<b>{_esc(tr.get('nro_tren') or '')}</b>", estilos["TablaCellCenter"]),
                    Paragraph(_esc(tr.get("origen") or ""), estilos["TablaCell"]),
                    Paragraph(_esc(tr.get("hora_salida") or ""), estilos["TablaCellCenter"]),
                    Paragraph(_esc(tr.get("destino") or ""), estilos["TablaCell"]),
                    Paragraph(_esc(tr.get("hora_llegada") or ""), estilos["TablaCellCenter"]),
                    Paragraph(_esc(tr.get("observaciones") or ""), estilos["TablaCell"]),
                ])

            # Rellenar filas vacías mínimas para mantener estructura uniforme
            while len(filas_t) < 6:
                filas_t.append(["", "", "", "", "", "", "", ""])

            t_tramos = Table(
                filas_t,
                colWidths=[0.8 * cm, 1.4 * cm, 1.8 * cm, 3.2 * cm, 1.6 * cm, 3.2 * cm, 1.6 * cm, 5.4 * cm],
            )
            t_tramos.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
                ("GRID", (0, 0), (-1, -1), 0.4, BORDER_COLOR),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ]))
            bloques.append(t_tramos)
            bloques.append(Spacer(1, 3))

            # 4. Pie de página legal y firmas
            if obs_pie:
                bloques.append(Paragraph(f"<b>Observación:</b> {_esc_nl(obs_pie)}", st_small))
                bloques.append(Spacer(1, 2))

            bloques.append(
                Paragraph(
                    "<b>Tomo conocimiento del siguiente diagrama según RITO Art. 7º</b> "
                    "(Acatamiento debido por el personal a las órdenes de sus superiores. "
                    "El personal deberá acatar de inmediato las órdenes que reciba de sus superiores, "
                    "pudiendo plantear el caso después por vía jerárquica si considera que dichas órdenes "
                    "no se ajustan a la reglamentación.)",
                    st_legal,
                )
            )
            bloques.append(Spacer(1, 8))

            firmas_data = [
                [
                    Paragraph(
                        "__________________________________________<br/>"
                        "<b>Notificado</b> - Firma / Aclaración / Legajo",
                        estilos["TablaCellCenter"],
                    ),
                    Paragraph(
                        "__________________________________________<br/>"
                        "<b>Firma y Sello del Jefe o Encargado</b>",
                        estilos["TablaCellCenter"],
                    ),
                ]
            ]
            t_firmas = Table(firmas_data, colWidths=[9.5 * cm, 9.5 * cm])
            t_firmas.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
            bloques.append(t_firmas)
            return bloques

        story: List[Any] = []
        story.extend(_construir_mitad("ORIGINAL"))
        story.append(Spacer(1, 8))
        story.append(
            HRFlowable(
                width="100%",
                thickness=1,
                color=colors.gray,
                spaceBefore=4,
                spaceAfter=8,
                dash=(4, 4),
            )
        )
        story.extend(_construir_mitad("DUPLICADO"))

        doc.build(story)
        return str(out_file)

