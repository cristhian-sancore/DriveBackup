
import os
import sys
import json
import time
from datetime import datetime

# Importações internas do projeto
try:
    import drive_client
    import models
except ImportError:
    # Caso rodado fora da pasta correta
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    import drive_client
    import models

# Configurações de Cores (ANSI)
class Cores:
    VERDE = '\033[92m'
    AMARELO = '\033[93m'
    VERMELHO = '\033[91m'
    AZUL = '\033[94m'
    CIANO = '\033[96m'
    RESET = '\033[0m'
    NEGRITO = '\033[1m'

def limpar_tela():
    os.system('cls' if os.name == 'nt' else 'clear')

def exibir_cabecalho():
    limpar_tela()
    print(f"{Cores.CIANO}╔══════════════════════════════════════════════════════════════════════════╗{Cores.RESET}")
    print(f"{Cores.CIANO}║{Cores.RESET}                {Cores.NEGRITO}DRIVEBACKUP - ATIVAÇÃO DE CONTA PESSOAL{Cores.RESET}           {Cores.CIANO}║{Cores.RESET}")
    print(f"{Cores.CIANO}╚══════════════════════════════════════════════════════════════════════════╝{Cores.RESET}")
    print()

def localizar_credenciais():
    """Tenta encontrar o arquivo client_secret.json no banco ou na pasta data."""
    print(f"{Cores.AZUL}[*]{Cores.RESET} Buscando configurações do sistema...")
    
    config = models.obter_config()
    json_path = config.get("google_json_path")
    
    if json_path and os.path.exists(json_path):
        return json_path
    
    # Se não está no banco, procura na pasta data
    print(f"{Cores.AMARELO}[!]{Cores.RESET} Caminho não configurado no banco. Procurando na pasta 'data'...")
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    if not os.path.exists(data_dir):
        return None
    
    arquivos_json = [f for f in os.listdir(data_dir) if f.startswith("client_secret") and f.endswith(".json")]
    
    if not arquivos_json:
        return None
    
    if len(arquivos_json) == 1:
        return os.path.join(data_dir, arquivos_json[0])
    
    # Se houver vários, usa o mais recente (ou o primeiro)
    return os.path.join(data_dir, arquivos_json[0])

def main():
    # Habilita cores no terminal do Windows se necessário
    if os.name == 'nt':
        os.system('')

    exibir_cabecalho()
    
    json_path = localizar_credenciais()
    
    if not json_path:
        print(f"{Cores.VERMELHO}[X] ERRO: Arquivo de credenciais (client_secret_*.json) não encontrado!{Cores.RESET}")
        print(f"    Certifique-se de que o arquivo JSON do Google Cloud está na pasta 'data'.")
        input("\nPressione ENTER para sair...")
        return

    print(f"{Cores.VERDE}[OK]{Cores.RESET} Credenciais encontradas: {os.path.basename(json_path)}")
    print(f"{Cores.AZUL}[*]{Cores.RESET} Iniciando fluxo de login seguro...")
    print("-" * 50)
    print(f"{Cores.NEGRITO}INSTRUÇÕES:{Cores.RESET}")
    print(f"1. Uma aba será aberta no seu navegador padrão.")
    print(f"2. Escolha sua conta do Google Drive.")
    print(f"3. Se aparecer 'O Google não verificou este app', clique em '{Cores.AMARELO}Avançado{Cores.RESET}'")
    print(f"   e depois em '{Cores.AMARELO}Acessar DriveBackup (não seguro){Cores.RESET}'.")
    print(f"4. Clique em '{Cores.VERDE}Continuar{Cores.RESET}' para conceder a permissão.")
    print("-" * 50)
    print(f"{Cores.AMARELO}>> O navegador deve abrir em instantes... <<{Cores.RESET}")
    
    time.sleep(2)
    
    try:
        # Chama a autenticação do drive_client
        # Isso abrirá o servidor local e o navegador
        drive_client.autenticar(json_path)
        
        print(f"\n{Cores.VERDE}╔══════════════════════════════════════════════════════════════════════════╗{Cores.RESET}")
        print(f"{Cores.VERDE}║            SUCESSO! O ACESSO FOI GERADO COM EXCELÊNCIA.              ║{Cores.RESET}")
        print(f"{Cores.VERDE}╚══════════════════════════════════════════════════════════════════════════╝{Cores.RESET}")
        
        # Testa a conexão para mostrar detalhes
        print(f"\n{Cores.AZUL}[*]{Cores.RESET} Validando conexão e obtendo dados da conta...")
        ok, msg = drive_client.testar_conexao(json_path)
        
        if ok:
            print(f"{Cores.VERDE}[Acesso Ativo]{Cores.RESET} {msg}")
            print(f"\nAgora você pode configurar seus backups para o Google Drive.")
        else:
            print(f"{Cores.AMARELO}[!]{Cores.RESET} Login realizado, mas houve um alerta: {msg}")

    except Exception as e:
        print(f"\n{Cores.VERMELHO}[X] OCORREU UM ERRO DURANTE O LOGIN:{Cores.RESET}")
        print(f"    {str(e)}")
        print(f"\n{Cores.NEGRITO}Dica:{Cores.RESET} Verifique se o Firewall não está bloqueando o acesso e se você está conectado à internet.")

    print("\n" + "="*70)
    input(f"{Cores.CIANO}Pressione ENTER para finalizar...{Cores.RESET}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Cores.AMARELO}[!] Operação cancelada pelo usuário.{Cores.RESET}")
        sys.exit(0)
