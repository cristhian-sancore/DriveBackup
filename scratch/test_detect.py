import os
import sys
import json

# Simula o BASE_DIR do app.py
BASE_DIR = r"D:\APP\PAINEL DO BKP\DriveBackup"

def test_detect():
    json_path = r"D:\APP\PAINEL DO BKP\DriveBackup\data\macro-tributary-468319-u4-331466793d0c.json"
    
    # Valida se o caminho atual já é um OAuth2
    is_oauth = False
    if json_path and os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                dados = json.load(f)
                if "web" in dados or "installed" in dados:
                    is_oauth = True
        except Exception as e:
            print(f"Erro ao ler JSON atual: {e}")
    
    print(f"is_oauth: {is_oauth}")
    
    if not is_oauth:
        # Se não é OAuth, busca um compatível na pasta data
        json_path = None
        data_dir = os.path.join(BASE_DIR, "data")
        print(f"DEBUG: Procurando OAuth em {data_dir}. BASE_DIR={BASE_DIR}")
        if os.path.exists(data_dir):
            arquivos = os.listdir(data_dir)
            print(f"DEBUG: data_dir existe. Arquivos: {arquivos}")
            for arq in sorted(arquivos):
                if arq.endswith(".json"):
                    caminho = os.path.join(data_dir, arq)
                    print(f"Verificando {arq}...")
                    try:
                        with open(caminho, 'r') as f:
                            dados = json.load(f)
                            if "web" in dados or "installed" in dados:
                                json_path = caminho
                                print(f"ACHOU: {json_path}")
                                break
                    except Exception as e:
                        print(f"Erro ao ler {arq}: {e}")
                        continue
    
    if not json_path:
        print("FALHA: Nenhum JSON OAuth encontrado.")
    else:
        print(f"SUCESSO: Usando {json_path}")

if __name__ == "__main__":
    test_detect()
