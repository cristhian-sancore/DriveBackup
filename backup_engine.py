"""
backup_engine.py - Motor de backup (compressão, cópia local, upload Drive)
"""
import os
import sys
import io
import zipfile
import shutil
import time
import tempfile
import threading
from datetime import datetime

import models
import drive_client
import notificador

# Força UTF-8 no stdout para evitar UnicodeEncodeError com emojis no Windows
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Progresso global para SSE
_progresso = {}
_lock = threading.Lock()


def log(msg):
    """Print seguro para Windows (evita crash com emojis)."""
    try:
        print(msg)
    except UnicodeEncodeError:
        # Remove caracteres não suportados e tenta novamente
        safe_msg = msg.encode('ascii', errors='replace').decode('ascii')
        print(safe_msg)


def get_progresso(job_id):
    """Retorna o progresso atual de um job."""
    with _lock:
        return _progresso.get(job_id, {
            "etapa": "idle",
            "percentual": 0,
            "mensagem": "",
            "arquivo_atual": ""
        })


def _set_progresso(job_id, etapa, percentual, mensagem="", arquivo_atual=""):
    with _lock:
        _progresso[job_id] = {
            "etapa": etapa,
            "percentual": percentual,
            "mensagem": mensagem,
            "arquivo_atual": arquivo_atual
        }


def _limpar_progresso(job_id):
    with _lock:
        _progresso.pop(job_id, None)


def contar_arquivos(caminhos_str):
    """Conta todos os arquivos, suportando múltiplos caminhos separados por pipe (|)"""
    total = 0
    caminhos = [c.strip() for c in caminhos_str.split('|') if c.strip()]
    
    for caminho in caminhos:
        if os.path.isfile(caminho):
            total += 1
        elif os.path.exists(caminho):
            for root, dirs, files in os.walk(caminho):
                total += len(files)
    return total


def compactar_pasta(origens_str, caminho_zip, job_id=None):
    """
    Compacta as origens dadas em um arquivo ZIP. 'origens_str' pode conter 
    múltiplos caminhos de arquivos/pastas separados por '|'.
    Retorna (caminho_zip, total_arquivos, tamanho_bytes).
    """
    total_arquivos = contar_arquivos(origens_str)
    if total_arquivos == 0:
        raise ValueError(f"As origens ('{origens_str}') estao vazias ou inacessiveis. Nenhum arquivo encontrado.")
    
    processados = 0
    caminhos = [c.strip() for c in origens_str.split('|') if c.strip()]

    with zipfile.ZipFile(caminho_zip, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for caminho_origem in caminhos:
            if not os.path.exists(caminho_origem):
                log(f"[ZIP] Aviso: Caminho nao encontrado: {caminho_origem}")
                continue

            # Se for um unico arquivo
            if os.path.isfile(caminho_origem):
                arcname = os.path.basename(caminho_origem)
                try:
                    zf.write(caminho_origem, arcname)
                except (PermissionError, OSError) as e:
                    log(f"[ZIP] Nao foi possivel compactar {caminho_origem}: {e}")
                
                processados += 1
                if job_id and total_arquivos > 0:
                    pct = int((processados / total_arquivos) * 100)
                    _set_progresso(job_id, "compactando", pct, 
                                  f"Compactando {processados}/{total_arquivos} arquivos", arcname)
            
            # Se for uma pasta
            else:
                for root, dirs, files in os.walk(caminho_origem):
                    for arquivo in files:
                        caminho_completo = os.path.join(root, arquivo)
                        arcname = os.path.relpath(caminho_completo, caminho_origem)
                        
                        # Evita caminhos duplicados na raiz do zip se for mais de uma pasta
                        if len(caminhos) > 1:
                            arcname = os.path.join(os.path.basename(caminho_origem), arcname)

                        try:
                            zf.write(caminho_completo, arcname)
                        except (PermissionError, OSError) as e:
                            log(f"[ZIP] Nao foi possivel compactar {caminho_completo}: {e}")
                        
                        processados += 1
                        if job_id and total_arquivos > 0:
                            pct = int((processados / total_arquivos) * 100)
                            _set_progresso(job_id, "compactando", pct,
                                          f"Compactando {processados}/{total_arquivos} arquivos",
                                          arcname)

    tamanho = os.path.getsize(caminho_zip)
    return caminho_zip, total_arquivos, tamanho


def copiar_local(caminho_zip, pasta_destino):
    """Copia o ZIP para a pasta de destino local."""
    os.makedirs(pasta_destino, exist_ok=True)
    destino = os.path.join(pasta_destino, os.path.basename(caminho_zip))
    shutil.copy2(caminho_zip, destino)
    return destino


def executar_backup(job_id):
    """
    Executa o backup completo de um job.
    Roda em thread separada.
    """
    job = models.obter_job(job_id)
    if not job:
        log(f"[Backup] Job {job_id} nao encontrado.")
        return

    config = models.obter_config()
    exec_id = models.registrar_inicio(job_id)
    inicio = time.time()

    # Nome seguro para o ZIP (remove caracteres especiais)
    nome_safe = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in job['nome'])
    nome_zip = f"{nome_safe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    caminho_zip_temp = os.path.join(tempfile.gettempdir(), nome_zip)

    try:
        # Verifica se ao menos uma pasta de origem existe (separa por pipe)
        caminhos_origem = [c.strip() for c in job["pasta_origem"].split('|') if c.strip()]
        if not caminhos_origem:
            raise FileNotFoundError("Nenhuma pasta de origem definida no job.")
        
        algum_existe = any(os.path.exists(c) for c in caminhos_origem)
        if not algum_existe:
            raise FileNotFoundError(f"Nenhuma das origens foi encontrada: {job['pasta_origem']}")

        # ===== ETAPA 1: Compactar =====
        _set_progresso(job_id, "compactando", 0, "Iniciando compressao...")
        log(f"[Backup] Compactando: {job['pasta_origem']}")

        caminho_zip, total_arquivos, tamanho_zip = compactar_pasta(
            job["pasta_origem"], caminho_zip_temp, job_id
        )

        log(f"[Backup] ZIP criado: {caminho_zip} ({tamanho_zip} bytes, {total_arquivos} arquivos)")

        destino_usado = job["destino_tipo"]

        # ===== ETAPA 2: Cópia Local =====
        if job["destino_tipo"] in ("local", "ambos"):
            if job.get("destino_local"):
                _set_progresso(job_id, "copiando_local", 50, "Copiando para destino local...")
                log(f"[Backup] Copiando para: {job['destino_local']}")
                copiar_local(caminho_zip, job["destino_local"])
                _set_progresso(job_id, "copiando_local", 100, "Copia local concluida!")
                log(f"[Backup] Copia local concluida!")

        # ===== ETAPA 3: Upload Google Drive =====
        if job["destino_tipo"] in ("drive", "ambos"):
            json_path = config.get("google_json_path", "")
            drive_id = job.get("destino_drive_id")

            if not json_path:
                raise Exception("Caminho das credenciais do Google Drive nao configurado.")
            if not drive_id:
                raise Exception("Pasta de destino no Google Drive nao selecionada.")

            _set_progresso(job_id, "enviando_drive", 0, "Iniciando upload para Google Drive...")
            log(f"[Backup] Fazendo upload para Drive (pasta: {drive_id})")

            def callback_upload(progresso):
                pct = int(progresso * 100)
                _set_progresso(job_id, "enviando_drive", pct,
                              f"Upload: {pct}% ({notificador.formatar_tamanho(int(tamanho_zip * progresso))} / {notificador.formatar_tamanho(tamanho_zip)})")

            drive_client.upload_arquivo(json_path, caminho_zip, drive_id, callback_upload)
            log(f"[Backup] Upload concluido!")

        # ===== ETAPA 4: Finalizar =====
        duracao = time.time() - inicio
        _set_progresso(job_id, "concluido", 100, "Backup concluido com sucesso!")

        models.registrar_fim(exec_id, "sucesso", tamanho_zip, total_arquivos,
                           destino_usado, "Backup concluido com sucesso")

        # Notifica WhatsApp
        try:
            notificador.notificar_sucesso(
                config, job["nome"], job["pasta_origem"],
                destino_usado, tamanho_zip, total_arquivos, duracao
            )
        except Exception as ne:
            log(f"[Backup] Erro ao notificar WhatsApp: {ne}")

        log(f"[Backup] SUCESSO - Job '{job['nome']}' concluido em {duracao:.1f}s")

    except Exception as e:
        duracao = time.time() - inicio
        erro_msg = str(e)
        log(f"[Backup] ERRO - Job '{job['nome']}': {erro_msg}")

        _set_progresso(job_id, "erro", 0, f"Erro: {erro_msg}")
        models.registrar_fim(exec_id, "erro", 0, 0, job["destino_tipo"], erro_msg)

        # Notifica erro no WhatsApp
        try:
            notificador.notificar_erro(config, job["nome"], erro_msg)
        except Exception as ne:
            log(f"[Backup] Erro ao notificar WhatsApp: {ne}")

    finally:
        # Limpa ZIP temporário
        if os.path.exists(caminho_zip_temp):
            try:
                os.remove(caminho_zip_temp)
            except:
                pass

        # Limpa progresso após 10 segundos
        def limpar():
            time.sleep(10)
            _limpar_progresso(job_id)
        threading.Thread(target=limpar, daemon=True).start()


def executar_backup_async(job_id):
    """Inicia o backup em uma thread separada."""
    t = threading.Thread(target=executar_backup, args=(job_id,), daemon=True)
    t.start()
    return t
