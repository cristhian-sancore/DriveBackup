"""
notificador.py - Envio de notificações via WhatsApp (Evolution API)
"""
import requests
from datetime import datetime
from logger import app_logger
from logger import app_logger
from logger import app_logger


def enviar_whatsapp(config, mensagem):
    """Envia mensagem via Evolution API."""
    url = config.get("evolution_url", "").rstrip("/")
    instance = config.get("evolution_instance", "")
    apikey = config.get("evolution_apikey", "")
    destino = config.get("whatsapp_destino", "")
    
    if not all([url, instance, apikey, destino]):
        app_logger.info("[WhatsApp] Configuração incompleta. Notificação não enviada.")
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
            app_logger.info(f"[WhatsApp] Notificação enviada para {destino}")
            return True
        else:
            app_logger.info(f"[WhatsApp] Erro {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        app_logger.info(f"[WhatsApp] Erro de conexão: {e}")
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


def notificar_auth_necessaria(config):
    """Envia notificação urgente de que o token do Google Drive expirou ou foi revogado."""
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    msg = (
        f"⚠️ *ATENÇÃO: AÇÃO NECESSÁRIA!* ⚠️\n\n"
        f"A conexão com o *Google Drive* foi perdida ou a permissão expirou.\n\n"
        f"Por favor, acesse o painel do DriveBackup o mais rápido possível e clique em *'Conectar via Navegador'* para autorizar o sistema novamente.\n\n"
        f"Os backups estão *interrompidos* até que a reconexão seja feita.\n\n"
        f"*Data do Alerta:* {agora}"
    )
    return enviar_whatsapp(config, msg)


def notificar_timeout(config, job_nome, duracao_min, limite_horas=12):
    """Envia notificação de que o backup excedeu o limite de tempo (timeout)."""
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    limite_min = limite_horas * 60
    msg = (
        f"⏰ *BACKUP TIMEOUT* ⚠️\n\n"
        f"*Job:* {job_nome}\n"
        f"*Tempo decorrido:* {duracao_min:.0f} minutos\n"
        f"*Limite:* {limite_horas}h ({limite_min} minutos)\n\n"
        f"O backup foi *cancelado automaticamente* porque excedeu o limite de {limite_horas} horas.\n"
        f"Isso pode indicar problemas de rede, disco lento ou arquivo muito grande.\n\n"
        f"_Verifique o painel e os logs para mais detalhes._\n"
        f"*Data:* {agora}"
    )
    return enviar_whatsapp(config, msg)

