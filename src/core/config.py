"""Configuraciones transversales y resolución de rutas del sistema RENO."""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

APP_NAME = "RENO"
VERSION = "2.1.0"
APP_VERSION = VERSION

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def obtener_directorio_aplicacion() -> Path:
    """Retorna el directorio base persistente de la aplicación.
    
    Si corre empaquetado (.exe de PyInstaller), retorna la carpeta donde reside el ejecutable.
    Si corre en entorno de desarrollo, retorna la raíz del proyecto.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return BASE_DIR


APP_DATA_DIR = obtener_directorio_aplicacion()

DOTACIONES = ["Todas", "PC", "LLV", "TY", "LP", "OA", "K5", "RE", "CÑ", "AK"]


def obtener_usuario_windows() -> str:
    """Retorna el nombre de usuario activo de la sesión del sistema operativo (Windows/OS)."""
    try:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or getpass.getuser()
        return str(user).strip() if user else "Desconocido"
    except Exception:
        return "Desconocido"


ESPECIALIDADES_EMPLEADO = (
    "Conductor Eléctrico",
    "Conductor Diesel",
    "Ayudante Habilitado",
    "Ayudante Conductor",
    "Guarda Eléctrico",
    "Guarda Diesel",
)

DIAS_SEMANA = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")

_DEFAULT_CONFIG_JSON_PATH = APP_DATA_DIR / "reno_config.json"
CONFIG_JSON_PATH = _DEFAULT_CONFIG_JSON_PATH
PATH_BASE_FILE = APP_DATA_DIR / "config.json"


def _ruta_config_usuario_fallback() -> Path | None:
    """Retorna una ruta de configuración en el perfil del usuario si está disponible."""
    base_usr = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if not base_usr:
        return None
    return Path(base_usr) / APP_NAME / "reno_config.json"


def sanitizar_url_supabase(url: str | None) -> str:
    """Normaliza URLs de Supabase (incluyendo URLs del Dashboard) a https://<ref>.supabase.co."""
    if not url:
        return ""
    import re

    u = str(url).strip().rstrip("/")
    if not u:
        return ""
    m = re.search(r"supabase\.com/dashboard/project/([a-zA-Z0-9_\-]+)", u, re.IGNORECASE)
    if m:
        return f"https://{m.group(1)}.supabase.co"
    u = re.sub(r"/(rest|api|storage)/v\d+.*$", "", u, flags=re.IGNORECASE).rstrip("/")
    if not u:
        return ""
    if u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    elif not u.startswith("https://"):
        u = "https://" + u
    return u.rstrip("/")


def resource_path(rel_path: str | Path) -> Path:
    """Resuelve la ruta absoluta de un recurso, compatible con desarrollo y PyInstaller."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    else:
        base = BASE_DIR
    return base / rel_path


def obtener_rutas_icono_candidatas(prefer_ico: bool = False) -> list[Path]:
    """Devuelve rutas candidatas ordenadas para localizar el icono oficial de RENO.

    Prioriza archivos externos junto al ejecutable/directorio activo y luego
    los recursos empaquetados mediante ``resource_path`` (desarrollo y ``_MEIPASS``).
    """
    nombres = (
        ("assets/reno_icon.ico", "assets/reno_icon.png", "assets/icon.ico", "assets/icon.png", "reno_icon.ico", "reno_icon.png")
        if prefer_ico
        else ("assets/reno_icon.png", "assets/reno_icon.ico", "assets/icon.png", "assets/icon.ico", "reno_icon.png", "reno_icon.ico")
    )

    app_dir = obtener_directorio_aplicacion()
    candidatos: list[Path] = []
    vistos: set[str] = set()

    for rel in nombres:
        for ruta in (app_dir / rel, resource_path(rel)):
            clave = str(ruta)
            if clave not in vistos:
                vistos.add(clave)
                candidatos.append(ruta)
    return candidatos


def obtener_ruta_icono(prefer_ico: bool = False) -> Path | None:
    """Retorna la ruta del primer archivo de icono existente o None si no se encuentra."""
    for candidato in obtener_rutas_icono_candidatas(prefer_ico=prefer_ico):
        try:
            if candidato.is_file():
                return candidato
        except OSError:
            continue
    return None



def obtener_ruta_base_datos() -> tuple[Path, Path | None]:
    """Obtiene la ruta a la base de datos SQLite y opcionalmente el XLSX asociado.
    
    Lee desde 'config.json' ('database_path') o 'reno_config.json'.
    Si no existe o es relativa, se resuelve contra APP_DATA_DIR.
    """
    ruta_txt = ""
    if PATH_BASE_FILE.exists():
        try:
            contenido = PATH_BASE_FILE.read_text(encoding="utf-8").strip()
            if contenido:
                if contenido.startswith("{"):
                    data = json.loads(contenido)
                    ruta_txt = str(data.get("database_path") or data.get("path_base") or "").strip()
                else:
                    ruta_txt = contenido
        except Exception:
            pass

    # Si no había config.json pero existe el archivo legado path_base, migrarlo y eliminarlo
    legacy_file = APP_DATA_DIR / "path_base"
    if not ruta_txt and legacy_file.exists():
        try:
            leg_val = legacy_file.read_text(encoding="utf-8").strip()
            if leg_val:
                ruta_txt = leg_val
                guardar_ruta_base_datos(leg_val)
            legacy_file.unlink(missing_ok=True)
        except Exception:
            pass

    if not ruta_txt and CONFIG_JSON_PATH.exists():
        try:
            with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                ruta_txt = str(cfg.get("database_path", "")).strip()
        except Exception:
            pass

    if not ruta_txt:
        ruta_txt = "RENO.sqlite"

    path_obj = Path(ruta_txt)
    if not path_obj.is_absolute():
        path_obj = APP_DATA_DIR / path_obj

    if path_obj.suffix.lower() == ".xlsx":
        xlsx_path = path_obj
        sqlite_path = path_obj.with_suffix(".sqlite")
    else:
        sqlite_path = path_obj if path_obj.suffix.lower() == ".sqlite" else path_obj.with_suffix(".sqlite")
        xlsx_candidato = sqlite_path.with_suffix(".xlsx")
        xlsx_path = xlsx_candidato if xlsx_candidato.exists() else None

    return sqlite_path, xlsx_path


def guardar_ruta_base_datos(nueva_ruta: str | Path) -> bool:
    """Guarda la ruta de la base de datos en config.json y en reno_config.json."""
    ruta_str = str(nueva_ruta).strip()
    exito = True
    try:
        data: dict[str, Any] = {}
        if PATH_BASE_FILE.exists():
            try:
                contenido = PATH_BASE_FILE.read_text(encoding="utf-8").strip()
                if contenido.startswith("{"):
                    data = json.loads(contenido)
            except Exception:
                data = {}
        data["database_path"] = ruta_str
        with open(PATH_BASE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        (APP_DATA_DIR / "path_base").unlink(missing_ok=True)
    except Exception:
        exito = False

    try:
        cfg = cargar_configuracion_app()
        cfg["database_path"] = ruta_str
        if not guardar_configuracion_app(cfg):
            exito = False
    except Exception:
        exito = False

    return exito


def cargar_variables_entorno(sobreescribir: bool = True) -> dict[str, str]:
    """Carga variables desde el archivo .env si existe en APP_DATA_DIR o BASE_DIR."""
    env_vars: dict[str, str] = {}
    candidatos: list[Path] = []
    for p in (APP_DATA_DIR / ".env", BASE_DIR / ".env"):
        if p not in candidatos:
            candidatos.append(p)

    for env_file in candidatos:
        if env_file.exists():
            try:
                for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k:
                        env_vars[k] = v
                        if sobreescribir or not os.environ.get(k):
                            os.environ[k] = v
                break
            except Exception:
                pass
    return env_vars


def cargar_configuracion_app() -> dict[str, Any]:
    """Carga reno_config.json con valores por defecto y soporte de .env."""
    env_vars = cargar_variables_entorno()
    sb_url_env = (env_vars.get("SUPABASE_URL") or os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    sb_anon_env = (env_vars.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_ANON_KEY") or "").strip()
    sb_serv_env = (
        env_vars.get("SUPABASE_SERVICE_KEY")
        or env_vars.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or ""
    ).strip()
    sb_bucket_env = (
        env_vars.get("SUPABASE_STORAGE_BUCKET")
        or os.environ.get("SUPABASE_STORAGE_BUCKET")
        or "updates"
    ).strip() or "updates"
    updater_url_env = (env_vars.get("RENO_UPDATES_URL") or os.environ.get("RENO_UPDATES_URL") or "").strip()

    defaults: dict[str, Any] = {
        "supabase_url": sb_url_env,
        "supabase_anon_key": sb_anon_env,
        "supabase_service_role_key": sb_serv_env,
        "supabase_storage_bucket": sb_bucket_env,
        "updater_url": updater_url_env,
        "updater_auto_check": True,
        "sqlite_journal_mode": "WAL",  # 'WAL' o 'DELETE'
        "sync_mode": "",  # 'LOCAL', 'SHARED_SQLITE', 'SUPABASE', 'RING' (vacío = inferir por credenciales)
        "sync_ring_primary": "SHARED_SQLITE",  # 'SHARED_SQLITE' o 'SUPABASE'
        "shared_sqlite_path": "",
        "sync_auto_enabled": True,
        "sync_interval_sec": 45,
        "autosync_habilitado": False,
        "autosync_intervalo_min": 5,
        "autorefresco_habilitado": True,
        "autorefresco_intervalo_seg": 30,
        "proxy_mode": "direct",  # 'direct', 'manual', 'windows_sso'
        "proxy_host": "",
        "proxy_port": 8080,
        "proxy_user": "",
        "proxy_pass": "",
        "proxy_sso_server": "",
        "proxy_auth": "manual_fijo",  # 'manual_fijo', 'sesion_reno', 'windows_sspi'
        "tema_visual": "claro",
    }

    rutas_leer: list[Path] = []
    if CONFIG_JSON_PATH.exists():
        rutas_leer.append(CONFIG_JSON_PATH)

    if CONFIG_JSON_PATH == _DEFAULT_CONFIG_JSON_PATH:
        usr_cfg = _ruta_config_usuario_fallback()
        if usr_cfg and usr_cfg.exists():
            try:
                if not CONFIG_JSON_PATH.exists() or usr_cfg.stat().st_mtime >= CONFIG_JSON_PATH.stat().st_mtime:
                    rutas_leer.append(usr_cfg)
            except OSError:
                rutas_leer.append(usr_cfg)

    for ruta_cfg in rutas_leer:
        try:
            with open(ruta_cfg, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    if isinstance(v, (bool, int, float)) or v or k not in defaults:
                        defaults[k] = v
        except Exception:
            pass
    return defaults


def guardar_configuracion_app(config: dict[str, Any]) -> bool:
    """Guarda la configuración en reno_config.json (con respaldo en perfil de usuario si aplica)."""
    # Nunca persistir claves privilegiadas service_role en el archivo local del cliente
    config_segura = {k: v for k, v in config.items() if k != "supabase_service_role_key"}
    try:
        CONFIG_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(config_segura, f, indent=2, ensure_ascii=False)
        if CONFIG_JSON_PATH == _DEFAULT_CONFIG_JSON_PATH:
            usr_cfg = _ruta_config_usuario_fallback()
            if usr_cfg and usr_cfg.exists():
                try:
                    usr_cfg.unlink(missing_ok=True)
                except OSError:
                    pass
        return True
    except Exception:
        if CONFIG_JSON_PATH == _DEFAULT_CONFIG_JSON_PATH:
            usr_cfg = _ruta_config_usuario_fallback()
            if usr_cfg is not None:
                try:
                    usr_cfg.parent.mkdir(parents=True, exist_ok=True)
                    with open(usr_cfg, "w", encoding="utf-8") as f:
                        json.dump(config_segura, f, indent=2, ensure_ascii=False)
                    return True
                except Exception:
                    return False
        return False


cargar_configuracion_json = cargar_configuracion_app
guardar_configuracion_json = guardar_configuracion_app

