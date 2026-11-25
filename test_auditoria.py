"""
test_auditoria.py - Testes de verificação das correções da auditoria
"""
import os
import sys
import types
import tempfile
import unittest
from unittest.mock import patch, MagicMock


def _instalar_mocks_opcionais():
    """Garante mocks mínimos apenas se dependências externas não estiverem instaladas."""
    if "apscheduler" not in sys.modules:
        try:
            import apscheduler  # noqa: F401
        except ImportError:
            ap = types.ModuleType("apscheduler")
            ap_sched = types.ModuleType("apscheduler.schedulers")
            ap_bg = types.ModuleType("apscheduler.schedulers.background")
            ap_trig = types.ModuleType("apscheduler.triggers")
            ap_cron = types.ModuleType("apscheduler.triggers.cron")

            class DummyScheduler:
                def __init__(self, *a, **kw):
                    self._jobs = {}
                def start(self):
                    pass
                def get_job(self, jid):
                    return self._jobs.get(jid)
                def remove_job(self, jid):
                    self._jobs.pop(jid, None)
                def add_job(self, *a, **kw):
                    jid = kw.get("id", "job")
                    self._jobs[jid] = MagicMock(id=jid, name=kw.get("name", jid), next_run_time=None)
                def get_jobs(self):
                    return list(self._jobs.values())
                def shutdown(self, wait=False):
                    pass

            ap_bg.BackgroundScheduler = DummyScheduler
            ap_cron.CronTrigger = MagicMock()
            sys.modules["apscheduler"] = ap
            sys.modules["apscheduler.schedulers"] = ap_sched
            sys.modules["apscheduler.schedulers.background"] = ap_bg
            sys.modules["apscheduler.triggers"] = ap_trig
            sys.modules["apscheduler.triggers.cron"] = ap_cron

    for mod_name in (
        "google",
        "google.oauth2",
        "google.oauth2.credentials",
        "google.oauth2.service_account",
        "google_auth_oauthlib",
        "google_auth_oauthlib.flow",
        "google.auth",
        "google.auth.transport",
        "google.auth.transport.requests",
        "googleapiclient",
        "googleapiclient.discovery",
        "googleapiclient.http",
        "googleapiclient.errors",
    ):
        if mod_name not in sys.modules:
            try:
                __import__(mod_name)
            except ImportError:
                m = types.ModuleType(mod_name)
                if mod_name == "google_auth_oauthlib.flow":
                    m.InstalledAppFlow = MagicMock()
                    m.Flow = MagicMock()
                elif mod_name == "google.oauth2.credentials":
                    m.Credentials = MagicMock()
                elif mod_name == "google.oauth2.service_account":
                    m.Credentials = MagicMock()
                elif mod_name == "google.auth.transport.requests":
                    m.Request = MagicMock()
                elif mod_name == "googleapiclient.discovery":
                    m.build = MagicMock()
                elif mod_name == "googleapiclient.http":
                    m.MediaFileUpload = MagicMock()
                elif mod_name == "googleapiclient.errors":
                    m.HttpError = Exception
                sys.modules[mod_name] = m


_instalar_mocks_opcionais()

import notificador  # noqa: E402
import models  # noqa: E402
import backup_engine  # noqa: E402
import app as flask_app  # noqa: E402


class TestAuditoria(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_base_dir = models.BASE_DIR
        self.orig_data_dir = models.DATA_DIR
        self.orig_db_path = models.DB_PATH
        models.BASE_DIR = self.tmp_dir.name
        models.DATA_DIR = os.path.join(self.tmp_dir.name, "data")
        models.DB_PATH = os.path.join(models.DATA_DIR, "drivebackup.db")
        models.init_db()

    def tearDown(self):
        models.BASE_DIR = self.orig_base_dir
        models.DATA_DIR = self.orig_data_dir
        models.DB_PATH = self.orig_db_path
        self.tmp_dir.cleanup()

    def test_notificador_sem_nameerror_e_timeout_12h(self):
        ok = notificador.enviar_whatsapp({}, "teste")
        self.assertFalse(ok)
        with patch("notificador.enviar_whatsapp", return_value=True) as mock_send:
            res = notificador.notificar_timeout({}, "JobTeste", 725, limite_horas=12)
            self.assertTrue(res)
            msg = mock_send.call_args[0][1]
            self.assertIn("12h", msg)
            self.assertNotIn("60 minutos", msg)

    def test_models_sem_segredos_hardcoded(self):
        cfg = models.obter_config()
        self.assertEqual(cfg.get("evolution_apikey"), "")
        self.assertEqual(cfg.get("whatsapp_destino"), "")
        res = models.detectar_e_atualizar_google_json()
        self.assertIsNone(res)
        self.assertFalse(os.path.exists(os.path.join(models.DATA_DIR, "client_secret_default.json")))

    def test_backup_engine_compactar_e_dedup_fila(self):
        src_dir = os.path.join(self.tmp_dir.name, "origem")
        os.makedirs(src_dir, exist_ok=True)
        arq = os.path.join(src_dir, "exemplo.txt")
        with open(arq, "w", encoding="utf-8") as f:
            f.write("conteudo de teste")

        zip_path = os.path.join(self.tmp_dir.name, "saida.zip")
        caminho, total, tamanho, ignorados = backup_engine.compactar_pasta(src_dir, zip_path)
        self.assertTrue(os.path.exists(caminho))
        self.assertEqual(total, 1)
        self.assertGreater(tamanho, 0)
        self.assertEqual(ignorados, 0)

        with patch("backup_engine.threading.Thread") as mock_thread:
            backup_engine._worker_ativo = False
            backup_engine._jobs_na_fila.clear()
            while not backup_engine._fila_backup.empty():
                backup_engine._fila_backup.get_nowait()
                backup_engine._fila_backup.task_done()

            backup_engine.executar_backup_async(999)
            backup_engine.executar_backup_async(999)
            self.assertEqual(backup_engine._fila_backup.qsize(), 1)
            mock_thread.assert_called_once()
            backup_engine._worker_ativo = False
            backup_engine._jobs_na_fila.clear()
            while not backup_engine._fila_backup.empty():
                backup_engine._fila_backup.get_nowait()
                backup_engine._fila_backup.task_done()

    def test_app_csrf_login_e_settings(self):
        from werkzeug.security import generate_password_hash
        models.salvar_config({
            "senha_admin": generate_password_hash("nova_senha_123"),
            "google_json_path": "C:/caminho/cred.json",
            "evolution_apikey": "api_secreta",
            "git_token": "ghp_secreto",
        })

        client = flask_app.app.test_client()

        # POST sem CSRF deve falhar com 403
        resp = client.post("/login", data={"senha": "nova_senha_123"})
        self.assertEqual(resp.status_code, 403)

        # GET /login gera csrf_token na sessão
        client.get("/login")
        with client.session_transaction() as sess:
            token = sess["csrf_token"]

        # Fallback antigo 'f@$p3l' NÃO deve funcionar após troca de senha
        resp_bad = client.post("/login", data={"senha": "f@$p3l", "csrf_token": token})
        self.assertEqual(resp_bad.status_code, 200)
        with client.session_transaction() as sess:
            self.assertFalse(sess.get("logged_in", False))

        # Login com a senha correta funciona
        resp_ok = client.post("/login", data={"senha": "nova_senha_123", "csrf_token": token})
        self.assertEqual(resp_ok.status_code, 302)
        with client.session_transaction() as sess:
            self.assertTrue(sess.get("logged_in"))

        # Salvar settings sem google_json_path e com tokens em branco preserva valores existentes
        resp_set = client.post("/settings", data={
            "evolution_url": "https://nova.url/",
            "evolution_instance": "INST",
            "evolution_apikey": "",
            "whatsapp_destino": "5511999999999",
            "git_repo_url": "https://github.com/cristhian-sancore/DriveBackup",
            "git_token": "",
            "csrf_token": token,
        })
        self.assertEqual(resp_set.status_code, 302)
        cfg = models.obter_config()
        self.assertEqual(cfg["google_json_path"], "C:/caminho/cred.json")
        self.assertEqual(cfg["evolution_apikey"], "api_secreta")
        self.assertEqual(cfg["git_token"], "ghp_secreto")

    def test_novas_funcionalidades_exclusoes_aes_export_senha(self):
        # 1. Exclusões + Criptografia AES no ZIP
        src_dir = os.path.join(self.tmp_dir.name, "origem_adv")
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(src_dir, "keep.txt"), "w", encoding="utf-8") as f:
            f.write("manter")
        with open(os.path.join(src_dir, "lixo.tmp"), "w", encoding="utf-8") as f:
            f.write("ignorar")

        zip_path = os.path.join(self.tmp_dir.name, "seguro.zip")
        _, total, _, ignorados = backup_engine.compactar_pasta(
            src_dir, zip_path, exclusoes_str="*.tmp", zip_senha="senha_forte"
        )
        self.assertEqual(total, 1)
        self.assertEqual(ignorados, 0)

        import pyzipper
        with pyzipper.AESZipFile(zip_path, "r") as zf:
            zf.setpassword(b"senha_forte")
            self.assertEqual(zf.namelist(), ["keep.txt"])
            self.assertEqual(zf.read("keep.txt"), b"manter")

        # 2. Exportar / Importar jobs e Alterar senha
        jid = models.criar_job({
            "nome": "JobExport",
            "pasta_origem": src_dir,
            "destino_tipo": "local",
            "destino_local": self.tmp_dir.name,
            "dias_semana": ["seg"],
            "horario": "22:00",
            "horarios_extras": "12:00, 18:00",
            "exclusoes": "*.tmp",
            "pre_comando": "",
            "zip_senha": "abc",
            "retencao": 5,
            "retencao_dias": 30,
        })
        self.assertGreater(jid, 0)
        exportado = models.exportar_dados()
        self.assertEqual(len(exportado["jobs"]), 1)
        self.assertNotIn("secret_key", exportado["configuracao"])
        self.assertNotIn("senha_admin", exportado["configuracao"])

        qtd_imp = models.importar_dados(exportado)
        self.assertEqual(qtd_imp, 1)


if __name__ == "__main__":
    unittest.main()
