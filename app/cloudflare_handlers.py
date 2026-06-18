import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import app.state as state
import app.cloudflare as cf
from app.core import is_authorized


def cf_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Зоны и DNS", callback_data="cf:zones")],
        [InlineKeyboardButton("🔑 Задать токен", callback_data="cf:token")],
    ])


def cf_zone_markup(idx):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Изменить запись", callback_data=f"cf:setrec:{idx}")],
        [InlineKeyboardButton("🗑 Удалить запись", callback_data=f"cf:delrec:{idx}")],
        [InlineKeyboardButton("🧹 Очистить кэш", callback_data=f"cf:purge:{idx}")],
        [InlineKeyboardButton("⬅️ К зонам", callback_data="cf:zones")],
    ])


def cf_menu_text():
    if cf.has_token():
        status = "Токен задан ✅"
    else:
        status = "Токен не задан. Нажми «Задать токен» или добавь CLOUDFLARE_API_TOKEN в .env."
    return "☁️ Cloudflare\n\n" + status + "\n\nВыбери действие:"


async def show_cloudflare_menu(update: Update):
    await update.message.reply_text(cf_menu_text(), reply_markup=cf_menu_markup())


async def cf_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    await query.answer()
    if not is_authorized(update):
        await query.edit_message_text(f"⛔ Нет доступа. Ваш Telegram ID: {uid}")
        return

    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else "menu"

    if action == "menu":
        await query.edit_message_text(cf_menu_text(), reply_markup=cf_menu_markup())
        return

    if action == "token":
        ctx.user_data["mode"] = "cf_token"
        await query.edit_message_text(
            "Пришли Cloudflare API Token (права Zone:Read + DNS:Edit). "
            "Создать: Cloudflare → My Profile → API Tokens → Create Token → Edit zone DNS."
        )
        return

    if action == "zones":
        zlist, err = await asyncio.to_thread(cf.zones)
        if err:
            await query.edit_message_text(f"❌ Cloudflare: {err}", reply_markup=cf_menu_markup())
            return
        if not zlist:
            await query.edit_message_text("Зоны не найдены или у токена нет доступа.", reply_markup=cf_menu_markup())
            return
        ctx.user_data["cf_zones"] = [(z["id"], z["name"]) for z in zlist]
        rows = [[InlineKeyboardButton(z["name"], callback_data=f"cf:zone:{i}")] for i, z in enumerate(zlist)]
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="cf:menu")])
        await query.edit_message_text("Выбери зону:", reply_markup=InlineKeyboardMarkup(rows))
        return

    # Действия по конкретной зоне (индекс в сохранённом списке)
    zones = ctx.user_data.get("cf_zones") or []

    def zone_at(i):
        return zones[i] if 0 <= i < len(zones) else (None, None)

    if action in ("zone", "purge", "setrec", "delrec") and len(parts) > 2:
        try:
            idx = int(parts[2])
        except Exception:
            await query.edit_message_text("Список зон устарел.", reply_markup=cf_menu_markup())
            return
        zid, zname = zone_at(idx)
        if not zid:
            await query.edit_message_text("Список зон устарел, открой «Зоны и DNS» заново.", reply_markup=cf_menu_markup())
            return

        if action == "zone":
            recs, err = await asyncio.to_thread(cf.dns_records, zid)
            await query.edit_message_text(cf.format_records(zname, recs, err), reply_markup=cf_zone_markup(idx))
            return
        if action == "purge":
            res = await asyncio.to_thread(cf.purge_cache_id, zid, zname)
            await query.edit_message_text(res, reply_markup=cf_zone_markup(idx))
            return
        if action == "setrec":
            ctx.user_data["mode"] = "cf_set_record"
            await query.edit_message_text(
                f"Зона {zname}. Введи запись в формате:\n"
                f"имя тип значение [proxied]\n\n"
                f"Например:\npanel.{zname} A 1.2.3.4 proxied"
            )
            return
        if action == "delrec":
            ctx.user_data["mode"] = "cf_del_record"
            await query.edit_message_text(
                f"Зона {zname}. Введи имя записи для удаления (опц. тип):\n"
                f"имя [тип]\n\nНапример:\nold.{zname} A"
            )
            return

    await query.edit_message_text(cf_menu_text(), reply_markup=cf_menu_markup())


async def handle_cf_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE, mode: str):
    text = (update.message.text or "").strip()

    if mode == "cf_token":
        cf.save_token(text)
        ctx.user_data.pop("mode", None)
        await update.message.reply_text("✅ Cloudflare токен сохранён.", reply_markup=cf_menu_markup())
        return True

    if mode == "cf_set_record":
        parts = text.split()
        if len(parts) < 3:
            await update.message.reply_text("❌ Формат: имя тип значение [proxied]\nНапример: panel.example.com A 1.2.3.4 proxied")
            return True
        name, rtype, content = parts[0], parts[1], parts[2]
        proxied = None
        if len(parts) >= 4:
            proxied = parts[3].lower() in {"proxied", "yes", "on", "1", "да"}
        res = await asyncio.to_thread(cf.set_dns, name, rtype, content, proxied)
        ctx.user_data.pop("mode", None)
        await update.message.reply_text(res, reply_markup=cf_menu_markup())
        return True

    if mode == "cf_del_record":
        parts = text.split()
        if not parts:
            await update.message.reply_text("❌ Укажи имя записи.")
            return True
        name = parts[0]
        rtype = parts[1] if len(parts) > 1 else None
        res = await asyncio.to_thread(cf.delete_dns, name, rtype)
        ctx.user_data.pop("mode", None)
        await update.message.reply_text(res, reply_markup=cf_menu_markup())
        return True

    return False
