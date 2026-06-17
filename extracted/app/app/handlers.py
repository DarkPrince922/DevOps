import asyncio
import json
import logging
import tempfile
import os
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import app.state as state
from app.gpt import transcribe_audio, analyze_image, gpt_call
from app.executor import ssh_exec as direct_ssh_exec, local_exec as direct_local_exec
logger = logging.getLogger(__name__)


def _extract_json_object(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _format_k8s_plan(plan):
    lines = ["🤖 План K8s ИИ", ""]
    if plan.get("summary"):
        lines.append(str(plan.get("summary")))
    if plan.get("explanation"):
        lines.append(str(plan.get("explanation")))
    ops = plan.get("operations") or []
    if ops:
        lines.append("\nОперации:")
        for i, op in enumerate(ops, 1):
            kind = op.get("kind") or "Deployment"
            name = op.get("name") or op.get("deployment") or "по умолчанию"
            ns = op.get("namespace") or "по умолчанию"
            lines.append(f"{i}. {op.get('op')} {kind}/{name} ns={ns}")
        lines.append("\nПодтверди применение кнопкой ниже.")
    else:
        lines.append("\nОпераций для применения нет.")
    return "\n".join(lines)[:3500]


from app.core import (
    ACTIVE_TASKS, CONFIRMED_TASKS, PENDING_ADMIN_SETTING, PENDING_CONFIRM, PENDING_CONTINUE,
    RUNNING_TASKS, SERVERS_FILE, SESSION_HISTORY, STOP_FLAGS, admin_panel_markup,
    admin_panel_text, agents_menu_markup, agents_menu_text, providers_menu_markup, providers_menu_text, build_status, get_proxy_config,
    get_servers, is_authorized,
    kb, kb_docker, kb_monitoring, kb_servers, kb_settings, normalize_socks5,
    reset_session, run_agent, save_json, save_proxy_config, save_sessions,
    send_text, set_setting
)
from app.ui import provider_add_step_markup, agent_add_step_markup

from app.commands import (
    DIRECT_COMMANDS, get_preapproved_commands, save_preapproved_commands,
    preapproved_edit_markup, preapproved_edit_cmd_markup, show_preapproved_commands,
    preapproved_callback, run_direct_command
)
from app.monitoring_handlers import send_load_graphs
from app.k8s_handlers import show_k8s_menu, handle_k8s_input, handle_k8s_document, project_k8s_ai_system, apply_project_k8s_operations
from app.settings_handlers import (
    admin_command, backup_command, admin_callback, agent_callback, provider_callback, proxy_callback,
    proxy_menu_markup, proxy_status_text, projects_menu_markup, projects_menu_text, project_callback
)
from app.server_handlers import (
    delete_server_callback, server_buttons_markup, auth_type_markup, server_callback, host_fingerprint_markup
)






async def handle_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(update):
        await update.message.reply_text(f"⛔ Нет доступа. Ваш Telegram ID: {uid}")
        return
    if uid in ACTIVE_TASKS:
        await update.message.reply_text("⏳ Предыдущая задача ещё выполняется. Дождись завершения или нажми ⏹ СТОП.")
        return

    ACTIVE_TASKS.add(uid)
    RUNNING_TASKS[uid] = asyncio.current_task()
    status = await update.message.reply_text(build_status(uid, 0, "Анализирую изображение..."))
    tmp_path = None
    try:
        if update.message.photo:
            item = update.message.photo[-1]
            suffix = ".jpg"
        else:
            item = update.message.document
            suffix = Path(item.file_name or "image.jpg").suffix or ".jpg"
        tg_file = await ctx.bot.get_file(item.file_id)
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        await tg_file.download_to_drive(tmp_path)
        caption = (update.message.caption or "").strip()
        image_text, err = await asyncio.to_thread(analyze_image, tmp_path, caption or None)
        if err:
            answer = f"❌ Не удалось обработать изображение: {err}"
        elif caption:
            state.STATUS_TEXT[uid] = "Изображение распознано, выполняю..."
            goal = f"Пользователь прислал изображение с подписью: {caption}\n\nАнализ изображения:\n{image_text}\n\nВыполни просьбу пользователя с учётом изображения."
            answer = await run_agent(goal, update, status, uid)
        else:
            answer = "🖼 Анализ изображения:\n" + image_text
    except asyncio.CancelledError:
        answer = "⏹ Остановлено."
    except Exception as e:
        answer = f"Ошибка обработки изображения: {e}"
    finally:
        ACTIVE_TASKS.discard(uid)
        RUNNING_TASKS.pop(uid, None)
        if tmp_path:
            try: os.remove(tmp_path)
            except Exception:
                logger.exception("cleanup failed")
    try: await status.delete()
    except Exception:
        logger.exception("telegram cleanup failed")
    await send_text(update, answer)

async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(update):
        await update.message.reply_text(f"⛔ Нет доступа. Ваш Telegram ID: {uid}")
        return
    if uid in ACTIVE_TASKS:
        await update.message.reply_text("⏳ Предыдущая задача ещё выполняется. Дождись завершения или нажми ⏹ СТОП.")
        return

    ACTIVE_TASKS.add(uid)
    RUNNING_TASKS[uid] = asyncio.current_task()
    status = await update.message.reply_text(build_status(uid, 0, "Слушаю голосовое..."))
    tmp_path = None
    try:
        voice = update.message.voice or update.message.audio
        tg_file = await ctx.bot.get_file(voice.file_id)
        fd, tmp_path = tempfile.mkstemp(suffix=".ogg")
        os.close(fd)
        await tg_file.download_to_drive(tmp_path)
        text, err = await asyncio.to_thread(transcribe_audio, tmp_path)
        if err:
            answer = f"❌ Не удалось распознать голосовое: {err}"
        else:
            state.STATUS_TEXT[uid] = "Голос распознан, выполняю..."
            answer = await run_agent(text, update, status, uid)
    except asyncio.CancelledError:
        answer = "⏹ Остановлено."
    except Exception as e:
        answer = f"Ошибка обработки голосового: {e}"
    finally:
        ACTIVE_TASKS.discard(uid)
        RUNNING_TASKS.pop(uid, None)
        if tmp_path:
            try: os.remove(tmp_path)
            except Exception:
                logger.exception("cleanup failed")
    try: await status.delete()
    except Exception:
        logger.exception("telegram cleanup failed")
    await send_text(update, answer)


TEXT_FILE_MAX_BYTES = 2 * 1024 * 1024
TEXT_FILE_EXTENSIONS = {
    ".txt", ".text", ".log", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".xml",
    ".html", ".htm", ".css", ".js", ".ts", ".tsx", ".jsx", ".py", ".sh", ".bash",
    ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".env", ".example", ".dockerfile",
    ".sql", ".php", ".rb", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".swift", ".kt", ".kts", ".r", ".lua", ".pl", ".vue", ".svelte"
}
TEXT_FILE_MIME_PREFIXES = ("text/",)
TEXT_FILE_MIME_TYPES = {
    "application/json", "application/xml", "application/javascript", "application/x-javascript",
    "application/typescript", "application/x-sh", "application/x-shellscript", "application/x-yaml",
    "application/yaml", "application/toml", "application/sql", "application/octet-stream"
}


def _decode_text_file(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def _is_supported_text_document(doc) -> bool:
    filename = doc.file_name or "file.txt"
    suffix = Path(filename).suffix.lower()
    mime = (doc.mime_type or "").lower()
    if suffix in TEXT_FILE_EXTENSIONS:
        return True
    if mime.startswith(TEXT_FILE_MIME_PREFIXES) or mime in TEXT_FILE_MIME_TYPES:
        return True
    return False


async def handle_text_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(update):
        await update.message.reply_text(f"⛔ Нет доступа. Ваш Telegram ID: {uid}")
        return
    if uid in ACTIVE_TASKS:
        await update.message.reply_text("⏳ Предыдущая задача ещё выполняется. Дождись завершения или нажми ⏹ СТОП.")
        return

    doc = update.message.document
    filename = doc.file_name or "file.txt"
    suffix = Path(filename).suffix or ".txt"
    ACTIVE_TASKS.add(uid)
    RUNNING_TASKS[uid] = asyncio.current_task()
    if not _is_supported_text_document(doc):
        await update.message.reply_text("❌ Этот тип файла не поддерживается для чтения как текст.")
        return
    if doc.file_size and doc.file_size > TEXT_FILE_MAX_BYTES:
        await update.message.reply_text(f"❌ Файл слишком большой для чтения как текст (лимит {TEXT_FILE_MAX_BYTES // 1024 // 1024} МБ).")
        return

    status = await update.message.reply_text(build_status(uid, 0, "Читаю файл..."))
    tmp_path = None
    try:
        tg_file = await ctx.bot.get_file(doc.file_id)
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        await tg_file.download_to_drive(tmp_path)
        data = Path(tmp_path).read_bytes()
        if b"\x00" in data[:4096]:
            answer = "❌ Файл похож на бинарный, чтение как текста отменено."
            await send_text(update, answer)
            return
        file_text = _decode_text_file(data)
        caption = (update.message.caption or "").strip()
        if caption:
            state.STATUS_TEXT[uid] = "Файл прочитан, выполняю..."
            goal = f"Пользователь прислал файл {filename} с подписью: {caption}\n\nСодержимое файла:\n{file_text}\n\nВыполни просьбу пользователя с учётом файла."
            answer = await run_agent(goal, update, status, uid)
        else:
            answer = f"📄 Содержимое файла {filename}:\n" + file_text
    except asyncio.CancelledError:
        answer = "⏹ Остановлено."
    except Exception as e:
        answer = f"Ошибка обработки файла: {e}"
    finally:
        ACTIVE_TASKS.discard(uid)
        RUNNING_TASKS.pop(uid, None)
        if tmp_path:
            try: os.remove(tmp_path)
            except Exception:
                logger.exception("cleanup failed")
    try: await status.delete()
    except Exception:
        logger.exception("telegram cleanup failed")
    await send_text(update, answer)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(f"⛔ Нет доступа. Ваш Telegram ID: {update.effective_user.id}")
        return
    await update.message.reply_text(
        "🚀 Агент готов. Пиши что нужно сделать.\n\n♻️ Сброс сессии — очистить контекст. ⏹ СТОП — остановить задачу.",
        reply_markup=kb()
    )

async def confirm_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    await query.answer()
    if not is_authorized(update):
        await query.edit_message_text(f"⛔ Нет доступа. Ваш Telegram ID: {uid}")
        return
    if uid not in PENDING_CONFIRM:
        await query.edit_message_text("Подтверждение уже неактуально.")
        return
    if query.data == "confirm:no":
        PENDING_CONFIRM.pop(uid, None)
        await query.edit_message_text("❌ Отменено.")
        return
    if uid in ACTIVE_TASKS:
        await query.edit_message_text("⏳ Предыдущая задача ещё выполняется.")
        return

    text = PENDING_CONFIRM.pop(uid)
    CONFIRMED_TASKS.add(uid)
    await query.edit_message_text("✅ Подтверждено. Выполняю...")
    ACTIVE_TASKS.add(uid)
    RUNNING_TASKS[uid] = asyncio.current_task()
    status = await query.message.reply_text(build_status(uid, 0, "Запускаю..."))
    try:
        answer = await run_agent(text, update, status, uid)
    except asyncio.CancelledError:
        answer = "⏹ Остановлено."
    except Exception as e:
        answer = f"Ошибка: {e}"
    finally:
        ACTIVE_TASKS.discard(uid)
        RUNNING_TASKS.pop(uid, None)
    try: await status.delete()
    except Exception:
        logger.exception("telegram cleanup failed")
    await send_text(update, answer)


async def continue_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    await query.answer()
    if not is_authorized(update):
        await query.edit_message_text(f"⛔ Нет доступа. Ваш Telegram ID: {uid}")
        return
    if uid not in PENDING_CONTINUE:
        await query.edit_message_text("Продолжение уже неактуально.")
        return
    if uid in ACTIVE_TASKS:
        await query.edit_message_text("⏳ Предыдущая задача ещё выполняется.")
        return

    text = PENDING_CONTINUE.pop(uid)
    await query.edit_message_text("▶️ Продолжаю выполнение...")
    ACTIVE_TASKS.add(uid)
    RUNNING_TASKS[uid] = asyncio.current_task()
    status = await query.message.reply_text(build_status(uid, 0, "Запускаю..."))
    try:
        answer = await run_agent(text, update, status, uid, continue_existing=True)
    except asyncio.CancelledError:
        answer = "⏹ Остановлено."
    except Exception as e:
        answer = f"Ошибка: {e}"
    finally:
        ACTIVE_TASKS.discard(uid)
        RUNNING_TASKS.pop(uid, None)
    try: await status.delete()
    except Exception:
        logger.exception("telegram cleanup failed")
    await send_text(update, answer)


async def handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid  = update.effective_user.id
    if not is_authorized(update):
        await update.message.reply_text(f"⛔ Нет доступа. Ваш Telegram ID: {uid}")
        return
    mode = ctx.user_data.get("mode")

    if text == "⏹ СТОП":
        ctx.user_data.clear()
        STOP_FLAGS[uid] = True
        PENDING_CONFIRM.pop(uid, None)
        CONFIRMED_TASKS.discard(uid)
        proc = state.ACTIVE_PROCESSES.get(uid)
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, 15)
            except Exception:
                try: proc.kill()
                except Exception:
                    logger.exception("process kill failed")
        chan = state.ACTIVE_CHANNELS.get(uid)
        if chan:
            try: chan.close()
            except Exception:
                logger.exception("cleanup failed")
        task = RUNNING_TASKS.pop(uid, None)
        ACTIVE_TASKS.discard(uid)
        if task and not task.done():
            task.cancel()
        await update.message.reply_text("⏹ Аварийная остановка выполнена.", reply_markup=kb())
        return

    if text == "❌ Отмена":
        ctx.user_data.clear()
        PENDING_ADMIN_SETTING.pop(uid, None)
        PENDING_CONFIRM.pop(uid, None)
        CONFIRMED_TASKS.discard(uid)
        await update.message.reply_text("❌ Действие отменено.", reply_markup=kb())
        return

    if text == "♻️ Сброс сессии":
        ctx.user_data.clear()
        PENDING_ADMIN_SETTING.pop(uid, None)
        reset_session(uid)
        await update.message.reply_text("♻️ Сессия сброшена.", reply_markup=kb())
        return

    if text == "⬅️ Назад":
        ctx.user_data.pop("section", None)
        await update.message.reply_text("Главное меню:", reply_markup=kb())
        return

    if text == "📡 Статус":
        await run_direct_command(update, text, DIRECT_COMMANDS[text])
        return

    if text == "✅ Команды":
        await show_preapproved_commands(update)
        return

    if text == "📊 Мониторинг":
        ctx.user_data["section"] = "monitoring"
        await update.message.reply_text("📊 Мониторинг:", reply_markup=kb_monitoring())
        return

    if text == "🐳 Docker":
        if ctx.user_data.get("section") == "docker":
            await run_direct_command(update, "🐳 Docker", "docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'")
        else:
            ctx.user_data["section"] = "docker"
            await update.message.reply_text("🐳 Docker:", reply_markup=kb_docker())
        return

    if text == "🌐 Серверы":
        await update.message.reply_text("🌐 Серверы:", reply_markup=kb_servers())
        return

    if text == "⚙️ Настройки":
        await update.message.reply_text("⚙️ Настройки:", reply_markup=kb_settings())
        return

    if text == "☸️ Kubernetes":
        await show_k8s_menu(update)
        return

    if text == "🛠 Админ панель":
        if not state.is_admin_id(uid):
            await update.message.reply_text(f"⛔ Админ-панель доступна только администратору. Ваш Telegram ID: {uid}")
            return
        await update.message.reply_text(admin_panel_text(), reply_markup=admin_panel_markup())
        return

    if text == "📦 Бекап бота":
        await backup_command(update, ctx)
        return

    if text == "🤖 ИИ-агенты":
        await update.message.reply_text(agents_menu_text(), reply_markup=agents_menu_markup())
        return

    if text == "🏪 Провайдеры":
        await update.message.reply_text(providers_menu_text(), reply_markup=providers_menu_markup())
        return

    if text == "🧦 Прокси":
        await update.message.reply_text(proxy_status_text(), reply_markup=proxy_menu_markup())
        return

    if text == "📁 Проекты":
        await update.message.reply_text(projects_menu_text(), reply_markup=projects_menu_markup())
        return

    if mode in {"k8s_config", "k8s_secret", "k8s_image", "k8s_kubeconfig"}:
        handled = await handle_k8s_input(update, ctx, mode)
        if handled:
            return

    if mode == "admin_allowed_add":
        try:
            state.add_allowed_user(int(text.strip()))
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось добавить пользователя: {e}")
            return
        ctx.user_data.clear()
        await update.message.reply_text("✅ Пользователь добавлен.", reply_markup=kb())
        await update.message.reply_text(admin_panel_text(), reply_markup=admin_panel_markup())
        return

    if mode == "admin_allowed_remove":
        try:
            state.remove_allowed_user(int(text.strip()))
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось удалить пользователя: {e}")
            return
        ctx.user_data.clear()
        await update.message.reply_text("✅ Пользователь удалён.", reply_markup=kb())
        await update.message.reply_text(admin_panel_text(), reply_markup=admin_panel_markup())
        return

    if mode == "admin_setting":
        key = PENDING_ADMIN_SETTING.get(uid)
        try:
            set_setting(key, text)
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось сохранить: {e}")
            return
        PENDING_ADMIN_SETTING.pop(uid, None)
        ctx.user_data.clear()
        await update.message.reply_text("✅ Настройка сохранена.", reply_markup=kb())
        await update.message.reply_text(admin_panel_text(), reply_markup=admin_panel_markup())
        return

    if mode == "add_provider_name":
        parts = [x.strip() for x in text.split("|", 1)]
        ctx.user_data["provider_name"] = parts[0]
        if len(parts) == 2:
            url = parts[1].rstrip("/")
            if not url.startswith(("http://", "https://")):
                await update.message.reply_text("❌ Base URL должен начинаться с http:// или https://", reply_markup=provider_add_step_markup("base_url"))
                ctx.user_data["mode"] = "add_provider_url"
                return
            ctx.user_data["provider_url"] = url
            ctx.user_data["mode"] = "add_provider_key"
            await update.message.reply_text("✅ Название и Base URL приняты. Теперь введи API key или отправь '-' чтобы использовать ключ из .env:", reply_markup=provider_add_step_markup("api_key"))
            return
        ctx.user_data["mode"] = "add_provider_url"
        await update.message.reply_text("✅ Название принято. Теперь введи Base URL:", reply_markup=provider_add_step_markup("base_url"))
        return

    if mode == "add_provider_url":
        url = text.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            await update.message.reply_text("❌ Base URL должен начинаться с http:// или https://", reply_markup=provider_add_step_markup("base_url"))
            return
        ctx.user_data["provider_url"] = url
        ctx.user_data["mode"] = "add_provider_key"
        await update.message.reply_text("✅ Base URL принят. Теперь введи API key или отправь '-' чтобы использовать ключ из .env:", reply_markup=provider_add_step_markup("api_key"))
        return

    if mode == "add_provider_key":
        api_key = "" if text.strip() in ("-", "—") else text.strip()
        if not state.add_provider(ctx.user_data.get("provider_name", ""), ctx.user_data.get("provider_url", ""), api_key):
            await update.message.reply_text("❌ Не удалось добавить провайдера.")
            return
        ctx.user_data.clear()
        await update.message.reply_text("✅ Провайдер добавлен.", reply_markup=kb())
        await update.message.reply_text(providers_menu_text(), reply_markup=providers_menu_markup())
        return

    if mode == "add_agent_name":
        ctx.user_data["agent_name"] = text.strip()
        ctx.user_data["mode"] = "add_agent_model"
        await update.message.reply_text("✅ Название принято. Теперь введи модель. Можно указать как developer/model, так и просто model:", reply_markup=agent_add_step_markup("model"))
        return

    if mode == "add_agent_model":
        ctx.user_data["agent_model"] = text.strip()
        ctx.user_data["mode"] = "add_agent_provider"
        await update.message.reply_text("✅ Модель принята. Теперь выбери провайдера для этой модели:", reply_markup=agent_add_step_markup("provider"))
        return

    if mode == "add_agent_provider":
        provider = text.strip()
        if provider not in [p["name"] for p in state.get_providers().get("providers", [])]:
            await update.message.reply_text("❌ Провайдер не найден. Введи точное название из списка провайдеров.", reply_markup=agent_add_step_markup("provider"))
            return
        if not state.add_agent(ctx.user_data.get("agent_name", ""), ctx.user_data.get("agent_model", ""), provider):
            await update.message.reply_text("❌ Не удалось добавить агента.")
            return
        ctx.user_data.clear()
        await update.message.reply_text("✅ ИИ-агент добавлен.", reply_markup=kb())
        await update.message.reply_text(agents_menu_text(), reply_markup=agents_menu_markup())
        return

    if mode == "project_deploy_config":
        project = state.active_project()
        if not project:
            await update.message.reply_text("❌ Активный проект не выбран.")
            ctx.user_data.clear()
            return
        parts = [x.strip() for x in text.split("|")]
        if len(parts) < 7:
            parts += [""] * (7 - len(parts))
        image, build_cmd, push_cmd, deploy_cmd, namespace, deployment, container = parts[:7]
        if not state.update_project_deploy(
            project["name"],
            docker_image=image, build_cmd=build_cmd, push_cmd=push_cmd, deploy_cmd=deploy_cmd,
            k8s_namespace=namespace, k8s_deployment=deployment, k8s_container=container,
        ):
            await update.message.reply_text("❌ Не удалось обновить настройки проекта.")
            return
        reset_session(uid)
        ctx.user_data.clear()
        await update.message.reply_text("✅ Docker/K8s настройки проекта сохранены.", reply_markup=kb())
        await update.message.reply_text(projects_menu_text(), reply_markup=projects_menu_markup())
        return

    if mode == "project_k8s_ai":
        project = state.active_project()
        if not project:
            ctx.user_data.clear()
            await update.message.reply_text("❌ Активный проект не выбран.")
            return
        await update.message.reply_text("🤖 Готовлю план изменений Kubernetes...")
        msg = gpt_call([
            {"role": "system", "content": project_k8s_ai_system(project)},
            {"role": "user", "content": text},
        ])
        try:
            plan = _extract_json_object(msg.get("content") or "")
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось разобрать план ИИ: {e}", reply_markup=kb())
            return
        ctx.user_data["project_k8s_ai_plan"] = plan
        await update.message.reply_text(
            _format_k8s_plan(plan),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Применить", callback_data="project:k8s_apply"),
                InlineKeyboardButton("❌ Отмена", callback_data="project:k8s_cancel"),
            ]])
        )
        return

    if mode == "add_project":
        parts = [x.strip() for x in text.split("|", 3)]
        if len(parts) < 4:
            await update.message.reply_text("❌ Неверный формат. Нужно: Название | Сервер | /путь | инструкции/промт")
            return
        name, server, path, prompt = parts
        servers = get_servers()
        server = state.normalize_server_name(server, servers)
        if server != "Главный сервер" and server not in servers:
            await update.message.reply_text("❌ Сервер не найден. Укажи 'Главный сервер' или имя из списка серверов.")
            return
        if not state.add_project(name, server, path, prompt):
            await update.message.reply_text("❌ Не удалось добавить проект. Проверь название, сервер и абсолютный путь.")
            return
        state.set_active_project(name)
        reset_session(uid)
        ctx.user_data.clear()
        await update.message.reply_text("✅ Проект добавлен и выбран активным.", reply_markup=kb())
        await update.message.reply_text(projects_menu_text(), reply_markup=projects_menu_markup())
        return

    if mode == "add_proxy":
        proxy = normalize_socks5(text)
        if not proxy:
            await update.message.reply_text("❌ Неверный формат. Нужно: user:pass@host:port")
            return
        cfg = get_proxy_config()
        if proxy not in cfg["items"]:
            cfg["items"].append(proxy)
        cfg["current"] = proxy
        cfg["enabled"] = True
        save_proxy_config(cfg)
        ctx.user_data.clear()
        await update.message.reply_text("✅ Прокси добавлен и включён.", reply_markup=kb())
        return

    if uid in PENDING_CONFIRM:
        if text == "✅ Подтверждаю":
            text = PENDING_CONFIRM.pop(uid)
            CONFIRMED_TASKS.add(uid)
        elif text == "❌ Отмена":
            PENDING_CONFIRM.pop(uid, None)
            await update.message.reply_text("❌ Отменено.")
            return
        else:
            await update.message.reply_text(
                "Есть действие, ожидающее подтверждения. Выбери действие:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Подтвердить", callback_data="confirm:yes"),
                    InlineKeyboardButton("❌ Отмена", callback_data="confirm:no"),
                ]])
            )
            return

    if uid in ACTIVE_TASKS:
        await update.message.reply_text("⏳ Предыдущая задача ещё выполняется. Дождись завершения или нажми ⏹ СТОП.")
        return

    if text == "📈 Графики":
        await send_load_graphs(update)
        return

    if text in DIRECT_COMMANDS:
        await run_direct_command(update, text, DIRECT_COMMANDS[text])
        return

    shortcuts = {
        "📊 CPU":      "покажи загрузку CPU: top -bn1 | head -5 и ps aux --sort=-%cpu | head -6",
        "💾 RAM":      "покажи память: free -h",
        "💽 Диск":     "покажи место на диске: df -h",
        "🐳 Docker":   "покажи запущенные docker контейнеры: docker ps",
        "📋 Логи":     "покажи последние 30 строк логов docker compose: docker compose logs --tail=30",
        "🔍 Процессы": "покажи топ процессов по CPU и RAM: ps aux --sort=-%cpu | head -10",
        "🌐 Сеть":     "покажи открытые порты: ss -tlnp",
        "🕐 Аптайм":   "покажи аптайм: uptime",
        "💻 Система":  "покажи информацию о системе: uname -a && cat /etc/os-release | head -3 && uptime",
        "🧹 Очистить": "очисти неиспользуемые docker образы: docker system prune -f",
    }

    if text in shortcuts:
        text = shortcuts[text]

    elif text == "📋 Список серверов":
        servers = get_servers()
        if not servers:
            await update.message.reply_text("Серверов нет.", reply_markup=kb_servers())
        else:
            await update.message.reply_text("Выбери сервер:", reply_markup=server_buttons_markup())
        return

    elif text == "➕ Добавить":
        ctx.user_data["mode"] = "add_name"
        ctx.user_data.pop("comment", None)
        await update.message.reply_text("Введи имя сервера:")
        return

    elif text == "➖ Удалить":
        servers = get_servers()
        if not servers:
            await update.message.reply_text("Серверов нет.")
            return
        buttons = [[InlineKeyboardButton(f"🗑 {name}", callback_data=f"delserver:{i}")] for i, name in enumerate(servers.keys())]
        await update.message.reply_text("Выбери сервер для удаления:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if mode == "precmd_add_title":
        title = text.strip()
        if not title:
            await update.message.reply_text("❌ Название не может быть пустым. Введи название команды:")
            return
        ctx.user_data["precmd_new_title"] = title
        ctx.user_data["mode"] = "precmd_add_cmd"
        await update.message.reply_text("Введи команду:")
        return

    if mode == "precmd_add_cmd":
        command = text.strip()
        if not command:
            await update.message.reply_text("❌ Команда не может быть пустой. Введи команду:")
            return
        title = ctx.user_data.get("precmd_new_title", "Новая команда")
        items = get_preapproved_commands()
        items.append({"title": title, "command": command})
        save_preapproved_commands(items)
        ctx.user_data.clear()
        await update.message.reply_text(f"✅ Команда добавлена: {title}", reply_markup=kb())
        await update.message.reply_text("⚙️ Редактирование предодобренных команд:", reply_markup=preapproved_edit_markup())
        return

    if mode in ("precmd_set_title", "precmd_set_cmd"):
        items = get_preapproved_commands()
        idx = ctx.user_data.get("precmd_edit_idx")
        if not isinstance(idx, int) or idx < 0 or idx >= len(items):
            ctx.user_data.clear()
            await update.message.reply_text("Команда не найдена.", reply_markup=kb())
            return
        value = text.strip()
        if not value:
            await update.message.reply_text("❌ Значение не может быть пустым. Введи ещё раз:")
            return
        if mode == "precmd_set_title":
            items[idx]["title"] = value
        else:
            items[idx]["command"] = value
        save_preapproved_commands(items)
        title = items[idx]["title"]
        ctx.user_data.clear()
        await update.message.reply_text(f"✅ Команда сохранена: {title}", reply_markup=kb())
        await update.message.reply_text(f"✏️ {title}\n\nКоманда:\n{items[idx]['command']}", reply_markup=preapproved_edit_cmd_markup(idx))
        return

    if mode == "add_name":
        ctx.user_data["name"] = text
        ctx.user_data["mode"] = "add_host"
        await update.message.reply_text("Хост (IP или домен):")
        return

    if mode == "add_host":
        ctx.user_data["host"] = text
        ctx.user_data["mode"] = "add_user"
        await update.message.reply_text("Пользователь:")
        return

    if mode == "add_user":
        ctx.user_data["user"] = text
        ctx.user_data["mode"] = "add_auth"
        await update.message.reply_text(
            "Выбери тип авторизации кнопкой:",
            reply_markup=auth_type_markup()
        )
        return

    if mode == "add_auth":
        await update.message.reply_text(
            "Тип авторизации нужно выбрать кнопкой ниже:",
            reply_markup=auth_type_markup()
        )
        return

    if mode == "add_pass":
        ctx.user_data["password"] = text
        ctx.user_data["mode"] = "add_comment"
        await update.message.reply_text("Комментарий к серверу (или '-' чтобы пропустить):")
        return

    if mode == "add_key":
        ctx.user_data["private_key"] = text
        ctx.user_data["mode"] = "add_key_passphrase"
        await update.message.reply_text("Passphrase ключа, если есть, или '-' если ключ без passphrase:")
        return

    if mode == "add_key_passphrase":
        if text.strip() != "-":
            ctx.user_data["passphrase"] = text
        ctx.user_data["mode"] = "add_comment"
        await update.message.reply_text("Комментарий к серверу (или '-' чтобы пропустить):")
        return

    if mode == "add_comment":
        servers = get_servers()
        name = ctx.user_data["name"]
        cfg = {
            "host": ctx.user_data["host"],
            "user": ctx.user_data["user"],
            "auth_type": ctx.user_data.get("auth_type", "password")
        }
        if cfg["auth_type"] == "key":
            cfg["private_key"] = ctx.user_data["private_key"]
            if ctx.user_data.get("passphrase"):
                cfg["passphrase"] = ctx.user_data["passphrase"]
        else:
            cfg["password"] = ctx.user_data["password"]
        if text.strip() != "-":
            cfg["comment"] = text.strip()

        await update.message.reply_text("🔌 Проверяю SSH-соединение без ИИ...")
        test_result = await asyncio.to_thread(direct_ssh_exec, cfg, "echo OK", False, None)
        ok = test_result.strip() == "OK"

        servers[name] = cfg
        save_json(SERVERS_FILE, servers)
        if "HOST_FINGERPRINT_CHANGED:" in test_result:
            idx = list(servers.keys()).index(name)
            ctx.user_data.clear()
            await update.message.reply_text(
                f"⚠️ Fingerprint SSH-хоста для «{name}» изменился. Если сервер переустановлен, подтверди перезапись кнопкой.",
                reply_markup=host_fingerprint_markup(idx)
            )
            return
        ctx.user_data.clear()
        status = "✅ соединение успешно" if ok else f"⚠️ соединение не удалось: {test_result[:500]}"
        await update.message.reply_text(f"✅ Сервер {name} добавлен.\n{status}", reply_markup=kb_servers())
        return

    if mode == "server_comment":
        servers = get_servers()
        name = ctx.user_data.get("server_edit_name")
        if name in servers:
            if text.strip() == "-":
                servers[name].pop("comment", None)
            else:
                servers[name]["comment"] = text.strip()
            save_json(SERVERS_FILE, servers)
            await update.message.reply_text(f"✅ Комментарий для «{name}» сохранён.", reply_markup=kb_servers())
        else:
            await update.message.reply_text("Сервер не найден.", reply_markup=kb_servers())
        ctx.user_data.clear()
        return

    if mode == "server_rename":
        servers = get_servers()
        old_name = ctx.user_data.get("server_edit_name")
        new_name = text.strip()
        if not new_name:
            await update.message.reply_text("❌ Название не может быть пустым.", reply_markup=kb_servers())
        elif old_name not in servers:
            await update.message.reply_text("Сервер не найден.", reply_markup=kb_servers())
        elif new_name != old_name and new_name in servers:
            await update.message.reply_text("❌ Сервер с таким названием уже есть.", reply_markup=kb_servers())
        else:
            if new_name != old_name:
                items = list(servers.items())
                servers = {new_name if k == old_name else k: v for k, v in items}
                save_json(SERVERS_FILE, servers)
                reset_session(uid)
            await update.message.reply_text(f"✅ Сервер переименован: «{old_name}» → «{new_name}»", reply_markup=kb_servers())
        ctx.user_data.clear()
        return

    if mode == "server_password":
        servers = get_servers()
        name = ctx.user_data.get("server_edit_name")
        if name not in servers:
            await update.message.reply_text("Сервер не найден.", reply_markup=kb_servers())
            ctx.user_data.clear()
            return
        cfg = dict(servers[name])
        cfg["password"] = text
        await update.message.reply_text("🔌 Проверяю SSH-соединение без ИИ...")
        test_result = await asyncio.to_thread(direct_ssh_exec, cfg, "echo OK", False, None)
        ok = test_result.strip() == "OK"
        servers[name] = cfg
        save_json(SERVERS_FILE, servers)
        if "HOST_FINGERPRINT_CHANGED:" in test_result:
            idx = list(servers.keys()).index(name)
            ctx.user_data.clear()
            await update.message.reply_text(
                f"⚠️ Fingerprint SSH-хоста для «{name}» изменился. Если сервер переустановлен, подтверди перезапись кнопкой.",
                reply_markup=host_fingerprint_markup(idx)
            )
            return
        reset_session(uid)
        ctx.user_data.clear()
        status = "✅ соединение успешно" if ok else f"⚠️ соединение не удалось: {test_result[:500]}"
        await update.message.reply_text(f"✅ Пароль для «{name}» обновлён.\n{status}", reply_markup=kb_servers())
        return

    ACTIVE_TASKS.add(uid)
    RUNNING_TASKS[uid] = asyncio.current_task()
    status = await update.message.reply_text(build_status(uid, 0, "Запускаю..."))
    try:
        answer = await run_agent(text, update, status, uid)
    except asyncio.CancelledError:
        answer = "⏹ Остановлено."
    except Exception as e:
        answer = f"Ошибка: {e}"
    finally:
        ACTIVE_TASKS.discard(uid)
        RUNNING_TASKS.pop(uid, None)

    try: await status.delete()
    except Exception:
        logger.exception("telegram cleanup failed")

    await send_text(update, answer)

