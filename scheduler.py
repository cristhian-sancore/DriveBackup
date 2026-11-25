"""
scheduler.py - Agendador de backups com APScheduler
"""
import json
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import models
import backup_engine
from logger import app_logger
from logger import app_logger

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


def _remover_todos_subjobs(scheduler, job_id):
    """Remove o job principal e eventuais horários extras do scheduler."""
    prefix = _job_id_str(job_id)
    for j in list(scheduler.get_jobs()):
        if j.id == prefix or j.id.startswith(f"{prefix}_"):
            scheduler.remove_job(j.id)


def agendar_job(job):
    """Agenda ou re-agenda um job de backup (suporta múltiplos horários)."""
    scheduler = get_scheduler()
    job_tag = _job_id_str(job["id"])

    # Remove agendamentos anteriores se existirem
    _remover_todos_subjobs(scheduler, job["id"])

    if not job.get("ativo"):
        app_logger.info(f"[Scheduler] Job '{job['nome']}' esta inativo, nao agendado.")
        return

    # Parse dias da semana
    dias_semana = json.loads(job.get("dias_semana", "[]"))
    if not dias_semana:
        app_logger.info(f"[Scheduler] Job '{job['nome']}' sem dias configurados.")
        return

    # Converte para formato cron
    dias_cron = ",".join([DIAS_MAP.get(d, d) for d in dias_semana if d in DIAS_MAP])
    if not dias_cron:
        app_logger.info(f"[Scheduler] Job '{job['nome']}' dias inválidos: {dias_semana}")
        return

    # Coleta horário principal + horários extras
    horarios_raw = [job.get("horario", "22:00")]
    extras = (job.get("horarios_extras") or "").strip()
    if extras:
        for h in extras.replace(";", ",").split(","):
            h = h.strip()
            if h and h not in horarios_raw:
                horarios_raw.append(h)

    agendados = []
    for idx, horario in enumerate(horarios_raw):
        try:
            partes = horario.split(":")
            hora = int(partes[0])
            minuto = int(partes[1]) if len(partes) > 1 else 0
            if not (0 <= hora <= 23 and 0 <= minuto <= 59):
                continue
        except (ValueError, IndexError):
            app_logger.warning(f"[Scheduler] Horario invalido ignorado em '{job['nome']}': {horario}")
            continue

        sub_id = job_tag if idx == 0 else f"{job_tag}_{idx}"
        trigger = CronTrigger(
            day_of_week=dias_cron,
            hour=hora,
            minute=minuto
        )

        scheduler.add_job(
            backup_engine.executar_backup_async,
            trigger=trigger,
            args=[job["id"]],
            id=sub_id,
            name=f"Backup: {job['nome']} ({hora:02d}:{minuto:02d})",
            replace_existing=True,
            misfire_grace_time=3600  # 1 hora de tolerância
        )
        agendados.append(f"{hora:02d}:{minuto:02d}")

    if agendados:
        app_logger.info(f"[Scheduler] Job '{job['nome']}' agendado: {dias_cron} às {', '.join(agendados)}")


def remover_job(job_id):
    """Remove todos os agendamentos de um job."""
    scheduler = get_scheduler()
    _remover_todos_subjobs(scheduler, job_id)
    app_logger.info(f"[Scheduler] Job {job_id} removido do agendamento.")


def verificar_timeouts_jobs(tempo_limite_horas=12):
    """Verifica execuções em andamento que ultrapassaram o limite (padrão 12 horas) e as aborta automaticamente."""
    conn = None
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
                    app_logger.warning(f"[Timeout Watchdog] Execucao {r['id']} (Job {r['job_id']}) excedeu {tempo_limite_horas}h. Abortando...")
                    backup_engine.abortar_backup(r['job_id'])
                    cursor.execute("""
                        UPDATE execucoes 
                        SET status='timeout', fim=?, mensagem=?
                        WHERE id=?
                    """, (agora.isoformat(), f"Backup cancelado e abortado automaticamente após exceder o tempo limite de {tempo_limite_horas} horas.", r['id']))
            except Exception as e:
                app_logger.error(f"[Timeout Watchdog] Erro ao checar tempo da execucao {r['id']}: {e}")
        conn.commit()
    except Exception as e:
        app_logger.error(f"[Timeout Watchdog] Erro na rotina de verificação: {e}")
    finally:
        if conn:
            conn.close()


def carregar_todos_jobs():
    """Carrega e agenda todos os jobs do banco e inicia rotina de checagem de timeout."""
    jobs = models.listar_jobs()
    for job in jobs:
        agendar_job(job)
    app_logger.info(f"[Scheduler] {len(jobs)} jobs carregados.")
    
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
        app_logger.error(f"[Scheduler] Erro ao iniciar watchdog de timeouts: {e}")


def listar_proximas_execucoes():
    """Lista a próxima execução mais próxima de cada job agendado."""
    scheduler = get_scheduler()
    melhores = {}
    for job in scheduler.get_jobs():
        if not job.id.startswith("backup_job_"):
            continue
        proxima = job.next_run_time
        if not proxima:
            continue
        base_id = job.id.replace("backup_job_", "").split("_")[0]
        if base_id not in melhores or proxima < melhores[base_id]["dt"]:
            melhores[base_id] = {
                "dt": proxima,
                "job_id": base_id,
                "nome": job.name,
                "proxima_execucao": proxima.strftime("%d/%m/%Y %H:%M"),
            }
    return [
        {"job_id": v["job_id"], "nome": v["nome"], "proxima_execucao": v["proxima_execucao"]}
        for v in melhores.values()
    ]


def parar():
    """Para o scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
