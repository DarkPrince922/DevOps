import asyncio, time, re, os
import logging
from pathlib import Path
from telegram import ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from app.gpt import clean_text

import app.state as state

logger = logging.getLogger(__name__)

async def send_text(update, text):
    text = clean_text(text)
    if not text: return
    msg = update.effective_message

    sent_files = set()
    for m in re.finditer(r"(?:^|\s)(/app/data/[^\s]+)", text):
        path = Path(m.group(1).strip('.,;:!?)\"\''))
        if path.exists() and path.is_file() and str(path) not in sent_files:
            with open(path, "rb") as f:
                await msg.reply_document(document=f, filename=path.name, caption=f"📎 {path.name}")
            sent_files.add(str(path))
            await asyncio.sleep(0.1)

    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        await msg.reply_text(chunk)
        await asyncio.sleep(0.1)

def fmt_time(seconds):
    if seconds < 60: return f"{seconds}с"
    return f"{seconds//60}м {seconds%60}с"

def build_status(uid, step, action):
    elapsed = int(time.time() - state.START_TIME.get(uid, time.time()))
    return f"⏳ {action}\nШаг: {step} | Время: {fmt_time(elapsed)}"

def is_authorized(update):
    if update.effective_user:
        state.set_current_user_id(update.effective_user.id)
        return state.is_user_authorized(update.effective_user.id)
    return False

def is_admin(update):
    if update.effective_user:
        state.set_current_user_id(update.effective_user.id)
        return state.is_admin_id(update.effective_user.id)
    return False

async def status_updater(uid, status_msg, stop_event):
    """Фоновая задача — обновляет статус каждые 3 секунды"""
    step = 0
    while not stop_event.is_set():
        await asyncio.sleep(3)
        if stop_event.is_set(): break
        step += 1
        action = state.STATUS_TEXT.get(uid, "Думаю...")
        try:
            await status_msg.edit_text(build_status(uid, step, action))
        except Exception:
            logger.exception("status update failed")

def kb():
    return ReplyKeyboardMarkup([
        ["📊 Мониторинг", "🐳 Docker"],
        ["📡 Статус", "✅ Команды"],
        ["🌐 Серверы", "☸️ Kubernetes"],
        ["☁️ Cloudflare", "🛰 Remnawave"],
        ["⚙️ Настройки"],
        ["♻️ Сброс сессии", "❌ Отмена", "⏹ СТОП"]
    ], resize_keyboard=True, is_persistent=True)

def kb_monitoring():
    return ReplyKeyboardMarkup([
        ["📈 Графики", "📊 CPU", "💾 RAM"],
        ["💽 Диск", "🔍 Процессы"],
        ["🌐 Сеть", "🕐 Аптайм"],
        ["💻 Система"],
        ["⬅️ Назад", "❌ Отмена", "⏹ СТОП"]
    ], resize_keyboard=True, is_persistent=True)

def kb_docker():
    return ReplyKeyboardMarkup([
        ["📦 Контейнеры", "📋 Логи"],
        ["🧹 Очистить"],
        ["⬅️ Назад", "❌ Отмена", "⏹ СТОП"]
    ], resize_keyboard=True, is_persistent=True)

def kb_servers():
    return ReplyKeyboardMarkup([
        ["📋 Список серверов"],
        ["➕ Добавить", "➖ Удалить"],
        ["⬅️ Назад", "❌ Отмена", "⏹ СТОП"]
    ], resize_keyboard=True, is_persistent=True)

def kb_settings():
    rows = []
    if state.is_admin_id():
        rows.append(["🛠 Админ панель"])
    rows += [
        ["🤖 ИИ-агенты", "🏪 Провайдеры"],
        ["🧦 Прокси"],
        ["📁 Проекты"],
        ["♻️ Сброс сессии"],
        ["⬅️ Назад", "❌ Отмена", "⏹ СТОП"]
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def providers_menu_markup():
    cfg = state.get_providers()
    rows = []
    for i, p in enumerate(cfg["providers"]):
        mark = "✅ " if p["name"] == cfg["active"] else ""
        rows.append([
            InlineKeyboardButton(f"{mark}{p['name']}", callback_data=f"provider:select:{i}"),
            InlineKeyboardButton("🗑", callback_data=f"provider:del:{i}"),
        ])
    rows.append([InlineKeyboardButton("➕ Добавить провайдера", callback_data="provider:add")])
    return InlineKeyboardMarkup(rows)

def providers_menu_text():
    cfg = state.get_providers()
    lines = ["🏪 Провайдеры / реселлеры API\n"]
    if not cfg.get("providers"):
        lines.append("Провайдеров пока нет. Добавь свой OpenAI-совместимый URL и API key.")
        return "\n".join(lines)
    active = next((p for p in cfg["providers"] if p["name"] == cfg["active"]), cfg["providers"][0])
    lines.append(f"Активный: {active['name']}")
    lines.append(f"URL: {active['base_url']}")
    lines.append(f"API key: {'задан ✅' if active.get('api_key') else 'из .env'}\n")
    lines.append("Выбери провайдера или добавь свой OpenAI-совместимый URL и API key")
    return "\n".join(lines)


def provider_add_step_markup(step):
    labels = {
        "name": "✏️ Название",
        "base_url": "🌐 Base URL",
        "api_key": "🔑 API key",
    }
    return InlineKeyboardMarkup([[InlineKeyboardButton(labels.get(step, "✏️ Ввод"), callback_data="provider:noop")]])

def agent_add_step_markup(step):
    if step == "provider":
        rows = []
        for i, p in enumerate(state.get_providers().get("providers", [])):
            rows.append([InlineKeyboardButton(p["name"], callback_data=f"agent:provider:{i}")])
        return InlineKeyboardMarkup(rows or [[InlineKeyboardButton("🏪 Провайдер", callback_data="agent:noop")]])
    labels = {"name": "✏️ Название", "model": "🧠 Модель"}
    return InlineKeyboardMarkup([[InlineKeyboardButton(labels.get(step, "✏️ Ввод"), callback_data="agent:noop")]])


def agents_menu_markup():
    cfg = state.get_agents()
    rows = []
    rows.append([InlineKeyboardButton(
        f"👥 Совместная работа: {'вкл ✅' if cfg.get('collab') else 'выкл'}",
        callback_data="agent:collab")])
    selected = set(cfg.get("collab_agents", []))
    for i, a in enumerate(cfg["agents"]):
        lead = "▶️ " if a["name"] == cfg["active"] else ""
        team = "☑️" if a["name"] in selected else "⬜️"
        rows.append([
            InlineKeyboardButton(f"{lead}{a['name']} [{a.get('provider','?')}] ({a['model']})", callback_data=f"agent:select:{i}"),
            InlineKeyboardButton(team, callback_data=f"agent:team:{i}"),
            InlineKeyboardButton("🗑", callback_data=f"agent:del:{i}"),
        ])
    rows.append([InlineKeyboardButton("➕ Добавить агента", callback_data="agent:add")])
    return InlineKeyboardMarkup(rows)

def agents_menu_text():
    cfg = state.get_agents()
    lines = ["🤖 ИИ-агенты\n"]
    if not cfg.get("agents"):
        lines.append("ИИ-агентов пока нет. Сначала добавь провайдера, затем добавь своего агента.")
        return "\n".join(lines)
    selected = cfg.get("collab_agents", [])
    lines.append(f"Ведущий агент: {cfg['active']}")
    lines.append(f"Совместная работа: {'включена' if cfg.get('collab') else 'выключена'}")
    lines.append("Советники: " + (", ".join(selected) if selected else "не выбраны") + "\n")
    lines.append("Формат агента: имя [провайдер] (модель).")
    lines.append("▶️ — ведущая модель, она принимает итоговое решение и выполняет инструменты.")
    lines.append("☑️ — модели-советники: перед выполнением задачи они независимо дают краткие рекомендации, риски и варианты решения. Ведущий агент учитывает их советы.")
    return "\n".join(lines)


def projects_menu_markup():
    cfg = state.get_projects()
    rows = []
    rows.append([InlineKeyboardButton("Без активного проекта" + (" ✅" if not cfg.get("active") else ""), callback_data="project:disable")])
    for i, p in enumerate(cfg["projects"]):
        mark = "✅ " if p["name"] == cfg.get("active") else ""
        rows.append([
            InlineKeyboardButton(f"{mark}{p['name']}", callback_data=f"project:select:{i}"),
            InlineKeyboardButton("🗑", callback_data=f"project:del:{i}"),
        ])
    if cfg.get("active"):
        rows.append([InlineKeyboardButton("⚙️ Docker/K8s", callback_data="project:deploy_config"), InlineKeyboardButton("🚀 Деплой", callback_data="project:deploy")])
        rows.append([InlineKeyboardButton("🤖 K8s ИИ", callback_data="project:k8s_ai")])
    rows.append([InlineKeyboardButton("➕ Добавить проект", callback_data="project:add")])
    return InlineKeyboardMarkup(rows)

def projects_menu_text():
    cfg = state.get_projects()
    active = state.active_project()
    lines = ["📁 Проекты\n"]
    if active:
        lines.append(f"Активный: {active['name']}")
        lines.append(f"Сервер: {active['server']}")
        lines.append(f"Путь: {active['path']}")
        if active.get("prompt"):
            prompt = active["prompt"]
            lines.append(f"Инструкции: {prompt[:700]}{'...' if len(prompt) > 700 else ''}")
        if any(active.get(k) for k in ("docker_image", "build_cmd", "push_cmd", "deploy_cmd", "k8s_namespace", "k8s_deployment")):
            lines.append("\nDocker/K8s:")
            if active.get("docker_image"):
                lines.append(f"Image: {active['docker_image']}")
            if active.get("k8s_namespace"):
                lines.append(f"Namespace: {active['k8s_namespace']}")
            if active.get("k8s_deployment"):
                lines.append(f"Deployment: {active['k8s_deployment']}")
            if active.get("k8s_container"):
                lines.append(f"Container: {active['k8s_container']}")
            if active.get("build_cmd"):
                lines.append("Build command: задана")
            if active.get("push_cmd"):
                lines.append("Push command: задана")
            if active.get("deploy_cmd"):
                lines.append("Deploy command: задана")
    else:
        lines.append("Активный проект не выбран.")
    lines.append("\nДобавь проект, чтобы бот автоматически учитывал сервер, рабочую директорию и отдельные инструкции при задачах по нему.")
    return "\n".join(lines)

def admin_panel_markup():
    cfg = state.get_settings()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📄 Лимит вывода: {cfg['max_output']}", callback_data="admin:set:max_output")],
        [InlineKeyboardButton(f"🧠 История: {cfg['max_history_chars']}", callback_data="admin:set:max_history_chars")],
        [InlineKeyboardButton(f"🔁 Автопродолжение: {cfg['auto_continue_limit']}", callback_data="admin:set:auto_continue_limit")],
        [InlineKeyboardButton(f"🧭 Шаги агента: {cfg['max_agent_steps']}", callback_data="admin:set:max_agent_steps")],
        [InlineKeyboardButton(f"🚨 Уведомления о падениях: {'вкл' if cfg.get('service_monitor_enabled') else 'выкл'}", callback_data="admin:toggle:service_monitor_enabled")],
        [InlineKeyboardButton("📦 Бекап бота", callback_data="admin:backup")],
        [InlineKeyboardButton("👥 Разрешённые пользователи", callback_data="admin:allowed:list")],
        [InlineKeyboardButton("➕ Добавить пользователя", callback_data="admin:allowed:add"), InlineKeyboardButton("➖ Удалить пользователя", callback_data="admin:allowed:remove")],
        [InlineKeyboardButton("♻️ Сбросить все сессии", callback_data="admin:reset_sessions")],
    ])

def admin_panel_text():
    cfg = state.get_settings()
    return (
        "🛠 Админ панель\n\n"
        f"🤖 Модель: {cfg['model']}\n"
        f"📄 Лимит вывода команд: {cfg['max_output']} символов\n"
        f"🧠 Лимит истории: {cfg['max_history_chars']} символов\n"
        f"🔁 Автопродолжений: {cfg['auto_continue_limit']}\n"
        f"🧭 Лимит шагов агента: {cfg['max_agent_steps']}\n"
        f"🚨 Уведомления о падениях: {'включены' if cfg.get('service_monitor_enabled') else 'отключены'}\n"
        f"👥 Разрешённых пользователей: {len(state.get_allowed_users())}\n\n"
        "Выбери настройку для изменения:"
    )

