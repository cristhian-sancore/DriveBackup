import os
import sys
import json

# Adiciona o diretório atual ao sys.path para importar os módulos do app
sys.path.append(r'D:\APP\PAINEL DO BKP\DriveBackup')

import drive_client
import models

def testar_todos_jsons():
    data_dir = r'D:\APP\PAINEL DO BKP\DriveBackup\data'
    jsons = [f for f in os.listdir(data_dir) if f.endswith('.json') and f != 'token.json']
    
    print(f"Encontrados {len(jsons)} arquivos JSON em {data_dir}")
    
    for arq in jsons:
        caminho = os.path.join(data_dir, arq)
        print(f"\nTestando: {arq}")
        try:
            ok, msg = drive_client.testar_conexao(caminho)
            if ok:
                print(f"[OK] {msg}")
                # Se funcionar, vamos sugerir este como o correto
                return caminho
            else:
                print(f"[FALHA] {msg}")
        except Exception as e:
            print(f"[ERRO] {e}")
    return None

if __name__ == "__main__":
    caminho_vencendor = testar_todos_jsons()
    if caminho_vencendor:
        print(f"\nSugerido atualizar google_json_path para: {caminho_vencendor}")
    else:
        print("\nNenhum arquivo de credenciais funcionou.")
