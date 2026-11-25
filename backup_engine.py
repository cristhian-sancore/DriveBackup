"""
backup_engine.py - Motor de backup (compressão, cópia local, upload Drive)
"""
import os
import sys
import io
import fnmatch
import subprocess
import zipfile
import shutil
import time
import tempfile
import threading
from datetime import datetime, timedelta

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
_jobs_na_fila = set()
_worker_ativo = False
_worker_lock = threading.Lock()
_abort_flags = {}  # job_id -> threading.Event()
_job_em_execucao = None  # ID do job rodando agora

# Timeout máximo para cada backup (em segundos) - 12 horas
TIMEOUT_BACKUP_SEGUNDOS = 43200


from logger import app_logger

def log(msg):
    """Loga a mensagem no arquivo e no console."""
    app_logger.info(msg)


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


def _verificar_timeout(inicio, job_id=None):
    """Verifica se o backup excedeu o tempo limite. Levanta TimeoutError se sim."""
    decorrido = time.time() - inicio
    if decorrido > TIMEOUT_BACKUP_SEGUNDOS:
        minutos = decorrido / 60
        raise TimeoutError(
            f"Backup excedeu o limite de {TIMEOUT_BACKUP_SEGUNDOS // 60} minutos "
            f"({minutos:.0f} min decorridos). Processo cancelado automaticamente."
        )


def _parse_exclusoes(exclusoes_str):
    if not exclusoes_str:
        return []
    return [p.strip() for p in exclusoes_str.replace(",", ";").split(";") if p.strip()]


def _deve_excluir(nome, rel_path, padroes):
    if not padroes:
        return False
    nome_l = nome.lower()
    rel_l = rel_path.replace("\\", "/").lower()
    for p in padroes:
        pl = p.lower().rstrip("/\\")
        if fnmatch.fnmatch(nome_l, pl) or fnmatch.fnmatch(rel_l, pl):
            return True
    return False


def contar_arquivos(caminhos_str, exclusoes_str=""):
    """Conta todos os arquivos, suportando múltiplos caminhos separados por pipe (|) e filtros de exclusão."""
    total = 0
    padroes = _parse_exclusoes(exclusoes_str)
    caminhos = [c.strip() for c in caminhos_str.split('|') if c.strip()]
    
    for caminho in caminhos:
        if os.path.isfile(caminho):
            if not _deve_excluir(os.path.basename(caminho), os.path.basename(caminho), padroes):
                total += 1
        elif os.path.exists(caminho):
            for root, dirs, files in os.walk(caminho):
                dirs[:] = [d for d in dirs if not _deve_excluir(d, os.path.relpath(os.path.join(root, d), caminho), padroes)]
                for f in files:
                    rel = os.path.relpath(os.path.join(root, f), caminho)
                    if not _deve_excluir(f, rel, padroes):
                        total += 1
    return total


def _abrir_zip_escrita(caminho_zip, zip_senha=""):
    if zip_senha:
        import pyzipper
        zf = pyzipper.AESZipFile(
            caminho_zip, 'w',
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES
        )
        zf.setpassword(zip_senha.encode("utf-8"))
        return zf, True
    return zipfile.ZipFile(caminho_zip, 'w', zipfile.ZIP_DEFLATED, allowZip64=True), False


def compactar_pasta(origens_str, caminho_zip, job_id=None, inicio=None, exclusoes_str="", zip_senha=""):
    """
    Compacta as origens dadas em um arquivo ZIP (com suporte a exclusões e senha AES).
    Retorna (caminho_zip, total_arquivos, tamanho_bytes, arquivos_ignorados).
    """
    padroes = _parse_exclusoes(exclusoes_str)
    total_arquivos = contar_arquivos(origens_str, exclusoes_str)
    if total_arquivos == 0:
        raise ValueError(f"As origens ('{origens_str}') estao vazias ou inacessiveis. Nenhum arquivo encontrado.")
    
    processados = 0
    ignorados = 0
    caminhos = [c.strip() for c in origens_str.split('|') if c.strip()]

    zf, usar_aes = _abrir_zip_escrita(caminho_zip, zip_senha)
    with zf:
        for caminho_origem in caminhos:
            if not os.path.exists(caminho_origem):
                log(f"[ZIP] Aviso: Caminho nao encontrado: {caminho_origem}")
                continue

            # Se for um unico arquivo
            if os.path.isfile(caminho_origem):
                if _deve_excluir(os.path.basename(caminho_origem), os.path.basename(caminho_origem), padroes):
                    continue
                if job_id and job_id in _abort_flags and _abort_flags[job_id].is_set():
                    raise InterruptedError("Backup abortado pelo usuario.")
                if inicio:
                    _verificar_timeout(inicio, job_id)
                
                arcname = os.path.basename(caminho_origem)
                try:
                    if usar_aes:
                        zf.write(caminho_origem, arcname)
                    else:
                        zinfo = zipfile.ZipInfo.from_file(caminho_origem, arcname)
                        zinfo.compress_type = zipfile.ZIP_DEFLATED
                        with zf.open(zinfo, 'w') as dest, open(caminho_origem, 'rb') as src:
                            while True:
                                if job_id and job_id in _abort_flags and _abort_flags[job_id].is_set():
                                    raise InterruptedError("Backup abortado pelo usuario.")
                                if inicio:
                                    _verificar_timeout(inicio, job_id)
                                chunk = src.read(1024 * 1024 * 5) # 5MB
                                if not chunk: break
                                dest.write(chunk)
                except (PermissionError, OSError) as e:
                    ignorados += 1
                    log(f"[ZIP] Nao foi possivel compactar {caminho_origem}: {e}")
                
                processados += 1
                if job_id and total_arquivos > 0:
                    pct = int((processados / total_arquivos) * 100)
                    _set_progresso(job_id, "compactando", pct, 
                                  f"Compactando {processados}/{total_arquivos} arquivos", arcname)
            
            # Se for uma pasta
            else:
                for root, dirs, files in os.walk(caminho_origem):
                    dirs[:] = [d for d in dirs if not _deve_excluir(d, os.path.relpath(os.path.join(root, d), caminho_origem), padroes)]
                    for arquivo in files:
                        rel_local = os.path.relpath(os.path.join(root, arquivo), caminho_origem)
                        if _deve_excluir(arquivo, rel_local, padroes):
                            continue
                        if job_id and job_id in _abort_flags and _abort_flags[job_id].is_set():
                            raise InterruptedError("Backup abortado pelo usuario.")
                        if inicio:
                            _verificar_timeout(inicio, job_id)
                        
                        caminho_completo = os.path.join(root, arquivo)
                        arcname = rel_local
                        
                        # Evita caminhos duplicados na raiz do zip se for mais de uma pasta
                        if len(caminhos) > 1:
                            arcname = os.path.join(os.path.basename(caminho_origem), arcname)

                        try:
                            if usar_aes:
                                zf.write(caminho_completo, arcname)
                            else:
                                zinfo = zipfile.ZipInfo.from_file(caminho_completo, arcname)
                                zinfo.compress_type = zipfile.ZIP_DEFLATED
                                with zf.open(zinfo, 'w') as dest, open(caminho_completo, 'rb') as src:
                                    while True:
                                        if job_id and job_id in _abort_flags and _abort_flags[job_id].is_set():
                                            raise InterruptedError("Backup abortado pelo usuario.")
                                        if inicio:
                                            _verificar_timeout(inicio, job_id)
                                        chunk = src.read(1024 * 1024 * 5) # 5MB
                                        if not chunk: break
                                        dest.write(chunk)
                        except (PermissionError, OSError) as e:
                            ignorados += 1
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
    if usar_aes:
        import pyzipper
        with pyzipper.AESZipFile(caminho_zip, 'r') as zf_check:
            zf_check.setpassword(zip_senha.encode("utf-8"))
            resultado = zf_check.testzip()
    else:
        with zipfile.ZipFile(caminho_zip, 'r') as zf_check:
            resultado = zf_check.testzip()
    if resultado is not None:
        raise ValueError(f"Arquivo ZIP corrompido! Primeiro arquivo com erro: {resultado}")
    log(f"[ZIP] Integridade OK!")
    
    return caminho_zip, total_arquivos, tamanho, ignorados


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
    """Remove backups antigos conforme a política de retenção (quantidade e/ou dias) do job."""
    retencao = int(job.get("retencao") or 0)
    retencao_dias = int(job.get("retencao_dias") or 0)
    if retencao <= 0 and retencao_dias <= 0:
        return  # Sem limite = manter todos
    
    nome_safe = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in job['nome'])
    limite_data = datetime.now() - timedelta(days=retencao_dias) if retencao_dias > 0 else None
    
    # Limpa backups locais
    if job["destino_tipo"] in ("local", "ambos") and job.get("destino_local"):
        pasta = job["destino_local"]
        if os.path.exists(pasta):
            arquivos = sorted(
                [f for f in os.listdir(pasta) if f.startswith(nome_safe) and f.endswith('.zip')],
                reverse=True  # Mais recente primeiro
            )
            para_remover = set(arquivos[retencao:]) if retencao > 0 else set()
            if limite_data:
                for arq in arquivos:
                    caminho_arq = os.path.join(pasta, arq)
                    try:
                        if datetime.fromtimestamp(os.path.getmtime(caminho_arq)) < limite_data:
                            para_remover.add(arq)
                    except OSError:
                        pass
            for arq in sorted(para_remover):
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
            ids_remover = {a['id']: a for a in zips_job[retencao:]} if retencao > 0 else {}
            if limite_data:
                for arq in zips_job:
                    ct = arq.get('createdTime', '')
                    if ct:
                        try:
                            dt_arq = datetime.fromisoformat(ct.replace('Z', '+00:00')).replace(tzinfo=None)
                            if dt_arq < limite_data:
                                ids_remover[arq['id']] = arq
                        except ValueError:
                            pass
            for fid, arq in ids_remover.items():
                try:
                    drive_client.excluir_arquivo(json_path, fid)
                    log(f"[Retencao] Backup antigo removido do Drive: {arq['name']}")
                except Exception as e:
                    log(f"[Retencao] Erro ao remover do Drive {arq['name']}: {e}")
        except Exception as e:
            log(f"[Retencao] Erro ao limpar Drive: {e}")


def copiar_local(caminho_zip, pasta_destino, job_id=None):
    """Copia o ZIP para a pasta de destino local (em chunks para permitir abort)."""
    os.makedirs(pasta_destino, exist_ok=True)
    destino = os.path.join(pasta_destino, os.path.basename(caminho_zip))
    
    with open(caminho_zip, 'rb') as src, open(destino, 'wb') as dst:
        while True:
            if job_id and job_id in _abort_flags and _abort_flags[job_id].is_set():
                dst.close()
                if os.path.exists(destino):
                    try:
                        os.remove(destino)
                    except: pass
                raise InterruptedError("Backup abortado pelo usuario.")
            
            chunk = src.read(1024 * 1024 * 10) # 10MB
            if not chunk: break
            dst.write(chunk)
            
    shutil.copystat(caminho_zip, destino)
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
        # ===== ETAPA 0: Pré-comando (ex: gbak Firebird) =====
        pre_cmd = (job.get("pre_comando") or "").strip()
        if pre_cmd:
            _set_progresso(job_id, "verificando", 0, "Executando pré-comando...")
            log(f"[Backup] Executando pre-comando: {pre_cmd}")
            proc = subprocess.run(
                pre_cmd, shell=True, capture_output=True, text=True, timeout=3600
            )
            if proc.returncode != 0:
                saida_err = (proc.stderr or proc.stdout or "").strip()
                raise RuntimeError(f"Pré-comando falhou (código {proc.returncode}): {saida_err[:300]}")
            log("[Backup] Pre-comando concluido com sucesso.")

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

        caminho_zip, total_arquivos, tamanho_zip, arquivos_ignorados = compactar_pasta(
            job["pasta_origem"], caminho_zip_temp, job_id, inicio,
            exclusoes_str=job.get("exclusoes", ""),
            zip_senha=job.get("zip_senha", "")
        )
        _verificar_timeout(inicio, job_id)  # Verifica timeout após compactação

        log(f"[Backup] ZIP criado: {caminho_zip} ({tamanho_zip} bytes, {total_arquivos} arquivos, {arquivos_ignorados} ignorados)")

        destino_usado = job["destino_tipo"]

        # ===== ETAPA 2: Cópia Local =====
        if job["destino_tipo"] in ("local", "ambos"):
            if job.get("destino_local"):
                _set_progresso(job_id, "copiando_local", 50, "Copiando para destino local...")
                log(f"[Backup] Copiando para: {job['destino_local']}")
                copiar_local(caminho_zip, job["destino_local"], job_id)
                _verificar_timeout(inicio, job_id)  # Verifica timeout após cópia local
                _set_progresso(job_id, "copiando_local", 100, "Copia local concluida!")
                log(f"[Backup] Copia local concluida!")

        # ===== ETAPA 3: Upload Google Drive =====
        if job["destino_tipo"] in ("drive", "ambos"):
            _verificar_timeout(inicio, job_id)  # Verifica timeout antes do upload
            json_path = config.get("google_json_path", "")
            drive_id = job.get("destino_drive_id")

            if not json_path:
                raise Exception("Caminho das credenciais do Google Drive nao configurado.")
            if not drive_id:
                raise Exception("Pasta de destino no Google Drive nao selecionada.")

            # Verifica cota disponível no Google Drive antes do upload
            try:
                cota = drive_client.obter_info_cota(json_path)
                livre_drive = cota.get("livre_bytes")
                if livre_drive is not None and tamanho_zip > livre_drive:
                    raise IOError(
                        f"Espaco insuficiente no Google Drive! Livre: {notificador.formatar_tamanho(livre_drive)}, "
                        f"Necessario: {notificador.formatar_tamanho(tamanho_zip)}"
                    )
            except IOError:
                raise
            except Exception as ce:
                log(f"[Backup] Aviso: nao foi possivel verificar cota do Drive: {ce}")

            _set_progresso(job_id, "enviando_drive", 0, "Iniciando upload para Google Drive...")
            log(f"[Backup] Fazendo upload para Drive (pasta: {drive_id})")

            # Calcula tempo restante para o upload (desconta o que já gastou)
            tempo_restante = max(60, TIMEOUT_BACKUP_SEGUNDOS - (time.time() - inicio))

            def callback_upload(progresso):
                if job_id and job_id in _abort_flags and _abort_flags[job_id].is_set():
                    raise InterruptedError("Backup abortado pelo usuario.")
                    
                pct = int(progresso * 100)
                _set_progresso(job_id, "enviando_drive", pct,
                              f"Upload: {pct}% ({notificador.formatar_tamanho(int(tamanho_zip * progresso))} / {notificador.formatar_tamanho(tamanho_zip)})")

            drive_client.upload_arquivo(json_path, caminho_zip, drive_id, callback_upload, timeout_segundos=int(tempo_restante))
            log(f"[Backup] Upload concluido!")

        # ===== ETAPA 4: Finalizar =====
        duracao = time.time() - inicio
        msg_final = "Backup concluido com sucesso"
        if arquivos_ignorados > 0:
            msg_final = f"Backup concluido com avisos: {arquivos_ignorados} arquivo(s) bloqueado(s)/inacessivel(is) ignorado(s)"
        _set_progresso(job_id, "concluido", 100, msg_final)

        models.registrar_fim(exec_id, "sucesso", tamanho_zip, total_arquivos - arquivos_ignorados,
                           destino_usado, msg_final)

        # Notifica WhatsApp
        try:
            notificador.notificar_sucesso(
                config, job["nome"], job["pasta_origem"],
                destino_usado, tamanho_zip, total_arquivos - arquivos_ignorados, duracao
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

    except TimeoutError as te:
        duracao = time.time() - inicio
        erro_msg = str(te)
        duracao_min = duracao / 60
        
        _set_progresso(job_id, "timeout", 0, f"TIMEOUT: {erro_msg}")
        models.registrar_fim(exec_id, "timeout", 0, 0, job["destino_tipo"], f"TIMEOUT: {erro_msg}")
        log(f"[Backup] TIMEOUT - Job '{job['nome']}': {erro_msg} (durou {duracao_min:.0f} min)")

        # Notifica timeout no WhatsApp
        try:
            notificador.notificar_timeout(config, job["nome"], duracao_min, limite_horas=TIMEOUT_BACKUP_SEGUNDOS // 3600)
        except Exception as ne:
            log(f"[Backup] Erro ao notificar WhatsApp (timeout): {ne}")

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
        
        with _worker_lock:
            _jobs_na_fila.discard(job_id)
            _job_em_execucao = job_id
        _abort_flags[job_id] = threading.Event()
        
        log(f"[Fila] Iniciando backup do job {job_id} (restam {_fila_backup.qsize()} na fila)")
        executar_backup(job_id)
        _fila_backup.task_done()
    with _worker_lock:
        _worker_ativo = False
        _job_em_execucao = None
    log("[Fila] Todos os backups da fila foram concluidos.")


def executar_backup_async(job_id):
    """Adiciona o job na fila de backups sem duplicar se já estiver rodando ou na fila."""
    global _worker_ativo
    
    with _worker_lock:
        if job_id == _job_em_execucao or job_id in _jobs_na_fila:
            log(f"[Fila] Job {job_id} ja esta em execucao ou aguardando na fila. Ignorando duplicata.")
            return None
        _jobs_na_fila.add(job_id)
        _fila_backup.put(job_id)
        log(f"[Fila] Job {job_id} adicionado a fila (posicao: {_fila_backup.qsize()})")
        
        if not _worker_ativo:
            _worker_ativo = True
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


def abortar_todos_os_backups():
    """Aborta todos os backups em andamento ou na fila durante parada ou reinício do serviço."""
    with _worker_lock:
        _jobs_na_fila.clear()
        while not _fila_backup.empty():
            try:
                _fila_backup.get_nowait()
                _fila_backup.task_done()
            except Exception:
                break
            
    for job_id, event in list(_abort_flags.items()):
        event.set()
        log(f"[Shutdown] Sinal de abort enviado forçadamente para o job {job_id}")
        
    conn = None
    try:
        conn = models.get_db()
        conn.execute("""
            UPDATE execucoes 
            SET status='abortado', fim=?, mensagem='Backup abortado pelo reinicio ou parada do servico.'
            WHERE status='em_andamento'
        """, (datetime.now().isoformat(),))
        conn.commit()
    except Exception as e:
        log(f"[Shutdown] Erro ao atualizar status no banco: {e}")
    finally:
        if conn:
            conn.close()


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
