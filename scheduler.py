"""
scheduler.py - Agendador de backups com APScheduler
"""
import json
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import models
import backup_engine

_scheduler = None

# Mapa de dias da semana pt-br -> cron
DIAS_MAP = {
    "seg": "mon",
    "ter": "tue",
    "qua": "wed",
    "qui": "thu",
    "sex": "fri",
    "sab": "sat",
    "dom": "sun"
}


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="America/Cuiaba")
        _scheduler.start()
    return _scheduler


def _job_id_str(job_id):
    return f"backup_job_{job_id}"


def agendar_job(job):
    """Agenda ou re-agenda um job de backup."""
    scheduler = get_scheduler()
    job_tag = _job_id_str(job["id"])

    # Remove agendamento anterior se existir
    existing = scheduler.get_job(job_tag)
    if existing:
        scheduler.remove_job(job_tag)

    if not job.get("ativo"):
        print(f"[Scheduler] Job '{job['nome']}' esta inativo, nao agendado.")
        return

    # Parse dias da semana
    dias_semana = json.loads(job.get("dias_semana", "[]"))
    if not dias_semana:
        print(f"[Scheduler] Job '{job['nome']}' sem dias configurados.")
        return

    # Converte para formato cron
    dias_cron = ",".join([DIAS_MAP.get(d, d) for d in dias_semana if d in DIAS_MAP])
    if not dias_cron:
        print(f"[Scheduler] Job '{job['nome']}' dias inválidos: {dias_semana}")
        return

    # Parse horário
    horario = job.get("horario", "22:00")
    partes = horario.split(":")
    hora = int(partes[0]) if len(partes) > 0 else 22
    minuto = int(partes[1]) if len(partes) > 1 else 0

    trigger = CronTrigger(
        day_of_week=dias_cron,
        hour=hora,
        minute=minuto
    )

    scheduler.add_job(
        backup_engine.executar_backup_async,
        trigger=trigger,
        args=[job["id"]],
        id=job_tag,
        name=f"Backup: {job['nome']}",
        replace_existing=True,
        misfire_grace_time=3600  # 1 hora de tolerância
    )

    print(f"[Scheduler] Job '{job['nome']}' agendado: {dias_cron} às {hora:02d}:{minuto:02d}")


def remover_job(job_id):
    """Remove agendamento de um job."""
    scheduler = get_scheduler()
    job_tag = _job_id_str(job_id)
    existing = scheduler.get_job(job_tag)
    if existing:
        scheduler.remove_job(job_tag)
        print(f"[Scheduler] Job {job_id} removido do agendamento.")


def verificar_timeouts_jobs(tempo_limite_horas=12):
    """Verifica execuções em andamento que ultrapassaram o limite (padrão 12 horas) e as aborta automaticamente."""
    try:
        from datetime import datetime
        conn = models.get_db()
        cursor = conn.cursor()
        rows = cursor.execute("SELECT id, job_id, inicio FROM execucoes WHERE status='em_andamento'").fetchall()
        agora = datetime.now()
        for r in rows:
            try:
                inicio_dt = datetime.fromisoformat(r['inicio'])
                segundos = (agora - inicio_dt).total_seconds()
                if segundos > tempo_limite_horas * 3600:
                    print(f"[Timeout Watchdog] Execucao {r['id']} (Job {r['job_id']}) excedeu {tempo_limite_horas}h. Abortando...")
                    backup_engine.abortar_backup(r['job_id'])
                    cursor.execute("""
                        UPDATE execucoes 
                        SET status='timeout', fim=?, mensagem=?
                        WHERE id=?
                    """, (agora.isoformat(), f"Backup cancelado e abortado automaticamente após exceder o tempo limite de {tempo_limite_horas} horas.", r['id']))
            except Exception as e:
                print(f"[Timeout Watchdog] Erro ao checar tempo da execucao {r['id']}: {e}")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Timeout Watchdog] Erro na rotina de verificação: {e}")


def carregar_todos_jobs():
    """Carrega e agenda todos os jobs do banco e inicia rotina de checagem de timeout."""
    jobs = models.listar_jobs()
    for job in jobs:
        agendar_job(job)
    print(f"[Scheduler] {len(jobs)} jobs carregados.")
    
    try:
        scheduler = get_scheduler()
        scheduler.add_job(
            verificar_timeouts_jobs,
            'interval',
            minutes=10,
            id='watchdog_timeouts_12h',
            replace_existing=True,
            name='Watchdog: Verificacao de Timeouts (12h)'
        )
        verificar_timeouts_jobs()
    except Exception as e:
        print(f"[Scheduler] Erro ao iniciar watchdog de timeouts: {e}")


def listar_proximas_execucoes():
    """Lista as próximas execuções agendadas."""
    scheduler = get_scheduler()
    proximas = []
    for job in scheduler.get_jobs():
        proxima = job.next_run_time
        if proxima:
            proximas.append({
                "job_id": job.id.replace("backup_job_", ""),
                "nome": job.name,
                "proxima_execucao": proxima.strftime("%d/%m/%Y %H:%M")
            })
    return proximas


def parar():
    """Para o scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
