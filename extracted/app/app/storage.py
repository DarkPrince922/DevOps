import time
from pathlib import Path
import app.state as state
from app.state import load_json, save_json



def prune_sessions():
    cutoff = time.time() - state.SESSION_TTL_DAYS * 86400
    for uid, ts in list(state.SESSION_UPDATED.items()):
        try:
            expired = float(ts) < cutoff
        except Exception:
            expired = True
        if expired:
            state.SESSION_HISTORY.pop(uid, None)
            state.SESSION_UPDATED.pop(uid, None)


def prune_backups(max_keep=None, max_age_days=14):
    if max_keep is None:
        max_keep = state.BACKUP_RETENTION
    now = time.time()
    backups = sorted(state.DATA_DIR.glob('DevOps*.tar.gz'), key=lambda x: x.stat().st_mtime, reverse=True)
    for idx, backup in enumerate(backups):
        try:
            too_old = now - backup.stat().st_mtime > max_age_days * 86400
            if idx >= max_keep or too_old:
                backup.unlink()
        except FileNotFoundError:
            pass


def get_servers():
    return load_json(state.SERVERS_FILE, {})


def load_sessions():
    data = load_json(state.SESSIONS_FILE, {})
    if isinstance(data, dict) and "sessions" in data:
        sessions = data.get("sessions", {})
        updated = data.get("updated", {})
    else:
        sessions, updated = data if isinstance(data, dict) else {}, {}
    cutoff = time.time() - state.SESSION_TTL_DAYS * 86400
    state.SESSION_HISTORY.clear(); state.SESSION_UPDATED.clear()
    for uid, hist in sessions.items():
        ts = float(updated.get(uid, time.time()))
        if ts >= cutoff:
            state.SESSION_HISTORY[uid] = hist
            state.SESSION_UPDATED[uid] = ts
    prune_sessions()
    save_sessions()

def save_sessions():
    prune_sessions()
    save_json(state.SESSIONS_FILE, {"sessions": state.SESSION_HISTORY, "updated": state.SESSION_UPDATED})

def reset_session(uid):
    key = str(uid)
    state.SESSION_HISTORY.pop(key, None)
    state.SESSION_UPDATED.pop(key, None)
    save_sessions()


def get_session(uid, servers):
    key = str(uid)
    names = (["Главный сервер"] if state.is_admin_id(uid) else []) + list(servers.keys())
    system_text = state.SYSTEM_PROMPT + f"\nДоступные серверы: {', '.join(names)}. Для Главного сервера используй local_exec — это выполнение команд прямо в контейнере бота без SSH."
    project = state.active_project()
    if project:
        project_server = state.normalize_server_name(project.get("server"), servers)
        tool = "local_exec" if project_server == "Главный сервер" else "ssh_exec"
        system_text += (
            f"\n\nАктивный проект: {project['name']}."
            f"\nСервер проекта: {project_server}."
            f"\nРабочая директория проекта: {project['path']}."
            f"\nПеред командами по этому проекту переходи в эту директорию и используй {tool}."
            f"\nИнструкции проекта: {project.get('prompt') or 'нет отдельных инструкций'}."
        )
    system = {"role": "system", "content": system_text}
    history = state.SESSION_HISTORY.get(key)
    if not history:
        history = [system]
    else:
        history[0] = system
    return history


def set_session(uid, history):
    from app.gpt import trim_history
    key = str(uid)
    state.SESSION_HISTORY[key] = trim_history(history)
    state.SESSION_UPDATED[key] = time.time()
    save_sessions()
