import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from app.executor import ssh_exec as direct_ssh_exec
from app.core import (
    SERVERS_FILE, get_servers, is_authorized, kb_servers, reset_session, save_json
)

async def delete_server_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    await query.answer()
    if not is_authorized(update):
        await query.edit_message_text(f"⛔ Нет доступа. Ваш Telegram ID: {uid}")
        return

    servers = get_servers()
    token = query.data.split(":", 1)[1]
    if token == "cancel":
        await query.edit_message_text("❌ Удаление отменено.")
        return
    if query.data.startswith("delserver:"):
        names = list(servers.keys())
        try:
            name = names[int(token)]
        except Exception:
            await query.edit_message_text("Сервер не найден или список изменился.")
            return
    else:
        name = token
        if name not in servers:
            await query.edit_message_text("Сервер уже удалён или не найден.")
            return

    servers.pop(name, None)
    save_json(SERVERS_FILE, servers)
    reset_session(uid)
    await query.edit_message_text(f"✅ Сервер «{name}» удалён из списка.")


def server_buttons_markup():
    servers = get_servers()
    rows = [[InlineKeyboardButton(f"🖥 {name}", callback_data=f"server:{i}")] for i, name in enumerate(servers.keys())]
    rows.append([InlineKeyboardButton("➕ Добавить сервер", callback_data="server:add")])
    return InlineKeyboardMarkup(rows)


def auth_type_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Пароль", callback_data="server:addauth:password")],
        [InlineKeyboardButton("🗝 SSH key", callback_data="server:addauth:key")],
    ])

def host_fingerprint_markup(idx):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Перезаписать fingerprint", callback_data=f"server:fp:{idx}:yes")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"server:fp:{idx}:no")],
    ])

async def server_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    await query.answer()
    if not is_authorized(update):
        await query.edit_message_text(f"⛔ Нет доступа. Ваш Telegram ID: {uid}")
        return
    data = query.data.split(":")
    if len(data) >= 2 and data[1] == "add":
        ctx.user_data["mode"] = "add_name"
        ctx.user_data.pop("comment", None)
        await query.message.reply_text("Введи имя сервера:")
        return
    if len(data) >= 3 and data[1] == "addauth":
        auth = data[2]
        if auth == "key":
            ctx.user_data["auth_type"] = "key"
            ctx.user_data["mode"] = "add_key"
            await query.edit_message_text("Выбран SSH key. Пришли приватный SSH-ключ целиком (BEGIN/END PRIVATE KEY):")
            return
        if auth == "password":
            ctx.user_data["auth_type"] = "password"
            ctx.user_data["mode"] = "add_pass"
            await query.edit_message_text("Выбран пароль. Введи пароль:")
            return
    servers = get_servers()
    names = list(servers.keys())
    if len(data) >= 4 and data[1] == "fp":
        try:
            idx = int(data[2]); name = names[idx]; cfg = servers[name]
        except Exception:
            await query.edit_message_text("Сервер не найден или список изменился.")
            return
        if data[3] != "yes":
            await query.edit_message_text(f"❌ Перезапись fingerprint для «{name}» отменена.")
            return
        await query.edit_message_text(f"🔄 Перезаписываю fingerprint для «{name}»...")
        result = await asyncio.to_thread(direct_ssh_exec, cfg, "echo OK", False, None, True)
        if result.strip() == "OK":
            await query.edit_message_text(f"✅ Fingerprint для «{name}» перезаписан, SSH-соединение успешно.", reply_markup=kb_servers())
        else:
            await query.edit_message_text(f"⚠️ Не удалось перезаписать fingerprint для «{name}»: {result[:500]}", reply_markup=kb_servers())
        return
    try:
        idx = int(data[1]); name = names[idx]; cfg = servers[name]
    except Exception:
        await query.edit_message_text("Сервер не найден или список изменился.")
        return
    if len(data) >= 3 and data[2] == "comment":
        ctx.user_data["mode"] = "server_comment"
        ctx.user_data["server_edit_name"] = name
        await query.message.reply_text(f"Введи комментарий для сервера «{name}»:")
        return
    if len(data) >= 3 and data[2] == "rename":
        ctx.user_data["mode"] = "server_rename"
        ctx.user_data["server_edit_name"] = name
        await query.message.reply_text(f"Введи новое название для сервера «{name}»:")
        return
    if len(data) >= 3 and data[2] == "password":
        ctx.user_data["mode"] = "server_password"
        ctx.user_data["server_edit_name"] = name
        await query.message.reply_text(f"Введи новый пароль для сервера «{name}»:")
        return
    comment = cfg.get("comment") or "—"
    port = cfg.get("port", 22)
    text = f"🖥 {name}\nHost: {cfg.get('user')}@{cfg.get('host')}:{port}\nКомментарий: {comment}"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Название", callback_data=f"server:{idx}:rename")],
        [InlineKeyboardButton("🔑 Пароль", callback_data=f"server:{idx}:password")],
        [InlineKeyboardButton("📝 Комментарий", callback_data=f"server:{idx}:comment")],
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"delserver:{idx}")],
    ]))
