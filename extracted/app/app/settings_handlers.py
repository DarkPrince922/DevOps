import time
import tarfile
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import app.state as state
from app.core import (
    PENDING_ADMIN_SETTING, SESSION_HISTORY, admin_panel_markup, admin_panel_text, agents_menu_markup,
    agents_menu_text, providers_menu_markup, providers_menu_text, get_proxy_config, is_authorized, normalize_socks5, save_proxy_config,
    save_sessions, send_text, set_setting, reset_session, get_servers
)
from app.ui import projects_menu_markup, projects_menu_text, provider_add_step_markup, agent_add_step_markup
from app.executor import local_exec as run_local_exec, ssh_exec as run_ssh_exec
from app.k8s_handlers import apply_project_k8s_operations


def _project_deploy_command(project):
    cmds = []
    for key in ("build_cmd", "push_cmd"):
        if project.get(key):
            cmds.append(project[key])
    if project.get("deploy_cmd"):
        cmds.append(project["deploy_cmd"])
    elif project.get("docker_image") and project.get("k8s_namespace") and project.get("k8s_deployment") and project.get("k8s_container"):
        ns = project["k8s_namespace"]
        dep = project["k8s_deployment"]
        container = project["k8s_container"]
        image = project["docker_image"]
        cmds.append(f"kubectl -n {ns} set image deployment/{dep} {container}={image}")
        cmds.append(f"kubectl -n {ns} rollout status deployment/{dep} --timeout=180s")
    if not cmds:
        return None
    return "cd " + project["path"] + " && " + " && ".join(cmds)


def _run_project_deploy(uid):
    project = state.active_project()
    if not project:
        return "❌ Активный проект не выбран."
    cmd = _project_deploy_command(project)
    if not cmd:
        return "❌ Для проекта не настроены команды Docker/K8s."
    servers = get_servers()
    server = state.normalize_server_name(project.get("server"), servers)
    if server == "Главный сервер":
        return run_local_exec(cmd, allow_dangerous=True, uid=uid)
    cfg = servers.get(server)
    if not cfg:
        return "❌ Сервер проекта не найден."
    return run_ssh_exec(cfg, cmd, allow_dangerous=True, uid=uid)

async def admin_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(update) or not state.is_admin_id(uid):
        await update.message.reply_text(f"⛔ Админ-панель доступна только администратору. Ваш Telegram ID: {uid}")
        return
    await update.message.reply_text(admin_panel_text(), reply_markup=admin_panel_markup())


async def backup_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(update) or not state.is_admin_id(uid):
        await update.effective_message.reply_text(f"⛔ Бекап доступен только администратору. Ваш Telegram ID: {uid}")
        return

    ts = time.strftime("%Y%m%d_%H%M%S")
    project_dir = Path(__file__).resolve().parent.parent
    backup_dir = project_dir / "data"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"DevOps_full_backup_{ts}.tar.gz"

    msg = update.effective_message
    await msg.reply_text("📦 Создаю полный бекап бота...")
    try:
        excluded_names = {"data"}
        with tarfile.open(backup_path, "w:gz") as tar:
            for item in project_dir.rglob("*"):
                rel = item.relative_to(project_dir)
                if item == backup_path:
                    continue
                if rel.parts and rel.parts[0] in excluded_names:
                    continue
                if item.name == ".env" or item.name.startswith(".env.") or item.name.startswith(".env"):
                    continue
                if "__pycache__" in item.parts:
                    continue
                if item.suffixes[-2:] == [".tar", ".gz"] or item.name.endswith((".tar.gz", ".tgz")):
                    continue
                tar.add(item, arcname=Path(project_dir.name) / rel, recursive=False)
        with open(backup_path, "rb") as f:
            await msg.reply_document(document=f, filename=backup_path.name, caption="✅ Полный бекап бота")
    except Exception as e:
        await msg.reply_text(f"❌ Не удалось создать или отправить бекап: {e}")

async def admin_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    await query.answer()
    if not is_authorized(update) or not state.is_admin_id(uid):
        await query.edit_message_text(f"⛔ Админ-панель доступна только администратору. Ваш Telegram ID: {uid}")
        return
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else "menu"
    if action == "backup":
        await backup_command(update, ctx)
        return

    if action == "allowed" and len(parts) > 2:
        sub = parts[2]
        if sub == "list":
            users = state.get_allowed_users()
            text = "👥 Разрешённые пользователи:\n" + ("\n".join(str(x) for x in users) if users else "список пуст")
            await query.edit_message_text(text, reply_markup=admin_panel_markup())
            return
        if sub in {"add", "remove"}:
            ctx.user_data["mode"] = "admin_allowed_add" if sub == "add" else "admin_allowed_remove"
            await query.edit_message_text("Введи Telegram ID пользователя:")
            return

    if action == "toggle" and len(parts) > 2:
        key = parts[2]
        if key != "service_monitor_enabled":
            await query.edit_message_text("❌ Неизвестная настройка.")
            return
        import app.state as _state
        cfg = _state.get_settings()
        set_setting(key, not bool(cfg.get(key)))
        await query.edit_message_text(admin_panel_text(), reply_markup=admin_panel_markup())
        return
    if action == "set" and len(parts) > 2:
        key = parts[2]
        labels = {
            "max_output": "лимит вывода команд числом от 500 до 20000",
            "max_history_chars": "лимит истории числом от 5000 до 200000",
            "auto_continue_limit": "количество автопродолжений числом от 0 до 10",
            "max_agent_steps": "лимит шагов агента числом от 1 до 50",
        }
        if key not in labels:
            await query.edit_message_text("❌ Неизвестная настройка.")
            return
        PENDING_ADMIN_SETTING[uid] = key
        ctx.user_data["mode"] = "admin_setting"
        await query.edit_message_text(f"Введи новое значение: {labels[key]}")
        return
    if action == "reset_sessions":
        SESSION_HISTORY.clear()
        save_sessions()
        await query.edit_message_text("✅ Все сессии сброшены.", reply_markup=admin_panel_markup())
        return
    await query.edit_message_text(admin_panel_text(), reply_markup=admin_panel_markup())

def proxy_menu_markup():
    cfg = get_proxy_config()
    rows = [[InlineKeyboardButton("🔴 Выключить" if cfg.get("enabled") else "🟢 Включить", callback_data="proxy:toggle")]]
    for i, item in enumerate(cfg.get("items", [])):
        mark = "✅ " if item == cfg.get("current") and cfg.get("enabled") else ""
        rows.append([
            InlineKeyboardButton(f"{mark}{item}", callback_data=f"proxy:select:{i}"),
            InlineKeyboardButton("🗑", callback_data=f"proxy:del:{i}"),
        ])
    rows.append([InlineKeyboardButton("➕ Добавить socks5", callback_data="proxy:add")])
    return InlineKeyboardMarkup(rows)

def proxy_status_text():
    cfg = get_proxy_config()
    if cfg.get("enabled") and cfg.get("current"):
        return f"🧦 Прокси включён:\n{cfg['current']}\n\nВыбери действие:"
    return "🧦 Прокси выключен.\n\nФормат добавления: user:pass@host:port"

async def proxy_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    await query.answer()
    if not is_authorized(update) or not state.is_admin_id(uid):
        await query.edit_message_text(f"⛔ Прокси доступны только администратору. Ваш Telegram ID: {uid}")
        return
    cfg = get_proxy_config()
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else "menu"

    if action == "add":
        ctx.user_data["mode"] = "add_proxy"
        await query.edit_message_text("Введи socks5 прокси в формате:\nuser:pass@host:port")
        return
    if action == "toggle":
        if not cfg.get("items") and not cfg.get("current"):
            ctx.user_data["mode"] = "add_proxy"
            await query.edit_message_text("Сначала добавь socks5 прокси в формате:\nuser:pass@host:port")
            return
        cfg["enabled"] = not cfg.get("enabled")
        if cfg["enabled"] and not cfg.get("current"):
            cfg["current"] = cfg["items"][0]
        save_proxy_config(cfg)
    elif action == "select" and len(parts) > 2:
        try:
            item = cfg.get("items", [])[int(parts[2])]
            cfg["current"] = item
            cfg["enabled"] = True
            save_proxy_config(cfg)
        except Exception:
            pass
    elif action == "del" and len(parts) > 2:
        try:
            item = cfg["items"].pop(int(parts[2]))
            if cfg.get("current") == item:
                cfg["current"] = cfg["items"][0] if cfg["items"] else ""
                cfg["enabled"] = bool(cfg["current"])
            save_proxy_config(cfg)
        except Exception:
            pass
    await query.edit_message_text(proxy_status_text(), reply_markup=proxy_menu_markup())

async def agent_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    await query.answer()
    if not is_authorized(update):
        await query.edit_message_text(f"⛔ Нет доступа. Ваш Telegram ID: {uid}")
        return

    cfg = state.get_agents()
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else "menu"

    if action == "noop":
        return

    if action == "collab":
        state.toggle_collab()
        await query.edit_message_text(agents_menu_text(), reply_markup=agents_menu_markup())
        return

    if action == "noop":
        return

    if action == "select" and len(parts) > 2:
        try:
            item = cfg.get("agents", [])[int(parts[2])]
            state.set_active_agent(item["name"])
        except Exception:
            pass
        await query.edit_message_text(agents_menu_text(), reply_markup=agents_menu_markup())
        return

    if action == "team" and len(parts) > 2:
        try:
            item = cfg.get("agents", [])[int(parts[2])]
            state.toggle_collab_agent(item["name"])
        except Exception:
            pass
        await query.edit_message_text(agents_menu_text(), reply_markup=agents_menu_markup())
        return

    if action == "del" and len(parts) > 2:
        try:
            item = cfg.get("agents", [])[int(parts[2])]
            state.del_agent(item["name"])
        except Exception:
            pass
        await query.edit_message_text(agents_menu_text(), reply_markup=agents_menu_markup())
        return

    if action == "add":
        if not state.get_providers().get("providers"):
            await query.edit_message_text("❌ Сначала добавь провайдера.", reply_markup=providers_menu_markup())
            return
        ctx.user_data.clear()
        ctx.user_data["mode"] = "add_agent_name"
        await query.edit_message_text("Введи название ИИ-агента:", reply_markup=agent_add_step_markup("name"))
        return

    if action == "provider" and len(parts) > 2 and ctx.user_data.get("mode") == "add_agent_provider":
        try:
            provider = state.get_providers().get("providers", [])[int(parts[2])]["name"]
            if not state.add_agent(ctx.user_data.get("agent_name", ""), ctx.user_data.get("agent_model", ""), provider):
                await query.edit_message_text("❌ Не удалось добавить агента.")
                return
            ctx.user_data.clear()
            await query.edit_message_text("✅ ИИ-агент добавлен.", reply_markup=agents_menu_markup())
        except Exception:
            await query.edit_message_text("❌ Не удалось выбрать провайдера.", reply_markup=agent_add_step_markup("provider"))
        return

    await query.edit_message_text(agents_menu_text(), reply_markup=agents_menu_markup())

async def provider_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    await query.answer()
    if not is_authorized(update):
        await query.edit_message_text(f"⛔ Нет доступа. Ваш Telegram ID: {uid}")
        return

    cfg = state.get_providers()
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else "menu"

    if action == "select" and len(parts) > 2:
        try:
            item = cfg.get("providers", [])[int(parts[2])]
            state.set_active_provider(item["name"])
            try:
                from app.gpt import reload_api_config
                reload_api_config()
            except Exception:
                pass
        except Exception:
            pass
        await query.edit_message_text(providers_menu_text(), reply_markup=providers_menu_markup())
        return

    if action == "del" and len(parts) > 2:
        try:
            item = cfg.get("providers", [])[int(parts[2])]
            state.del_provider(item["name"])
            try:
                from app.gpt import reload_api_config
                reload_api_config()
            except Exception:
                pass
        except Exception:
            pass
        await query.edit_message_text(providers_menu_text(), reply_markup=providers_menu_markup())
        return

    if action == "add":
        ctx.user_data.clear()
        ctx.user_data["mode"] = "add_provider_name"
        await query.edit_message_text("Введи название провайдера:", reply_markup=provider_add_step_markup("name"))
        return

    await query.edit_message_text(providers_menu_text(), reply_markup=providers_menu_markup())

async def project_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    await query.answer()
    if not is_authorized(update):
        await query.edit_message_text(f"⛔ Нет доступа. Ваш Telegram ID: {uid}")
        return

    cfg = state.get_projects()
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else "menu"

    if action == "disable":
        state.set_active_project(None)
        reset_session(uid)
        await query.edit_message_text(projects_menu_text(), reply_markup=projects_menu_markup())
        return

    if action == "select" and len(parts) > 2:
        try:
            item = cfg.get("projects", [])[int(parts[2])]
            state.set_active_project(item["name"])
            reset_session(uid)
        except Exception:
            pass
        await query.edit_message_text(projects_menu_text(), reply_markup=projects_menu_markup())
        return

    if action == "del" and len(parts) > 2:
        try:
            item = cfg.get("projects", [])[int(parts[2])]
            state.del_project(item["name"])
            reset_session(uid)
        except Exception:
            pass
        await query.edit_message_text(projects_menu_text(), reply_markup=projects_menu_markup())
        return

    if action == "deploy_config":
        ctx.user_data["mode"] = "project_deploy_config"
        await query.edit_message_text(
            "Введи Docker/K8s настройки активного проекта в формате:\n"
            "image | build_cmd | push_cmd | deploy_cmd | namespace | deployment | container\n\n"
            "Можно оставить поле пустым. Если deploy_cmd пустой, бот выполнит kubectl set image + rollout status по namespace/deployment/container."
        )
        return

    if action == "deploy":
        await query.edit_message_text("🚀 Запускаю деплой активного проекта...")
        result = _run_project_deploy(uid)
        if len(result) > 3500:
            result = result[-3500:]
        await query.message.reply_text("🚀 Деплой завершён.\n\n" + result, reply_markup=projects_menu_markup())
        return

    if action == "k8s_ai":
        project = state.active_project()
        if not project:
            await query.edit_message_text("❌ Активный проект не выбран.", reply_markup=projects_menu_markup())
            return
        ctx.user_data["mode"] = "project_k8s_ai"
        await query.edit_message_text(
            "Опиши, что изменить в Kubernetes для активного проекта.\n"
            "Например: увеличь replicas до 2, смени image, добавь env в deployment, измени ConfigMap.\n\n"
            "Перед применением бот покажет план и попросит подтверждение."
        )
        return

    if action == "k8s_apply":
        plan = ctx.user_data.get("project_k8s_ai_plan")
        project = state.active_project()
        if not plan or not project:
            await query.edit_message_text("❌ План не найден или проект не выбран.", reply_markup=projects_menu_markup())
            return
        try:
            result = apply_project_k8s_operations(plan, project)
        except Exception as e:
            result = f"❌ Не удалось применить изменения: {e}"
        ctx.user_data.pop("project_k8s_ai_plan", None)
        ctx.user_data.pop("mode", None)
        await query.edit_message_text(result, reply_markup=projects_menu_markup())
        return

    if action == "k8s_cancel":
        ctx.user_data.pop("project_k8s_ai_plan", None)
        ctx.user_data.pop("mode", None)
        await query.edit_message_text("❌ Изменения Kubernetes отменены.", reply_markup=projects_menu_markup())
        return

    if action == "add":
        ctx.user_data["mode"] = "add_project"
        await query.edit_message_text(
            "Введи проект в формате:\nНазвание | Сервер | /путь/к/проекту | инструкции/промт\n\n"
            "Сервер: Главный сервер или имя из меню серверов.\n"
            "Например:\nDevOps bot | Швейцария | /root/DevOps | Не перезапускать контейнер без явной команды."
        )
        return

    await query.edit_message_text(projects_menu_text(), reply_markup=projects_menu_markup())

