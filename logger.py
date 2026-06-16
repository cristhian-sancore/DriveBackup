import os
import sys
import logging
from logging.handlers import RotatingFileHandler

# Determina o BASE_DIR (suporte a executável pyinstaller)
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

LOG_FILE = os.path.join(DATA_DIR, "DriveBackup.log")

def get_logger(name="DriveBackup"):
    logger = logging.getLogger(name)
    
    # Evita adicionar múltiplos handlers se já foi configurado
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # Formato: [2026-06-16 09:30:00] [INFO] Mensagem
        formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        
        # Handler para o arquivo (Rotaciona em 5MB, mantém até 3 arquivos antigos)
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # Handler para o console (caso seja rodado manualmente via CMD)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
    return logger

# Instância global pronta para uso
app_logger = get_logger()
