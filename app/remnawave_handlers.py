import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import app.remnawave as rw
from app.core import is_authorized


def rw_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Статистика", callback_data="rw:stats")],
        [InlineKeyboardButton("👤 Пользователи", callback_data="rw:users"),
         InlineKeyboardButton("🖥 Ноды", callback_data="rw:nodes")],
        [InlineKeyboardButton("🔌 Хосты", callback_data="rw:hosts")],
        [InlineKeyboardButton("🔑 Подключить панель", callback_data="rw:connect")],
    ])


def rw_menu_text():
    if rw.has_creds():
        status = "Панель подключена ✅"
    else:
        status = "Панель не подключена. Нажми «Подключить панель» или задай REMNAWAVE_URL/REMNAWAVE_TOKEN в .env."
    return ("🛰 Remnawave\n\n" + status +
            "\n\nСоздание нод, правку Xray-конфига и хостов выполняет ИИ-агент по запросу "
            "(опасные операции — с подтверждением).\n\nВыбери действие:")


async def show_remnawave_menu(update: Update):
    await update.message.reply_text(rw_menu_text(), reply_markup=rw_menu_markup())


async def rw_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    await query.answer()
    if not is_authorized(update):
        await query.edit_message_text(f"⛔ Нет доступа. Ваш Telegram ID: {uid}")
        return

    action = query.data.split(":", 1)[1] if ":" in query.data else "menu"

    if action == "menu":
        await query.edit_message_text(rw_menu_text(), reply_markup=rw_menu_markup())
        return
    if action == "connect":
        ctx.user_data["mode"] = "rw_connect"
        await query.edit_message_text(
            "Пришли данные панели в формате:\nURL | API_TOKEN\n\n"
            "Например:\nhttps://panel.example.com | eyJhbGciOi...\n\n"
            "Токен создаётся в панели Remnawave → API Tokens."
        )
        return

    if action in ("stats", "users", "nodes", "hosts"):
        if not rw.has_creds():
            await query.edit_message_text("❌ Панель не подключена. Нажми «Подключить панель».", reply_markup=rw_menu_markup())
            return
        fn = {"stats": rw.system_stats, "users": rw.list_users, "nodes": rw.list_nodes, "hosts": rw.list_hosts}[action]
        result = await asyncio.to_thread(fn)
        await query.edit_message_text(result, reply_markup=rw_menu_markup())
        return

    await query.edit_message_text(rw_menu_text(), reply_markup=rw_menu_markup())


async def handle_rw_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE, mode: str):
    if mode != "rw_connect":
        return False
    text = (update.message.text or "").strip()
    parts = [p.strip() for p in text.split("|", 1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        await update.message.reply_text("❌ Формат: URL | API_TOKEN\nНапример: https://panel.example.com | eyJ...")
        return True
    url, token = parts
    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("❌ URL должен начинаться с http:// или https://")
        return True
    rw.save_creds(url, token)
    ctx.user_data.pop("mode", None)
    check = await asyncio.to_thread(rw.system_stats)
    ok = not check.startswith("❌")
    note = "✅ подключение работает" if ok else f"⚠️ сохранено, но проверка не прошла: {check[:300]}"
    await update.message.reply_text(f"✅ Панель Remnawave сохранена.\n{note}", reply_markup=rw_menu_markup())
    return True
