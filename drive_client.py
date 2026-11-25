"""
drive_client.py - Wrapper do Google Drive API v3
Suporta OAuth2 (conta pessoal) e Service Account (Google Workspace)
"""
import os

from logger import app_logger
import json
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError

SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/userinfo.email', 'openid']
import sys

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TOKEN_FILE = os.path.join(BASE_DIR, "data", "token.json")

_service = None


def autenticar(json_path, flow_web=False):
    """
    Autentica com o Google Drive.
    Detecta automaticamente se é OAuth2 (credentials.json) ou Service Account.
    """
    global _service
    if not json_path or not os.path.exists(json_path):
        # Se não tem JSON, tenta carregar o token existente silenciosamente
        if os.path.exists(TOKEN_FILE):
            try:
                creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                    with open(TOKEN_FILE, 'w') as token:
                        token.write(creds.to_json())
                _service = build('drive', 'v3', credentials=creds)
                return _service
            except Exception as e:
                if "invalid_grant" in str(e).lower():
                    app_logger.info(f"[DRIVE] Token existente invalidado (invalid_grant). Removendo {TOKEN_FILE}")
                    if os.path.exists(TOKEN_FILE):
                        try: os.remove(TOKEN_FILE)
                        except: pass
                    
                    try:
                        import models, notificador
                        config = models.obter_config()
                        if not flow_web:
                            notificador.notificar_auth_necessaria(config)
                    except Exception as ex:
                        app_logger.info("Erro ao tentar enviar alerta de WhatsApp de auth:", ex)
                else:
                    app_logger.info(f"Erro ao carregar token existente: {e}")
        
        raise FileNotFoundError(f"Arquivo de credenciais nao encontrado: {json_path}")
    
    # Lê o JSON para detectar o tipo
    with open(json_path, 'r') as f:
        cred_data = json.load(f)
    
    creds = None
    
    if cred_data.get("type") == "service_account":
        # ===== Service Account =====
        creds = service_account.Credentials.from_service_account_file(json_path, scopes=SCOPES)
    else:
        # ===== OAuth2 (Desktop App) =====
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        
        if os.path.exists(TOKEN_FILE):
            try:
                creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            except:
                creds = None
        
        # Se expirou, tenta renovar e SALVAR
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(TOKEN_FILE, 'w') as token:
                    token.write(creds.to_json())
                app_logger.info("[DRIVE] Token renovado e salvo com sucesso.")
            except Exception as e:
                app_logger.info(f"[DRIVE] Erro ao renovar token: {e}")
                if "invalid_grant" in str(e).lower():
                    if os.path.exists(TOKEN_FILE): os.remove(TOKEN_FILE)
                
                try:
                    import models, notificador
                    config = models.obter_config()
                    if not flow_web:
                        notificador.notificar_auth_necessaria(config)
                except Exception as ex:
                    app_logger.info("Erro ao tentar enviar alerta de WhatsApp de auth:", ex)
                creds = None

        # Se não é válido, precisamos de nova autenticação
        if not creds or not creds.valid:
            if flow_web:
                # O app.py tratará o fluxo web
                return None
            
            # Fluxo Desktop (Navegador Local)
            flow = InstalledAppFlow.from_client_secrets_file(json_path, SCOPES)
            creds = flow.run_local_server(port=0, prompt='consent', access_type='offline')
            
            # Salva o novo token
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
    
    _service = build('drive', 'v3', credentials=creds)
    return _service


def get_service(json_path=None):
    """Retorna o service do Drive, autenticando se necessário."""
    global _service
    if _service:
        # Verifica se o serviço atual ainda é válido (token não expirou)
        try:
            if _service._http.credentials and _service._http.credentials.expired:
                 _service = None # Força re-autenticação
        except:
            pass

    if _service is None:
        autenticar(json_path)
        
    if _service is None:
        raise Exception("Google Drive nao autenticado. Configure as credenciais.")
    return _service


def obter_info_cota(json_path):
    """Retorna informações da conta e cota de armazenamento do Google Drive."""
    service = get_service(json_path)
    about = service.about().get(fields="user, storageQuota").execute()
    email = about.get("user", {}).get("emailAddress", "Desconhecido")
    quota = about.get("storageQuota", {})
    usado = int(quota.get("usage", 0))
    limite = int(quota.get("limit", 0)) if quota.get("limit") else 0
    livre = max(0, limite - usado) if limite > 0 else None
    return {
        "email": email,
        "usado_bytes": usado,
        "limite_bytes": limite,
        "livre_bytes": livre,
    }


def testar_conexao(json_path, flow_web=False):
    """Testa se as credenciais funcionam."""
    try:
        global _service
        _service = None  # Força re-autenticação
        service = autenticar(json_path, flow_web=flow_web)
        if service is None:
            return False, "Google Drive não autenticado. Realize o login pelo painel."
        about = service.about().get(fields="user, storageQuota").execute()
        email = about.get("user", {}).get("emailAddress", "Desconhecido")
        quota = about.get("storageQuota", {})
        
        # Mostra espaço disponível
        if quota.get("limit"):
            usado = int(quota.get("usage", 0)) / (1024**3)
            total = int(quota.get("limit", 0)) / (1024**3)
            return True, f"Conectado como: {email} ({usado:.1f}GB / {total:.1f}GB)"
        
        return True, f"Conectado como: {email}"
    except Exception as e:
        return False, str(e)


def listar_pastas(json_path, parent_id=None):
    """Lista as subpastas diretas de uma pasta no Drive."""
    service = get_service(json_path)
    
    if not parent_id or parent_id == "root":
        query = "mimeType='application/vnd.google-apps.folder' and trashed=false and 'root' in parents"
    else:
        query = f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    
    try:
        all_folders = []
        page_token = None
        while True:
            results = service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, parents)",
                orderBy="name",
                pageSize=200,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            
            all_folders.extend(results.get("files", []))
            page_token = results.get("nextPageToken")
            if not page_token:
                break
        
        return [{"id": f["id"], "name": f["name"], "parents": f.get("parents", [])} for f in all_folders]
    except HttpError as e:
        app_logger.info(f"Erro ao listar pastas: {e}")
        return []


def listar_arvore_completa(json_path):
    """
    Busca TODAS as pastas acessíveis e monta a árvore hierárquica.
    """
    service = get_service(json_path)
    query = "mimeType='application/vnd.google-apps.folder' and trashed=false"
    
    try:
        all_folders = []
        page_token = None
        while True:
            results = service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, parents)",
                orderBy="name",
                pageSize=500,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            
            all_folders.extend(results.get("files", []))
            page_token = results.get("nextPageToken")
            if not page_token:
                break
        
        # Monta mapa de id -> pasta
        folder_map = {}
        for f in all_folders:
            folder_map[f["id"]] = {
                "id": f["id"],
                "name": f["name"],
                "parents": f.get("parents", []),
                "children": []
            }
        
        # Identifica raízes
        roots = []
        all_ids = set(folder_map.keys())
        
        for fid, folder in folder_map.items():
            parent_ids = folder["parents"]
            if not parent_ids or not any(pid in all_ids for pid in parent_ids):
                roots.append(folder)
            else:
                for pid in parent_ids:
                    if pid in folder_map:
                        folder_map[pid]["children"].append(folder)
                        break
        
        def sort_tree(nodes):
            nodes.sort(key=lambda x: x["name"].lower())
            for node in nodes:
                sort_tree(node["children"])
        
        sort_tree(roots)
        
        return roots
    except HttpError as e:
        app_logger.info(f"Erro ao montar arvore: {e}")
        return []


def criar_pasta(json_path, nome, parent_id=None):
    """Cria uma nova pasta no Drive."""
    service = get_service(json_path)
    
    metadata = {
        "name": nome,
        "mimeType": "application/vnd.google-apps.folder"
    }
    if parent_id and parent_id != "root":
        metadata["parents"] = [parent_id]
    
    try:
        folder = service.files().create(
            body=metadata,
            fields="id, name",
            supportsAllDrives=True
        ).execute()
        return {"id": folder["id"], "name": folder["name"]}
    except HttpError as e:
        raise Exception(f"Erro ao criar pasta: {e}")


def upload_arquivo(json_path, caminho_local, parent_id, callback=None, timeout_segundos=3600):
    """
    Faz upload de um arquivo para o Google Drive com progresso.
    timeout_segundos: Limite de tempo total para o upload (padrão: 3600s = 1 hora).
    """
    service = get_service(json_path)
    
    if not parent_id or parent_id == "root":
        raise Exception("Pasta de destino no Drive e obrigatoria.")
    
    nome_arquivo = os.path.basename(caminho_local)
    tamanho = os.path.getsize(caminho_local)
    
    metadata = {
        "name": nome_arquivo,
        "parents": [parent_id]
    }
    
    # Chunk de 10MB para uploads grandes
    chunk_size = 10 * 1024 * 1024
    
    media = MediaFileUpload(
        caminho_local,
        mimetype="application/zip",
        resumable=True,
        chunksize=chunk_size
    )
    
    try:
        request = service.files().create(
            body=metadata,
            media_body=media,
            fields="id, name, size",
            supportsAllDrives=True
        )
        
        response = None
        retry_count = 0
        max_retries = 5
        import time
        upload_inicio = time.time()
        
        while response is None:
            # Verifica timeout global do upload
            tempo_decorrido = time.time() - upload_inicio
            if tempo_decorrido > timeout_segundos:
                raise TimeoutError(
                    f"Upload excedeu o limite de {timeout_segundos // 60} minutos "
                    f"({tempo_decorrido / 60:.0f} min decorridos). "
                    f"Arquivo: {nome_arquivo} ({tamanho / 1024 / 1024:.1f} MB)"
                )
            
            try:
                status, response = request.next_chunk()
                if status and callback:
                    callback(status.progress())
                retry_count = 0 # Reseta retries ao ter sucesso em um chunk
            except (ConnectionError, OSError, IOError) as e:
                # Erros de rede/conexão - retry com backoff
                retry_count += 1
                if retry_count > max_retries:
                    raise Exception(f"Erro de conexao no upload apos {max_retries} tentativas: {e}")
                time.sleep(2 * retry_count)
                continue
            except Exception as e:
                # Captura erro de SSL/EOF ou timeout
                if ("EOF" in str(e) or "violation of protocol" in str(e) or "timeout" in str(e).lower()) and retry_count < max_retries:
                    retry_count += 1
                    time.sleep(2 * retry_count) # Backoff exponencial simples
                    continue
                raise Exception(f"Erro no upload após {retry_count} tentativas: {e}")
        
        if callback:
            callback(1.0)
        
        return {
            "id": response["id"],
            "name": response["name"],
            "size": response.get("size", tamanho)
        }
    except HttpError as e:
        raise Exception(f"Erro no upload: {e}")


def deletar_arquivo(json_path, file_id):
    """Remove arquivo ou pasta do Drive."""
    service = get_service(json_path)
    try:
        service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
        return True
    except HttpError as e:
        raise Exception(f"Erro ao deletar: {e}")


def listar_arquivos_pasta(json_path, folder_id):
    """Lista todos os arquivos dentro de uma pasta do Drive (mais recentes primeiro)."""
    service = get_service(json_path)
    try:
        results = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id, name, createdTime, size, mimeType)",
            orderBy="createdTime desc",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=200
        ).execute()
        return results.get("files", [])
    except HttpError as e:
        raise Exception(f"Erro ao listar arquivos: {e}")


def baixar_arquivo(json_path, file_id):
    """Obtém metadados e generator de chunks para download direto de um arquivo do Drive."""
    service = get_service(json_path)
    meta = service.files().get(
        fileId=file_id,
        fields="id, name, size, mimeType",
        supportsAllDrives=True
    ).execute()
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)

    import io
    def stream_chunks():
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request, chunksize=5 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()
            buffer.seek(0)
            data = buffer.read()
            if data:
                yield data
            buffer.seek(0)
            buffer.truncate(0)

    return meta, stream_chunks()


def excluir_arquivo(json_path, file_id):
    """Alias para deletar_arquivo (usado pela retenção)."""
    return deletar_arquivo(json_path, file_id)
