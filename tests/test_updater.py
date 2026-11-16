"""Pruebas unitarias completas para el servicio de actualizaciones automáticas (Auto-Updater OTA)."""

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from src.services.updater_service import (
    InfoActualizacion,
    calcular_sha256,
    comparar_versiones,
    dividir_archivo,
    es_actualizacion_obligatoria,
    parsear_version,
    resolver_url_manifiesto,
)


class TestUpdaterService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_parsear_version(self):
        self.assertEqual(parsear_version("2.0.0"), (2, 0, 0))
        self.assertEqual(parsear_version("v2.1.3"), (2, 1, 3))
        self.assertEqual(parsear_version("V1.7"), (1, 7, 0))
        self.assertEqual(parsear_version("3"), (3, 0, 0))
        self.assertEqual(parsear_version(""), (0, 0, 0))
        self.assertEqual(parsear_version("2.0.0-beta.1"), (2, 0, 0))

    def test_comparar_versiones_major(self):
        es_mayor, es_menor, es_parche, es_sup = comparar_versiones("2.0.0", "3.0.0")
        self.assertTrue(es_sup)
        self.assertTrue(es_mayor)
        self.assertFalse(es_menor)
        self.assertFalse(es_parche)

    def test_comparar_versiones_minor(self):
        es_mayor, es_menor, es_parche, es_sup = comparar_versiones("2.0.0", "2.1.0")
        self.assertTrue(es_sup)
        self.assertFalse(es_mayor)
        self.assertTrue(es_menor)
        self.assertFalse(es_parche)

    def test_comparar_versiones_patch(self):
        es_mayor, es_menor, es_parche, es_sup = comparar_versiones("2.0.0", "2.0.1")
        self.assertTrue(es_sup)
        self.assertFalse(es_mayor)
        self.assertFalse(es_menor)
        self.assertTrue(es_parche)

    def test_comparar_versiones_iguales_y_menores(self):
        # Misma versión
        self.assertEqual(comparar_versiones("2.0.0", "2.0.0"), (False, False, False, False))
        # Versión remota inferior
        self.assertEqual(comparar_versiones("2.1.0", "2.0.9"), (False, False, False, False))
        self.assertEqual(comparar_versiones("3.0.0", "2.9.9"), (False, False, False, False))

    def test_criterio_obligatoriedad_semver(self):
        # Versiones Mayores (X) y Menores (Y) son OBLIGATORIAS
        self.assertTrue(es_actualizacion_obligatoria("2.0.0", "3.0.0"))
        self.assertTrue(es_actualizacion_obligatoria("2.0.0", "2.1.0"))

        # Versión Parche (Z) es OPCIONAL
        self.assertFalse(es_actualizacion_obligatoria("2.0.0", "2.0.1"))
        self.assertFalse(es_actualizacion_obligatoria("2.0.1", "2.0.2"))

        # Si el flag de manifiesto es True, cualquier versión es OBLIGATORIA
        self.assertTrue(es_actualizacion_obligatoria("2.0.0", "2.0.1", flag_manifiesto=True))

        # Si local es menor a min_version_permitida, es OBLIGATORIA
        self.assertTrue(
            es_actualizacion_obligatoria(
                v_local="1.9.0",
                v_remota="2.0.1",
                min_version_permitida="2.0.0",
            )
        )
        self.assertFalse(
            es_actualizacion_obligatoria(
                v_local="2.0.0",
                v_remota="2.0.1",
                min_version_permitida="2.0.0",
            )
        )

    def test_info_actualizacion_dataclass(self):
        data = {
            "version": "2.0.1",
            "fecha": "12/11/2026",
            "titulo": "Actualización de Rendimiento",
            "novedades": ["[Operación] Mejora en carga de novedades", "[Seguridad] Corrección en auth"],
            "instalador_nombre": "RENO_Setup_v2.0.1.exe",
            "instalador_url": "https://supabase.co/storage/v1/object/public/updates/RENO_Setup_v2.0.1.exe",
            "sha256": "abcdef1234567890",
            "tamano_bytes": 45000000,
            "obligatoria": False,
            "min_version_permitida": "2.0.0",
            "partes": [
                "https://supabase.co/storage/v1/object/public/updates/RENO_Setup_v2.0.1.exe.part1",
                "https://supabase.co/storage/v1/object/public/updates/RENO_Setup_v2.0.1.exe.part2",
            ],
        }
        info = InfoActualizacion.from_dict(data)
        self.assertEqual(info.version, "2.0.1")
        self.assertEqual(info.fecha, "12/11/2026")
        self.assertEqual(len(info.novedades), 2)
        self.assertEqual(info.tamano_bytes, 45000000)
        self.assertFalse(info.obligatoria)
        self.assertEqual(len(info.partes), 2)

        dict_export = info.to_dict()
        self.assertEqual(dict_export["version"], "2.0.1")
        self.assertEqual(len(dict_export["partes"]), 2)

    def test_calcular_sha256(self):
        archivo = Path(self.temp_dir) / "prueba.bin"
        contenido = b"RENO Actualizacion Test Content 12345"
        with open(archivo, "wb") as f:
            f.write(contenido)

        esperado = hashlib.sha256(contenido).hexdigest().lower()
        calculado = calcular_sha256(archivo)
        self.assertEqual(calculado, esperado)

    def test_dividir_archivo_en_partes_y_reconstruir(self):
        # Crear un archivo simulado de 6 MB y dividirlo en partes de 2 MB
        archivo_original = Path(self.temp_dir) / "instalador_grande.bin"
        tamano_total = 6 * 1024 * 1024  # 6 MB
        contenido = os.urandom(tamano_total)

        with open(archivo_original, "wb") as f:
            f.write(contenido)

        hash_original = calcular_sha256(archivo_original)

        carpeta_partes = Path(self.temp_dir) / "partes"
        partes = dividir_archivo(archivo_original, tamano_max_mb=2, carpeta_salida=carpeta_partes)

        # Deben ser 3 partes de 2 MB cada una
        self.assertEqual(len(partes), 3)
        self.assertTrue(partes[0].name.endswith(".part1"))
        self.assertTrue(partes[1].name.endswith(".part2"))
        self.assertTrue(partes[2].name.endswith(".part3"))

        # Reconstruir concatenando las partes (tal como hace DescargarActualizacionWorker)
        archivo_reconstruido = Path(self.temp_dir) / "instalador_reconstruido.bin"
        with open(archivo_reconstruido, "wb") as f_out:
            for p in partes:
                with open(p, "rb") as f_in:
                    f_out.write(f_in.read())

        self.assertEqual(archivo_reconstruido.stat().st_size, tamano_total)
        hash_reconstruido = calcular_sha256(archivo_reconstruido)
        self.assertEqual(hash_reconstruido, hash_original)

    def test_dividir_archivo_menor_al_limite_no_se_divide(self):
        archivo_chico = Path(self.temp_dir) / "archivo_chico.bin"
        with open(archivo_chico, "wb") as f:
            f.write(b"Contenido menor a 25 MB")

        partes = dividir_archivo(archivo_chico, tamano_max_mb=25)
        self.assertEqual(len(partes), 1)
        self.assertEqual(partes[0], archivo_chico)

    def test_resolver_url_manifiesto(self):
        config = {
            "updater_url": "https://mi-servidor.com/version.json",
            "supabase_url": "https://xyz.supabase.co",
        }
        urls = resolver_url_manifiesto(config)
        self.assertEqual(urls[0], "https://mi-servidor.com/version.json")
        self.assertIn("https://mi-servidor.com/reno_version.json", urls)
        self.assertIn("https://xyz.supabase.co/storage/v1/object/public/updates/version.json", urls)
        self.assertIn("https://xyz.supabase.co/storage/v1/object/public/updates/reno_version.json", urls)
        self.assertFalse(any("crono-pct-web" in u for u in urls))

    def test_resolver_url_manifiesto_normaliza_variantes_supabase_y_ignora_placeholder(self):
        # 1. URL raíz del proyecto Supabase en updater_url
        urls_root = resolver_url_manifiesto({"updater_url": "https://abc123.supabase.co"})
        self.assertEqual(
            urls_root,
            [
                "https://abc123.supabase.co/storage/v1/object/public/updates/version.json",
                "https://abc123.supabase.co/storage/v1/object/public/updates/reno_version.json",
            ],
        )

        # 2. URL del Dashboard de Supabase (incluyendo formato /storage/files/buckets/<bucket>)
        urls_dash = resolver_url_manifiesto(
            {"updater_url": "https://supabase.com/dashboard/project/projxyz/storage/files/buckets/ota_bucket"}
        )
        self.assertEqual(
            urls_dash,
            [
                "https://projxyz.supabase.co/storage/v1/object/public/ota_bucket/version.json",
                "https://projxyz.supabase.co/storage/v1/object/public/ota_bucket/reno_version.json",
            ],
        )

        # 3. URL de objeto sin /public/ genera también el endpoint /public/
        urls_obj = resolver_url_manifiesto(
            {"updater_url": "https://projxyz.supabase.co/storage/v1/object/updates/version.json"}
        )
        self.assertIn("https://projxyz.supabase.co/storage/v1/object/updates/version.json", urls_obj)
        self.assertIn("https://projxyz.supabase.co/storage/v1/object/public/updates/version.json", urls_obj)
        self.assertIn("https://projxyz.supabase.co/storage/v1/object/public/updates/reno_version.json", urls_obj)

        # 4. Carpeta de bucket no agrega la carpeta desnuda sin version.json
        urls_dir = resolver_url_manifiesto(
            {"updater_url": "https://projxyz.supabase.co/storage/v1/object/public/updates"}
        )
        self.assertEqual(
            urls_dir,
            [
                "https://projxyz.supabase.co/storage/v1/object/public/updates/version.json",
                "https://projxyz.supabase.co/storage/v1/object/public/updates/reno_version.json",
            ],
        )

        # 5. URL de ejemplo 'tu-proyecto.supabase.co' se ignora por completo
        self.assertEqual(resolver_url_manifiesto({"supabase_url": "https://tu-proyecto.supabase.co"}), [])


class TestPublicadorYDotenv(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_construir_headers_storage_sb_secret_sin_bearer(self):
        import scripts.publicar_actualizacion as pub

        hdrs = pub.construir_headers_storage('  "sb_secret_abc1234567890xyz"  ', "application/octet-stream")
        self.assertEqual(hdrs["apikey"], "sb_secret_abc1234567890xyz")
        self.assertEqual(hdrs["x-upsert"], "true")
        self.assertEqual(hdrs["Content-Type"], "application/octet-stream")
        self.assertNotIn("Authorization", hdrs)

    def test_construir_headers_storage_jwt_con_bearer(self):
        import scripts.publicar_actualizacion as pub

        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
        hdrs = pub.construir_headers_storage(jwt, "application/json")
        self.assertEqual(hdrs["apikey"], jwt)
        self.assertEqual(hdrs["Authorization"], f"Bearer {jwt}")

    def test_construir_headers_supabase_proxy(self):
        from src.core.proxy import construir_headers_supabase

        h_pub = construir_headers_supabase("sb_publishable_12345")
        self.assertEqual(h_pub["apikey"], "sb_publishable_12345")
        self.assertNotIn("Authorization", h_pub)

        h_sec = construir_headers_supabase("sb_secret_12345")
        self.assertEqual(h_sec["apikey"], "sb_secret_12345")
        self.assertNotIn("Authorization", h_sec)

        h_jwt = construir_headers_supabase("eyJabc.def.ghi")
        self.assertEqual(h_jwt["Authorization"], "Bearer eyJabc.def.ghi")

    def test_es_credencial_real(self):
        import scripts.publicar_actualizacion as pub

        self.assertFalse(pub._es_credencial_real("https://tu-proyecto.supabase.co", "sb_secret_123456789012345"))
        self.assertFalse(pub._es_credencial_real("https://xyiwvzfieualsfaifwfq.supabase.co", "sb_secret_pega_tu_clave_secreta_aqui"))
        self.assertFalse(pub._es_credencial_real("https://xyiwvzfieualsfaifwfq.supabase.co", "sb_secret_tu_clave_secreta_aqui"))
        self.assertFalse(pub._es_credencial_real("https://xyiwvzfieualsfaifwfq.supabase.co", "sb_secret_..."))
        self.assertTrue(pub._es_credencial_real("https://xyiwvzfieualsfaifwfq.supabase.co", "sb_secret_real_token_1234567890"))
        self.assertTrue(pub._es_credencial_real("https://xyiwvzfieualsfaifwfq.supabase.co", "sb_secret_randomaqui1234567890"))

    def test_cargar_variables_entorno_prioriza_dotenv_y_bom(self):
        from unittest.mock import patch
        import src.core.config as cfg_mod

        env_file = self.temp_dir / ".env"
        # Escribir con BOM UTF-8 y comillas
        env_file.write_bytes(
            "\ufeffSUPABASE_URL=\"https://miapp.supabase.co\"\nSUPABASE_SERVICE_KEY='sb_secret_desde_dotenv_12345'\n".encode("utf-8")
        )

        old_url = os.environ.get("SUPABASE_URL")
        old_key = os.environ.get("SUPABASE_SERVICE_KEY")
        try:
            os.environ["SUPABASE_URL"] = "https://viejo.supabase.co"
            os.environ["SUPABASE_SERVICE_KEY"] = "sb_secret_vieja_sesion"
            with patch.object(cfg_mod, "APP_DATA_DIR", self.temp_dir), patch.object(cfg_mod, "BASE_DIR", self.temp_dir):
                cargadas = cfg_mod.cargar_variables_entorno()
            self.assertEqual(cargadas["SUPABASE_URL"], "https://miapp.supabase.co")
            self.assertEqual(cargadas["SUPABASE_SERVICE_KEY"], "sb_secret_desde_dotenv_12345")
            self.assertEqual(os.environ["SUPABASE_URL"], "https://miapp.supabase.co")
            self.assertEqual(os.environ["SUPABASE_SERVICE_KEY"], "sb_secret_desde_dotenv_12345")
        finally:
            if old_url is None:
                os.environ.pop("SUPABASE_URL", None)
            else:
                os.environ["SUPABASE_URL"] = old_url
            if old_key is None:
                os.environ.pop("SUPABASE_SERVICE_KEY", None)
            else:
                os.environ["SUPABASE_SERVICE_KEY"] = old_key

    def test_main_dispara_subida_automatica_con_args_cli_o_dotenv(self):
        import sys
        from unittest.mock import patch
        import scripts.publicar_actualizacion as pub
        import src.core.config as cfg_mod

        exe_falso = self.temp_dir / "RENO_Setup_v2.0.0.exe"
        exe_falso.write_bytes(b"MZ_DUMMY_INSTALLER" * 100)
        salida_dir = self.temp_dir / "out"
        salida_dir.mkdir()

        # .env de prueba en carpeta temporal aislada
        env_temp = self.temp_dir / ".env"
        env_temp.write_text(
            "SUPABASE_URL=https://xyiwvzfieualsfaifwfq.supabase.co\n"
            "SUPABASE_SERVICE_KEY=sb_secret_real_env_key_1234567890\n",
            encoding="utf-8",
        )

        old_env = {k: os.environ.get(k) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY")}
        try:
            # Caso 1: usuario pasa --supabase-url y --service-key SIN --subir
            subidos_cli = []
            argv_cli = [
                "publicar_actualizacion.py",
                "--instalador", str(exe_falso),
                "--salida-dir", str(salida_dir),
                "--supabase-url", "https://xyiwvzfieualsfaifwfq.supabase.co",
                "--service-key", "sb_secret_real_cli_key_1234567890",
            ]
            with patch.object(sys, "argv", argv_cli), \
                 patch.object(pub, "ROOT_DIR", self.temp_dir), \
                 patch.object(cfg_mod, "APP_DATA_DIR", self.temp_dir), \
                 patch.object(cfg_mod, "BASE_DIR", self.temp_dir), \
                 patch.object(pub, "subir_archivo_a_supabase", side_effect=lambda u, b, k, a: subidos_cli.append((u, b, k, a.name)) or True):
                pub.main()

            nombres_cli = [x[3] for x in subidos_cli]
            self.assertIn("version.json", nombres_cli)
            self.assertIn("reno_version.json", nombres_cli)
            self.assertIn("RENO_Setup_v2.0.0.exe", nombres_cli)
            self.assertEqual(subidos_cli[0][2], "sb_secret_real_cli_key_1234567890")

            # Caso 2: credenciales reales desde archivo .env sin flags CLI -> también sube automáticamente
            subidos_env = []
            argv_env = [
                "publicar_actualizacion.py",
                "--instalador", str(exe_falso),
                "--salida-dir", str(salida_dir),
            ]
            # Simular variable previa vieja en sesión que DEBE ser sobreescrita por .env
            os.environ["SUPABASE_SERVICE_KEY"] = "sb_secret_vieja_sesion_override"
            with patch.object(sys, "argv", argv_env), \
                 patch.object(pub, "ROOT_DIR", self.temp_dir), \
                 patch.object(cfg_mod, "APP_DATA_DIR", self.temp_dir), \
                 patch.object(cfg_mod, "BASE_DIR", self.temp_dir), \
                 patch.object(pub, "subir_archivo_a_supabase", side_effect=lambda u, b, k, a: subidos_env.append((u, b, k, a.name)) or True):
                pub.main()

            self.assertEqual(len(subidos_env), 3)
            self.assertEqual(subidos_env[0][2], "sb_secret_real_env_key_1234567890")

            # Caso 3: --no-subir impide subida aunque existan credenciales reales en .env
            subidos_no = []
            argv_no = argv_env + ["--no-subir"]
            with patch.object(sys, "argv", argv_no), \
                 patch.object(pub, "ROOT_DIR", self.temp_dir), \
                 patch.object(cfg_mod, "APP_DATA_DIR", self.temp_dir), \
                 patch.object(cfg_mod, "BASE_DIR", self.temp_dir), \
                 patch.object(pub, "subir_archivo_a_supabase", side_effect=lambda u, b, k, a: subidos_no.append(a.name) or True):
                pub.main()

            self.assertEqual(subidos_no, [])
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_subir_archivo_httpx_sb_secret_omite_bearer_y_crea_bucket(self):
        from unittest.mock import MagicMock, patch
        import scripts.publicar_actualizacion as pub

        archivo = self.temp_dir / "version.json"
        archivo.write_text('{"version": "2.0.0"}', encoding="utf-8")

        resp_404 = MagicMock(status_code=404, text='{"statusCode":"404","error":"Bucket not found"}')
        resp_bucket = MagicMock(status_code=200, text='{"name":"updates"}')
        resp_200 = MagicMock(status_code=200, text='{"Key":"updates/version.json"}')

        post_calls = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, headers=None, content=None, json=None):
                post_calls.append((url, dict(headers or {})))
                if url.endswith("/storage/v1/bucket"):
                    return resp_bucket
                if len([c for c in post_calls if "/object/" in c[0]]) == 1:
                    return resp_404
                return resp_200

        with patch("httpx.Client", FakeClient):
            ok = pub.subir_archivo_a_supabase(
                "https://xyiwvzfieualsfaifwfq.supabase.co",
                "updates",
                "sb_secret_real_token_1234567890",
                archivo,
            )

        self.assertTrue(ok)
        self.assertEqual(len(post_calls), 3)
        self.assertTrue(post_calls[1][0].endswith("/storage/v1/bucket"))
        for _, hdrs in post_calls:
            self.assertEqual(hdrs["apikey"], "sb_secret_real_token_1234567890")
            self.assertNotIn("Authorization", hdrs)

    def test_subir_archivo_usa_proxy_manual_y_fallback_windows_sso(self):
        from unittest.mock import MagicMock, patch
        import scripts.publicar_actualizacion as pub
        from src.core.proxy import MODO_MANUAL, MODO_WINDOWS_SSO

        archivo = self.temp_dir / "version.json"
        archivo.write_text('{"version": "2.0.1"}', encoding="utf-8")

        # 1. Proxy Manual pasa URL de proxy con credenciales al cliente httpx
        init_kwargs = []
        resp_200 = MagicMock(status_code=200, text="OK")

        class FakeProxyClient:
            def __init__(self, *args, **kwargs):
                init_kwargs.append(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, headers=None, content=None, json=None):
                return resp_200

        cfg_manual = {
            "proxy_mode": MODO_MANUAL,
            "proxy_host": "proxy.empresa.local",
            "proxy_port": 3128,
            "proxy_user": "usr",
            "proxy_pass": "pwd",
        }
        with patch("httpx.Client", FakeProxyClient):
            ok_man = pub.subir_archivo_a_supabase(
                "https://xyiwvzfieualsfaifwfq.supabase.co",
                "updates",
                "sb_secret_real_token_1234567890",
                archivo,
                proxy_config=cfg_manual,
            )
        self.assertTrue(ok_man)
        self.assertTrue(
            init_kwargs[0].get("proxy") == "http://usr:pwd@proxy.empresa.local:3128"
            or init_kwargs[0].get("proxies") == "http://usr:pwd@proxy.empresa.local:3128"
        )

        # 2. Modo Windows SSO delega a ejecutar_request_supabase (con SSPI/WinHTTP)
        cfg_sso = {"proxy_mode": MODO_WINDOWS_SSO, "proxy_sso_server": "10.0.0.5:8080"}
        with patch.object(pub, "ejecutar_request_supabase", return_value=(200, b"{}", {})) as m_req:
            ok_sso = pub.subir_archivo_a_supabase(
                "https://xyiwvzfieualsfaifwfq.supabase.co",
                "updates",
                "sb_secret_real_token_1234567890",
                archivo,
                proxy_config=cfg_sso,
            )
        self.assertTrue(ok_sso)
        self.assertEqual(m_req.call_count, 1)
        self.assertEqual(m_req.call_args.kwargs["config"], cfg_sso)


class TestUpdaterConProxy(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_resolver_url_manifiesto_prefijo_y_bucket(self):
        urls = resolver_url_manifiesto(
            {
                "updater_url": "https://cdn.miempresa.com/reno",
                "supabase_url": "https://xyz.supabase.co",
                "supabase_storage_bucket": "reno_updates",
            }
        )
        self.assertIn("https://cdn.miempresa.com/reno/version.json", urls)
        self.assertIn("https://xyz.supabase.co/storage/v1/object/public/reno_updates/version.json", urls)

    def test_verificar_actualizacion_worker_usa_proxy_configurado_y_maneja_407(self):
        from unittest.mock import patch
        from src.core.proxy import MODO_MANUAL, MODO_WINDOWS_SSO, ProxyAuthError
        from src.services.updater_service import VerificarActualizacionWorker

        cfg_manual = {
            "proxy_mode": MODO_MANUAL,
            "proxy_host": "proxy.empresa.ar",
            "proxy_port": 8080,
            "proxy_user": "user1",
            "proxy_pass": "pass1",
            "supabase_url": "https://xyz.supabase.co",
        }
        manifiesto = json.dumps(
            {
                "version": "2.1.0",
                "fecha": "13/11/2026",
                "titulo": "Release 2.1.0",
                "novedades": ["Mejora proxy"],
                "instalador_nombre": "RENO_Setup_v2.1.0.exe",
                "instalador_url": "https://xyz.supabase.co/storage/v1/object/public/updates/RENO_Setup_v2.1.0.exe",
            }
        ).encode("utf-8")

        worker = VerificarActualizacionWorker(version_local="2.0.0", config=cfg_manual)
        disponible = []
        errores = []
        worker.actualizacion_disponible.connect(lambda info, oblig: disponible.append((info, oblig)))
        worker.error_verificacion.connect(lambda err: errores.append(err))

        calls = []

        def _fake_req(config, url, method="GET", headers=None, timeout_sec=12):
            calls.append((config, url, headers))
            return 200, manifiesto, {}

        with patch("src.services.updater_service.ejecutar_request_supabase", side_effect=_fake_req):
            worker.run()

        self.assertEqual(errores, [])
        self.assertEqual(len(disponible), 1)
        self.assertEqual(disponible[0][0].version, "2.1.0")
        self.assertTrue(disponible[0][1])
        self.assertEqual(calls[0][0], cfg_manual)
        self.assertEqual(calls[0][2].get("Cache-Control"), "no-cache")

        # Si el proxy rechaza con HTTP 407 en modo Windows SSO, emite error explícito
        cfg_sso = {"proxy_mode": MODO_WINDOWS_SSO, "supabase_url": "https://xyz.supabase.co"}
        worker_407 = VerificarActualizacionWorker(version_local="2.0.0", config=cfg_sso)
        errores_407 = []
        worker_407.error_verificacion.connect(lambda err: errores_407.append(err))
        with patch(
            "src.services.updater_service.ejecutar_request_supabase",
            side_effect=ProxyAuthError("HTTP 407: Proxy rechazó credenciales SSO"),
        ):
            worker_407.run()

        self.assertEqual(len(errores_407), 1)
        self.assertIn("407", errores_407[0])

    def test_descargar_actualizacion_worker_multiparte_usa_proxy_y_sha256(self):
        from unittest.mock import patch
        from src.core.proxy import MODO_WINDOWS_SSO
        from src.services.updater_service import DescargarActualizacionWorker

        part1 = b"PARTE_UNO_BINARIA_" * 100
        part2 = b"PARTE_DOS_BINARIA_" * 100
        completo = part1 + part2
        sha_esperado = hashlib.sha256(completo).hexdigest()

        info = InfoActualizacion(
            version="2.1.0",
            fecha="13/11/2026",
            titulo="Release",
            instalador_nombre="RENO_Setup_v2.1.0_test.exe",
            partes=[
                "https://xyz.supabase.co/storage/v1/object/public/updates/RENO.part1",
                "https://xyz.supabase.co/storage/v1/object/public/updates/RENO.part2",
            ],
            sha256=sha_esperado,
            tamano_bytes=len(completo),
        )
        cfg_sso = {"proxy_mode": MODO_WINDOWS_SSO, "proxy_sso_server": "proxy.corp:8080"}
        worker = DescargarActualizacionWorker(info=info, config=cfg_sso)

        completados = []
        errores = []
        progresos = []
        worker.descarga_completada.connect(lambda ruta: completados.append(ruta))
        worker.error_descarga.connect(lambda err: errores.append(err))
        worker.progreso.connect(lambda act, tot, vel, msg: progresos.append((act, tot, msg)))

        dl_calls = []

        def _fake_download(config, url, archivo_out, headers=None, on_chunk=None, should_cancel=None, timeout_sec=60):
            dl_calls.append((config, url))
            datos = part1 if url.endswith(".part1") else part2
            archivo_out.write(datos)
            if on_chunk:
                on_chunk(len(datos), len(datos))
            return len(datos)

        with patch("src.services.updater_service.descargar_archivo_con_proxy", side_effect=_fake_download), \
             patch("src.services.updater_service.tempfile.gettempdir", return_value=str(self.temp_dir)):
            worker.run()

        self.assertEqual(errores, [])
        self.assertEqual(len(completados), 1)
        self.assertEqual(len(dl_calls), 2)
        self.assertEqual(dl_calls[0][0], cfg_sso)
        self.assertEqual(dl_calls[1][0], cfg_sso)
        self.assertEqual(Path(completados[0]).read_bytes(), completo)
        self.assertGreaterEqual(len(progresos), 1)

    def test_actualizacion_dialog_reenvia_config_al_worker(self):
        from unittest.mock import MagicMock, patch
        from PySide6.QtWidgets import QApplication
        from src.core.proxy import MODO_MANUAL
        from src.ui.views.dialogs.actualizacion_dialog import ActualizacionDialog

        app = QApplication.instance() or QApplication([])
        info = InfoActualizacion(
            version="2.0.5",
            fecha="13/11/2026",
            titulo="Patch",
            instalador_url="https://xyz.supabase.co/storage/v1/object/public/updates/RENO.exe",
        )
        cfg_proxy = {"proxy_mode": MODO_MANUAL, "proxy_host": "10.10.0.1", "proxy_port": 3128}
        dlg = ActualizacionDialog(info, es_obligatoria=False, config=cfg_proxy)
        fake_worker = MagicMock()
        fake_worker.isRunning.return_value = False

        with patch(
            "src.ui.views.dialogs.actualizacion_dialog.DescargarActualizacionWorker",
            return_value=fake_worker,
        ) as mock_worker_cls:
            dlg._iniciar_descarga()

        mock_worker_cls.assert_called_once()
        self.assertEqual(mock_worker_cls.call_args.kwargs.get("config"), cfg_proxy)
        fake_worker.start.assert_called_once()
        dlg.close()


class TestUpdaterOTAConfigYRegresiones(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_verificar_actualizacion_worker_sin_config_ni_fallback_ajeno(self):
        from unittest.mock import patch
        from src.services.updater_service import VerificarActualizacionWorker

        worker_vacio = VerificarActualizacionWorker(
            version_local="2.0.0",
            config={"updater_url": "", "supabase_url": ""},
        )
        errores_vacio = []
        al_dia_vacio = []
        worker_vacio.error_verificacion.connect(errores_vacio.append)
        worker_vacio.sistema_actualizado.connect(lambda: al_dia_vacio.append(True))

        with patch("src.services.updater_service.ejecutar_request_supabase") as mock_req:
            worker_vacio.run()

        mock_req.assert_not_called()
        self.assertEqual(al_dia_vacio, [])
        self.assertEqual(len(errores_vacio), 1)
        self.assertIn("No hay URL", errores_vacio[0])

    def test_verificar_actualizacion_worker_error_404_loguea_warning_y_no_usa_otro_proyecto(self):
        from unittest.mock import patch
        from src.services.updater_service import VerificarActualizacionWorker

        cfg = {
            "updater_url": "https://xyiwvzfieualsfaifwfq.supabase.co/storage/v1/object/public/updates/version.json",
            "supabase_url": "https://xyiwvzfieualsfaifwfq.supabase.co",
        }
        worker = VerificarActualizacionWorker(version_local="2.0.0", config=cfg)
        errores = []
        al_dia = []
        urls_consultadas = []
        worker.error_verificacion.connect(errores.append)
        worker.sistema_actualizado.connect(lambda: al_dia.append(True))

        def _fake_404(config, url, method="GET", headers=None, timeout_sec=12):
            urls_consultadas.append(url)
            return 404, b'{"statusCode":"404","error":"Bucket not found"}', {}

        with patch("src.services.updater_service.ejecutar_request_supabase", side_effect=_fake_404), \
             self.assertLogs("reno.UpdaterService", level="WARNING") as cm_logs:
            worker.run()

        self.assertEqual(al_dia, [])
        self.assertEqual(len(errores), 1)
        self.assertIn("HTTP 404", errores[0])
        self.assertIn("xyiwvzfieualsfaifwfq.supabase.co", errores[0])
        self.assertFalse(any("crono-pct-web" in u for u in urls_consultadas))
        self.assertTrue(any("404" in msg for msg in cm_logs.output))

    def test_verificar_actualizacion_worker_rechaza_json_sin_version(self):
        from unittest.mock import patch
        from src.services.updater_service import VerificarActualizacionWorker

        cfg = {
            "updater_url": "https://xyiwvzfieualsfaifwfq.supabase.co/storage/v1/object/public/updates/version.json",
        }
        worker = VerificarActualizacionWorker(version_local="2.0.0", config=cfg)
        errores = []
        al_dia = []
        worker.error_verificacion.connect(errores.append)
        worker.sistema_actualizado.connect(lambda: al_dia.append(True))

        with patch(
            "src.services.updater_service.ejecutar_request_supabase",
            return_value=(200, b'{"status": "ok"}', {}),
        ):
            worker.run()

        self.assertEqual(al_dia, [])
        self.assertEqual(len(errores), 1)
        self.assertIn("version", errores[0])

    def test_publicador_sin_urls_no_inserta_crono_pct_web(self):
        import sys
        from unittest.mock import patch
        import scripts.publicar_actualizacion as pub
        import src.core.config as cfg_mod

        salida_dir = self.temp_dir / "out"
        salida_dir.mkdir()
        argv = [
            "publicar_actualizacion.py",
            "--salida-dir",
            str(salida_dir),
            "--no-subir",
        ]
        old_env = {k: os.environ.get(k) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "RENO_UPDATES_URL")}
        try:
            for k in old_env:
                os.environ.pop(k, None)
            with patch.object(sys, "argv", argv), \
                 patch.object(pub, "ROOT_DIR", self.temp_dir), \
                 patch.object(pub, "ENV_DOTENV", {}), \
                 patch.object(cfg_mod, "APP_DATA_DIR", self.temp_dir), \
                 patch.object(cfg_mod, "BASE_DIR", self.temp_dir), \
                 patch.object(cfg_mod, "_DEFAULT_CONFIG_JSON_PATH", self.temp_dir / "reno_config.json"), \
                 patch.object(cfg_mod, "CONFIG_JSON_PATH", self.temp_dir / "reno_config.json"):
                pub.main()

            manifiesto = json.loads((salida_dir / "version.json").read_text(encoding="utf-8"))
            self.assertNotIn("crono-pct-web", json.dumps(manifiesto))
            self.assertEqual(manifiesto["instalador_url"], "")
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_config_app_fallback_en_perfil_usuario_si_carpeta_no_escribible(self):
        from unittest.mock import patch
        import src.core.config as cfg_mod

        default_cfg = self.temp_dir / "program_files" / "reno_config.json"
        default_cfg.parent.mkdir(parents=True, exist_ok=True)
        local_appdata = self.temp_dir / "local_appdata"
        real_open = open

        def _fake_open(file, mode="r", *args, **kwargs):
            if Path(file) == default_cfg and "w" in mode:
                raise PermissionError("Solo lectura")
            return real_open(file, mode, *args, **kwargs)

        old_env = {k: os.environ.get(k) for k in ("LOCALAPPDATA", "RENO_UPDATES_URL", "SUPABASE_URL")}
        try:
            os.environ["LOCALAPPDATA"] = str(local_appdata)
            os.environ.pop("RENO_UPDATES_URL", None)
            os.environ.pop("SUPABASE_URL", None)
            url_ota = "https://xyiwvzfieualsfaifwfq.supabase.co/storage/v1/object/public/updates/version.json"

            with patch.object(cfg_mod, "APP_DATA_DIR", default_cfg.parent), \
                 patch.object(cfg_mod, "BASE_DIR", default_cfg.parent), \
                 patch.object(cfg_mod, "_DEFAULT_CONFIG_JSON_PATH", default_cfg), \
                 patch.object(cfg_mod, "CONFIG_JSON_PATH", default_cfg), \
                 patch("builtins.open", side_effect=_fake_open):
                ok = cfg_mod.guardar_configuracion_app({"updater_url": url_ota})
                leido = cfg_mod.cargar_configuracion_app()

            self.assertTrue(ok)
            self.assertEqual(leido.get("updater_url"), url_ota)
            self.assertTrue((local_appdata / "RENO" / "reno_config.json").exists())
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_admin_config_widget_protege_edicion_ota_sugiere_sanitizado_y_emite_senal(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QApplication, QMessageBox
        import src.core.config as cfg_mod
        from src.data.database import DatabaseManager
        from src.data.migrations import init_database_schema
        from src.ui.views.dialogs.admin_dialogs import AdminConfigWidget

        app = QApplication.instance() or QApplication([])
        cfg_file = self.temp_dir / "reno_config.json"
        cfg_file.write_text(
            json.dumps({"supabase_url": "", "updater_url": ""}),
            encoding="utf-8",
        )
        db = DatabaseManager(self.temp_dir / "cfg_test.sqlite", journal_mode="WAL")
        init_database_schema(db)

        old_env = {k: os.environ.get(k) for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "RENO_UPDATES_URL")}
        for k in old_env:
            os.environ.pop(k, None)

        widget = None
        try:
            with patch.object(cfg_mod, "APP_DATA_DIR", self.temp_dir), \
                 patch.object(cfg_mod, "BASE_DIR", self.temp_dir), \
                 patch.object(cfg_mod, "_DEFAULT_CONFIG_JSON_PATH", cfg_file), \
                 patch.object(cfg_mod, "CONFIG_JSON_PATH", cfg_file):
                widget = AdminConfigWidget(db)
                self.assertFalse(widget._hay_cambios_sin_guardar)

                # Simular edición del usuario en URL OTA y refresco periódico (forzar=False)
                url_usuario = "https://xyiwvzfieualsfaifwfq.supabase.co/storage/v1/object/public/updates/version.json"
                widget.txt_updater_url.setText(url_usuario)
                self.assertTrue(widget._hay_cambios_sin_guardar)
                widget.recargar(forzar=False)
                self.assertEqual(widget.txt_updater_url.text(), url_usuario)

                # Botón "Desde Supabase" sanitiza URLs del Dashboard de Supabase
                widget.txt_supa_url.setText("https://supabase.com/dashboard/project/xyiwvzfieualsfaifwfq/settings/api")
                widget._sugerir_url_updates()
                self.assertEqual(widget.txt_updater_url.text(), url_usuario)

                # Guardar emite señal configuracion_guardada y persiste la URL
                guardadas = []
                widget.configuracion_guardada.connect(lambda: guardadas.append(True))
                with patch.object(QMessageBox, "information"), patch.object(QMessageBox, "warning"):
                    widget._guardar()

                self.assertEqual(guardadas, [True])
                self.assertFalse(widget._hay_cambios_sin_guardar)
                self.assertEqual(cfg_mod.cargar_configuracion_app().get("updater_url"), url_usuario)

                # Si fallara la escritura JSON, no emite éxito ni limpia la marca de cambios
                widget.txt_updater_url.setText("https://otra-url.supabase.co")
                self.assertTrue(widget._hay_cambios_sin_guardar)
                guardadas.clear()
                with patch("src.core.config.guardar_configuracion_json", return_value=False), \
                     patch.object(QMessageBox, "information") as m_info, \
                     patch.object(QMessageBox, "warning") as m_warn:
                    widget._guardar()

                self.assertEqual(guardadas, [])
                self.assertTrue(widget._hay_cambios_sin_guardar)
                m_info.assert_not_called()
                m_warn.assert_called_once()
        finally:
            if widget is not None:
                widget.cerrar_limpiamente()
                widget.close()
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_mainwindow_usa_config_ota_activa_y_sincroniza_al_guardar(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QApplication
        import src.core.config as cfg_mod
        from src.data.database import DatabaseManager
        from src.data.migrations import init_database_schema
        from src.domain.enums import PERMISOS_BASE
        from src.services.auth_service import AuthService
        from src.services.backup_service import BackupService
        from src.services.excel_service import ExcelService
        from src.services.pdf_service import PDFService
        from src.services.records_service import RecordsService
        from src.services.word_service import WordService
        from src.ui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        cfg_file = self.temp_dir / "reno_config.json"
        cfg_file.write_text(json.dumps({"supabase_url": "", "updater_url": ""}), encoding="utf-8")

        db = DatabaseManager(self.temp_dir / "mw_ota.sqlite", journal_mode="WAL")
        init_database_schema(db)
        auth = AuthService(db)
        auth.inicializar_permisos(PERMISOS_BASE)
        auth.crear_administrador_inicial("admin", "Admin123!", legajo=1001)
        usuario = auth.autenticar("admin", "Admin123!")

        old_env = {k: os.environ.get(k) for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "RENO_UPDATES_URL")}
        for k in old_env:
            os.environ.pop(k, None)

        win = None
        try:
            with patch.object(cfg_mod, "APP_DATA_DIR", self.temp_dir), \
                 patch.object(cfg_mod, "BASE_DIR", self.temp_dir), \
                 patch.object(cfg_mod, "_DEFAULT_CONFIG_JSON_PATH", cfg_file), \
                 patch.object(cfg_mod, "CONFIG_JSON_PATH", cfg_file):
                win = MainWindow(
                    db=db,
                    records_service=RecordsService(db),
                    auth_service=auth,
                    excel_service=ExcelService(db),
                    pdf_service=PDFService(),
                    word_service=WordService(),
                    backup_service=BackupService(db),
                    usuario_actual=usuario,
                    supabase_sync=None,
                )
                # Simular estado de actualización cacheado previo y cambio sin guardar en widget_config
                win._ultima_info_actualizacion = InfoActualizacion(version="2.0.1", fecha="16/11/2026", titulo="Old")
                win.btn_actualizar.setVisible(True)

                url_nueva = "https://xyiwvzfieualsfaifwfq.supabase.co/storage/v1/object/public/updates/version.json"
                win.tab_administracion.widget_config.txt_updater_url.setText(url_nueva)
                cfg_activa = win._obtener_config_updater_activa()
                self.assertEqual(cfg_activa.get("updater_url"), url_nueva)

                # Al guardar desde diálogo, recarga la pestaña de config y limpia la caché anterior
                cfg_file.write_text(json.dumps({"updater_url": url_nueva}), encoding="utf-8")
                win._on_configuracion_guardada_desde_dialog()
                self.assertIsNone(win._ultima_info_actualizacion)
                self.assertFalse(win.btn_actualizar.isVisible())
                self.assertEqual(win.tab_administracion.widget_config.txt_updater_url.text(), url_nueva)
        finally:
            if win is not None:
                win.close()
                app.processEvents()
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
