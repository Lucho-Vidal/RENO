# RENO v2.0.0 — Registro de Novedades y Cambios de Turnos

Sistema de escritorio para la gestión operativa ferroviaria y el control de dotaciones, migrado a **PySide6-Essentials** bajo el estándar visual y arquitectónico **CRONO-PCT**. Combina almacenamiento local **SQLite Local-First** con sincronización híbrida hacia **Supabase (PostgreSQL 15+ REST)**, soporte para proxy corporativo y actualizaciones automáticas OTA.

---

## Características Principales

- **Panel de Control (Dashboard):** KPIs operativos en tiempo real, 4 gráficos interactivos dibujados con `QPainter` y exportación en imagen **PNG**, informe ejecutivo **PDF** (ReportLab) y desglose **Excel** (OpenPyXL).
- **Buscador de Turnos e Itinerarios:** Consulta de turnos y trenes con cruce en tiempo real frente a novedades activas, relevos de ciclo, permutas e importación de itinerarios y paradas intermedias (`tren_paradas`) desde Excel.
- **Novedades, Cambios de Turno y Ordenamientos Operativos:** Contenedor unificado con formularios validados, cálculo automático de fechas fin según duración de novedad, control de solapamientos, exportación PDF/Excel/Word y generación de borradores/envíos mediante Microsoft Outlook (`pywin32` MAPI).
- **Turnos Sin Cubrir y Personal de Ciclo:** Seguimiento diario de turnos descubiertos (`exportar_descubierto`), asignación drag-and-drop y por tramos sin solapamientos, calendario mensual de ciclo con cálculo automático de descansos según **CCT** (jornada de 6 hs: 18 hs entre jornadas / 42 hs post-franco; jornada de 7 hs: 17 hs / 41 hs) y exportación individual o masiva a Excel.
- **Administración y Control RBAC:**
  - Usuarios con hashing `bcrypt`, expiración de claves configurable, cambio obligatorio en primer ingreso y roles/permisos granulares.
  - Catálogos operativos CRUD (`Tipos de Novedad`, `Dotaciones`, `Personal de Estación`) y gestión completa de empleados (`BASE`) con soft delete.
  - Copias de seguridad automáticas diarias y manuales, papelera de recuperación y trazabilidad/auditoría completa.
  - Visor y exportación de logs rotativos de diagnóstico TXT (`reno.log`).
- **Sincronización Híbrida y Red Corporativa:** Cola transaccional `sync_outbox` hacia tablas `reno_*` en Supabase usando **Publishable Key** (`sb_publishable_*`), indicador visual de estado en barra inferior y soporte de proxy (`Directo`, `Manual` o `Windows SSO / NTLM / WPAD`).
- **Actualizaciones Automáticas (OTA):** Comprobación automática o manual contra `version.json` con soporte de proxy, descarga en segundo plano (incluyendo ensamblado multi-parte `.part1`, `.part2`, ...), verificación criptográfica **SHA-256** y ejecución desatendida de Inno Setup.
- **Interfaz y Experiencia de Uso:** Tema Claro y Modo Oscuro Moderno, iconografía oficial (`assets/reno_icon.ico` / `reno_icon.png`), modales maximizados configurables y ciclo de cierre seguro unificado (`BaseDialog` / `BaseMaximizedDialog`).

---

## Estructura del Proyecto

```text
├── main.py                              # Punto de entrada único de la aplicación
├── main.spec                            # Especificación PyInstaller (ejecutable dist/RENO.exe)
├── build.ps1                            # Script PowerShell de compilación
├── RENO.iss                             # Instalador Windows Inno Setup (LZMA2 Ultra64)
├── requirements.txt                     # Dependencias Python
├── supabase_reno_schema.sql             # Esquema SQL completo para Supabase (tablas reno_* y bucket updates)
├── .env.example                         # Plantilla de variables de entorno para Supabase y OTA
├── INSTRUCTIVO_PUBLICAR_ACTUALIZACIONES.md
├── assets/                              # Iconos oficiales (.ico y .png)
├── scripts/
│   └── publicar_actualizacion.py        # CLI para SHA-256, split >48 MB, version.json y subida a Supabase Storage
├── src/
│   ├── core/                            # Configuración, logger rotativo, proxy SSO/manual, QSettings y tema/iconos
│   ├── domain/                          # Modelos, enumeraciones RBAC y validadores puros (sin dependencias externas)
│   ├── data/                            # DatabaseManager (WAL/DELETE), migraciones SQLite, repositorios y sync outbox
│   ├── services/                        # Records, Auth, Excel, PDF (ReportLab), Word, Mail, Backup y Updater
│   └── ui/                              # MainWindow, hojas QSS claro/oscuro, modelos Qt, vistas, diálogos y workers
└── tests/                               # Suite automatizada pytest
```

---

## Instalación y Ejecución en Desarrollo

### Requisitos
- **Python 3.11+** (recomendado en Windows para soporte completo de `pywin32` / Outlook y empaquetado `.exe`).

### Instalación de dependencias
```bash
python -m pip install -r requirements.txt
```

Dependencias principales incluidas en `requirements.txt`:
- `PySide6-Essentials>=6.5.0`
- `openpyxl>=3.1.2`
- `bcrypt>=4.0.0`
- `pywin32>=306`
- `reportlab>=4.0.0`
- `python-docx>=1.0.0`
- `httpx>=0.25.0`

### Ejecutar la aplicación
```bash
python main.py
```

---

## Configuración y Persistencia

1. **Base de datos SQLite (`config.json`):**
   - La ruta activa de la base de datos se almacena en `config.json` bajo la clave `"database_path"` (reemplaza al archivo legado `path_base`, con migración automática si existía).
   - Por defecto crea/abre `RENO.sqlite` en el directorio de la aplicación. Si se configura una ruta compartida de red, puede alternarse entre modo journal `WAL` o `DELETE`.
   - En la primera ejecución, si existe un archivo Excel `.xlsx` asociado junto a la base (y la tabla `empleados` está vacía), importa automáticamente la plantilla `BASE`.
   - También puede modificarse desde **Administración > ⚙️ Configuración del Sistema**.

2. **Configuración del sistema (`reno_config.json`):**
   - Persiste el tema visual (`claro` / `oscuro`), URL y **Publishable Key** (`sb_publishable_*`) de Supabase, frecuencia de auto-sincronización, configuración de proxy corporativo (`direct`, `manual`, `windows_sso`) y la URL del feed de actualizaciones (`updater_url`).

3. **Variables de entorno (`.env`):**
   - Copie `.env.example` a `.env` en la raíz si necesita inicializar credenciales o publicar actualizaciones OTA por CLI:
     ```env
     SUPABASE_URL=https://tu-proyecto.supabase.co
     SUPABASE_SERVICE_KEY=sb_secret_tu_clave_secreta_aqui
     SUPABASE_ANON_KEY=sb_publishable_tu_clave_publica_aqui
     SUPABASE_STORAGE_BUCKET=updates
     RENO_UPDATES_URL=https://tu-proyecto.supabase.co/storage/v1/object/public/updates
     ```

4. **Base remota en Supabase (opcional):**
   - Ejecute [`supabase_reno_schema.sql`](supabase_reno_schema.sql) en el **SQL Editor** de su proyecto Supabase para crear las tablas con prefijo `reno_*`, políticas RLS y el bucket público `updates`.

---

## Pruebas Automatizadas

Ejecutar toda la suite desde la raíz del repositorio:

```bash
pytest tests/
```

En entornos Linux sin servidor gráfico (CI / headless):

```bash
QT_QPA_PLATFORM=offscreen pytest tests/
```

*(Nota: algunas pruebas de integración de itinerarios requieren el archivo local opcional `Indice_45.xlsx` o credenciales activas de Supabase.)*

---

## Empaquetado y Publicación de Actualizaciones (OTA)

1. **Generar ejecutable autónomo (`dist/RENO.exe`):**
   ```powershell
   python -m pip install pyinstaller
   python -m PyInstaller --clean --noconfirm main.spec
   ```
   O mediante el script incluido:
   ```powershell
   .\build.ps1
   ```

2. **Generar instalador Windows (`Instalador/RENO_Setup_v<VERSION>.exe`):**
   - Abrir [`RENO.iss`](RENO.iss) con **Inno Setup Compiler** y compilar (**Build / F9**). Utiliza compresión `lzma2/ultra64` e instalación sin requerir elevación de permisos de administrador.

3. **Publicar actualización OTA en Supabase Storage:**
   ```bash
   python scripts/publicar_actualizacion.py
   ```
   - Toma automáticamente `SUPABASE_URL` y `SUPABASE_SERVICE_KEY` desde `.env`, calcula el **SHA-256**, divide automáticamente el instalador en fragmentos de ~25 MB si supera los 48 MB, genera `Instalador/version.json` y sube todo al bucket `updates`.
   - Para ver todas las opciones avanzadas y el flujo detallado, consultar [`INSTRUCTIVO_PUBLICAR_ACTUALIZACIONES.md`](INSTRUCTIVO_PUBLICAR_ACTUALIZACIONES.md).

---

## Licencia

Este proyecto se distribuye bajo la licencia **Creative Commons Atribución 4.0 Internacional (CC BY 4.0)** — Autor original: **Vidal Luciano**. Consulte [`LICENSE.txt`](LICENSE.txt) para más detalles.
