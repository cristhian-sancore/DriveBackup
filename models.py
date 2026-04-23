"""
models.py - Banco de dados SQLite para o DriveBackup
"""
import sqlite3
import os
import json
from datetime import datetime

import sys

if getattr(sys, 'frozen', False):
    # Se compilado em exe, salva o banco ao lado do executável final
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "drivebackup.db")


def get_db():
    """Retorna conexão com o banco SQLite."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Cria as tabelas no banco de dados."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            pasta_origem TEXT NOT NULL,
            destino_tipo TEXT NOT NULL DEFAULT 'local',
            destino_drive_id TEXT,
            destino_drive_nome TEXT,
            destino_local TEXT,
            dias_semana TEXT NOT NULL DEFAULT '[]',
            horario TEXT NOT NULL DEFAULT '22:00',
            ativo INTEGER NOT NULL DEFAULT 1,
            retencao INTEGER NOT NULL DEFAULT 0,
            criado_em DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS execucoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            inicio DATETIME NOT NULL,
            fim DATETIME,
            status TEXT NOT NULL DEFAULT 'em_andamento',
            tamanho_zip INTEGER DEFAULT 0,
            arquivos_total INTEGER DEFAULT 0,
            destino_usado TEXT,
            mensagem TEXT,
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS configuracao (
            chave TEXT PRIMARY KEY,
            valor TEXT
        );
    """)

    # Configurações padrão
    defaults = {
        "google_json_path": "",
        "evolution_url": "https://chatapi.fassilcloud.net/",
        "evolution_instance": "SIA",
        "evolution_apikey": "C9C8966DDB42-4BA9-9428-372BE0AE3382",
        "whatsapp_destino": "5565996772226",
        "porta_servidor": "5555",
        "git_repo_url": "https://github.com/cristhian-sancore/DriveBackup"
    }
    for chave, valor in defaults.items():
        cursor.execute(
            "INSERT OR IGNORE INTO configuracao (chave, valor) VALUES (?, ?)",
            (chave, valor)
        )

    conn.commit()
    
    # Migração: adiciona coluna retencao se não existir (para bancos antigos)
    try:
        cursor.execute("SELECT retencao FROM jobs LIMIT 1")
    except:
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN retencao INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except:
            pass
    
    conn.close()


# ========================
# CRUD - Jobs
# ========================

def criar_job(dados):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO jobs (nome, pasta_origem, destino_tipo, destino_drive_id,
                         destino_drive_nome, destino_local, dias_semana, horario, ativo, retencao)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
    """, (
        dados["nome"],
        dados["pasta_origem"],
        dados["destino_tipo"],
        dados.get("destino_drive_id"),
        dados.get("destino_drive_nome"),
        dados.get("destino_local"),
        json.dumps(dados.get("dias_semana", [])),
        dados.get("horario", "22:00"),
        dados.get("retencao", 0)
    ))
    conn.commit()
    job_id = cursor.lastrowid
    conn.close()
    return job_id


def atualizar_job(job_id, dados):
    conn = get_db()
    conn.execute("""
        UPDATE jobs SET nome=?, pasta_origem=?, destino_tipo=?, destino_drive_id=?,
                       destino_drive_nome=?, destino_local=?, dias_semana=?, horario=?, retencao=?
        WHERE id=?
    """, (
        dados["nome"],
        dados["pasta_origem"],
        dados["destino_tipo"],
        dados.get("destino_drive_id"),
        dados.get("destino_drive_nome"),
        dados.get("destino_local"),
        json.dumps(dados.get("dias_semana", [])),
        dados.get("horario", "22:00"),
        dados.get("retencao", 0),
        job_id
    ))
    conn.commit()
    conn.close()


def obter_job(job_id):
    conn = get_db()
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return dict(job) if job else None


def listar_jobs():
    conn = get_db()
    jobs = conn.execute("SELECT * FROM jobs ORDER BY criado_em DESC").fetchall()
    conn.close()
    return [dict(j) for j in jobs]


def excluir_job(job_id):
    conn = get_db()
    conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    conn.commit()
    conn.close()


def toggle_job(job_id):
    conn = get_db()
    conn.execute("UPDATE jobs SET ativo = CASE WHEN ativo=1 THEN 0 ELSE 1 END WHERE id=?", (job_id,))
    conn.commit()
    job = conn.execute("SELECT ativo FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return dict(job)["ativo"] if job else None


# ========================
# CRUD - Execuções
# ========================

def registrar_inicio(job_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO execucoes (job_id, inicio, status)
        VALUES (?, ?, 'em_andamento')
    """, (job_id, datetime.now().isoformat()))
    conn.commit()
    exec_id = cursor.lastrowid
    conn.close()
    return exec_id


def registrar_fim(exec_id, status, tamanho_zip=0, arquivos_total=0, destino_usado="", mensagem=""):
    conn = get_db()
    conn.execute("""
        UPDATE execucoes SET fim=?, status=?, tamanho_zip=?, arquivos_total=?,
                            destino_usado=?, mensagem=?
        WHERE id=?
    """, (
        datetime.now().isoformat(), status, tamanho_zip,
        arquivos_total, destino_usado, mensagem, exec_id
    ))
    conn.commit()
    conn.close()


def listar_execucoes(limit=50, job_id=None):
    conn = get_db()
    if job_id:
        rows = conn.execute("""
            SELECT e.*, j.nome as job_nome FROM execucoes e
            JOIN jobs j ON e.job_id = j.id
            WHERE e.job_id=?
            ORDER BY e.inicio DESC LIMIT ?
        """, (job_id, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT e.*, j.nome as job_nome FROM execucoes e
            JOIN jobs j ON e.job_id = j.id
            ORDER BY e.inicio DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def obter_ultima_execucao(job_id):
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM execucoes WHERE job_id=?
        ORDER BY inicio DESC LIMIT 1
    """, (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def contar_estatisticas():
    conn = get_db()
    total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    jobs_ativos = conn.execute("SELECT COUNT(*) FROM jobs WHERE ativo=1").fetchone()[0]
    total_exec = conn.execute("SELECT COUNT(*) FROM execucoes").fetchone()[0]
    sucessos = conn.execute("SELECT COUNT(*) FROM execucoes WHERE status='sucesso'").fetchone()[0]
    erros = conn.execute("SELECT COUNT(*) FROM execucoes WHERE status='erro'").fetchone()[0]
    ultimo = conn.execute("""
        SELECT e.*, j.nome as job_nome FROM execucoes e
        JOIN jobs j ON e.job_id = j.id
        ORDER BY e.inicio DESC LIMIT 1
    """).fetchone()
    conn.close()
    return {
        "total_jobs": total_jobs,
        "jobs_ativos": jobs_ativos,
        "total_execucoes": total_exec,
        "sucessos": sucessos,
        "erros": erros,
        "taxa_sucesso": round((sucessos / total_exec * 100), 1) if total_exec > 0 else 0,
        "ultima_execucao": dict(ultimo) if ultimo else None
    }


# ========================
# Configuração
# ========================

def obter_config():
    conn = get_db()
    rows = conn.execute("SELECT chave, valor FROM configuracao").fetchall()
    conn.close()
    return {r["chave"]: r["valor"] for r in rows}


def salvar_config(dados):
    conn = get_db()
    for chave, valor in dados.items():
        conn.execute(
            "INSERT OR REPLACE INTO configuracao (chave, valor) VALUES (?, ?)",
            (chave, valor)
        )
    conn.commit()
    conn.close()


def detectar_e_atualizar_google_json():
    """
    Varre a pasta data em busca de arquivos JSON de credenciais.
    Prioriza Service Account para automação.
    """
    config = obter_config()
    # Se já tem um caminho válido configurado, não faz nada
    if config.get("google_json_path") and os.path.exists(config.get("google_json_path")):
        return config.get("google_json_path")

    # Lista arquivos JSON na pasta data
    if not os.path.exists(DATA_DIR):
        return None

    arquivos = [f for f in os.listdir(DATA_DIR) if f.endswith(".json")]
    
    # Procura primeiro por Service Account
    for arq in arquivos:
        caminho = os.path.join(DATA_DIR, arq)
        try:
            with open(caminho, 'r', encoding='utf-8') as f:
                dados = json.load(f)
                if dados.get("type") == "service_account":
                    salvar_config({"google_json_path": caminho})
                    return caminho
        except:
            continue
            
    # Se não achou Service Account, pega o primeiro client_secret que achar
    for arq in arquivos:
        if arq.startswith("client_secret"):
            caminho = os.path.join(DATA_DIR, arq)
            salvar_config({"google_json_path": caminho})
            return caminho
            
    return None
