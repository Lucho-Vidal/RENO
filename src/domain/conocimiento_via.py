"""Reglas de dominio puras para Conocimiento de Vía por Sector (Personal de Conducción).

- Catálogo base editable de sectores de la Línea Roca (19 sectores iniciales).
- Cálculo de vencimiento por convenio a los 2 años con doble umbral de alerta (60 y 30 días).
- Convención ferroviaria Línea Roca: PC (Plaza Constitución) = Km 0;
  todo recorrido que se aleja de PC (origen_sigla -> destino_sigla) es ASCENDENTE (ASC)
  y todo recorrido que regresa hacia PC (destino_sigla -> origen_sigla) es DESCENDENTE (DESC).
- Resolución de sectores por grafo (BFS) a partir de origen/destino y paradas intermedias de un turno.
"""

from __future__ import annotations

from collections import deque
from datetime import date, datetime
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.domain.estaciones import ESTACIONES_BASE, CatalogoEstacionesIndex
from src.domain.validators import parsear_fecha

# 19 sectores iniciales de la Línea Roca (origen_sigla -> destino_sigla = sentido ASC, alejándose de PC km 0)
SECTORES_VIA_BASE: Tuple[Dict[str, Any], ...] = (
    {"codigo": "PC-TY", "origen_sigla": "PC", "destino_sigla": "TY", "descripcion": "Plaza Constitución - Témperley", "orden": 1},
    {"codigo": "PC-BZ", "origen_sigla": "PC", "destino_sigla": "BZ", "descripcion": "Plaza Constitución - Berazategui", "orden": 2},
    {"codigo": "BZ-VE", "origen_sigla": "BZ", "destino_sigla": "VE", "descripcion": "Berazategui - Villa Elisa", "orden": 3},
    {"codigo": "VE-LP", "origen_sigla": "VE", "destino_sigla": "LP", "descripcion": "Villa Elisa - La Plata", "orden": 4},
    {"codigo": "BZ-BQ", "origen_sigla": "BZ", "destino_sigla": "BQ", "descripcion": "Berazategui - Bosques", "orden": 5},
    {"codigo": "BQ-GT", "origen_sigla": "BQ", "destino_sigla": "GT", "descripcion": "Bosques - Gutiérrez", "orden": 6},
    {"codigo": "GT-VE", "origen_sigla": "GT", "destino_sigla": "VE", "descripcion": "Gutiérrez - Villa Elisa", "orden": 7},
    {"codigo": "TY-BQ", "origen_sigla": "TY", "destino_sigla": "BQ", "descripcion": "Témperley - Bosques", "orden": 8},
    {"codigo": "TY-BO", "origen_sigla": "TY", "destino_sigla": "BO", "descripcion": "Témperley - Burzaco", "orden": 9},
    {"codigo": "BO-GW", "origen_sigla": "BO", "destino_sigla": "GW", "descripcion": "Burzaco - Glew", "orden": 10},
    {"codigo": "GW-AK", "origen_sigla": "GW", "destino_sigla": "AK", "descripcion": "Glew - Alejandro Korn", "orden": 11},
    {"codigo": "TY-LLV", "origen_sigla": "TY", "destino_sigla": "LLV", "descripcion": "Témperley - Llavallol", "orden": 12},
    {"codigo": "LLV-MG", "origen_sigla": "LLV", "destino_sigla": "MG", "descripcion": "Llavallol - Monte Grande", "orden": 13},
    {"codigo": "MG-ZZ", "origen_sigla": "MG", "destino_sigla": "ZZ", "descripcion": "Monte Grande - Ezeiza", "orden": 14},
    {"codigo": "ZZ-CÑ", "origen_sigla": "ZZ", "destino_sigla": "CÑ", "descripcion": "Ezeiza - Cañuelas", "orden": 15},
    {"codigo": "TY-HDO", "origen_sigla": "TY", "destino_sigla": "HDO", "descripcion": "Témperley - Haedo", "orden": 16},
    {"codigo": "CÑ-MN", "origen_sigla": "CÑ", "destino_sigla": "MN", "descripcion": "Cañuelas - Monte", "orden": 17},
    {"codigo": "CÑ-LB", "origen_sigla": "CÑ", "destino_sigla": "LB", "descripcion": "Cañuelas - Lobos", "orden": 18},
    {"codigo": "LP-Univ", "origen_sigla": "LP", "destino_sigla": "UNIV", "descripcion": "La Plata - Tren Universitario", "orden": 19},
)

# Alias operativos que mapean siglas intermedias / equivalentes al nodo canónico de sector
_SIGLAS_TREN_UNIVERSITARIO = frozenset({"UNIV", "AQ", "IF", "MNA", "PDO", "D73", "PLO", "CCC", "AV7", "MV", "HSJ"})
_ALIAS_NODO_SECTOR: Dict[str, str] = {
    # Gutiérrez figura como JG en ESTACIONES_BASE y GT en el código de sector BQ-GT / GT-VE
    "JG": "GT",
    # Estaciones intermedias habituales que pertenecen a un nodo cercano si aparecen como cabecera
    "UNIV": "UNIV",
    "UNIVERSITARIO": "UNIV",
    "TREN UNIVERSITARIO": "UNIV",
}
for _s in _SIGLAS_TREN_UNIVERSITARIO:
    _ALIAS_NODO_SECTOR[_s] = "UNIV"

# Estaciones intermedias de cada sector base (para resolver origen/destino que caen en medio de un sector)
_ESTACIONES_INTERMEDIAS_POR_SECTOR: Dict[str, Tuple[str, ...]] = {
    "PC-TY": ("HY", "ALL", "GL", "LS", "RE", "BF", "ZA"),
    "PC-BZ": ("HY", "ALL", "SD", "LLD", "WL", "NB", "BN", "QL", "ZT"),
    "BZ-VE": ("TS", "HD", "PY"),
    "VE-LP": ("BLL", "RI", "OA"),
    "BZ-BQ": ("VÑ", "RH", "SG"),
    "BQ-GT": ("SS",),
    "TY-BQ": ("JM", "LLZ", "CY", "AG", "FV", "ZB"),
    "TY-BO": ("AD",),
    "BO-GW": ("CHP",),
    "GW-AK": ("GC",),
    "TY-LLV": ("UD",),
    "LLV-MG": ("GLL",),
    "MG-ZZ": ("EJ",),
    "ZZ-CÑ": ("HZZ", "UF", "TZ", "SP", "MX", "VC", "PT", "KL", "LV", "HCA"),
    "TY-HDO": ("HE", "SC", "JX", "K34", "K33", "DL", "TB", "SJ", "BR"),
    "CÑ-MN": ("AT",),
    "CÑ-LB": ("UA", "E"),
    "LP-UNIV": tuple(sorted(_SIGLAS_TREN_UNIVERSITARIO)),
}

ESTADOS_CONOCIMIENTO = (
    "VIGENTE",
    "POR_VENCER_60",
    "POR_VENCER_30",
    "VENCIDO",
    "SIN_REGISTRO",
)


def _norm_texto(valor: Optional[str]) -> str:
    raw = " ".join(str(valor or "").strip().split())
    nfkd = unicodedata.normalize("NFKD", raw)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def es_personal_conduccion(especialidad: Optional[str]) -> bool:
    """Devuelve True si la especialidad corresponde a personal de conducción (conductores y ayudantes)."""
    norm = _norm_texto(especialidad)
    if not norm:
        return False
    if "guarda" in norm:
        return False
    return ("conductor" in norm) or ("ayudante" in norm)


def es_ayudante_conduccion(especialidad: Optional[str]) -> bool:
    """Devuelve True si la especialidad es de ayudante (sin alertas de vencimiento)."""
    norm = _norm_texto(especialidad)
    return "ayudante" in norm


def calcular_fecha_vencimiento(fecha_conocimiento: date, anios: int = 2) -> date:
    """Suma ``anios`` años calendario a ``fecha_conocimiento`` manejando años bisiestos."""
    try:
        return fecha_conocimiento.replace(year=fecha_conocimiento.year + anios)
    except ValueError:
        # Caso 29 de febrero en año no bisiesto -> 28 de febrero
        return fecha_conocimiento.replace(year=fecha_conocimiento.year + anios, day=28)


def normalizar_fecha_iso(fecha_valor: Any) -> Optional[str]:
    """Normaliza DD/MM/YYYY o YYYY-MM-DD a formato ISO YYYY-MM-DD."""
    if fecha_valor is None:
        return None
    if isinstance(fecha_valor, datetime):
        return fecha_valor.date().strftime("%Y-%m-%d")
    if isinstance(fecha_valor, date):
        return fecha_valor.strftime("%Y-%m-%d")
    raw = str(fecha_valor).strip()
    if not raw or raw in ("-", "None", "null", "NULL"):
        return None
    dt = parsear_fecha(raw, "Fecha de conocimiento", obligatorio=False)
    if not dt:
        return None
    return dt.date().strftime("%Y-%m-%d")


def evaluar_vigencia_conocimiento(
    fecha_conocimiento: Any,
    hoy: Optional[date] = None,
    anios_vigencia: int = 2,
    umbral_alerta_60: int = 60,
    umbral_alerta_30: int = 30,
    suprimir_alertas: bool = False,
) -> Dict[str, Any]:
    """Evalúa la vigencia de un registro de conocimiento de vía (convenio: 2 años).

    Umbrales de alerta:
    - ``VENCIDO``: ``dias_restantes < 0``
    - ``POR_VENCER_30``: ``0 <= dias_restantes <= 30``
    - ``POR_VENCER_60``: ``30 < dias_restantes <= 60``
    - ``VIGENTE``: ``dias_restantes > 60``

    Si ``suprimir_alertas=True`` (p. ej. Ayudantes), el ``estado_alerta`` es ``SIN_ALERTA``
    (nunca dispara alertas visuales de 60/30 días ni vencido), conservando ``fecha_vencimiento``
    y ``dias_restantes`` para consulta informativa.
    """
    iso = normalizar_fecha_iso(fecha_conocimiento)
    if not iso:
        return {
            "fecha_iso": "",
            "fecha_display": "",
            "fecha_vencimiento_iso": "",
            "fecha_vencimiento_display": "",
            "dias_restantes": None,
            "estado": "SIN_REGISTRO",
            "estado_alerta": "SIN_REGISTRO",
        }

    f_con = date.fromisoformat(iso)
    f_venc = calcular_fecha_vencimiento(f_con, anios=anios_vigencia)
    ref = hoy or date.today()
    dias_restantes = (f_venc - ref).days

    if dias_restantes < 0:
        estado_real = "VENCIDO"
    elif dias_restantes <= umbral_alerta_30:
        estado_real = "POR_VENCER_30"
    elif dias_restantes <= umbral_alerta_60:
        estado_real = "POR_VENCER_60"
    else:
        estado_real = "VIGENTE"

    estado_alerta = "SIN_ALERTA" if suprimir_alertas else estado_real

    return {
        "fecha_iso": f_con.strftime("%Y-%m-%d"),
        "fecha_display": f_con.strftime("%d/%m/%Y"),
        "fecha_vencimiento_iso": f_venc.strftime("%Y-%m-%d"),
        "fecha_vencimiento_display": f_venc.strftime("%d/%m/%Y"),
        "dias_restantes": dias_restantes,
        "estado": estado_real,
        "estado_alerta": estado_alerta,
    }


def _normalizar_nodo_sigla(
    texto: Optional[str],
    catalogo: Optional[CatalogoEstacionesIndex] = None,
) -> str:
    raw = str(texto or "").strip()
    if not raw:
        return ""
    up = raw.upper().strip()
    if up in _ALIAS_NODO_SECTOR:
        return _ALIAS_NODO_SECTOR[up]
    if catalogo is not None:
        st = catalogo.resolver_estacion(raw)
        if st:
            sigla = str(st.get("sigla") or "").strip().upper()
            if sigla in _ALIAS_NODO_SECTOR:
                return _ALIAS_NODO_SECTOR[sigla]
            if sigla:
                return sigla
    return up


def _construir_catalogo_default() -> CatalogoEstacionesIndex:
    return CatalogoEstacionesIndex(ESTACIONES_BASE)


_CACHE_CATALOGO_DEFAULT: Optional[CatalogoEstacionesIndex] = None


def resolver_sectores_recorrido(
    origen: str,
    destino: str,
    sectores: Sequence[Dict[str, Any]],
    paradas_intermedias: Optional[Sequence[str]] = None,
    catalogo: Optional[CatalogoEstacionesIndex] = None,
) -> List[Dict[str, Any]]:
    """Resuelve los sectores de vía recorridos entre ``origen`` y ``destino`` usando BFS.

    Convención de sentidos (Línea Roca, PC = km 0):
    - Recorrer un sector de ``origen_sigla -> destino_sigla`` (alejándose de PC) => ``ASC``.
    - Recorrer un sector de ``destino_sigla -> origen_sigla`` (regresando hacia PC) => ``DESC``.

    Retorna lista ordenada de dicts: ``{"sector_id", "codigo", "sentido", "origen_nodo", "destino_nodo"}``.
    """
    cat = catalogo
    if cat is None:
        global _CACHE_CATALOGO_DEFAULT
        if _CACHE_CATALOGO_DEFAULT is None:
            _CACHE_CATALOGO_DEFAULT = _construir_catalogo_default()
        cat = _CACHE_CATALOGO_DEFAULT

    # Construir adyacencia del grafo de sectores activos
    adyacencia: Dict[str, List[Tuple[str, Dict[str, Any], str]]] = {}
    estacion_a_sectores: Dict[str, List[Dict[str, Any]]] = {}

    for sec in sectores:
        if not sec.get("activo", 1):
            continue
        cod = str(sec.get("codigo") or "").strip()
        ori = _normalizar_nodo_sigla(sec.get("origen_sigla"), cat)
        des = _normalizar_nodo_sigla(sec.get("destino_sigla"), cat)
        if not ori or not des or ori == des:
            continue
        # Arista directa: ori -> des = ASC (aumenta km desde PC)
        adyacencia.setdefault(ori, []).append((des, dict(sec), "ASC"))
        # Arista inversa: des -> ori = DESC (disminuye km hacia PC)
        adyacencia.setdefault(des, []).append((ori, dict(sec), "DESC"))

        intermedias = _ESTACIONES_INTERMEDIAS_POR_SECTOR.get(cod.upper(), ())
        for est_sig in intermedias:
            estacion_a_sectores.setdefault(est_sig.upper(), []).append(dict(sec))

    nodo_ori = _normalizar_nodo_sigla(origen, cat)
    nodo_des = _normalizar_nodo_sigla(destino, cat)
    if not nodo_ori or not nodo_des or nodo_ori == nodo_des:
        return []

    def _candidatos_nodo(sigla: str) -> List[str]:
        if sigla in adyacencia:
            return [sigla]
        # Si la estación es intermedia dentro de un sector, usar sus nodos extremos
        cands: List[str] = []
        for sec in estacion_a_sectores.get(sigla, ()):
            a = _normalizar_nodo_sigla(sec.get("origen_sigla"), cat)
            b = _normalizar_nodo_sigla(sec.get("destino_sigla"), cat)
            for n in (a, b):
                if n in adyacencia and n not in cands:
                    cands.append(n)
        return cands

    def _bfs(inicio: str, fin: str) -> Optional[List[Dict[str, Any]]]:
        if inicio == fin:
            return []
        if inicio not in adyacencia or fin not in adyacencia:
            return None
        cola: deque[Tuple[str, List[Dict[str, Any]]]] = deque([(inicio, [])])
        visitados = {inicio}
        while cola:
            actual, camino = cola.popleft()
            for vecino, sec_data, sentido in adyacencia.get(actual, ()):
                if vecino in visitados:
                    continue
                paso = {
                    "sector_id": sec_data.get("id"),
                    "codigo": sec_data.get("codigo"),
                    "sentido": sentido,
                    "origen_nodo": actual,
                    "destino_nodo": vecino,
                }
                nuevo_camino = camino + [paso]
                if vecino == fin:
                    return nuevo_camino
                visitados.add(vecino)
                cola.append((vecino, nuevo_camino))
        return None

    # Si hay paradas intermedias que coinciden con nodos clave del grafo (p. ej. BQ o GT),
    # encadenar segmentos para desambiguar rutas alternativas (ej. PC->BZ->BQ->GT->VE vs PC->BZ->VE).
    waypoints: List[str] = []
    if paradas_intermedias:
        for p in paradas_intermedias:
            sig = _normalizar_nodo_sigla(p, cat)
            if sig and sig in adyacencia and sig not in (nodo_ori, nodo_des):
                if not waypoints or waypoints[-1] != sig:
                    waypoints.append(sig)

    inicios = _candidatos_nodo(nodo_ori)
    fines = _candidatos_nodo(nodo_des)
    if not inicios or not fines:
        return []

    if waypoints:
        secuencia_nodos = [inicios[0], *waypoints, fines[0]]
        camino_compuesto: List[Dict[str, Any]] = []
        valido = True
        for i in range(len(secuencia_nodos) - 1):
            sub = _bfs(secuencia_nodos[i], secuencia_nodos[i + 1])
            if sub is None:
                valido = False
                break
            camino_compuesto.extend(sub)
        if valido and camino_compuesto:
            # Deduplicar manteniendo orden (por si un waypoint estaba en borde)
            vistos_paso = set()
            limpio = []
            for p in camino_compuesto:
                k = (p["codigo"], p["sentido"])
                if k not in vistos_paso:
                    vistos_paso.add(k)
                    limpio.append(p)
            return limpio

    mejor: Optional[List[Dict[str, Any]]] = None
    for ini in inicios:
        for fin in fines:
            cand = _bfs(ini, fin)
            if cand is not None:
                if mejor is None or len(cand) < len(mejor):
                    mejor = cand
    return mejor or []
