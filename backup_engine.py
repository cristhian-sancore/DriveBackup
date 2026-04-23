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

# Sistema de fila sequencial
import queue
_fila_backup = queue.Queue()
_worker_ativo = False
_abort_flags = {}  # job_id -> threading.Event()
_job_em_execucao = None  # ID do job rodando agora


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
                # Verifica abort
                if job_id and job_id in _abort_flags and _abort_flags[job_id].is_set():
                    raise InterruptedError("Backup abortado pelo usuario.")
                
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
                        # Verifica abort
                        if job_id and job_id in _abort_flags and _abort_flags[job_id].is_set():
                            raise InterruptedError("Backup abortado pelo usuario.")
                        
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
    
    # Verificação de integridade do ZIP
    log(f"[ZIP] Verificando integridade do arquivo...")
    with zipfile.ZipFile(caminho_zip, 'r') as zf:
        resultado = zf.testzip()
        if resultado is not None:
            raise ValueError(f"Arquivo ZIP corrompido! Primeiro arquivo com erro: {resultado}")
    log(f"[ZIP] Integridade OK!")
    
    return caminho_zip, total_arquivos, tamanho


def verificar_espaco_disco(caminho, tamanho_necessario):
    """Verifica se há espaço suficiente no disco para o backup."""
    try:
        total, usado, livre = shutil.disk_usage(os.path.splitdrive(caminho)[0] or caminho)
        # Precisa de pelo menos 2x o tamanho estimado (margem de segurança)
        if livre < tamanho_necessario * 2:
            livre_mb = livre / (1024 * 1024)
            necessario_mb = (tamanho_necessario * 2) / (1024 * 1024)
            raise IOError(f"Espaco insuficiente no disco! Livre: {livre_mb:.0f}MB, Necessario: {necessario_mb:.0f}MB")
        return True
    except IOError:
        raise
    except Exception:
        return True  # Se não conseguir verificar, continua


def limpar_backups_antigos(job, config):
    """Remove backups antigos conforme a política de retenção do job."""
    retencao = job.get("retencao", 0)
    if retencao <= 0:
        return  # Sem limite = manter todos
    
    nome_safe = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in job['nome'])
    
    # Limpa backups locais
    if job["destino_tipo"] in ("local", "ambos") and job.get("destino_local"):
        pasta = job["destino_local"]
        if os.path.exists(pasta):
            arquivos = sorted(
                [f for f in os.listdir(pasta) if f.startswith(nome_safe) and f.endswith('.zip')],
                reverse=True  # Mais recente primeiro
            )
            excedentes = arquivos[retencao:]
            for arq in excedentes:
                try:
                    os.remove(os.path.join(pasta, arq))
                    log(f"[Retencao] Backup antigo removido: {arq}")
                except Exception as e:
                    log(f"[Retencao] Erro ao remover {arq}: {e}")
    
    # Limpa backups no Google Drive
    if job["destino_tipo"] in ("drive", "ambos") and job.get("destino_drive_id"):
        try:
            json_path = config.get("google_json_path", "")
            drive_id = job.get("destino_drive_id")
            arquivos_drive = drive_client.listar_arquivos_pasta(json_path, drive_id)
            
            # Filtra apenas os ZIPs deste job
            zips_job = sorted(
                [f for f in arquivos_drive if f['name'].startswith(nome_safe) and f['name'].endswith('.zip')],
                key=lambda x: x.get('createdTime', ''),
                reverse=True
            )
            excedentes = zips_job[retencao:]
            for arq in excedentes:
                try:
                    drive_client.excluir_arquivo(json_path, arq['id'])
                    log(f"[Retencao] Backup antigo removido do Drive: {arq['name']}")
                except Exception as e:
                    log(f"[Retencao] Erro ao remover do Drive {arq['name']}: {e}")
        except Exception as e:
            log(f"[Retencao] Erro ao limpar Drive: {e}")


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
        # Verifica espaço em disco antes de iniciar
        _set_progresso(job_id, "verificando", 0, "Verificando espaco em disco...")
        try:
            # Estima tamanho baseado na origem
            tamanho_estimado = 0
            for c in caminhos_origem:
                if os.path.isfile(c):
                    tamanho_estimado += os.path.getsize(c)
                elif os.path.isdir(c):
                    for root, dirs, files in os.walk(c):
                        for f in files:
                            try:
                                tamanho_estimado += os.path.getsize(os.path.join(root, f))
                            except:
                                pass
            verificar_espaco_disco(tempfile.gettempdir(), tamanho_estimado)
        except IOError as e:
            raise Exception(str(e))

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

        # ===== ETAPA 5: Retenção =====
        try:
            _set_progresso(job_id, "limpando", 95, "Aplicando politica de retencao...")
            limpar_backups_antigos(job, config)
        except Exception as re:
            log(f"[Backup] Erro na retencao: {re}")

    except Exception as e:
        duracao = time.time() - inicio
        erro_msg = str(e)
        
        # Verifica se foi um abort
        abortado = job_id in _abort_flags and _abort_flags[job_id].is_set()
        if abortado:
            erro_msg = "Backup abortado pelo usuario."
            _set_progresso(job_id, "abortado", 0, erro_msg)
            models.registrar_fim(exec_id, "abortado", 0, 0, job["destino_tipo"], erro_msg)
        else:
            _set_progresso(job_id, "erro", 0, f"Erro: {erro_msg}")
            models.registrar_fim(exec_id, "erro", 0, 0, job["destino_tipo"], erro_msg)
        
        log(f"[Backup] {'ABORTADO' if abortado else 'ERRO'} - Job '{job['nome']}': {erro_msg}")

        # Notifica erro no WhatsApp (só se não foi abort manual)
        if not abortado:
            try:
                notificador.notificar_erro(config, job["nome"], erro_msg)
            except Exception as ne:
                log(f"[Backup] Erro ao notificar WhatsApp: {ne}")

    finally:
        global _job_em_execucao
        _job_em_execucao = None
        _abort_flags.pop(job_id, None)
        
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


def _worker_fila():
    """Worker que processa a fila de backups sequencialmente."""
    global _worker_ativo, _job_em_execucao
    _worker_ativo = True
    
    while not _fila_backup.empty():
        try:
            job_id = _fila_backup.get_nowait()
        except queue.Empty:
            break
        
        _job_em_execucao = job_id
        _abort_flags[job_id] = threading.Event()
        
        log(f"[Fila] Iniciando backup do job {job_id} (restam {_fila_backup.qsize()} na fila)")
        executar_backup(job_id)
        _fila_backup.task_done()
    
    _worker_ativo = False
    _job_em_execucao = None
    log("[Fila] Todos os backups da fila foram concluidos.")


def executar_backup_async(job_id):
    """Adiciona o job na fila de backups. Se o worker não estiver rodando, inicia."""
    global _worker_ativo
    
    _fila_backup.put(job_id)
    log(f"[Fila] Job {job_id} adicionado a fila (posicao: {_fila_backup.qsize()})")
    
    if not _worker_ativo:
        t = threading.Thread(target=_worker_fila, daemon=True)
        t.start()
    
    return None


def abortar_backup(job_id):
    """Aborta o backup de um job em execução."""
    if job_id in _abort_flags:
        _abort_flags[job_id].set()
        log(f"[Abort] Sinal de abort enviado para o job {job_id}")
        return True
    return False


def obter_job_em_execucao():
    """Retorna o ID do job que está rodando no momento."""
    return _job_em_execucao


def obter_fila():
    """Retorna informações da fila de backups."""
    return {
        "em_execucao": _job_em_execucao,
        "tamanho_fila": _fila_backup.qsize(),
        "worker_ativo": _worker_ativo
    }
