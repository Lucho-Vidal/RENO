"""Servicio de correo electrónico mediante Outlook clásico (MAPI COM).

Permite el envío ordinario de informes de novedades y el envío seguro
'sin rastro' para recuperación de contraseñas de acceso.
"""

import mimetypes
import os
from pathlib import Path
import platform
import re
import tempfile
import time
import uuid
from email.message import EmailMessage
from typing import List, Optional, Tuple

from src.core.logger import get_logger

logger = get_logger("MailService")

OL_FOLDER_SENT_MAIL = 5
OL_FOLDER_DELETED_ITEMS = 3
OL_FOLDER_DRAFTS = 16
OL_PERMANENT_DELETE = 2

_EMAIL_RE = re.compile(r"^[^@\s\r\n,;<>]+@[^@\s\r\n,;<>]+\.[^@\s\r\n,;<>]+$")


def _sanitizar_cabecera(texto: str) -> str:
    """Elimina saltos de línea CR/LF para prevenir inyección de cabeceras."""
    return re.sub(r"[\r\n]+", " ", str(texto or "")).strip()


def _sanitizar_destinatarios(destinatarios: Optional[List[str]]) -> List[str]:
    """Limpia y valida la lista de correos electrónicos de destino."""
    limpios: List[str] = []
    for d in destinatarios or []:
        val = _sanitizar_cabecera(str(d))
        if not val:
            continue
        if _EMAIL_RE.match(val):
            limpios.append(val)
        else:
            logger.warning("Destinatario de correo inválido ignorado: %r", val)
    return limpios


class MailService:
    """Cliente MAPI COM para Microsoft Outlook (compatible con Outlook 2016 y fallback .eml)."""

    @staticmethod
    def _abrir_borrador_eml_fallback(
        destinatarios: List[str],
        asunto: str,
        cuerpo_html: str,
        cuerpo_texto: str,
        adjuntos: List[str],
    ) -> Tuple[bool, str]:
        """Genera un archivo .eml con X-Unsent: 1 y lo abre con el cliente de correo (Outlook 2016)."""
        try:
            msg = EmailMessage()
            msg["Subject"] = _sanitizar_cabecera(asunto)
            dests = _sanitizar_destinatarios(destinatarios)
            if dests:
                msg["To"] = ", ".join(dests)
            msg["X-Unsent"] = "1"

            if cuerpo_html:
                msg.set_content(cuerpo_texto or "Se adjunta documento.")
                msg.add_alternative(cuerpo_html, subtype="html")
            else:
                msg.set_content(cuerpo_texto or "")

            for adj in adjuntos:
                if adj and os.path.exists(adj):
                    ctype, _ = mimetypes.guess_type(adj)
                    maintype, subtype = (ctype.split("/", 1) if ctype else ("application", "octet-stream"))
                    with open(adj, "rb") as f_adj:
                        msg.add_attachment(
                            f_adj.read(),
                            maintype=maintype,
                            subtype=subtype,
                            filename=os.path.basename(adj),
                        )

            out_dir = Path(adjuntos[0]).parent if (adjuntos and os.path.exists(adjuntos[0])) else Path(tempfile.gettempdir())
            eml_path = out_dir / f"Borrador_RENO_{uuid.uuid4().hex[:8]}.eml"
            with open(eml_path, "wb") as f_eml:
                f_eml.write(msg.as_bytes())

            if platform.system() == "Windows" and hasattr(os, "startfile"):
                os.startfile(str(eml_path))
                logger.info("Borrador .eml abierto mediante os.startfile: %s", eml_path)
                return True, f"Se abrió el borrador en Outlook ({eml_path.name})."
            return True, f"Borrador generado en:\n{eml_path}"
        except Exception as eml_err:
            logger.error("Error generando borrador .eml de respaldo: %s", eml_err)
            return False, f"No se pudo abrir Outlook ni generar el borrador .eml:\n{eml_err}"

    @staticmethod
    def crear_borrador_outlook(
        destinatarios: Optional[List[str]] = None,
        asunto: str = "",
        cuerpo_html: str = "",
        cuerpo_texto: str = "",
        adjuntos: Optional[List[str]] = None,
        enviar_directo: bool = False,
    ) -> Tuple[bool, str]:
        """Crea un borrador en Microsoft Outlook (incl. Outlook 2016) con adjuntos y lo abre en pantalla (o lo envía directamente)."""
        lista_dest = _sanitizar_destinatarios(destinatarios)
        asunto_limpio = _sanitizar_cabecera(asunto)
        lista_adj = [os.path.abspath(adj) for adj in (adjuntos or []) if adj and os.path.exists(adj)]

        com_error: Optional[str] = None
        com_inicializado = False
        try:
            import pythoncom
            import win32com.client
            try:
                pythoncom.CoInitialize()
                com_inicializado = True
            except Exception as e_com:
                logger.debug("CoInitialize en crear_borrador_outlook: %s", e_com)
        except ImportError:
            com_error = "Falta la dependencia 'pywin32' para interactuar con Microsoft Outlook."
            if not enviar_directo:
                return MailService._abrir_borrador_eml_fallback(
                    lista_dest, asunto_limpio, cuerpo_html, cuerpo_texto, lista_adj
                )
            return False, com_error

        try:
            outlook = win32com.client.Dispatch("Outlook.Application")

            # En Outlook 2016, si no estaba abierto previamente, es necesario inicializar la sesión MAPI
            # y acceder a Borradores para que Save()/Display() abran correctamente la ventana.
            try:
                namespace = outlook.GetNamespace("MAPI")
                try:
                    namespace.Logon("", "", False, False)
                except Exception as e_logon:
                    logger.debug("MAPI Logon omitido: %s", e_logon)
                namespace.GetDefaultFolder(OL_FOLDER_DRAFTS)
            except Exception as e_ns:
                logger.debug("No se pudo precargar carpeta Borradores MAPI: %s", e_ns)

            mensaje = outlook.CreateItem(0)

            if lista_dest:
                mensaje.To = "; ".join(lista_dest)
                try:
                    mensaje.Recipients.ResolveAll()
                except Exception as e_res:
                    logger.debug("ResolveAll en Outlook: %s", e_res)

            mensaje.Subject = asunto_limpio

            for adj_abs in lista_adj:
                mensaje.Attachments.Add(adj_abs)

            if enviar_directo and lista_dest:
                if cuerpo_html:
                    mensaje.HTMLBody = cuerpo_html
                else:
                    mensaje.Body = cuerpo_texto
                mensaje.Send()
                logger.info(f"Correo enviado directamente vía Outlook a {len(lista_dest)} destinatarios.")
                return True, f"Correo enviado exitosamente a {', '.join(lista_dest)}."
            else:
                # Mostrar ventana de borrador en Outlook 2016 (Display primero para cargar firma si existe)
                if cuerpo_html:
                    mensaje.HTMLBody = cuerpo_html
                elif cuerpo_texto:
                    mensaje.Body = cuerpo_texto

                try:
                    mensaje.Save()
                except Exception as e_save:
                    logger.debug("No se pudo pre-guardar borrador en Outlook: %s", e_save)

                mensaje.Display(False)
                try:
                    inspector = getattr(mensaje, "GetInspector", None)
                    if inspector is not None:
                        inspector.Activate()
                except Exception as e_insp:
                    logger.debug("No se pudo activar inspector de Outlook: %s", e_insp)
                logger.info("Borrador de correo abierto exitosamente en Microsoft Outlook.")
                return True, "Se abrió el correo en Microsoft Outlook listo para enviar."
        except Exception as e:
            logger.error(f"Error al crear o enviar correo en Outlook COM: {e}")
            if not enviar_directo:
                ok_fb, msg_fb = MailService._abrir_borrador_eml_fallback(
                    lista_dest, asunto_limpio, cuerpo_html, cuerpo_texto, lista_adj
                )
                if ok_fb:
                    return True, msg_fb
            return False, f"No se pudo conectar con Microsoft Outlook:\n{e}"
        finally:
            if com_inicializado:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    @staticmethod
    def enviar_sin_rastro(
        destinatarios: List[str],
        asunto: str,
        cuerpo: str,
    ) -> None:
        """Envía un correo y elimina permanentemente la copia de Enviados y Eliminados.

        Usa DeleteAfterSubmit=True para que Outlook descarte la copia al enviar,
        y barre las carpetas de enviados y eliminados con un identificador único
        para purgar cualquier residuo sin dejar rastro de contraseñas temporales.
        """
        lista_dest = _sanitizar_destinatarios(destinatarios)
        if not lista_dest:
            raise RuntimeError("No hay destinatarios válidos configurados.")
        asunto_limpio = _sanitizar_cabecera(asunto)

        com_inicializado = False
        try:
            import pythoncom
            import win32com.client
            try:
                pythoncom.CoInitialize()
                com_inicializado = True
            except Exception as e_com:
                logger.debug("CoInitialize en enviar_sin_rastro: %s", e_com)
        except ImportError as error:
            raise RuntimeError("Falta la dependencia pywin32 para enviar mediante Outlook.") from error

        try:
            token = uuid.uuid4().hex[:12]

            outlook = win32com.client.Dispatch("Outlook.Application")
            mensaje = outlook.CreateItem(0)
            mensaje.To = ";".join(lista_dest)
            mensaje.Subject = asunto_limpio
            mensaje.Body = cuerpo

            try:
                mensaje.DeleteAfterSubmit = True
            except Exception as e_del:
                logger.warning("No se pudo establecer DeleteAfterSubmit en Outlook: %s", e_del)

            try:
                prop = mensaje.UserProperties.Add("X-TempPwd-Token", 1, False)
                prop.Value = token
            except Exception as e_prop:
                logger.warning("No se pudo etiquetar X-TempPwd-Token en Outlook: %s", e_prop)

            mensaje.Send()

            # Limpieza residual
            try:
                time.sleep(0.3)
                namespace = outlook.GetNamespace("MAPI")
                carpetas_a_revisar = []

                for folder_id in (OL_FOLDER_SENT_MAIL, OL_FOLDER_DELETED_ITEMS):
                    try:
                        carpetas_a_revisar.append(namespace.GetDefaultFolder(folder_id))
                    except Exception as e_fld:
                        logger.debug("No se pudo acceder a carpeta MAPI %s: %s", folder_id, e_fld)

                for folder in carpetas_a_revisar:
                    try:
                        items = folder.Items
                        total = items.Count
                        for i in range(total, max(0, total - 15), -1):
                            try:
                                item = items.Item(i)
                                prop = item.UserProperties.Find("X-TempPwd-Token")
                                if prop and prop.Value == token:
                                    item.Delete()
                            except Exception:
                                continue
                    except Exception as e_scan:
                        logger.debug("Error escaneando carpeta MAPI: %s", e_scan)
            except Exception as e:
                logger.warning(f"Error menor en barrido sin rastro de Outlook: {e}")
        finally:
            if com_inicializado:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
