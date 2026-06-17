import asyncio, json, time, shlex
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import app.state as state
from app.storage import get_servers, get_session, set_session
from app.gpt import gpt_call, consult_agents, trim_history, needs_confirmation, is_message_too_long, is_intermediate_answer, clean_text, model_extra_system_prompt
from app.executor import local_exec, ssh_exec, read_file, write_file, web_fetch, web_search
from app.ui import status_updater

async def _run_project_tool(name, args, servers, allow_dangerous, uid):
    project = state.active_project()
    if not project:
        return "❌ Активный проект не настроен"
    srv = state.normalize_server_name(project.get("server"), servers)
    base = str(project.get("path") or "").strip()
    if not base:
        return "❌ Рабочая директория проекта не настроена"

    def run(cmd):
        full = f"cd {shlex.quote(base)} && {cmd}"
        if srv == "Главный сервер":
            return local_exec(full, allow_dangerous, uid)
        if srv in servers:
            return ssh_exec(servers[srv], full, allow_dangerous, uid)
        return "❌ сервер проекта не найден"

    if name == "list_project_files":
        depth = int(args.get("max_depth") or 4)
        limit = int(args.get("limit") or 300)
        return run("find . -maxdepth " + shlex.quote(str(depth)) + " -type f "
                   "! -path '*/.git/*' ! -path '*/node_modules/*' ! -path '*/.venv/*' ! -path '*/venv/*' "
                   "! -path '*/__pycache__/*' ! -path '*/target/*' ! -path '*/dist/*' ! -path '*/build/*' "
                   f"| sort | head -n {limit}")
    if name == "search_project":
        q = args.get("query", "")
        limit = int(args.get("limit") or 80)
        return run("grep -RIn --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=venv "
                   "--exclude-dir=__pycache__ --exclude-dir=target --exclude-dir=dist --exclude-dir=build "
                   + shlex.quote(q) + f" . | head -n {limit}")
    if name == "read_project_file":
        rel = str(args.get("path") or "").lstrip("/")
        start = max(int(args.get("start") or 1), 1)
        end = max(int(args.get("end") or 400), start)
        script = (
            "from pathlib import Path\n"
            f"p=(Path('.')/{rel!r}).resolve()\n"
            "base=Path('.').resolve()\n"
            "if not str(p).startswith(str(base)):\n    print('❌ путь вне проекта')\n"
            "elif not p.exists():\n    print('файл не найден')\n"
            "else:\n"
            "    lines=p.read_text(errors='replace').splitlines()\n"
            f"    rng=lines[{start-1}:{end}]\n"
            f"    base_no={start}\n"
            "    [print(f'{i}:{line}') for i,line in enumerate(rng, base_no)]\n"
        )
        return run("python3 -c " + shlex.quote(script))
    if name == "apply_patch":
        patch = args.get("patch") or ""
        if not patch.strip():
            return "❌ пустой patch"
        script = "from pathlib import Path\nPath('/tmp/devops_bot.patch').write_text(" + repr(patch) + ", encoding='utf-8')\n"
        return run("python3 -c " + shlex.quote(script) + " && git apply --check /tmp/devops_bot.patch && git apply /tmp/devops_bot.patch")
    if name == "git_diff":
        return run("git diff --stat" if args.get("stat") else "git diff -- . ':!*.lock' | head -n 400")
    if name == "run_project_tests":
        return run(args.get("command") or "true")
    return "неизвестный проектный инструмент"

async def run_agent(goal, update, status_msg, uid, continue_existing=False):
    state.STOP_FLAGS[uid] = False
    state.START_TIME[uid] = time.time()
    state.STATUS_TEXT[uid] = "Думаю..."

    servers = get_servers()
    state.set_current_user_id(uid)
    history = get_session(uid, servers)
    active_agent = state.active_agent()
    model_name = (active_agent or {}).get("model") or state.active_agent_model()
    extra_prompt = model_extra_system_prompt(model_name)
    if extra_prompt and extra_prompt not in (history[0].get("content") or ""):
        history[0]["content"] = (history[0].get("content") or "") + "\n" + extra_prompt
    if continue_existing:
        history.append({"role": "user", "content": "Продолжай выполнение предыдущей задачи с места остановки. Учитывай уже выполненные шаги и доведи задачу до результата."})
    else:
        history.append({"role": "user", "content": goal})
    set_session(uid, history)

    # Совместная работа агентов: собираем мнения советников перед стартом
    if state.get_agents().get("collab"):
        state.STATUS_TEXT[uid] = "Совещаюсь с агентами..."
        try:
            advice = await asyncio.to_thread(consult_agents, history, state.active_agent_model())
        except Exception:
            advice = ""
        if advice:
            history.append({"role": "system", "content": advice})
            set_session(uid, history)

    stop_event = asyncio.Event()
    updater_task = asyncio.create_task(status_updater(uid, status_msg, stop_event))

    step = 0
    auto_continue_count = 0
    used_web_sources = False
    try:
        while True:
            step += 1

            if step > state.MAX_AGENT_STEPS:
                state.PENDING_CONTINUE[uid] = goal
                markup = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Продолжить", callback_data="continue:agent")]])
                await update.effective_message.reply_text(
                    f"⛔ Достигнут лимит шагов агента ({state.MAX_AGENT_STEPS}). Можно продолжить выполнение с места остановки.",
                    reply_markup=markup
                )
                return ""

            if state.STOP_FLAGS.get(uid):
                state.STOP_FLAGS[uid] = False
                return "⏹ Остановлено."

            state.STATUS_TEXT[uid] = "Запрос к GPT..."
            history = trim_history(history)
            set_session(uid, history)

            try:
                msg = await asyncio.to_thread(gpt_call, history)
            except Exception as e:
                return f"Ошибка API: {e}"

            if msg.get("tool_calls"):
                history.append(msg)
                set_session(uid, history)

                for call in msg["tool_calls"]:
                    if state.STOP_FLAGS.get(uid):
                        state.STOP_FLAGS[uid] = False
                        return "⏹ Остановлено."

                    name = call["function"]["name"]
                    try:
                        args = json.loads(call["function"].get("arguments") or "{}")
                    except Exception as e:
                        history.append({"role": "tool", "tool_call_id": call.get("id"), "content": f"❌ неверные аргументы инструмента: {e}"})
                        set_session(uid, history)
                        continue

                    need_confirm, reason = needs_confirmation(name, args)
                    if need_confirm and uid not in state.CONFIRMED_TASKS:
                        history.pop()
                        set_session(uid, history)
                        state.PENDING_CONFIRM[uid] = goal
                        short_reason = str(reason or "действие")
                        if len(short_reason) > 1200:
                            short_reason = short_reason[:1200] + "…"
                        markup = InlineKeyboardMarkup([[
                            InlineKeyboardButton("✅ Подтвердить", callback_data="confirm:yes"),
                            InlineKeyboardButton("❌ Отмена", callback_data="confirm:no"),
                        ]])
                        try:
                            await update.effective_message.reply_text(
                                "⚠️ Требуется подтверждение опасного действия:\n"
                                f"{short_reason}\n\n"
                                "Выбери действие:",
                                reply_markup=markup
                            )
                        except Exception:
                            await update.effective_message.reply_text(
                                "⚠️ Требуется подтверждение опасного действия. Выбери действие:",
                                reply_markup=markup
                            )
                        return ""

                    if name == "local_exec":
                        cmd = args["command"]
                        state.STATUS_TEXT[uid] = f"local: {cmd[:50]}"
                        result = await asyncio.to_thread(local_exec, cmd, uid in state.CONFIRMED_TASKS, uid)

                    elif name == "ssh_exec":
                        srv, cmd = args["server"], args["command"]
                        srv = state.normalize_server_name(srv, servers)
                        project = state.active_project()
                        if project:
                            project_srv = state.normalize_server_name(project.get("server"), servers)
                            if srv not in servers and srv != "Главный сервер":
                                srv = project_srv
                            if project_srv == srv:
                                project_path = str(project.get("path") or "").strip()
                                if project_path and not cmd.lstrip().startswith("cd "):
                                    cmd = f"cd {shlex.quote(project_path)} && {cmd}"
                        if srv == "Главный сервер":
                            state.STATUS_TEXT[uid] = f"main: {cmd[:50]}"
                            result = await asyncio.to_thread(local_exec, cmd, uid in state.CONFIRMED_TASKS, uid)
                        else:
                            state.STATUS_TEXT[uid] = f"ssh [{srv}]: {cmd[:40]}"
                            result = await asyncio.to_thread(ssh_exec, servers[srv], cmd, uid in state.CONFIRMED_TASKS, uid) if srv in servers else "сервер не найден"
                            if isinstance(result, str) and "HOST_FINGERPRINT_CHANGED:" in result and srv in servers:
                                names = list(servers.keys())
                                idx = names.index(srv)
                                await update.effective_message.reply_text(
                                    f"⚠️ Fingerprint SSH-хоста для «{srv}» изменился. Если сервер переустановлен, подтверди перезапись кнопкой.",
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Перезаписать fingerprint", callback_data=f"server:fp:{idx}:yes")], [InlineKeyboardButton("❌ Отмена", callback_data=f"server:fp:{idx}:no")]])
                                )
                                return ""

                    elif name == "web_search":
                        used_web_sources = True
                        state.STATUS_TEXT[uid] = f"search: {args['query'][:45]}"
                        result = await asyncio.to_thread(web_search, args["query"], args.get("sources", 5))

                    elif name == "web_fetch":
                        used_web_sources = True
                        state.STATUS_TEXT[uid] = f"web: {args['url'][:50]}"
                        result = await asyncio.to_thread(web_fetch, args["url"])

                    elif name == "write_file":
                        state.STATUS_TEXT[uid] = f"write: {args['path']}"
                        result = await asyncio.to_thread(write_file, args["path"], args["content"])

                    elif name == "read_file":
                        state.STATUS_TEXT[uid] = f"read: {args['path']}"
                        result = await asyncio.to_thread(read_file, args["path"])

                    elif name in ("list_project_files", "search_project", "read_project_file", "apply_patch", "git_diff", "run_project_tests"):
                        state.STATUS_TEXT[uid] = f"project: {name}"
                        result = await _run_project_tool(name, args, servers, uid in state.CONFIRMED_TASKS, uid)

                    else:
                        result = "неизвестный инструмент"

                    # Не отправляем промежуточный вывод команд в чат: он нужен только модели
                    # для принятия решения и финального краткого ответа.
                    tool_content = result[:5000] if name in ("web_search", "web_fetch") else result[:3000]
                    history.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": tool_content
                    })
                    set_session(uid, history)

                continue

            content = msg.get("content", "")
            if content:
                history.append({"role": "assistant", "content": content})
                set_session(uid, history)
                if is_intermediate_answer(content):
                    if auto_continue_count < max(state.AUTO_CONTINUE_LIMIT, 3):
                        auto_continue_count += 1
                        history.append({"role": "user", "content": "Это промежуточная фраза, а не результат. Не отвечай планом. Если нужны данные проекта/сервера/файлов — сейчас вызови tools/function calls. После инструментов дай финальный краткий итог."})
                        set_session(uid, history)
                        continue
                    state.PENDING_CONTINUE.pop(uid, None)
                    return "Не удалось продолжить: модель несколько раз вернула план вместо вызова инструментов. Попробуй повторить запрос или сменить модель."
                state.PENDING_CONTINUE.pop(uid, None)
                return content
            break

        return "Готово."

    finally:
        state.CONFIRMED_TASKS.discard(uid)
        stop_event.set()
        await asyncio.sleep(0.1)
        updater_task.cancel()
        elapsed = int(time.time() - state.START_TIME.get(uid, time.time()))
        state.STATUS_TEXT.pop(uid, None)
        state.START_TIME.pop(uid, None)

