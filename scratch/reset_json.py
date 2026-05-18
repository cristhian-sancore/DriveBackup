import sqlite3
import os

db_path = r'C:\BKP_FASPEL\data\drivebackup.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("UPDATE configuracoes SET valor = '' WHERE chave = 'google_json_path'")
    conn.commit()
    conn.close()
    print("Banco atualizado com sucesso!")
else:
    print("Banco nao encontrado.")
