import os
import sys
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def check_dependencies():
    print("[*] Verificando dependencias de compilacao...")
    try:
        import PyInstaller
    except ImportError:
        print("[*] Instalando PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

def build_exe():
    print("[*] Iniciando compilacao PyInstaller...")
    # Add-data parameter format depends on OS
    sep = ";" if os.name == "nt" else ":"
    
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name", "BKP_FASPEL_Setup",
        f"--add-data=templates{sep}templates",
        f"--add-data=static{sep}static",
        f"--add-data=data{sep}data",
        f"--add-data=nssm.exe{sep}.",
        f"--add-data=Desinstalar_BKP.exe{sep}.",
        "app.py"
    ]
    
    print(f"Executando comando: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print("\n==================================")
        print(" COMPILACAO CONCLUIDA COM SUCESSO! ")
        print("==================================")
        print(f" O seu instalador esta em: {os.path.join(BASE_DIR, 'dist', 'BKP_FASPEL_Setup.exe')}")
    else:
        print(f"[X] O PyInstaller falhou com o codigo: {result.returncode}")

if __name__ == "__main__":
    check_dependencies()
    build_exe()
