"""
app.py - Servidor Flask do DriveBackup
"""
import os
import sys
import json
import time
import threading
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, session

import models
import drive_client
import backup_engine
import scheduler
import notificador
from google_auth_oauthlib.flow import Flow
from datetime import datetime

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

def get_resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if getattr(sys, 'frozen', False):
    template_dir = get_resource_path('templates')
    static_dir = get_resource_path('static')
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
else:
    app = Flask(__name__)
app.secret_key = "drivebackup_secret_2024_fassil"

# ========================
# LOGIN & AUTENTICAÇÃO
# ========================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        senha = request.form.get("senha", "")
        if senha == "f@$p3l":
            session["logged_in"] = True
            flash("Login realizado com sucesso!", "success")
            return redirect(request.args.get("next") or url_for("dashboard"))
        else:
            flash("Senha incorreta.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    flash("Você saiu do sistema.", "success")
    return redirect(url_for("login"))


# ========================
# DASHBOARD
# ========================

@app.route("/")
@login_required
def dashboard():
    stats = models.contar_estatisticas()
    jobs = models.listar_jobs()
    execucoes = models.listar_execucoes(limit=5)
    proximas = scheduler.listar_proximas_execucoes()

    # Match próximas execuções aos jobs
    proximas_map = {}
    for p in proximas:
        proximas_map[str(p["job_id"])] = p["proxima_execucao"]

    return render_template("dashboard.html",
                           stats=stats, jobs=jobs,
                           execucoes=execucoes, proximas=proximas_map)


# ========================
# JOBS
# ========================

@app.route("/jobs")
@login_required
def listar_jobs_view():
    jobs = models.listar_jobs()
    proximas = scheduler.listar_proximas_execucoes()
    proximas_map = {}
    for p in proximas:
        proximas_map[str(p["job_id"])] = p["proxima_execucao"]

    # Adiciona última execução a cada job
    for job in jobs:
        job["ultima"] = models.obter_ultima_execucao(job["id"])
        job["dias_semana_list"] = json.loads(job.get("dias_semana", "[]"))

    return render_template("jobs.html", jobs=jobs, proximas=proximas_map)


@app.route("/jobs/novo", methods=["GET", "POST"])
@login_required
def novo_job():
    if request.method == "POST":
        dados = _parse_job_form(request.form)
        job_id = models.criar_job(dados)
        job = models.obter_job(job_id)
        scheduler.agendar_job(job)
        flash("Job criado com sucesso!", "success")
        return redirect(url_for("listar_jobs_view"))

    config = models.obter_config()
    drive_ok = bool(config.get("google_json_path"))
    return render_template("job_form.html", job=None, drive_ok=drive_ok)


@app.route("/jobs/<int:job_id>/editar", methods=["GET", "POST"])
@login_required
def editar_job(job_id):
    job = models.obter_job(job_id)
    if not job:
        flash("Job não encontrado.", "error")
        return redirect(url_for("listar_jobs_view"))

    if request.method == "POST":
        dados = _parse_job_form(request.form)
        models.atualizar_job(job_id, dados)
        job = models.obter_job(job_id)
        scheduler.agendar_job(job)
        flash("Job atualizado com sucesso!", "success")
        return redirect(url_for("listar_jobs_view"))

    job["dias_semana_list"] = json.loads(job.get("dias_semana", "[]"))
    config = models.obter_config()
    drive_ok = bool(config.get("google_json_path"))
    return render_template("job_form.html", job=job, drive_ok=drive_ok)


@app.route("/jobs/<int:job_id>/executar", methods=["POST"])
@login_required
def executar_job(job_id):
    job = models.obter_job(job_id)
    if not job:
        flash("Job não encontrado.", "error")
        return redirect(url_for("listar_jobs_view"))

    backup_engine.executar_backup_async(job_id)
    flash(f"Backup '{job['nome']}' iniciado!", "info")
    return redirect(url_for("listar_jobs_view"))


@app.route("/jobs/<int:job_id>/toggle", methods=["POST"])
@login_required
def toggle_job(job_id):
    novo_status = models.toggle_job(job_id)
    job = models.obter_job(job_id)
    if job:
        if novo_status:
            scheduler.agendar_job(job)
        else:
            scheduler.remover_job(job_id)
    return redirect(url_for("listar_jobs_view"))


@app.route("/jobs/<int:job_id>/excluir", methods=["POST"])
@login_required
def excluir_job(job_id):
    scheduler.remover_job(job_id)
    models.excluir_job(job_id)
    flash("Job excluído.", "success")
    return redirect(url_for("listar_jobs_view"))


def _parse_job_form(form):
    dias = form.getlist("dias_semana")
    return {
        "nome": form.get("nome", "").strip(),
        "pasta_origem": form.get("pasta_origem", "").strip(),
        "destino_tipo": form.get("destino_tipo", "local"),
        "destino_drive_id": form.get("destino_drive_id", "").strip() or None,
        "destino_drive_nome": form.get("destino_drive_nome", "").strip() or None,
        "destino_local": form.get("destino_local", "").strip() or None,
        "dias_semana": dias,
        "horario": form.get("horario", "22:00").strip()
    }


# ========================
# LOGS
# ========================

@app.route("/logs")
@login_required
def logs_view():
    execucoes = models.listar_execucoes(limit=100)
    return render_template("logs.html", execucoes=execucoes)


# ========================
# CONFIGURAÇÕES
# ========================

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_view():
    if request.method == "POST":
        dados = {
            "google_json_path": request.form.get("google_json_path", "").strip(),
            "evolution_url": request.form.get("evolution_url", "").strip(),
            "evolution_instance": request.form.get("evolution_instance", "").strip(),
            "evolution_apikey": request.form.get("evolution_apikey", "").strip(),
            "whatsapp_destino": request.form.get("whatsapp_destino", "").strip(),
            "git_repo_url": request.form.get("git_repo_url", "").strip(),
            "git_token": request.form.get("git_token", "").strip(),
        }
        models.salvar_config(dados)
        flash("Configurações salvas!", "success")
        return redirect(url_for("settings_view"))

    config = models.obter_config()
    return render_template("settings.html", config=config)


@app.route("/settings/testar-drive", methods=["POST"])
@login_required
def testar_drive():
    config = models.obter_config()
    json_path = config.get("google_json_path", "")
    ok, msg = drive_client.testar_conexao(json_path)
    return jsonify({"ok": ok, "mensagem": msg})


@app.route("/settings/testar-whatsapp", methods=["POST"])
@login_required
def testar_whatsapp():
    config = models.obter_config()
    ok = notificador.testar_notificacao(config)
    return jsonify({"ok": ok, "mensagem": "Mensagem enviada!" if ok else "Falha ao enviar."})


# ========================
# API - Manutenção do Sistema
# ========================
# GESTÃO DO SISTEMA E ATUALIZAÇÃO
# ========================

@app.route("/api/sistema/ping")
def api_sistema_ping():
    """Endpoint simples para verificar se o sistema voltou."""
    return jsonify({"ok": True})

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def obter_versao_local():
    """Retorna o SHA do último commit baixado."""
    version_file = os.path.join(BASE_DIR, "data", "version.json")
    if os.path.exists(version_file):
        try:
            with open(version_file, 'r') as f:
                return json.load(f).get("sha", "000000")
        except: pass
    return "unknown"

@app.route("/api/sistema/status_atualizacao")
@login_required
def api_sistema_status_atualizacao():
    """Verifica se existe um commit mais recente no GitHub."""
    try:
        import requests
        config = models.obter_config()
        token = config.get("git_token", "")
        repo_url = config.get("git_repo_url", "https://github.com/cristhian-sancore/DriveBackup")
        
        try:
            parts = repo_url.rstrip("/").split("/")
            owner, repo = parts[-2], parts[-1]
        except:
            owner, repo = "cristhian-sancore", "DriveBackup"

        headers = {"Authorization": f"token {token}"} if token else {}
        # Adiciona um timestamp para evitar cache da API do GitHub
        url = f"https://api.github.com/repos/{owner}/{repo}/commits/main?t={int(time.time())}"
        
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            latest_sha = response.json().get("sha")
            local_sha = obter_versao_local()
            return jsonify({
                "ok": True,
                "local_sha": local_sha[:7],
                "latest_sha": latest_sha[:7],
                "tem_atualizacao": latest_sha != local_sha
            })
    except Exception as e:
        return jsonify({"ok": False, "mensagem": str(e)})
    return jsonify({"ok": False, "mensagem": "Falha ao verificar versao"})

@app.route("/api/sistema/atualizar", methods=["POST"])
@login_required
def api_sistema_atualizar():
    """Baixa o novo BKP_FASPEL_Setup.exe diretamente do GitHub."""
    try:
        import requests
        config = models.obter_config()
        token = config.get("git_token", "")
        repo_url = config.get("git_repo_url", "https://github.com/cristhian-sancore/DriveBackup")
        
        try:
            parts = repo_url.rstrip("/").split("/")
            owner, repo = parts[-2], parts[-1]
        except:
            owner, repo = "cristhian-sancore", "DriveBackup"
        
        # 1. Busca o SHA mais recente primeiro
        headers = {"Authorization": f"token {token}"} if token else {}
        sha_url = f"https://api.github.com/repos/{owner}/{repo}/commits/main"
        sha_res = requests.get(sha_url, headers=headers, timeout=10)
        latest_sha = sha_res.json().get("sha") if sha_res.status_code == 200 else None

        # 2. Obtém a URL de download via API para garantir que o arquivo existe
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/BKP_FASPEL_Setup.exe?ref=main"
        res = requests.get(api_url, headers=headers, timeout=10)
        
        exe_url = None
        if res.status_code == 200:
            exe_url = res.json().get("download_url")
        else:
            # Tenta sem o _Setup como fallback
            api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/BKP_FASPEL.exe?ref=main"
            res = requests.get(api_url, headers=headers, timeout=10)
            if res.status_code == 200:
                exe_url = res.json().get("download_url")

        if not exe_url:
            return jsonify({"ok": False, "mensagem": f"Arquivo executável não encontrado no GitHub (404)."})

        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            
        exe_path = os.path.join(base_dir, "BKP_FASPEL_Setup.exe")
        
        response = requests.get(exe_url, stream=True, timeout=60)
        if response.status_code != 200:
            return jsonify({"ok": False, "mensagem": f"Erro ao baixar arquivo ({response.status_code})"})
            
        with open(exe_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # 3. Salva o novo SHA localmente
        if latest_sha:
            version_file = os.path.join(base_dir, "data", "version.json")
            with open(version_file, 'w') as f:
                json.dump({"sha": latest_sha, "date": datetime.now().isoformat()}, f)
            
        return jsonify({"ok": True, "atualizado": True, "mensagem": "Novo executável baixado! Clique em 'Reiniciar' para aplicar."})
    except Exception as e:
        return jsonify({"ok": False, "mensagem": f"Erro interno: {str(e)}"})


@app.route("/api/sistema/reiniciar", methods=["POST"])
@login_required
def api_sistema_reiniciar():
    """Reinicia o serviço do Windows via arquivo BAT temporário."""
    try:
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            
        bat_path = os.path.join(base_dir, "restart_app.bat")
        service_name = "BKP_FASPEL"
        
        content = f"""@echo off
setlocal
cd /d "%~dp0"
echo Parando o servico {service_name}...
net stop {service_name}
timeout /t 3 /nobreak > nul
taskkill /f /im BKP_FASPEL.exe > nul 2>&1
timeout /t 5 /nobreak > nul

if exist "BKP_FASPEL_Setup.exe" (
    echo Atualizando para a nova versao (Setup)...
    timeout /t 1 /nobreak > nul
    copy /y "BKP_FASPEL_Setup.exe" "BKP_FASPEL.exe"
    if %errorlevel% equ 0 (
        echo Sucesso ao copiar Setup!
        del "BKP_FASPEL_Setup.exe"
    )
)

timeout /t 2 /nobreak > nul

if exist "BKP_FASPEL_NEW.exe" (
    echo Atualizando para a nova versao (NEW)...
    timeout /t 1 /nobreak > nul
    copy /y "BKP_FASPEL_NEW.exe" "BKP_FASPEL.exe"
    if %errorlevel% equ 0 (
        echo Sucesso ao copiar NEW!
        del "BKP_FASPEL_NEW.exe"
    )
)

timeout /t 3 /nobreak > nul
echo Reiniciando o servico {service_name}...
net start {service_name} > update_log.txt 2>&1

if %errorlevel% neq 0 (
    echo [ERROR] net start falhou, tentando via NSSM...
    nssm.exe start {service_name} >> update_log.txt 2>&1
)

if %errorlevel% neq 0 (
    echo [ERROR] Falha critica ao iniciar o servico.
)

timeout /t 2 /nobreak > nul
del "%~f0" & exit
"""
        with open(bat_path, "w") as f:
            f.write(content)
            
        import subprocess
        subprocess.Popen([bat_path], shell=True, creationflags=subprocess.CREATE_NEW_CONSOLE)
        
        return jsonify({"ok": True, "mensagem": "Comando de reinício enviado. O sistema ficará offline brevemente."})
    except Exception as e:
        return jsonify({"ok": False, "mensagem": f"Falha ao agendar reinício: {str(e)}"})


# ========================
# API - Google Drive (AJAX)
# ========================

@app.route("/api/drive/pastas")
@login_required
def api_drive_pastas():
    parent_id = request.args.get("parent_id", "root")
    config = models.obter_config()
    json_path = config.get("google_json_path", "")
    if not json_path:
        return jsonify({"erro": "Credenciais do Google Drive não configuradas.", "pastas": []})

    try:
        pastas = drive_client.listar_pastas(json_path, parent_id if parent_id != "root" else None)
        return jsonify({"pastas": pastas, "erro": None})
    except Exception as e:
        return jsonify({"erro": str(e), "pastas": []})


@app.route("/api/drive/arvore")
@login_required
def api_drive_arvore():
    """Retorna a árvore COMPLETA de pastas do Drive em hierarquia."""
    config = models.obter_config()
    json_path = config.get("google_json_path", "")
    if not json_path:
        return jsonify({"erro": "Credenciais do Google Drive não configuradas.", "arvore": []})

    try:
        arvore = drive_client.listar_arvore_completa(json_path)
        return jsonify({"arvore": arvore, "erro": None})
    except Exception as e:
        return jsonify({"erro": str(e), "arvore": []})


@app.route("/api/drive/criar-pasta", methods=["POST"])
@login_required
def api_drive_criar_pasta():
    config = models.obter_config()
    json_path = config.get("google_json_path", "")
    nome = request.json.get("nome", "")
    parent_id = request.json.get("parent_id")

    if not json_path:
        return jsonify({"erro": "Credenciais não configuradas."})
    if not nome:
        return jsonify({"erro": "Nome da pasta é obrigatório."})

    try:
        pasta = drive_client.criar_pasta(json_path, nome, parent_id)
        return jsonify({"pasta": pasta, "erro": None})
    except Exception as e:
        return jsonify({"erro": str(e)})


# ========================
# GOOGLE DRIVE OAUTH WEB FLOW
# ========================

@app.route("/auth/google")
@login_required
def auth_google():
    """Inicia o fluxo de autorização do Google Drive via Web."""
    # Busca todos os JSONs na pasta data para encontrar um que seja OAuth (web ou installed)
    json_path = None
    data_dir = os.path.join(BASE_DIR, "data")
    if os.path.exists(data_dir):
        for arq in os.listdir(data_dir):
            if arq.endswith(".json"):
                caminho = os.path.join(data_dir, arq)
                try:
                    with open(caminho, 'r') as f:
                        dados = json.load(f)
                        if "web" in dados or "installed" in dados:
                            json_path = caminho
                            # Se achou, já salva como padrão se não houver um
                            models.salvar_config({"google_json_path": json_path})
                            break
                except:
                    continue

    if not json_path:
        flash("Arquivo de credenciais OAuth (client_secret.json) não encontrado na pasta data. Certifique-se de que o arquivo é do tipo 'Web' ou 'Desktop'.", "error")
        return redirect(url_for('settings_view'))

    try:
        flow = Flow.from_client_secrets_file(
            json_path,
            scopes=['https://www.googleapis.com/auth/drive'],
            redirect_uri=url_for('oauth2callback', _external=True)
        )
        
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            prompt='consent',
            include_granted_scopes='true'
        )
        
        session['oauth_state'] = state
        # O code_verifier é necessário para as versões novas da biblioteca (PKCE)
        session['oauth_verifier'] = flow.code_verifier
        
        return redirect(authorization_url)
    except Exception as e:
        flash(f"Erro ao iniciar autenticação: {str(e)}", "error")
        return redirect(url_for('settings_view'))


@app.route("/oauth2callback")
@login_required
def oauth2callback():
    """Callback do Google após autorização."""
    try:
        state = session.get('oauth_state')
        
        # Localiza o JSON correto novamente para garantir consistência
        json_path = None
        data_dir = os.path.join(BASE_DIR, "data")
        for arq in os.listdir(data_dir):
            if arq.endswith(".json"):
                caminho = os.path.join(data_dir, arq)
                try:
                    with open(caminho, 'r') as f:
                        dados = json.load(f)
                        if "web" in dados or "installed" in dados:
                            json_path = caminho
                            break
                except: continue
        
        if not json_path:
            raise Exception("Arquivo client_secret não encontrado no callback.")

        flow = Flow.from_client_secrets_file(
            json_path,
            scopes=['https://www.googleapis.com/auth/drive'],
            state=state,
            redirect_uri=url_for('oauth2callback', _external=True)
        )
        
        flow.fetch_token(
            authorization_response=request.url,
            code_verifier=session.get('oauth_verifier')
        )
        
        creds = flow.credentials
        
        # Salva o token no caminho esperado pelo drive_client.py
        with open(drive_client.TOKEN_FILE, 'w') as token_file:
            token_file.write(creds.to_json())
        
        flash("Google Drive conectado com sucesso!", "success")
        return redirect(url_for('settings_view'))
    except Exception as e:
        print(f"ERRO NO CALLBACK OAUTH: {e}")
        flash(f"Erro no retorno do Google: {str(e)}", "error")
        return redirect(url_for('settings_view'))


# ========================
# API - Explorador Local
# ========================

@app.route("/api/local/explorar")
@login_required
def api_local_explorar():
    import string
    caminho = request.args.get("path", "").strip()
    
    # Se vazio, lista os discos do Windows
    if not caminho:
        discos = []
        for letra in string.ascii_uppercase:
            disco = f"{letra}:\\"
            if os.path.exists(disco):
                discos.append({"nome": disco, "caminho": disco, "tipo": "dir"})
        return jsonify({"itens": discos, "caminho_atual": ""})
    
    # Se houver caminho, lista o conteúdo do diretório
    if not os.path.exists(caminho):
        return jsonify({"erro": "Caminho não encontrado.", "itens": [], "caminho_atual": caminho})
    
    if not os.path.isdir(caminho):
        return jsonify({"erro": "O caminho não é uma pasta.", "itens": [], "caminho_atual": caminho})

    try:
        itens = []
        for nome in os.listdir(caminho):
            caminho_completo = os.path.join(caminho, nome)
            try:
                tipo = "dir" if os.path.isdir(caminho_completo) else "file"
                itens.append({"nome": nome, "caminho": caminho_completo, "tipo": tipo})
            except Exception:
                # Ignora arquivos sem permissão de leitura
                pass
        
        # Ordena: pastas primeiro, depois arquivos alfabeticamente
        itens.sort(key=lambda x: (x["tipo"] == "file", x["nome"].lower()))
        
        return jsonify({"itens": itens, "caminho_atual": caminho, "erro": None})
    except PermissionError:
        return jsonify({"erro": "Acesso negado a esta pasta.", "itens": [], "caminho_atual": caminho})
    except Exception as e:
        return jsonify({"erro": str(e), "itens": [], "caminho_atual": caminho})


# ========================
# SSE - Progresso em tempo real
# ========================

@app.route("/api/progresso/<int:job_id>")
@login_required
def api_progresso(job_id):
    def stream():
        while True:
            prog = backup_engine.get_progresso(job_id)
            yield f"data: {json.dumps(prog)}\n\n"
            if prog["etapa"] in ("concluido", "erro", "idle"):
                break
            time.sleep(1)

    return Response(stream(), mimetype="text/event-stream")


# ========================
# INICIALIZAÇÃO
# ========================

def inicializar():
    """Inicializa banco, agendador e autentica Drive automaticamente."""
    models.init_db()
    
    # Auto-detecção e Login do Google Drive
    print("[*] Verificando credenciais do Google Drive...")
    json_path = models.detectar_e_atualizar_google_json()
    if json_path:
        print(f"[*] Credenciais encontradas: {os.path.basename(json_path)}")
        ok, msg = drive_client.testar_conexao(json_path)
        if ok:
            print(f"[OK] Google Drive conectado: {msg}")
        else:
            print(f"[!] Falha ao conectar no Drive: {msg}")
    else:
        print("[!] Nenhuma credencial do Google Drive encontrada na pasta 'data'.")

    scheduler.carregar_todos_jobs()
    print("=" * 50)
    print("  DriveBackup - Sistema de Backup")
    print("=" * 50)


if __name__ == "__main__":
    if getattr(sys, 'frozen', False) and "--service-run" not in sys.argv:
        # ==== MODO INSTALADOR (EXE duplo clique) ====
        import shutil
        import subprocess
        
        PASTA_DESTINO = r"C:\BKP_FASPEL"
        NOME_SERVICO = "BKP_FASPEL"
        
        print("="*50)
        print("     INSTALADOR - DRIVEBACKUP FASPEL")
        print("="*50)
        print(f"[*] Preparando ambiente em {PASTA_DESTINO}...")
        os.makedirs(PASTA_DESTINO, exist_ok=True)
        
        exe_destino = os.path.join(PASTA_DESTINO, "BKP_FASPEL.exe")
        nssm_origem = os.path.join(sys._MEIPASS, "nssm.exe")
        nssm_destino = os.path.join(PASTA_DESTINO, "nssm.exe")
        uninst_origem = os.path.join(sys._MEIPASS, "Desinstalar_BKP.exe")
        uninst_destino = os.path.join(PASTA_DESTINO, "Desinstalar_BKP.exe")
        
        try:
            print("[*] Copiando sistema...")
            shutil.copy2(sys.executable, exe_destino)
            shutil.copy2(nssm_origem, nssm_destino)
            if os.path.exists(uninst_origem):
                shutil.copy2(uninst_origem, uninst_destino)
            
            # Copia a pasta data (banco de dados e token.json) que vai vim embutida no EXE
            pasta_dados_origem = os.path.join(sys._MEIPASS, "data")
            pasta_dados_destino = os.path.join(PASTA_DESTINO, "data")
            if os.path.exists(pasta_dados_origem):
                os.makedirs(pasta_dados_destino, exist_ok=True)
                for item in os.listdir(pasta_dados_origem):
                    s = os.path.join(pasta_dados_origem, item)
                    d = os.path.join(pasta_dados_destino, item)
                    if os.path.isfile(s):
                        # Só copia se o arquivo não existir ou se for o token.json (para atualizar o acesso)
                        if not os.path.exists(d) or item == "token.json":
                            shutil.copy2(s, d)
                    elif os.path.isdir(s):
                        if not os.path.exists(d):
                            shutil.copytree(s, d)
            
            print("[*] Arquivos instalados!")
        except Exception as e:
            print(f"[X] Erro critico ao copiar arquivos: {e}")
            input("Pressione ENTER para sair...")
            sys.exit(1)
            
        print("[*] Instalando Servico do Windows...")
        # Para e remove se o servico ja existir
        subprocess.run([nssm_destino, "stop", NOME_SERVICO], capture_output=True)
        subprocess.run([nssm_destino, "remove", NOME_SERVICO, "confirm"], capture_output=True)
        
        # Cria e configura servico via NSSM no services.msc
        subprocess.run([nssm_destino, "install", NOME_SERVICO, exe_destino, "--service-run"])
        subprocess.run([nssm_destino, "set", NOME_SERVICO, "AppDirectory", PASTA_DESTINO])
        subprocess.run([nssm_destino, "set", NOME_SERVICO, "Description", "Sistema automatizado de Backup Faspel (DriveBackup)"])
        subprocess.run([nssm_destino, "set", NOME_SERVICO, "Start", "SERVICE_AUTO_START"])
        
        print("[*] Iniciando o Servico pela primeira vez...")
        subprocess.run([nssm_destino, "start", NOME_SERVICO])
        
        print()
        print("==================================================")
        print("   INSTALACAO CONCLUIDA COM SUCESSO! ")
        print("   Acesse o painel web no link: http://localhost:5555")
        print("==================================================")
        input("Pressione ENTER para fechar essa janela...")
        sys.exit(0)

    # ==== MODO SERVIDOR (Rotina Normal via --service-run ou DEV) ====
    inicializar()
    config = models.obter_config()
    porta = int(config.get("porta_servidor", 5555))
    print(f"  Painel: http://localhost:{porta}")
    print("=" * 50)
    app.run(host="0.0.0.0", port=porta, debug=False)
