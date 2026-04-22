"""
notificador.py - Envio de notificações via WhatsApp (Evolution API)
"""
import requests
from datetime import datetime


def enviar_whatsapp(config, mensagem):
    """Envia mensagem via Evolution API."""
    url = config.get("evolution_url", "").rstrip("/")
    instance = config.get("evolution_instance", "")
    apikey = config.get("evolution_apikey", "")
    destino = config.get("whatsapp_destino", "")
    
    if not all([url, instance, apikey, destino]):
        print("[WhatsApp] Configuração incompleta. Notificação não enviada.")
        return False
    
    endpoint = f"{url}/message/sendText/{instance}"
    headers = {
        "apikey": apikey,
        "Content-Type": "application/json"
    }
    payload = {
        "number": destino,
        "text": mensagem
    }
    
    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=30)
        if resp.status_code in (200, 201):
            print(f"[WhatsApp] Notificação enviada para {destino}")
            return True
        else:
            print(f"[WhatsApp] Erro {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        print(f"[WhatsApp] Erro de conexão: {e}")
        return False


def formatar_tamanho(bytes_val):
    """Formata bytes em KB, MB, GB."""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / 1024 / 1024:.1f} MB"
    else:
        return f"{bytes_val / 1024 / 1024 / 1024:.2f} GB"


def notificar_sucesso(config, job_nome, pasta_origem, destino_tipo, tamanho_zip, arquivos_total, duracao_seg):
    """Envia notificação de backup concluído com sucesso."""
    # Formata duração
    minutos = int(duracao_seg // 60)
    segundos = int(duracao_seg % 60)
    if minutos > 0:
        duracao_str = f"{minutos}min {segundos}s"
    else:
        duracao_str = f"{segundos}s"
    
    destino_map = {"drive": "☁️ Google Drive", "local": "💾 Local", "ambos": "☁️ Drive + 💾 Local"}
    destino_str = destino_map.get(destino_tipo, destino_tipo)
    
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    
    msg = (
        f"📦 *BACKUP CONCLUÍDO* ✅\n\n"
        f"*Job:* {job_nome}\n"
        f"*Origem:* {pasta_origem}\n"
        f"*Destino:* {destino_str}\n"
        f"*Tamanho:* {formatar_tamanho(tamanho_zip)}\n"
        f"*Arquivos:* {arquivos_total:,}\n"
        f"*Duração:* {duracao_str}\n"
        f"*Data:* {agora}"
    )
    
    return enviar_whatsapp(config, msg)


def notificar_erro(config, job_nome, erro_msg):
    """Envia notificação de falha no backup."""
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    
    msg = (
        f"🚨 *BACKUP FALHOU* ❌\n\n"
        f"*Job:* {job_nome}\n"
        f"*Erro:* {erro_msg}\n"
        f"*Data:* {agora}\n\n"
        f"_Verifique o painel para mais detalhes._"
    )
    
    return enviar_whatsapp(config, msg)


def testar_notificacao(config):
    """Envia mensagem de teste."""
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    msg = (
        f"🟢 *TESTE - DriveBackup* 🟢\n\n"
        f"Conexão com WhatsApp funcionando!\n"
        f"*Data:* {agora}"
    )
    return enviar_whatsapp(config, msg)
