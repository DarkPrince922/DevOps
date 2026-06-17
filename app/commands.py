import asyncio
import json
import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from app.executor import ssh_exec as direct_ssh_exec, local_exec as direct_local_exec
import app.state as state
from app.core import get_servers, is_authorized

logger = logging.getLogger(__name__)

DIRECT_COMMANDS = {
    "📡 Статус": "printf 'Host: '; hostname; printf 'Uptime: '; uptime -p 2>/dev/null || uptime; awk '/load average:/ {sub(/^.*load average: /,\"Load: \"); print}' /proc/loadavg 2>/dev/null || uptime | sed 's/.*load average:/Load:/'; free -m | awk '/Mem:/ {printf \"RAM: %d/%d MB (%.0f%%)\\n\", $3,$2,$3*100/$2}'; df -h / | awk 'NR==2 {print \"Disk /: \"$3\"/\"$2\" (\"$5\")\"}'; if command -v docker >/dev/null 2>&1; then running=$(docker ps -q 2>/dev/null | wc -l); all=$(docker ps -aq 2>/dev/null | wc -l); echo \"Docker: $running running / $all total\"; fi",
    "📊 CPU": "read _ u n sy i rest < /proc/stat; t=$((u+n+sy+i)); sleep 1; read _ u2 n2 sy2 i2 rest < /proc/stat; t2=$((u2+n2+sy2+i2)); awk -v dt=$((t2-t)) -v di=$((i2-i)) 'BEGIN{if(dt>0) printf \"CPU: %.1f%%\\n\", (dt-di)*100/dt; else print \"CPU: n/a\"}'; uptime | sed 's/.*load average:/Load:/'",
    "💾 RAM": "free -m | awk '/Mem:/ {printf \"RAM: %d/%d MB (%.0f%%)\\n\", $3,$2,$3*100/$2} /Swap:/ {printf \"Swap: %d/%d MB (%.0f%%)\\n\", $3,$2,($2?$3*100/$2:0)}'",
    "💽 Диск": "df -h / | awk 'NR==2 {print \"Root: \"$3\"/\"$2\" (\"$5\")\"}'; df -h --total 2>/dev/null | awk '/total/ {print \"Total: \"$3\"/\"$2\" (\"$5\")\"}'",
    "📦 Контейнеры": "running=$(docker ps -q 2>/dev/null | wc -l); all=$(docker ps -aq 2>/dev/null | wc -l); echo \"Containers: $running running / $all total\"; docker ps --format '{{.Names}} — {{.Status}}' 2>/dev/null | head -10",
    "📋 Логи": "docker compose logs --tail=10 2>&1 || docker logs --tail=10 $(docker ps -q | head -1) 2>&1",
    "🔍 Процессы": "ps -eo comm,%cpu,%mem --sort=-%cpu | head -6",
    "🌐 Сеть": "echo -n 'Listening ports: '; ss -tln 2>/dev/null | awk 'NR>1{split($4,a,\":\"); print a[length(a)]}' | sort -n | uniq | paste -sd ', ' -; ss -tan 2>/dev/null | awk 'NR>1{c[$1]++} END{for (s in c) print s\": \"c[s]}'",
    "🕐 Аптайм": "uptime -p; uptime | sed 's/.*load average:/Load:/'",
    "💻 Система": "hostname; . /etc/os-release 2>/dev/null; echo \"OS: ${PRETTY_NAME:-unknown}\"; echo \"Kernel: $(uname -r)\"; uptime -p",
}

async def run_direct_command(update: Update, title: str, cmd: str):
    uid = update.effective_user.id
    msg = update.effective_message
    servers = get_servers()
    if not servers:
        name, status, result = await _exec_target("local", "🏠 Локально", None, cmd)
        await msg.reply_text(f"{title}\n{name} — {status}:\n{result}")
        return

    await msg.reply_text(f"⏳ Выполняю без ИИ: {title}")
    tasks = [_exec_target(str(i), f"🖥 {name}", cfg, cmd, uid) for i, (name, cfg) in enumerate(servers.items())]
    rows = await asyncio.gather(*tasks)
    text = f"{title}\n" + "\n\n".join(f"{name} — {status}:\n{result}" for name, status, result in rows)
    for chunk in [text[i:i+3900] for i in range(0, len(text), 3900)]:
        await msg.reply_text(chunk)



PREAPPROVED_FILE = state.DATA_DIR / "preapproved_commands.json"
DEFAULT_PREAPPROVED_COMMANDS = [
    {"title": "🕐 Аптайм", "command": "uptime -p; uptime | sed 's/.*load average:/Load:/'"},
    {"title": "💾 Память", "command": "free -m | awk '/Mem:/ {printf \"RAM: %d/%d MB (%.0f%%)\\n\", $3,$2,$3*100/$2} /Swap:/ {printf \"Swap: %d/%d MB (%.0f%%)\\n\", $3,$2,($2?$3*100/$2:0)}'"},
    {"title": "💽 Диск", "command": "df -h / | awk 'NR==2 {print \"Root: \"$3\"/\"$2\" (\"$5\")\"}'"},
    {"title": "🐳 Docker", "command": "docker ps --format '{{.Names}} — {{.Status}}' 2>/dev/null | head -15 || true"},
    {"title": "🔍 Топ CPU", "command": "ps -eo comm,%cpu,%mem --sort=-%cpu | head -8"},
    {"title": "🌐 Порты", "command": "ss -tln 2>/dev/null | awk 'NR>1{print $4}' | head -30"},
]

COMMAND_TIMEOUT = 35


def _normalize_command_item(title, command):
    title = str(title or "").strip()
    command = str(command or "").strip()
    if not title or not command:
        return None
    return {"title": title, "command": command}


def _format_status(result):
    text = str(result or "")
    low = text.lower()
    if "timeout" in low or "timed out" in low:
        return "❌ timeout"
    if text.startswith("❌") or "ssh:" in low or "ошибка" in low or "error" in low:
        return "⚠️ Ошибка"
    return "✅ OK"


async def _exec_target(tid, name, cfg, cmd, uid=None):
    try:
        if tid == "local":
            result = await asyncio.wait_for(asyncio.to_thread(direct_local_exec, cmd, False, uid), timeout=COMMAND_TIMEOUT)
        else:
            result = await asyncio.wait_for(asyncio.to_thread(direct_ssh_exec, cfg, cmd, False, uid), timeout=COMMAND_TIMEOUT)
    except asyncio.TimeoutError:
        result = "❌ timeout"
    except Exception as e:
        result = f"❌ {e}"
    return name, _format_status(result), str(result)[:1200]


def ensure_preapproved_file():
    if not PREAPPROVED_FILE.exists():
        save_preapproved_commands(DEFAULT_PREAPPROVED_COMMANDS)
    else:
        try:
            os.chmod(PREAPPROVED_FILE, 0o600)
        except Exception:
            logger.exception("chmod preapproved commands failed")


def get_preapproved_commands():
    ensure_preapproved_file()
    try:
        data = json.loads(PREAPPROVED_FILE.read_text())
    except Exception:
        data = DEFAULT_PREAPPROVED_COMMANDS
    items = []
    if isinstance(data, dict):
        data = [{"title": k, "command": v} for k, v in data.items()]
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                normalized = _normalize_command_item(item.get("title"), item.get("command"))
                if normalized:
                    items.append(normalized)
    if not items:
        items = DEFAULT_PREAPPROVED_COMMANDS
        save_preapproved_commands(items)
    return items


def save_preapproved_commands(items):
    valid = []
    for item in items:
        if isinstance(item, dict):
            normalized = _normalize_command_item(item.get("title"), item.get("command"))
            if normalized:
                valid.append(normalized)
    PREAPPROVED_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PREAPPROVED_FILE.with_suffix(PREAPPROVED_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(valid or DEFAULT_PREAPPROVED_COMMANDS, indent=2, ensure_ascii=False))
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        logger.exception("chmod temp preapproved commands failed")
    os.replace(tmp, PREAPPROVED_FILE)
    try:
        os.chmod(PREAPPROVED_FILE, 0o600)
    except Exception:
        logger.exception("chmod preapproved commands failed")


def _all_targets():
    servers = get_servers()
    targets = [("local", "🏠 Главный сервер", None)]
    targets += [(str(i), f"🖥 {name}", cfg) for i, (name, cfg) in enumerate(servers.items())]
    return targets


def preapproved_markup():
    rows = []
    for i, item in enumerate(get_preapproved_commands()):
        rows.append([InlineKeyboardButton(item["title"][:48], callback_data=f"precmd:choose:{i}")])
    rows.append([InlineKeyboardButton("⚙️ Редактировать команды", callback_data="precmd:edit")])
    return InlineKeyboardMarkup(rows)


def preapproved_targets_markup(ctx, cmd_idx):
    selected = set(ctx.user_data.get("precmd_targets", {}).get(str(cmd_idx), {"local"}))
    rows = []
    for tid, name, _ in _all_targets():
        mark = "✅" if tid in selected else "⬜"
        rows.append([InlineKeyboardButton(f"{mark} {name}", callback_data=f"precmd:toggle:{cmd_idx}:{tid}")])
    rows.append([
        InlineKeyboardButton("✅ Выбрать все", callback_data=f"precmd:selectall:{cmd_idx}"),
        InlineKeyboardButton("⬜ Снять все", callback_data=f"precmd:clear:{cmd_idx}"),
    ])
    rows.append([InlineKeyboardButton("▶️ Выполнить", callback_data=f"precmd:exec:{cmd_idx}")])
    rows.append([InlineKeyboardButton("⬅️ К списку команд", callback_data="precmd:menu")])
    return InlineKeyboardMarkup(rows)


def preapproved_edit_markup():
    rows = [[InlineKeyboardButton(f"✏️ {item['title'][:45]}", callback_data=f"precmd:editcmd:{i}")]
            for i, item in enumerate(get_preapproved_commands())]
    rows.append([InlineKeyboardButton("➕ Добавить команду", callback_data="precmd:add")])
    rows.append([InlineKeyboardButton("⬅️ К списку команд", callback_data="precmd:menu")])
    return InlineKeyboardMarkup(rows)


def preapproved_edit_cmd_markup(idx):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Название", callback_data=f"precmd:settitle:{idx}")],
        [InlineKeyboardButton("⌨️ Команда", callback_data=f"precmd:setcmd:{idx}")],
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"precmd:delete:{idx}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="precmd:edit")],
    ])


async def show_preapproved_commands(update: Update):
    await update.message.reply_text(
        "✅ Предодобренные команды выполняются напрямую, без ИИ. Выбери команду:",
        reply_markup=preapproved_markup()
    )


async def _run_preapproved_on_targets(msg, title, cmd, selected):
    targets = [t for t in _all_targets() if t[0] in selected]
    if not targets:
        await msg.reply_text("❌ Не выбран ни один сервер.")
        return
    rows = await asyncio.gather(*[_exec_target(tid, name, cfg, cmd) for tid, name, cfg in targets])
    text = f"{title}\n" + "\n\n".join(f"{name} — {status}:\n{result}" for name, status, result in rows)
    for chunk in [text[i:i+3900] for i in range(0, len(text), 3900)]:
        await msg.reply_text(chunk)


async def preapproved_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    await query.answer()
    if not is_authorized(update):
        await query.edit_message_text(f"⛔ Нет доступа. Ваш Telegram ID: {uid}")
        return
    data = query.data.split(":")
    action = data[1] if len(data) > 1 else "menu"
    items = get_preapproved_commands()

    if action == "menu":
        await query.edit_message_text("✅ Предодобренные команды выполняются напрямую, без ИИ. Выбери команду:", reply_markup=preapproved_markup())
        return
    if action == "edit":
        await query.edit_message_text("⚙️ Редактирование предодобренных команд:", reply_markup=preapproved_edit_markup())
        return
    if action == "add":
        ctx.user_data["mode"] = "precmd_add_title"
        await query.message.reply_text("Введи название новой команды:")
        return
    if action == "editcmd":
        try:
            idx = int(data[2]); item = items[idx]
        except Exception:
            await query.edit_message_text("Команда не найдена или список изменился.")
            return
        await query.edit_message_text(f"✏️ {item['title']}\n\nКоманда:\n{item['command']}", reply_markup=preapproved_edit_cmd_markup(idx))
        return
    if action in ("settitle", "setcmd"):
        try:
            idx = int(data[2]); item = items[idx]
        except Exception:
            await query.edit_message_text("Команда не найдена или список изменился.")
            return
        ctx.user_data["mode"] = "precmd_set_title" if action == "settitle" else "precmd_set_cmd"
        ctx.user_data["precmd_edit_idx"] = idx
        await query.message.reply_text("Введи новое название:" if action == "settitle" else "Введи новую команду:")
        return
    if action == "delete":
        try:
            idx = int(data[2]); removed = items.pop(idx)
        except Exception:
            await query.edit_message_text("Команда не найдена или список изменился.")
            return
        save_preapproved_commands(items)
        await query.edit_message_text(f"🗑 Команда удалена: {removed['title']}", reply_markup=preapproved_edit_markup())
        return
    if action == "choose":
        try:
            idx = int(data[2]); item = items[idx]
        except Exception:
            await query.edit_message_text("Команда не найдена или список изменился.")
            return
        ctx.user_data.setdefault("precmd_targets", {}).setdefault(str(idx), {"local"})
        await query.edit_message_text(f"{item['title']}\n\nОтметь серверы галочками и нажми ▶️ Выполнить:", reply_markup=preapproved_targets_markup(ctx, idx))
        return
    if action == "toggle":
        try:
            idx = int(data[2]); tid = data[3]; items[idx]
        except Exception:
            await query.edit_message_text("Команда или сервер не найдены.")
            return
        selected = set(ctx.user_data.setdefault("precmd_targets", {}).setdefault(str(idx), {"local"}))
        selected.remove(tid) if tid in selected else selected.add(tid)
        ctx.user_data["precmd_targets"][str(idx)] = selected
        await query.edit_message_reply_markup(reply_markup=preapproved_targets_markup(ctx, idx))
        return
    if action in ("selectall", "clear"):
        try:
            idx = int(data[2]); items[idx]
        except Exception:
            await query.edit_message_text("Команда не найдена или список изменился.")
            return
        selected = {tid for tid, _, _ in _all_targets()} if action == "selectall" else set()
        ctx.user_data.setdefault("precmd_targets", {})[str(idx)] = selected
        await query.edit_message_reply_markup(reply_markup=preapproved_targets_markup(ctx, idx))
        return

    if action == "exec":
        try:
            idx = int(data[2]); item = items[idx]
        except Exception:
            await query.edit_message_text("Команда не найдена или список изменился.")
            return
        selected = set(ctx.user_data.get("precmd_targets", {}).get(str(idx), {"local"}))
        await query.message.reply_text(f"⏳ Выполняю без ИИ: {item['title']}")
        await _run_preapproved_on_targets(query.message, item["title"], item["command"], selected)
        return
