import os
import sys
import subprocess
import time
import shutil

NOME_SERVICO = "BKP_FASPEL"
PASTA_APP = r"C:\BKP_FASPEL"

def desinstalar():
    print("="*50)
    print("     DESINSTALADOR - DRIVEBACKUP FASPEL")
    print("="*50)
    
    nssm_bin = os.path.join(PASTA_APP, "nssm.exe")
    
    # Se o nssm não estiver na pasta oficial, tenta pegar do temp do pyinstaller (se embutido)
    if not os.path.exists(nssm_bin) and hasattr(sys, '_MEIPASS'):
        nssm_bin = os.path.join(sys._MEIPASS, "nssm.exe")

    if not os.path.exists(nssm_bin):
        print("[X] Erro: Utilitario nssm.exe nao encontrado. Tentando via SC...")
        subprocess.run(["sc", "stop", NOME_SERVICO], capture_output=True)
        subprocess.run(["sc", "delete", NOME_SERVICO], capture_output=True)
    else:
        print(f"[*] Parando o servico {NOME_SERVICO}...")
        subprocess.run([nssm_bin, "stop", NOME_SERVICO], capture_output=True)
        
        print(f"[*] Removendo o servico {NOME_SERVICO}...")
        subprocess.run([nssm_bin, "remove", NOME_SERVICO, "confirm"], capture_output=True)

    print("[*] Servico removido com sucesso!")
    
    print("\n[?] Deseja remover todos os arquivos da pasta C:\\BKP_FASPEL? (S/N)")
    print("    AVISO: Isso apagara permanentemente seus Backups locais, Logs e Configuracoes!")
    opcao = input("> ").strip().lower()
    
    if opcao == 's':
        print("[*] Removendo arquivos... Aguarde.")
        # Como o próprio desinstalador pode estar rodando dentro da pasta, ele pode falhar ao apagar a si mesmo.
        # Geralmente em EXEs compilados o Windows bloqueia.
        try:
            # Tenta apagar tudo exceto o próprio executável se ele estiver lá
            for item in os.listdir(PASTA_APP):
                item_path = os.path.join(PASTA_APP, item)
                try:
                    if os.path.isfile(item_path):
                        if not sys.executable.endswith(item): # Não apaga o próprio exe de desinstalação
                            os.remove(item_path)
                    else:
                        shutil.rmtree(item_path)
                except:
                    pass
            print("[*] Limpeza concluida (arquivos em uso foram mantidos).")
        except Exception as e:
            print(f"[!] Algumas pastas nao puderam ser removidas: {e}")
    else:
        print("[*] Pasta mantida por seguranca.")

    print("\nDesinstalacao finalizada!")
    input("Pressione ENTER para sair...")

if __name__ == "__main__":
    desinstalar()
