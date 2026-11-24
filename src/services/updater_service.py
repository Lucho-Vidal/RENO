"""Servicio centralizado de Actualizaciones Automáticas (Auto-Updater OTA) para RENO.

Adopta el estándar visual y arquitectónico de CRONO-PCT:
- Verificación asíncrona no bloqueante (SemVer determinista).
- Criterio de obligatoriedad: versiones Mayores (X) y Menores (Y) obligatorias; Parches (Z) opcionales.
- Soporte para descargas continuas y multi-parte (.part1, .part2) para eludir el límite de 50MB en Supabase Storage Free Tier.
- Verificación criptográfica SHA-256 de integridad antes de ejecutar.
- Lanzamiento desatendido Inno Setup (/SILENT) y reinicio coordinado.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from PySide6.QtCore import QObject, QThread, Signal
    from PySide6.QtWidgets import QApplication
except ImportError:
    class QObject:
        def __init__(self, parent=None):
            pass

    class QThread(QObject):
        def __init__(self, parent=None):
            super().__init__(parent)

        def start(self):
            pass

        def wait(self, msec=None):
            pass

        def isRunning(self):
            return False

    def Signal(*types):
        class _Signal:
            def connect(self, slot):
                pass
            def emit(self, *args):
                pass
        return _Signal()

    class QApplication:
        @staticmethod
        def instance():
            return None

from src.core.config import APP_NAME, APP_VERSION, cargar_configuracion_app, sanitizar_url_supabase
from src.core.logger import get_logger
from src.core.proxy import (
    MODO_DIRECTO,
    OfflineError,
    ProxyAuthError,
    construir_headers_supabase,
    descargar_archivo_con_proxy,
    ejecutar_request_supabase,
)

logger = get_logger("UpdaterService")


@dataclass
class InfoActualizacion:
    """Metadatos de la actualización remota obtenidos del manifiesto version.json."""

    version: str
    fecha: str
    titulo: str
    novedades: list[str] = field(default_factory=list)
    instalador_nombre: str = ""
    instalador_url: str = ""
    sha256: str = ""
    tamano_bytes: int = 0
    obligatoria: bool = False
    min_version_permitida: str = ""
    partes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InfoActualizacion":
        novedades_raw = data.get("novedades", [])
        if isinstance(novedades_raw, str):
            novedades = [n.strip() for n in novedades_raw.split("\n") if n.strip()]
        elif isinstance(novedades_raw, list):
            novedades = [str(n).strip() for n in novedades_raw if str(n).strip()]
        else:
            novedades = []

        partes_raw = data.get("partes", [])
        partes = [str(p).strip() for p in partes_raw if str(p).strip()] if isinstance(partes_raw, list) else []

        return cls(
            version=str(data.get("version", "0.0.0")).strip(),
            fecha=str(data.get("fecha", "")).strip(),
            titulo=str(data.get("titulo", "Actualización del Sistema")).strip(),
            novedades=novedades,
            instalador_nombre=str(data.get("instalador_nombre", "")).strip(),
            instalador_url=str(data.get("instalador_url", "")).strip(),
            sha256=str(data.get("sha256", "")).strip().lower(),
            tamano_bytes=int(data.get("tamano_bytes", 0) or 0),
            obligatoria=bool(data.get("obligatoria", False) or data.get("es_urgente", False)),
            min_version_permitida=str(data.get("min_version_permitida", "")).strip(),
            partes=partes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "fecha": self.fecha,
            "titulo": self.titulo,
            "novedades": self.novedades,
            "instalador_nombre": self.instalador_nombre,
            "instalador_url": self.instalador_url,
            "sha256": self.sha256,
            "tamano_bytes": self.tamano_bytes,
            "obligatoria": self.obligatoria,
            "min_version_permitida": self.min_version_permitida,
            "partes": self.partes,
        }


# --- Utilidades SemVer y Criptográficas ---


def parsear_version(v: str) -> tuple[int, int, int]:
    """Parsea una versión SemVer (ej. '2.0.0', 'v2.1.3') a tupla (mayor, menor, parche)."""
    if not v:
        return (0, 0, 0)
    v_clean = str(v).strip().lstrip("vV")
    partes: list[int] = []
    for chunk in v_clean.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        partes.append(int(digits) if digits else 0)
    while len(partes) < 3:
        partes.append(0)
    return (partes[0], partes[1], partes[2])


def comparar_versiones(v_local: str, v_remota: str) -> tuple[bool, bool, bool, bool]:
    """Compara versión local contra remota.
    
    Retorna: (es_mayor, es_menor, es_parche, es_superior)
    """
    loc = parsear_version(v_local)
    rem = parsear_version(v_remota)

    if rem <= loc:
        return (False, False, False, False)

    es_mayor = rem[0] > loc[0]
    es_menor = rem[0] == loc[0] and rem[1] > loc[1]
    es_parche = rem[0] == loc[0] and rem[1] == loc[1] and rem[2] > loc[2]
    return (es_mayor, es_menor, es_parche, True)


def es_actualizacion_obligatoria(
    v_local: str,
    v_remota: str,
    flag_manifiesto: bool = False,
    min_version_permitida: str = "",
) -> bool:
    """Evalúa si una versión es obligatoria según la regla CRONO-PCT:
    - Si v_local < min_version_permitida -> OBLIGATORIA.
    - Si el manifiesto tiene obligatoria=True o es_urgente=True -> OBLIGATORIA.
    - Si hay cambio Mayor (X) o Menor (Y) -> OBLIGATORIA (bases de datos / esquemas).
    - Si es solo Parche (Z) -> OPCIONAL (permite 'Recordarme más tarde').
    """
    if min_version_permitida:
        if parsear_version(v_local) < parsear_version(min_version_permitida):
            return True

    if flag_manifiesto:
        return True

    es_mayor, es_menor, es_parche, es_sup = comparar_versiones(v_local, v_remota)
    if not es_sup:
        return False

    if es_mayor or es_menor:
        return True

    return False


def calcular_sha256(ruta_archivo: str | Path, chunk_size: int = 65536) -> str:
    """Calcula el hash SHA-256 de un archivo en disco."""
    h = hashlib.sha256()
    with open(ruta_archivo, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest().lower()


def _expandir_candidatos_url_custom(url_custom: str, bucket: str) -> list[str]:
    """Normaliza la URL explícita de Actualizaciones (OTA) soportando JSON, bucket o raíz Supabase."""
    raw = str(url_custom or "").strip()
    if not raw:
        return []
    if raw.lower().startswith("http://"):
        logger.warning("URL OTA insegura (http://) rechazada: %s", raw)
        return []
    if not raw.lower().startswith("https://"):
        raw = f"https://{raw.lstrip('/')}"

    # Si pegaron la URL del Dashboard de Supabase (supabase.com/dashboard/project/<ref>...)
    m_dash = re.search(r"supabase\.com/dashboard/project/([a-zA-Z0-9_\-]+)", raw, re.IGNORECASE)
    if m_dash:
        proj = m_dash.group(1)
        m_bkt = re.search(r"/storage(?:/files)?/buckets/([^/?#]+)", raw, re.IGNORECASE)
        bkt = (m_bkt.group(1).strip() if m_bkt else bucket) or "updates"
        return [
            f"https://{proj}.supabase.co/storage/v1/object/public/{bkt}/version.json",
            f"https://{proj}.supabase.co/storage/v1/object/public/{bkt}/reno_version.json",
        ]

    parsed = urllib.parse.urlparse(raw)
    path_clean = parsed.path or ""
    path_lower = path_clean.lower()
    netloc_lower = (parsed.netloc or "").lower()

    # 1. URL directa a archivo .json
    if path_lower.endswith(".json"):
        candidatos = [raw]
        alt_filename = ""
        nombre_archivo = path_clean.rsplit("/", 1)[-1].lower()
        if nombre_archivo == "version.json":
            alt_filename = "reno_version.json"
        elif nombre_archivo == "reno_version.json":
            alt_filename = "version.json"

        if "/storage/v1/object/" in path_lower and "/storage/v1/object/public/" not in path_lower:
            public_path = re.sub(
                r"/storage/v1/object/(?!public/)",
                "/storage/v1/object/public/",
                path_clean,
                count=1,
                flags=re.IGNORECASE,
            )
            candidatos.append(urllib.parse.urlunparse(parsed._replace(path=public_path)))
            if alt_filename:
                alt_pub = public_path.rsplit("/", 1)[0] + f"/{alt_filename}"
                candidatos.append(urllib.parse.urlunparse(parsed._replace(path=alt_pub)))
        if alt_filename:
            alt_path = path_clean.rsplit("/", 1)[0] + f"/{alt_filename}"
            candidatos.append(urllib.parse.urlunparse(parsed._replace(path=alt_path)))
        return candidatos

    # 2. Raíz de proyecto Supabase (https://<ref>.supabase.co) sin ruta de Storage
    if netloc_lower.endswith(".supabase.co") and path_clean.strip("/") == "":
        return [
            f"https://{parsed.netloc}/storage/v1/object/public/{bucket}/version.json",
            f"https://{parsed.netloc}/storage/v1/object/public/{bucket}/reno_version.json",
        ]

    # 3. Carpeta / Bucket / Prefijo (sin .json final)
    base_custom = raw.rstrip("/")
    candidatos_dir: list[str] = []
    if "/storage/v1/object/" in path_lower and "/storage/v1/object/public/" not in path_lower:
        public_base = re.sub(
            r"/storage/v1/object/(?!public/)",
            "/storage/v1/object/public/",
            base_custom,
            count=1,
            flags=re.IGNORECASE,
        )
        candidatos_dir.append(f"{public_base}/version.json")
        candidatos_dir.append(f"{public_base}/reno_version.json")
    candidatos_dir.append(f"{base_custom}/version.json")
    candidatos_dir.append(f"{base_custom}/reno_version.json")
    return candidatos_dir


def resolver_url_manifiesto(config: dict[str, Any] | None = None) -> list[str]:
    """Resuelve la lista de URLs posibles para consultar version.json con prioridad:
    1. Endpoint explícito configurado en reno_config.json / UI OTA ('updater_url').
    2. Supabase Storage bucket configurado (por defecto 'updates') del proyecto configurado.
    """
    cfg = config if config is not None else cargar_configuracion_app()
    urls: list[str] = []
    bucket = str(cfg.get("supabase_storage_bucket") or "updates").strip().strip("/") or "updates"

    # 1. URL explícita configurada en Actualizaciones (OTA) o RENO_UPDATES_URL
    url_custom = str(cfg.get("updater_url", "")).strip()
    if url_custom:
        urls.extend(_expandir_candidatos_url_custom(url_custom, bucket))

    # 2. Supabase Storage bucket configurado (por defecto 'updates')
    sb_raw = str(cfg.get("supabase_url", "")).strip()
    sb_url = sanitizar_url_supabase(sb_raw) if sb_raw else ""
    if sb_url:
        host_sb = (urllib.parse.urlparse(sb_url).netloc or "").lower()
        if host_sb and "tu-proyecto." not in host_sb:
            urls.append(f"{sb_url}/storage/v1/object/public/{bucket}/version.json")
            urls.append(f"{sb_url}/storage/v1/object/public/{bucket}/reno_version.json")

    # Filtrar duplicados manteniendo orden
    vistas = set()
    resultado = []
    for u in urls:
        if u and u not in vistas:
            vistas.add(u)
            resultado.append(u)
    return resultado


def _construir_headers_para_url(url: str, base_headers: dict[str, str], config: dict[str, Any] | None) -> dict[str, str]:
    """Agrega headers de Supabase solo cuando la URL apunta a Storage autenticado del proyecto."""
    hdrs = dict(base_headers)
    cfg = config or {}
    parsed = urllib.parse.urlparse(url)
    path_lower = (parsed.path or "").lower()
    if "/storage/v1/object/" in path_lower and "/storage/v1/object/public/" not in path_lower:
        sb_url = sanitizar_url_supabase(str(cfg.get("supabase_url") or ""))
        sb_host = (urllib.parse.urlparse(sb_url).netloc or "").lower()
        api_key = str(cfg.get("supabase_anon_key") or cfg.get("supabase_service_role_key") or "").strip()
        if api_key and (not sb_host or parsed.netloc.lower() == sb_host):
            hdrs.update(construir_headers_supabase(api_key))
    return hdrs


def dividir_archivo(
    ruta_archivo: str | Path,
    tamano_max_mb: int = 25,
    carpeta_salida: str | Path | None = None,
) -> list[Path]:
    """Divide un archivo en partes (.part1, .part2, ...) si excede el tamaño indicado.
    
    Protección para eludir el límite de 50MB de Supabase Storage Free Tier.
    """
    origen = Path(ruta_archivo).resolve()
    if not origen.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {origen}")

    tamano_bytes = origen.stat().st_size
    bytes_max = tamano_max_mb * 1024 * 1024

    destino_dir = Path(carpeta_salida).resolve() if carpeta_salida else origen.parent
    destino_dir.mkdir(parents=True, exist_ok=True)

    if tamano_bytes <= bytes_max:
        return [origen]

    partes: list[Path] = []
    part_num = 1
    with open(origen, "rb") as f_in:
        while True:
            trozo = f_in.read(bytes_max)
            if not trozo:
                break
            nombre_parte = f"{origen.name}.part{part_num}"
            ruta_parte = destino_dir / nombre_parte
            with open(ruta_parte, "wb") as f_out:
                f_out.write(trozo)
            partes.append(ruta_parte)
            part_num += 1

    logger.info("Archivo %s dividido en %d partes de ~%d MB", origen.name, len(partes), tamano_max_mb)
    return partes


# --- Workers Asíncronos (QThread) ---


class VerificarActualizacionWorker(QThread):
    """Worker en segundo plano para consultar si existe una versión superior en remoto."""

    actualizacion_disponible = Signal(object, bool)  # (InfoActualizacion, es_obligatoria)
    sistema_actualizado = Signal()
    error_verificacion = Signal(str)

    def __init__(self, version_local: str = APP_VERSION, config: dict[str, Any] | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.version_local = version_local
        self.config = config if config is not None else cargar_configuracion_app()

    def run(self) -> None:
        candidatos_url = resolver_url_manifiesto(self.config)
        if not candidatos_url:
            msg_vacio = (
                "No hay URL de actualizaciones (OTA) ni URL de Supabase configurada. "
                "Configure la URL en Administración > Configuración > Actualizaciones (OTA)."
            )
            logger.warning(msg_vacio)
            self.error_verificacion.emit(msg_vacio)
            return

        base_headers = {
            "User-Agent": f"RENO-Updater/{self.version_local} (Windows; Python)",
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        }

        primer_error = ""
        ultimo_error = ""
        error_proxy = ""
        manifiesto_data: dict[str, Any] | None = None
        proxy_mode = (self.config or {}).get("proxy_mode", MODO_DIRECTO)

        for idx, url in enumerate(candidatos_url, start=1):
            headers = _construir_headers_para_url(url, base_headers, self.config)
            try:
                logger.info(
                    "Consultando manifiesto de actualización (%d/%d): %s (proxy_mode=%s)",
                    idx,
                    len(candidatos_url),
                    url,
                    proxy_mode,
                )
                status, body, _ = ejecutar_request_supabase(
                    config=self.config,
                    url=url,
                    method="GET",
                    headers=headers,
                    timeout_sec=12,
                )
                if status == 200:
                    raw = body.decode("utf-8", errors="ignore")
                    parsed = json.loads(raw)
                    if not isinstance(parsed, dict) or not str(parsed.get("version") or "").strip():
                        msg_inv = f"El recurso en {url} no contiene un campo 'version' válido."
                        logger.warning(msg_inv)
                        primer_error = primer_error or msg_inv
                        ultimo_error = msg_inv
                        continue
                    manifiesto_data = parsed
                    logger.info("Manifiesto obtenido exitosamente desde: %s", url)
                    break
                if status == 407:
                    error_proxy = "HTTP 407: El proxy corporativo requiere autenticación válida."
                    primer_error = primer_error or error_proxy
                    ultimo_error = error_proxy
                    logger.warning("Proxy rechazó consulta de actualización (%s): HTTP 407", url)
                else:
                    snippet = body.decode("utf-8", errors="ignore").strip().replace("\r", " ").replace("\n", " ")[:180]
                    detalle = f" ({snippet})" if snippet else ""
                    msg_err = f"HTTP {status} desde {url}{detalle}"
                    primer_error = primer_error or msg_err
                    ultimo_error = msg_err
                    logger.warning("Respuesta no exitosa al consultar %s: %s", url, msg_err)
            except ProxyAuthError as e:
                error_proxy = str(e)
                primer_error = primer_error or error_proxy
                ultimo_error = error_proxy
                logger.warning("Error de autenticación proxy en %s: %s", url, e)
            except (OfflineError, Exception) as e:
                msg_exc = f"Error al consultar {url}: {e}"
                primer_error = primer_error or msg_exc
                ultimo_error = msg_exc
                logger.warning("Fallo al consultar %s: %s", url, e)

        if not manifiesto_data:
            self.error_verificacion.emit(
                error_proxy or primer_error or ultimo_error or "No se pudo obtener el manifiesto de actualización"
            )
            return

        try:
            info = InfoActualizacion.from_dict(manifiesto_data)
            _, _, _, es_superior = comparar_versiones(self.version_local, info.version)

            if es_superior:
                es_obligatoria = es_actualizacion_obligatoria(
                    v_local=self.version_local,
                    v_remota=info.version,
                    flag_manifiesto=info.obligatoria,
                    min_version_permitida=info.min_version_permitida,
                )
                logger.info(
                    "Nueva versión detectada: %s (actual: %s, obligatoria: %s)",
                    info.version,
                    self.version_local,
                    es_obligatoria,
                )
                self.actualizacion_disponible.emit(info, es_obligatoria)
            else:
                logger.info("El sistema está al día (v%s)", self.version_local)
                self.sistema_actualizado.emit()
        except Exception as e:
            logger.exception("Error procesando datos del manifiesto: %s", e)
            self.error_verificacion.emit(f"Error procesando manifiesto: {e}")


class DescargarActualizacionWorker(QThread):
    """Worker en segundo plano para descargar el instalador (streaming + soporte multi-parte)."""

    progreso = Signal(int, int, float, str)  # (bytes_descargados, total_bytes, velocidad_bps, texto_progreso)
    descarga_completada = Signal(str)        # (ruta_instalador_completo)
    error_descarga = Signal(str)

    def __init__(self, info: InfoActualizacion, config: dict[str, Any] | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.info = info
        self.config = config if config is not None else cargar_configuracion_app()
        self._cancelado = False

    def cancelar(self) -> None:
        self._cancelado = True

    def run(self) -> None:
        carpeta_temp = (Path(tempfile.gettempdir()) / "RENO_Updates").resolve()
        carpeta_temp.mkdir(parents=True, exist_ok=True)

        nombre_raw = str(self.info.instalador_nombre or f"RENO_Setup_v{self.info.version}.exe").strip()
        if Path(nombre_raw).name != nombre_raw or "/" in nombre_raw or "\\" in nombre_raw:
            self.error_descarga.emit("Nombre de instalador inválido en el manifiesto de actualización.")
            return
        nombre_archivo = Path(nombre_raw).name
        if not nombre_archivo.lower().endswith(".exe"):
            nombre_archivo += ".exe"
        if not re.fullmatch(r"[A-Za-z0-9._-]+\.exe", nombre_archivo):
            self.error_descarga.emit("Nombre de instalador contiene caracteres no permitidos.")
            return
        ruta_final = (carpeta_temp / nombre_archivo).resolve()
        if not ruta_final.is_relative_to(carpeta_temp):
            self.error_descarga.emit("Ruta de descarga fuera del directorio temporal permitido.")
            return

        # Verificar SHA-256 obligatorio en el manifiesto antes de descargar
        sha_esperado = str(self.info.sha256 or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha_esperado):
            logger.error("Manifiesto OTA rechazado: falta hash SHA-256 válido.")
            self.error_descarga.emit(
                "El manifiesto de actualización no incluye un hash SHA-256 válido. Descarga abortada por seguridad."
            )
            return

        # Determinar si es descarga multi-parte o archivo único
        lista_urls = self.info.partes if self.info.partes else ([self.info.instalador_url] if self.info.instalador_url else [])
        if not lista_urls:
            self.error_descarga.emit("El manifiesto de actualización no contiene URLs de descarga válidas.")
            return
        for u in lista_urls:
            if not str(u or "").strip().lower().startswith("https://"):
                logger.error("URL de descarga OTA insegura rechazada: %s", u)
                self.error_descarga.emit("URL de descarga insegura (se requiere HTTPS). Descarga abortada.")
                return

        total_esperado = self.info.tamano_bytes or 0
        bytes_base_segmentos = 0
        bytes_acumulados = 0
        tiempo_inicio = time.time()
        ultimo_reporte = 0.0
        headers_base_dl = {"User-Agent": f"RENO-Downloader/{APP_VERSION}"}

        try:
            # Abrimos el archivo final en modo escritura binaria
            with open(ruta_final, "wb") as f_out:
                for idx, url in enumerate(lista_urls):
                    if self._cancelado:
                        break
                    headers_dl = _construir_headers_para_url(url, headers_base_dl, self.config)

                    logger.info(
                        "Descargando segmento %d/%d desde: %s (proxy_mode=%s)",
                        idx + 1,
                        len(lista_urls),
                        url,
                        (self.config or {}).get("proxy_mode", MODO_DIRECTO),
                    )

                    def _on_chunk(segment_bytes: int, _segment_total: int) -> None:
                        nonlocal bytes_acumulados, ultimo_reporte
                        bytes_acumulados = bytes_base_segmentos + segment_bytes
                        ahora = time.time()
                        if ahora - ultimo_reporte >= 0.15:  # Reporte a ~6 Hz
                            transcurrido = max(0.001, ahora - tiempo_inicio)
                            vel_bps = bytes_acumulados / transcurrido

                            if total_esperado > 0:
                                pct = min(100.0, (bytes_acumulados / total_esperado) * 100)
                                msg = f"{bytes_acumulados / (1024*1024):.1f} MB de {total_esperado / (1024*1024):.1f} MB ({pct:.0f}%)"
                            else:
                                msg = f"{bytes_acumulados / (1024*1024):.1f} MB descargados"

                            self.progreso.emit(bytes_acumulados, total_esperado, vel_bps, msg)
                            ultimo_reporte = ahora

                    escrito = descargar_archivo_con_proxy(
                        config=self.config,
                        url=url,
                        archivo_out=f_out,
                        headers=headers_dl,
                        on_chunk=_on_chunk,
                        should_cancel=lambda: self._cancelado,
                        timeout_sec=60,
                    )
                    bytes_base_segmentos += escrito
                    bytes_acumulados = bytes_base_segmentos

            if self._cancelado:
                try:
                    if ruta_final.exists():
                        ruta_final.unlink()
                except Exception:
                    pass
                self.error_descarga.emit("Descarga cancelada por el usuario.")
                return

            # Verificación criptográfica SHA-256 obligatoria
            hash_calculado = calcular_sha256(ruta_final)
            if hash_calculado != sha_esperado:
                logger.error(
                    "Error de integridad SHA-256. Esperado: %s, Calculado: %s",
                    sha_esperado,
                    hash_calculado,
                )
                try:
                    ruta_final.unlink()
                except Exception:
                    pass
                self.error_descarga.emit("El archivo descargado está corrupto (error de verificación SHA-256).")
                return
            logger.info("Integridad SHA-256 verificada con éxito: %s", hash_calculado)

            # Notificar éxito
            self.progreso.emit(bytes_acumulados, bytes_acumulados, 0.0, "Descarga completada e integridad verificada.")
            self.descarga_completada.emit(str(ruta_final))

        except InterruptedError:
            try:
                if ruta_final.exists():
                    ruta_final.unlink()
            except Exception:
                pass
            self.error_descarga.emit("Descarga cancelada por el usuario.")
        except ProxyAuthError as e:
            logger.warning("Error de autenticación proxy durante descarga: %s", e)
            try:
                if ruta_final.exists():
                    ruta_final.unlink()
            except Exception:
                pass
            self.error_descarga.emit(f"Error de autenticación del proxy corporativo: {e}")
        except Exception as e:
            logger.exception("Error durante la descarga de la actualización: %s", e)
            try:
                if ruta_final.exists():
                    ruta_final.unlink()
            except Exception:
                pass
            self.error_descarga.emit(f"Error durante la descarga: {e}")


def ejecutar_instalador_y_reiniciar(ruta_instalador: str | Path) -> bool:
    """Lanza el instalador de Inno Setup en modo silencioso y cierra RENO de forma limpia."""
    ruta = Path(ruta_instalador).resolve()
    if not ruta.exists():
        logger.error("No se encontró el instalador en: %s", ruta)
        return False

    args = ["/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"]
    logger.info("Lanzando instalador: %s con argumentos %s", ruta, args)

    try:
        if sys.platform == "win32":
            import subprocess
            subprocess.Popen([str(ruta)] + args, close_fds=True, shell=False)
        else:
            import subprocess
            subprocess.Popen([str(ruta)], close_fds=True)

        app = QApplication.instance()
        if app:
            app.quit()
        return True
    except Exception as e:
        logger.exception("Error al relanzar aplicación mediante instalador: %s", e)
        return False
