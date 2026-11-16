"""Catálogo canónico de estaciones ferroviarias y equivalencias nombre <-> abreviatura/sigla.

Módulo de dominio puro (0 dependencias externas) que permite normalizar nombres
de estaciones con o sin acentos, sufijos operativos ``(Pda.)`` / ``(Ap.)``,
variantes históricas (p. ej. ``Avellaneda`` <-> ``Kosteki y Santillán``) y
abreviaturas/siglas operativas (p. ej. ``PC``, ``TY``, ``ALL``, ``CÑ``).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ESTACIONES_BASE: Tuple[Dict[str, Any], ...] = (
    {"codigo_origen": 1, "nombre": "Plaza C.", "sigla": "PC", "aliases": ("Plaza Constitución", "Plaza Constitucion", "P. Constitución", "P. Constitucion", "Plaza", "P.C.")},
    {"codigo_origen": 2, "nombre": "Yrigoyen", "sigla": "HY", "aliases": ("H. Yrigoyen (Pda.)", "H. Yrigoyen", "Hipólito Yrigoyen", "Hipolito Yrigoyen", "H.Y.")},
    {"codigo_origen": 3, "nombre": "Avellaneda", "sigla": "ALL", "aliases": ("Kosteki y Santillán", "Kosteki y Santillan", "Darío Santillán y Maximiliano Kosteki", "Santillán y Kosteki", "K. y Santillán", "D. Santillán y M. Kosteki")},
    {"codigo_origen": 4, "nombre": "Gerli", "sigla": "GL", "aliases": ("Gerli (Pda.)",)},
    {"codigo_origen": 5, "nombre": "Lanús", "sigla": "LS", "aliases": ("Lanus",)},
    {"codigo_origen": 6, "nombre": "Escalada", "sigla": "RE", "aliases": ("R. de Escalada", "Remedios de Escalada", "R. Escalada")},
    {"codigo_origen": 7, "nombre": "Banfield", "sigla": "BF", "aliases": ()},
    {"codigo_origen": 8, "nombre": "Lomas de Zamora", "sigla": "ZA", "aliases": ("L. de Zamora", "Lomas")},
    {"codigo_origen": 9, "nombre": "Témperley", "sigla": "TY", "aliases": ("Temperley",)},
    {"codigo_origen": 10, "nombre": "Adrogué", "sigla": "AD", "aliases": ("Adrogue",)},
    {"codigo_origen": 11, "nombre": "Burzaco", "sigla": "BO", "aliases": ()},
    {"codigo_origen": 12, "nombre": "Longchamps", "sigla": "CHP", "aliases": ()},
    {"codigo_origen": 13, "nombre": "Glew", "sigla": "GW", "aliases": ()},
    {"codigo_origen": 14, "nombre": "Guernica", "sigla": "GC", "aliases": ()},
    {"codigo_origen": 15, "nombre": "Korn", "sigla": "AK", "aliases": ("A. Korn", "Alejandro Korn")},
    {"codigo_origen": 16, "nombre": "Turdera", "sigla": "UD", "aliases": ()},
    {"codigo_origen": 17, "nombre": "Llavallol", "sigla": "LLV", "aliases": ("LL",)},
    {"codigo_origen": 18, "nombre": "Guillón", "sigla": "GLL", "aliases": ("L. Guillón", "L. Guillon", "Guillon", "Luis Guillón", "Luis Guillon")},
    {"codigo_origen": 19, "nombre": "Monte Grande", "sigla": "MG", "aliases": ("Mte. Grande", "M. Grande")},
    {"codigo_origen": 20, "nombre": "El Jagüel", "sigla": "EJ", "aliases": ("El Jaguel (Pda.)", "El Jaguel", "El Jagüel (Pda.)", "Jagüel", "Jaguel")},
    {"codigo_origen": 21, "nombre": "Ezeiza", "sigla": "ZZ", "aliases": ()},
    {"codigo_origen": 22, "nombre": "Mármol", "sigla": "JM", "aliases": ("J. Mármol", "Jose Marmol", "José Mármol", "Marmol")},
    {"codigo_origen": 23, "nombre": "Calzada", "sigla": "LLZ", "aliases": ("Rafael Calzada", "R. Calzada")},
    {"codigo_origen": 24, "nombre": "Cláypole", "sigla": "CY", "aliases": ("Claypole",)},
    {"codigo_origen": 25, "nombre": "Sarandí", "sigla": "SD", "aliases": ("Sarandi",)},
    {"codigo_origen": 26, "nombre": "Domínico", "sigla": "LLD", "aliases": ("Villa Domínico", "V. Domínico", "Dominico")},
    {"codigo_origen": 27, "nombre": "Wilde", "sigla": "WL", "aliases": ()},
    {"codigo_origen": 28, "nombre": "Don Bosco", "sigla": "NB", "aliases": ("D. Bosco",)},
    {"codigo_origen": 29, "nombre": "Bernal", "sigla": "BN", "aliases": ()},
    {"codigo_origen": 30, "nombre": "Quilmes", "sigla": "QL", "aliases": ()},
    {"codigo_origen": 31, "nombre": "Ezpeleta", "sigla": "ZT", "aliases": ()},
    {"codigo_origen": 32, "nombre": "Berazategui", "sigla": "BZ", "aliases": ()},
    {"codigo_origen": 33, "nombre": "Plátanos", "sigla": "TS", "aliases": ("Platanos",)},
    {"codigo_origen": 34, "nombre": "Hudson", "sigla": "HD", "aliases": ()},
    {"codigo_origen": 35, "nombre": "Pereyra", "sigla": "PY", "aliases": ()},
    {"codigo_origen": 36, "nombre": "V.Elisa", "sigla": "VE", "aliases": ("V. Elisa", "Villa Elisa")},
    {"codigo_origen": 37, "nombre": "City Bell", "sigla": "BLL", "aliases": ("C. Bell",)},
    {"codigo_origen": 38, "nombre": "Gonnet", "sigla": "GT", "aliases": ()},
    {"codigo_origen": 39, "nombre": "Ringuelet", "sigla": "RI", "aliases": ()},
    {"codigo_origen": 40, "nombre": "Tolosa", "sigla": "OA", "aliases": ()},
    {"codigo_origen": 41, "nombre": "La Plata", "sigla": "LP", "aliases": ()},
    {"codigo_origen": 42, "nombre": "V.España", "sigla": "VÑ", "aliases": ("V. España", "Villa España")},
    {"codigo_origen": 43, "nombre": "Ranelagh", "sigla": "RH", "aliases": ()},
    {"codigo_origen": 44, "nombre": "Sourigues", "sigla": "SG", "aliases": ()},
    {"codigo_origen": 45, "nombre": "Bosques", "sigla": "BQ", "aliases": ()},
    {"codigo_origen": 46, "nombre": "Sta.Sofía", "sigla": "SS", "aliases": ("Sta. Sofía", "Santa Sofía", "Santa Sofia", "Sta. Sofia")},
    {"codigo_origen": 47, "nombre": "Gutierrez", "sigla": "JG", "aliases": ("Gutiérrez", "J. M. Gutiérrez")},
    {"codigo_origen": 48, "nombre": "Zeballos", "sigla": "ZB", "aliases": ("E. Zeballos", "Estanislao Zeballos")},
    {"codigo_origen": 49, "nombre": "Varela", "sigla": "FV", "aliases": ("F. Varela", "Florencio Varela")},
    {"codigo_origen": 50, "nombre": "Ardigó", "sigla": "AG", "aliases": ("Ardigo", "D. Ardigó")},
    {"codigo_origen": 51, "nombre": "H.Español", "sigla": "HE", "aliases": ("H. Español", "Hospital Español", "Htal. Español")},
    {"codigo_origen": 52, "nombre": "Santa Catalina", "sigla": "SC", "aliases": ("Sta. Catalina",)},
    {"codigo_origen": 53, "nombre": "Juan XXIII", "sigla": "JX", "aliases": ()},
    {"codigo_origen": 54, "nombre": "KM.34", "sigla": "K34", "aliases": ("Km 34", "Km. 34")},
    {"codigo_origen": 55, "nombre": "P.Turner", "sigla": "K33", "aliases": ("P. Turner", "Km 33", "Km. 33")},
    {"codigo_origen": 56, "nombre": "De Elia", "sigla": "DL", "aliases": ("De Elía", "A. De Elía")},
    {"codigo_origen": 57, "nombre": "Tablada", "sigla": "TB", "aliases": ("La Tablada",)},
    {"codigo_origen": 58, "nombre": "San Justo", "sigla": "SJ", "aliases": ()},
    {"codigo_origen": 59, "nombre": "Ing.Brian", "sigla": "BR", "aliases": ("Ing. Brian", "Ingeniero Brian")},
    {"codigo_origen": 60, "nombre": "Haedo", "sigla": "HDO", "aliases": ()},
    {"codigo_origen": 61, "nombre": "U.Ferroviaria", "sigla": "UF", "aliases": ("U. Ferroviaria (Pda.)", "U. Ferroviaria", "Universidad Ferroviaria", "U.Ferroviaria (Pda.)")},
    {"codigo_origen": 62, "nombre": "T.Suárez", "sigla": "TZ", "aliases": ("Tr. Suárez", "Tr. Suarez", "T. Suarez", "Tristán Suárez", "Tristan Suarez")},
    {"codigo_origen": 63, "nombre": "Spegazzini", "sigla": "SP", "aliases": ("C. Spegazzini (Pda.)", "C. Spegazzini", "Carlos Spegazzini")},
    {"codigo_origen": 64, "nombre": "M.Paz", "sigla": "MX", "aliases": ("Máximo Paz", "Maximo Paz", "M. Paz")},
    {"codigo_origen": 65, "nombre": "V.Casares", "sigla": "VC", "aliases": ("Vte. Casares", "Vicente Casares", "V. Casares")},
    {"codigo_origen": 66, "nombre": "A.Petión", "sigla": "PT", "aliases": ("A. Petión (Ap.)", "A. Petion (Ap.)", "A. Petión", "A. Petion", "A.Petion", "Alejandro Petión")},
    {"codigo_origen": 67, "nombre": "Kloosterman", "sigla": "KL", "aliases": ("Kloosterman (Ap.)",)},
    {"codigo_origen": 68, "nombre": "Dr.Levene", "sigla": "LV", "aliases": ("Dr. R. Levene (Ap.)", "Dr. R. Levene", "Dr. Levene", "Dr.Levene (Ap.)")},
    {"codigo_origen": 69, "nombre": "Cañuelas", "sigla": "CÑ", "aliases": ("Canuelas",)},
    {"codigo_origen": 70, "nombre": "Domselaar", "sigla": "D", "aliases": ()},
    {"codigo_origen": 71, "nombre": "Brandsen", "sigla": "BS", "aliases": ("Cnel. Brandsen",)},
    {"codigo_origen": 72, "nombre": "Jeppener", "sigla": "JPP", "aliases": ()},
    {"codigo_origen": 73, "nombre": "Altamirano", "sigla": "AO", "aliases": ()},
    {"codigo_origen": 74, "nombre": "Gandara", "sigla": "GA", "aliases": ("Gándara",)},
    {"codigo_origen": 75, "nombre": "Chascomus", "sigla": "CHC", "aliases": ("Chascomús",)},
    {"codigo_origen": 76, "nombre": "Uribelarrea", "sigla": "UA", "aliases": ("Uribe",)},
    {"codigo_origen": 77, "nombre": "Emp.Lobos", "sigla": "E", "aliases": ("Emp. Lobos", "Empalme Lobos")},
    {"codigo_origen": 78, "nombre": "Lobos", "sigla": "LB", "aliases": ()},
    {"codigo_origen": 79, "nombre": "Sdor.María", "sigla": "SA", "aliases": ("Sdor. María", "Salvador María", "Sdor. Maria")},
    {"codigo_origen": 80, "nombre": "R.Pérez", "sigla": "QZ", "aliases": ("R. Pérez", "Roque Pérez", "R. Perez")},
    {"codigo_origen": 81, "nombre": "Del Carril", "sigla": "DC", "aliases": ()},
    {"codigo_origen": 82, "nombre": "Cazón", "sigla": "CZ", "aliases": ("Cazon",)},
    {"codigo_origen": 83, "nombre": "Saladillo", "sigla": "LLO", "aliases": ()},
    {"codigo_origen": 84, "nombre": "La Barrancosa", "sigla": "BR", "aliases": ()},
    {"codigo_origen": 85, "nombre": "J.M.Micheo", "sigla": "CHE", "aliases": ("J. M. Micheo", "Micheo")},
    {"codigo_origen": 86, "nombre": "Gral.Alvear", "sigla": "GL", "aliases": ("Gral. Alvear", "General Alvear")},
    {"codigo_origen": 87, "nombre": "La Noria", "sigla": "NR", "aliases": ()},
    {"codigo_origen": 88, "nombre": "Abbott", "sigla": "AT", "aliases": ("Abbot",)},
    {"codigo_origen": 89, "nombre": "Monte", "sigla": "MN", "aliases": ("S.M. del Monte", "San Miguel del Monte")},
    {"codigo_origen": 90, "nombre": "Arquitectura", "sigla": "AQ", "aliases": ()},
    {"codigo_origen": 91, "nombre": "Informática", "sigla": "IF", "aliases": ("Informatica",)},
    {"codigo_origen": 92, "nombre": "Medicina", "sigla": "MNA", "aliases": ()},
    {"codigo_origen": 93, "nombre": "Periodismo", "sigla": "PDO", "aliases": ()},
    {"codigo_origen": 94, "nombre": "Diagonal 73", "sigla": "D73", "aliases": ("Diag. 73",)},
    {"codigo_origen": 95, "nombre": "Policlínico", "sigla": "PLO", "aliases": ("Policlinico", "Htal. Policlínico")},
    {"codigo_origen": 96, "nombre": "Circunvalación", "sigla": "CCC", "aliases": ("Circunvalacion",)},
    {"codigo_origen": 97, "nombre": "Av. 7", "sigla": "AV7", "aliases": ("Avenida 7",)},
    {"codigo_origen": 98, "nombre": "Meridiano V", "sigla": "MV", "aliases": ()},
    {"codigo_origen": 99, "nombre": "Htal. S.J. de Dios", "sigla": "HSJ", "aliases": ("Hospital San Juan de Dios", "H. S.J. de Dios")},
    {"codigo_origen": 100, "nombre": "Htal. Cuenca Alta", "sigla": "HCA", "aliases": ("Htal. Cuenca Alta (Ap.)", "Cuenca Alta", "Hospital Cuenca Alta")},
    {"codigo_origen": 101, "nombre": "Htal. Ezeiza", "sigla": "HZZ", "aliases": ("Htal. Ezeiza (Ap.)", "Hospital Ezeiza")},
    {"codigo_origen": 102, "nombre": "Adela", "sigla": "AA", "aliases": ()},
    {"codigo_origen": 103, "nombre": "Monasterio", "sigla": "TR", "aliases": ()},
    {"codigo_origen": 104, "nombre": "Lezama", "sigla": "LZ", "aliases": ()},
    {"codigo_origen": 105, "nombre": "Guerrero", "sigla": "G", "aliases": ()},
    {"codigo_origen": 106, "nombre": "Castelli", "sigla": "CLL", "aliases": ()},
    {"codigo_origen": 107, "nombre": "Sevigne", "sigla": "VG", "aliases": ("Sevigné",)},
    {"codigo_origen": 108, "nombre": "Dolores", "sigla": "DS", "aliases": ()},
    {"codigo_origen": 109, "nombre": "Parravicini", "sigla": "PV", "aliases": ()},
    {"codigo_origen": 110, "nombre": "Gral. Guido", "sigla": "GI", "aliases": ("General Guido",)},
    {"codigo_origen": 111, "nombre": "Maipú", "sigla": "PU", "aliases": ("Maipu",)},
    {"codigo_origen": 112, "nombre": "Las Armas", "sigla": "AS", "aliases": ()},
    {"codigo_origen": 113, "nombre": "Gral. Pirán", "sigla": "GP", "aliases": ("General Pirán", "Gral. Piran")},
    {"codigo_origen": 114, "nombre": "Cnel. Vidal", "sigla": "VD", "aliases": ("Coronel Vidal",)},
    {"codigo_origen": 115, "nombre": "Vivoratá", "sigla": "VV", "aliases": ("Vivorata",)},
    {"codigo_origen": 116, "nombre": "Cobo", "sigla": "CB", "aliases": ()},
    {"codigo_origen": 117, "nombre": "Camet", "sigla": "CT", "aliases": ()},
    {"codigo_origen": 118, "nombre": "Mar del Plata", "sigla": "MPN", "aliases": ("M. del Plata",)},
    # Puntos operativos de itinerario sin sigla oficial en estaciones.xlsx
    {"codigo_origen": None, "nombre": "Emp. Cañuelas", "sigla": "", "aliases": ("Empalme Cañuelas", "Emp Cañuelas")},
    {"codigo_origen": None, "nombre": "Cab. Km. 7,5", "sigla": "", "aliases": ("Km 7,5", "Km. 7,5", "Km 7.5", "Cabecera Km 7,5", "Cab. Km 7,5")},
    {"codigo_origen": None, "nombre": "Cruce Km. 4", "sigla": "", "aliases": ("Km 4", "Km. 4", "K4", "K.4")},
)

_RE_PARADA_SUFIJO = re.compile(r"\((?:pda|ap)\.?\)\s*$", re.IGNORECASE)
_RE_PUNTOS_ESPACIOS = re.compile(r"[.\s]+")


def normalizar_texto_estacion(texto: Optional[str], quitar_parentesis: bool = False) -> str:
    """Normaliza un nombre de estación preservando 'ñ' y quitando acentos de vocales."""
    raw = str(texto or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"\s+", " ", raw)
    if quitar_parentesis:
        raw = _RE_PARADA_SUFIJO.sub("", raw).strip()

    # Preservar la letra ñ/Ñ antes de descomponer tildes diacríticas
    raw = unicodedata.normalize("NFC", raw)
    marcado = raw.replace("Ñ", "\u0001").replace("ñ", "\u0002")
    nfd = unicodedata.normalize("NFD", marcado)
    sin_tildes = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    restaurado = sin_tildes.replace("\u0001", "ñ").replace("\u0002", "ñ")
    return re.sub(r"\s+", " ", restaurado.lower()).strip()


def _clave_compacta_estacion(texto: Optional[str], quitar_parentesis: bool = True) -> str:
    """Clave normalizada unificando puntos y espacios (p. ej. 'Dr.Levene' == 'Dr. Levene')."""
    base = normalizar_texto_estacion(texto, quitar_parentesis=quitar_parentesis)
    if not base:
        return ""
    return _RE_PUNTOS_ESPACIOS.sub(" ", base).strip()


def parsear_aliases_estacion(raw_aliases: Any) -> Tuple[str, ...]:
    """Convierte aliases en cadena o iterable a una tupla limpia sin duplicados."""
    if not raw_aliases:
        return ()
    if isinstance(raw_aliases, str):
        partes = [p.strip() for p in re.split(r"[,;|]", raw_aliases)]
    elif isinstance(raw_aliases, Iterable):
        partes = [str(p).strip() for p in raw_aliases]
    else:
        return ()

    vistos: set[str] = set()
    res: List[str] = []
    for p in partes:
        if p and p.lower() not in vistos:
            vistos.add(p.lower())
            res.append(p)
    return tuple(res)


class CatalogoEstacionesIndex:
    """Índice rápido en memoria para resolver nombres, alias y siglas de estaciones."""

    def __init__(self, catalogo: Optional[Sequence[Dict[str, Any]]] = None) -> None:
        source = catalogo if catalogo is not None else ESTACIONES_BASE
        self.estaciones: List[Dict[str, Any]] = []
        self._por_nombre_norm: Dict[str, List[int]] = {}
        self._por_sigla_upper: Dict[str, List[int]] = {}
        self._sugerencias_cache: Optional[List[str]] = None

        for raw in source:
            if int(raw.get("activo", 1)) == 0:
                continue
            nombre = str(raw.get("nombre") or "").strip()
            if not nombre:
                continue
            sigla = str(raw.get("sigla") or "").strip().upper()
            aliases = parsear_aliases_estacion(raw.get("aliases"))
            entry_idx = len(self.estaciones)

            names_norm: set[str] = set()
            for cand in (nombre, *aliases):
                n_raw = normalizar_texto_estacion(cand, quitar_parentesis=False)
                n_sin_par = normalizar_texto_estacion(cand, quitar_parentesis=True)
                n_comp = _clave_compacta_estacion(cand, quitar_parentesis=True)
                n_comp_raw = _clave_compacta_estacion(cand, quitar_parentesis=False)
                for n in (n_raw, n_sin_par, n_comp, n_comp_raw):
                    if n:
                        names_norm.add(n)
                        self._por_nombre_norm.setdefault(n, [])
                        if entry_idx not in self._por_nombre_norm[n]:
                            self._por_nombre_norm[n].append(entry_idx)

            if sigla:
                self._por_sigla_upper.setdefault(sigla, [])
                if entry_idx not in self._por_sigla_upper[sigla]:
                    self._por_sigla_upper[sigla].append(entry_idx)

            entry = {
                "id": raw.get("id"),
                "codigo_origen": raw.get("codigo_origen"),
                "nombre": nombre,
                "sigla": sigla,
                "aliases": aliases,
                "_names_norm": names_norm,
            }
            self.estaciones.append(entry)

    def resolver_estaciones_por_sigla(self, sigla: Optional[str]) -> List[Dict[str, Any]]:
        s_up = str(sigla or "").strip().upper()
        if not s_up:
            return []
        idxs = self._por_sigla_upper.get(s_up, [])
        return [self.estaciones[i] for i in idxs]

    def resolver_estaciones_candidatas(self, texto: Optional[str]) -> List[Dict[str, Any]]:
        """Resuelve estaciones por coincidencia de nombre/alias o por sigla exacta."""
        raw = str(texto or "").strip()
        if not raw:
            return []

        for key in (
            normalizar_texto_estacion(raw, quitar_parentesis=False),
            normalizar_texto_estacion(raw, quitar_parentesis=True),
            _clave_compacta_estacion(raw, quitar_parentesis=True),
        ):
            if key and key in self._por_nombre_norm:
                return [self.estaciones[i] for i in self._por_nombre_norm[key]]

        sig_up = raw.upper()
        if sig_up in self._por_sigla_upper:
            return [self.estaciones[i] for i in self._por_sigla_upper[sig_up]]

        return []

    def resolver_estacion(self, texto: Optional[str]) -> Optional[Dict[str, Any]]:
        cands = self.resolver_estaciones_candidatas(texto)
        return cands[0] if cands else None

    def obtener_sigla_estacion(self, texto: Optional[str]) -> str:
        raw = str(texto or "").strip()
        if not raw:
            return ""
        cands = self.resolver_estaciones_candidatas(raw)
        if not cands:
            return ""
        # Si ya es una sigla conocida válida, devolverla en mayúsculas
        if raw.upper() in self._por_sigla_upper:
            return raw.upper()
        return str(cands[0].get("sigla") or "").upper()

    def obtener_nombre_mostrar_estacion(self, texto: Optional[str], preferir_sigla: bool = False) -> str:
        raw = str(texto or "").strip()
        if not raw:
            return ""
        cands = self.resolver_estaciones_candidatas(raw)
        if not cands:
            return raw
        if preferir_sigla and cands[0].get("sigla"):
            return str(cands[0]["sigla"])
        return str(cands[0]["nombre"])

    def estaciones_equivalentes(self, est_a: Optional[str], est_b: Optional[str]) -> bool:
        """Indica si dos textos refieren a la misma estación (incluyendo abreviaturas/siglas)."""
        a = str(est_a or "").strip()
        b = str(est_b or "").strip()
        if not a or not b:
            return False

        ka = _clave_compacta_estacion(a, quitar_parentesis=True)
        kb = _clave_compacta_estacion(b, quitar_parentesis=True)
        if ka and ka == kb:
            return True

        cands_a = {id(s) for s in self.resolver_estaciones_candidatas(a)}
        cands_b = {id(s) for s in self.resolver_estaciones_candidatas(b)}
        return bool(cands_a and cands_b and (cands_a & cands_b))

    def coincide_filtro_estacion(self, valor_estacion: Optional[str], filtro: Optional[str]) -> bool:
        """Evalúa si el nombre de parada coincide con un filtro por texto, alias o sigla."""
        f_raw = str(filtro or "").strip()
        if not f_raw:
            return True
        v_raw = str(valor_estacion or "").strip()
        if not v_raw:
            return False

        v_norm = normalizar_texto_estacion(v_raw, quitar_parentesis=False)
        v_comp = _clave_compacta_estacion(v_raw, quitar_parentesis=True)
        f_norm = normalizar_texto_estacion(f_raw, quitar_parentesis=False)
        f_comp = _clave_compacta_estacion(f_raw, quitar_parentesis=True)

        f_upper = f_raw.upper()
        cands_val = self.resolver_estaciones_candidatas(v_raw)

        # Si el filtro coincide con una sigla exacta registrada en el catálogo (p. ej. "ZZ", "TY", "ALL", "D"),
        # priorizar coincidencia exacta por sigla/alias para evitar falsos positivos (p. ej. "zz" en "Spegazzini").
        if f_upper in self._por_sigla_upper:
            for st in cands_val:
                if st.get("sigla") and st["sigla"].upper() == f_upper:
                    return True
                if f_comp and f_comp in st["_names_norm"]:
                    return True
            if v_norm == f_norm or (f_comp and v_comp == f_comp):
                return True
            if not cands_val and f_comp and len(f_comp) >= 2:
                return bool(re.search(rf"(?:^|[\s.,/\-]){re.escape(f_comp)}(?:$|[\s.,/\-])", v_comp))
            return False

        if len(f_comp) >= 2:
            if f_norm and (f_norm in v_norm or (f_comp and f_comp in v_comp)):
                return True
        elif f_comp:
            if re.search(rf"(?:^|\s){re.escape(f_comp)}", v_comp):
                return True

        for st in cands_val:
            if f_comp and f_comp in st["_names_norm"]:
                return True
            if len(f_comp) >= 3:
                for alias_norm in st["_names_norm"]:
                    if f_comp in alias_norm:
                        return True
        return False

    def obtener_completado_estaciones(self) -> List[str]:
        """Devuelve lista ordenada de nombres, alias principales y siglas para autocompletado."""
        if self._sugerencias_cache is not None:
            return self._sugerencias_cache
        vistos_lower: set[str] = set()
        sugerencias: List[str] = []

        def _add(txt: str) -> None:
            t = str(txt or "").strip()
            if not t:
                return
            tl = t.lower()
            if tl not in vistos_lower:
                vistos_lower.add(tl)
                sugerencias.append(t)

        for st in self.estaciones:
            _add(st["nombre"])
            for al in st["aliases"]:
                # Excluir duplicados redundantes solo sin tilde en sugerencias visuales si ya está el nombre con tilde
                if normalizar_texto_estacion(al) != normalizar_texto_estacion(st["nombre"]):
                    _add(al)
            if st.get("sigla"):
                _add(st["sigla"])

        self._sugerencias_cache = sugerencias
        return self._sugerencias_cache


_DEFAULT_INDEX = CatalogoEstacionesIndex(ESTACIONES_BASE)


def resolver_estacion(
    texto: Optional[str],
    catalogo: Optional[Sequence[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    idx = _DEFAULT_INDEX if catalogo is None else CatalogoEstacionesIndex(catalogo)
    return idx.resolver_estacion(texto)


def resolver_estaciones_por_sigla(
    sigla: Optional[str],
    catalogo: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    idx = _DEFAULT_INDEX if catalogo is None else CatalogoEstacionesIndex(catalogo)
    return idx.resolver_estaciones_por_sigla(sigla)


def obtener_sigla_estacion(
    texto: Optional[str],
    catalogo: Optional[Sequence[Dict[str, Any]]] = None,
) -> str:
    idx = _DEFAULT_INDEX if catalogo is None else CatalogoEstacionesIndex(catalogo)
    return idx.obtener_sigla_estacion(texto)


def obtener_nombre_mostrar_estacion(
    texto: Optional[str],
    preferir_sigla: bool = False,
    catalogo: Optional[Sequence[Dict[str, Any]]] = None,
) -> str:
    idx = _DEFAULT_INDEX if catalogo is None else CatalogoEstacionesIndex(catalogo)
    return idx.obtener_nombre_mostrar_estacion(texto, preferir_sigla=preferir_sigla)


def estaciones_equivalentes(
    est_a: Optional[str],
    est_b: Optional[str],
    catalogo: Optional[Sequence[Dict[str, Any]]] = None,
) -> bool:
    idx = _DEFAULT_INDEX if catalogo is None else CatalogoEstacionesIndex(catalogo)
    return idx.estaciones_equivalentes(est_a, est_b)


def coincide_filtro_estacion(
    valor_estacion: Optional[str],
    filtro: Optional[str],
    catalogo: Optional[Sequence[Dict[str, Any]]] = None,
) -> bool:
    idx = _DEFAULT_INDEX if catalogo is None else CatalogoEstacionesIndex(catalogo)
    return idx.coincide_filtro_estacion(valor_estacion, filtro)


def obtener_completado_estaciones(
    catalogo: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[str]:
    idx = _DEFAULT_INDEX if catalogo is None else CatalogoEstacionesIndex(catalogo)
    return idx.obtener_completado_estaciones()
