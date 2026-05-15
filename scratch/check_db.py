import sqlite3
import os

db_path = r'D:\APP\PAINEL DO BKP\DriveBackup\data\drivebackup.db'
if not os.path.exists(db_path):
    print(f"Banco nao encontrado em {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
rows = conn.execute('SELECT * FROM configuracao').fetchall()
print("--- CONFIGURACOES ---")
for r in rows:
    print(f"{r['chave']}: {r['valor']}")

print("\n--- JOBS ---")
jobs = conn.execute('SELECT * FROM jobs').fetchall()
for j in jobs:
    print(f"ID: {j['id']} | Nome: {j['nome']} | Destino: {j['destino_tipo']} | DriveID: {j['destino_drive_id']}")

conn.close()
