"""
drive_client.py - Wrapper do Google Drive API v3
Suporta OAuth2 (conta pessoal) e Service Account (Google Workspace)
"""
import os
import json
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

SCOPES = ['https://www.googleapis.com/auth/drive']
import sys

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TOKEN_FILE = os.path.join(BASE_DIR, "data", "token.json")

_service = None


def autenticar(json_path):
    """
    Autentica com o Google Drive.
    Detecta automaticamente se é OAuth2 (credentials.json) ou Service Account.
    """
    global _service
    if not json_path or not os.path.exists(json_path):
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
        # Tenta carregar token salvo
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        
        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        
        # Se não tem token ou expirou
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                # Renova o token automaticamente
                creds.refresh(Request())
            else:
                # Primeira vez: abre o navegador para autorizar
                flow = InstalledAppFlow.from_client_secrets_file(
                    json_path, SCOPES)
                creds = flow.run_local_server(
                    port=0,
                    prompt='consent',
                    access_type='offline'
                )
            
            # Salva o token para não precisar autorizar novamente
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
    
    _service = build('drive', 'v3', credentials=creds)
    return _service


def get_service(json_path=None):
    """Retorna o service do Drive, autenticando se necessário."""
    global _service
    if _service is None and json_path:
        autenticar(json_path)
    if _service is None:
        raise Exception("Google Drive nao autenticado. Configure as credenciais.")
    return _service


def testar_conexao(json_path):
    """Testa se as credenciais funcionam."""
    try:
        global _service
        _service = None  # Força re-autenticação
        service = autenticar(json_path)
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
        print(f"Erro ao listar pastas: {e}")
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
        print(f"Erro ao montar arvore: {e}")
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


def upload_arquivo(json_path, caminho_local, parent_id, callback=None):
    """
    Faz upload de um arquivo para o Google Drive com progresso.
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
        while response is None:
            status, response = request.next_chunk()
            if status and callback:
                callback(status.progress())
        
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
