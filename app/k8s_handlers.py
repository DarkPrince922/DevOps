import datetime
import os
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from app.core import is_authorized
import app.state as state

def _k8s_cfg():
    return state.load_json(state.K8S_FILE, {}) if hasattr(state, "K8S_FILE") else {}

def _kubeconfig_path():
    if state.is_admin_id():
        return Path(os.environ.get("KUBECONFIG", "/app/data/kubeconfig.yaml"))
    cfg = _k8s_cfg()
    return Path(cfg.get("kubeconfig") or (state.DATA_DIR / "users" / str(state.current_user_id() or "unknown") / "kubeconfig.yaml"))


def _load_k8s():
    try:
        from kubernetes import client, config
    except ImportError as e:
        raise RuntimeError("зависимость kubernetes не установлена") from e
    kubeconfig_path = _kubeconfig_path()
    if kubeconfig_path.exists():
        config.load_kube_config(config_file=str(kubeconfig_path))
        return client
    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config()
    return client


def _namespace():
    cfg = _k8s_cfg()
    if state.is_admin_id():
        return cfg.get("namespace") or os.environ.get("K8S_NAMESPACE", "devops-bot")
    return cfg.get("namespace") or "default"


def _configmap_name():
    cfg = _k8s_cfg()
    if state.is_admin_id():
        return cfg.get("configmap") or os.environ.get("K8S_CONFIGMAP", "devops-bot-config")
    return cfg.get("configmap") or ""


def _secret_name():
    cfg = _k8s_cfg()
    if state.is_admin_id():
        return cfg.get("secret") or os.environ.get("K8S_SECRET", "devops-bot-secret")
    return cfg.get("secret") or ""


def _deployment_name():
    cfg = _k8s_cfg()
    if state.is_admin_id():
        return cfg.get("deployment") or os.environ.get("K8S_DEPLOYMENT", "devops-bot")
    return cfg.get("deployment") or ""


def _restart_deployment(client):
    apps = client.AppsV1Api()
    now = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    body = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": now
                    }
                }
            }
        }
    }
    apps.patch_namespaced_deployment(_deployment_name(), _namespace(), body)


def _set_config_value(kind: str, key: str, value: str):
    client = _load_k8s()
    core = client.CoreV1Api()
    if kind == "secret":
        body = {"stringData": {key: value}}
        core.patch_namespaced_secret(_secret_name(), _namespace(), body)
    else:
        body = {"data": {key: value}}
        core.patch_namespaced_config_map(_configmap_name(), _namespace(), body)
    _restart_deployment(client)


def _set_image(image: str):
    client = _load_k8s()
    apps = client.AppsV1Api()
    body = {"spec": {"template": {"spec": {"containers": [{"name": "devops-bot", "image": image}]}}}}
    apps.patch_namespaced_deployment(_deployment_name(), _namespace(), body)


def k8s_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔌 Подключить kubeconfig", callback_data="k8s:connect")],
        [InlineKeyboardButton("📊 Статус", callback_data="k8s:status")],
        [InlineKeyboardButton("🧩 Поды", callback_data="k8s:pods"), InlineKeyboardButton("📋 Логи", callback_data="k8s:logs")],
        [InlineKeyboardButton("⚠️ События", callback_data="k8s:events")],
        [InlineKeyboardButton("⚙️ Изменить ConfigMap", callback_data="k8s:set_config")],
        [InlineKeyboardButton("🔐 Изменить Secret", callback_data="k8s:set_secret")],
        [InlineKeyboardButton("🖼 Сменить Docker image", callback_data="k8s:set_image")],
        [InlineKeyboardButton("🔄 Перезапустить deployment", callback_data="k8s:restart")],
    ])


def k8s_menu_text():
    return (
        "☸️ Kubernetes\n\n"
        f"Namespace: `{_namespace()}`\n"
        f"Deployment: `{_deployment_name()}`\n"
        f"ConfigMap: `{_configmap_name()}`\n"
        f"Secret: `{_secret_name()}`\n"
        f"Kubeconfig: `{_kubeconfig_path()}`\n\n"
        "Выбери действие кнопкой ниже."
    )


def _selector_from_deployment(dep):
    labels = dep.spec.selector.match_labels or {}
    return ",".join(f"{k}={v}" for k, v in labels.items())


def _list_deployment_pods(client=None):
    client = client or _load_k8s()
    dep = client.AppsV1Api().read_namespaced_deployment(_deployment_name(), _namespace())
    selector = _selector_from_deployment(dep)
    pods = client.CoreV1Api().list_namespaced_pod(_namespace(), label_selector=selector).items
    return dep, pods


def _pods_status():
    client = _load_k8s()
    dep, pods = _list_deployment_pods(client)
    lines = [f"🧩 Поды deployment `{dep.metadata.name}`\n"]
    if not pods:
        lines.append("Поды не найдены.")
    for pod in pods[:10]:
        ready = 0
        total = len(pod.status.container_statuses or [])
        restarts = 0
        for cs in pod.status.container_statuses or []:
            ready += 1 if cs.ready else 0
            restarts += cs.restart_count or 0
        lines.append(f"• `{pod.metadata.name}` — {pod.status.phase}, ready {ready}/{total}, restarts {restarts}")
    return "\n".join(lines)


def _pod_logs():
    client = _load_k8s()
    _, pods = _list_deployment_pods(client)
    running = [p for p in pods if p.status.phase == "Running"] or pods
    if not running:
        return "📋 Логи\n\nПоды не найдены."
    pod = sorted(running, key=lambda p: p.metadata.creation_timestamp or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), reverse=True)[0]
    log = client.CoreV1Api().read_namespaced_pod_log(
        name=pod.metadata.name,
        namespace=_namespace(),
        tail_lines=int(os.environ.get("K8S_LOG_TAIL", "80")),
        timestamps=True,
    )
    if len(log) > 3500:
        log = log[-3500:]
    return f"📋 Логи `{pod.metadata.name}`\n\n```\n{log or 'лог пуст'}\n```"


def _k8s_events():
    client = _load_k8s()
    events = client.CoreV1Api().list_namespaced_event(_namespace()).items
    events = sorted(events, key=lambda e: e.last_timestamp or e.event_time or e.metadata.creation_timestamp or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), reverse=True)
    lines = ["⚠️ Последние события Kubernetes\n"]
    for e in events[:10]:
        obj = getattr(e.involved_object, "name", "object")
        reason = e.reason or "Event"
        msg = (e.message or "")[:140]
        lines.append(f"• {reason}: `{obj}` — {msg}")
    if len(lines) == 1:
        lines.append("Событий нет.")
    return "\n".join(lines)


def _project_k8s_defaults(project):
    return {
        "namespace": project.get("k8s_namespace") or _namespace(),
        "deployment": project.get("k8s_deployment") or _deployment_name(),
        "container": project.get("k8s_container") or "devops-bot",
        "image": project.get("docker_image") or "",
    }


def project_k8s_ai_system(project):
    defaults = _project_k8s_defaults(project)
    return (
        "Ты Kubernetes assistant. Сформируй только JSON без markdown для безопасного изменения Kubernetes через Python client. "
        "Формат: {\"summary\":\"кратко по-русски\",\"operations\":[...]} . "
        "Разрешённые операции: "
        "{\"op\":\"patch\",\"kind\":\"Deployment|ConfigMap|Secret|Service|Ingress\",\"namespace\":\"...\",\"name\":\"...\",\"patch\":{...}}, "
        "{\"op\":\"set_image\",\"namespace\":\"...\",\"deployment\":\"...\",\"container\":\"...\",\"image\":\"...\"}, "
        "{\"op\":\"scale\",\"namespace\":\"...\",\"deployment\":\"...\",\"replicas\":N}, "
        "{\"op\":\"restart\",\"namespace\":\"...\",\"deployment\":\"...\"}. "
        "Если данных недостаточно или запрос опасен (удаление namespace/PV/PVC/секретов целиком), верни operations пустым и explanation. "
        f"Проект: {project.get('name')}. Namespace по умолчанию: {defaults['namespace']}. "
        f"Deployment по умолчанию: {defaults['deployment']}. Container по умолчанию: {defaults['container']}. Image проекта: {defaults['image']}."
    )


def _api_for_kind(client, kind):
    kind = str(kind or "").lower()
    if kind in {"deployment", "deployments"}:
        return client.AppsV1Api(), "patch_namespaced_deployment"
    if kind in {"configmap", "configmaps"}:
        return client.CoreV1Api(), "patch_namespaced_config_map"
    if kind in {"secret", "secrets"}:
        return client.CoreV1Api(), "patch_namespaced_secret"
    if kind in {"service", "services"}:
        return client.CoreV1Api(), "patch_namespaced_service"
    if kind in {"ingress", "ingresses"}:
        return client.NetworkingV1Api(), "patch_namespaced_ingress"
    raise ValueError(f"Неподдерживаемый kind: {kind}")


def apply_project_k8s_operations(plan, project):
    ops = plan.get("operations") or []
    if not ops:
        return "❌ Нет операций для применения."
    if len(ops) > 10:
        raise ValueError("слишком много операций")
    client = _load_k8s()
    defaults = _project_k8s_defaults(project)
    done = []
    for op in ops:
        action = op.get("op")
        ns = op.get("namespace") or defaults["namespace"]
        if action == "patch":
            api, method = _api_for_kind(client, op.get("kind"))
            name = op.get("name") or (defaults["deployment"] if str(op.get("kind", "")).lower() == "deployment" else None)
            patch = op.get("patch")
            if not name or not isinstance(patch, dict):
                raise ValueError("patch требует name и patch-object")
            getattr(api, method)(name=name, namespace=ns, body=patch)
            done.append(f"patch {op.get('kind')}/{name} в {ns}")
        elif action == "set_image":
            dep = op.get("deployment") or defaults["deployment"]
            container = op.get("container") or defaults["container"]
            image = op.get("image") or defaults["image"]
            if not dep or not container or not image:
                raise ValueError("set_image требует deployment/container/image")
            body = {"spec": {"template": {"spec": {"containers": [{"name": container, "image": image}]}}}}
            client.AppsV1Api().patch_namespaced_deployment(name=dep, namespace=ns, body=body)
            done.append(f"set image {dep}/{container} в {ns}")
        elif action == "scale":
            dep = op.get("deployment") or defaults["deployment"]
            replicas = int(op.get("replicas"))
            if replicas < 0 or replicas > 50:
                raise ValueError("replicas вне допустимого диапазона 0..50")
            body = {"spec": {"replicas": replicas}}
            client.AppsV1Api().patch_namespaced_deployment_scale(name=dep, namespace=ns, body=body)
            done.append(f"scale {dep}={replicas} в {ns}")
        elif action == "restart":
            dep = op.get("deployment") or defaults["deployment"]
            now = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
            body = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}}
            client.AppsV1Api().patch_namespaced_deployment(name=dep, namespace=ns, body=body)
            done.append(f"restart {dep} в {ns}")
        else:
            raise ValueError(f"Неподдерживаемая операция: {action}")
    return "✅ Применено: " + "; ".join(done)


def k8s_health_alert():
    client = _load_k8s()
    dep, pods = _list_deployment_pods(client)
    desired = dep.status.replicas or dep.spec.replicas or 0
    ready = dep.status.ready_replicas or 0
    bad_pods = []
    for pod in pods:
        waiting = []
        for cs in pod.status.container_statuses or []:
            if cs.state and cs.state.waiting:
                waiting.append(cs.state.waiting.reason or "waiting")
        if pod.status.phase not in {"Running", "Succeeded"} or waiting:
            bad_pods.append(f"{pod.metadata.name}: {pod.status.phase}{' / ' + ','.join(waiting) if waiting else ''}")
    if ready < desired or bad_pods:
        lines = [f"🚨 Kubernetes: проблема deployment `{_deployment_name()}` в namespace `{_namespace()}`", f"Готово: {ready}/{desired}"]
        lines += [f"• {x}" for x in bad_pods[:5]]
        return "\n".join(lines)
    return None


def _deployment_status():
    client = _load_k8s()
    dep = client.AppsV1Api().read_namespaced_deployment(_deployment_name(), _namespace())
    images = ", ".join(c.image for c in dep.spec.template.spec.containers)
    ready = dep.status.ready_replicas or 0
    replicas = dep.status.replicas or 0
    updated = dep.status.updated_replicas or 0
    return f"☸️ Kubernetes статус\n\nDeployment: `{dep.metadata.name}`\nГотово: {ready}/{replicas}\nОбновлено: {updated}\nImage: `{images}`"


async def show_k8s_menu(update: Update):
    await update.message.reply_text(k8s_menu_text(), reply_markup=k8s_menu_markup())


async def k8s_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    await query.answer()
    if not is_authorized(update):
        await query.edit_message_text(f"⛔ Нет доступа. Ваш Telegram ID: {uid}")
        return
    action = query.data.split(":", 1)[1]
    if action == "status":
        try:
            await query.edit_message_text(_deployment_status(), reply_markup=k8s_menu_markup())
        except Exception as e:
            await query.edit_message_text(f"❌ Не удалось получить статус: {e}", reply_markup=k8s_menu_markup())
        return
    if action == "pods":
        try:
            await query.edit_message_text(_pods_status(), reply_markup=k8s_menu_markup())
        except Exception as e:
            await query.edit_message_text(f"❌ Не удалось получить поды: {e}", reply_markup=k8s_menu_markup())
        return
    if action == "logs":
        try:
            await query.edit_message_text(_pod_logs(), reply_markup=k8s_menu_markup())
        except Exception as e:
            await query.edit_message_text(f"❌ Не удалось получить логи: {e}", reply_markup=k8s_menu_markup())
        return
    if action == "events":
        try:
            await query.edit_message_text(_k8s_events(), reply_markup=k8s_menu_markup())
        except Exception as e:
            await query.edit_message_text(f"❌ Не удалось получить события: {e}", reply_markup=k8s_menu_markup())
        return
    if action == "restart":
        try:
            client = _load_k8s()
            _restart_deployment(client)
            await query.edit_message_text("✅ Deployment перезапущен.", reply_markup=k8s_menu_markup())
        except Exception as e:
            await query.edit_message_text(f"❌ Не удалось перезапустить deployment: {e}", reply_markup=k8s_menu_markup())
        return
    if action == "connect":
        ctx.user_data["mode"] = "k8s_kubeconfig"
        await query.edit_message_text(
            "Пришли kubeconfig YAML файлом или просто вставь содержимое YAML следующим сообщением.\n\n"
            f"Файл будет сохранён как `{_kubeconfig_path()}`.",
            parse_mode="Markdown",
        )
        return
    if action == "set_config":
        ctx.user_data["mode"] = "k8s_config"
        await query.edit_message_text("Введи ConfigMap в формате:\nKEY VALUE\n\nНапример:\nAI_MODEL openai/gpt-4o")
        return
    if action == "set_secret":
        ctx.user_data["mode"] = "k8s_secret"
        await query.edit_message_text("Введи Secret в формате:\nKEY VALUE\n\nНапример:\nAI_API_KEY sk-...")
        return
    if action == "set_image":
        ctx.user_data["mode"] = "k8s_image"
        await query.edit_message_text("Введи новый Docker image полностью:\nregistry.example.com/devops-bot:tag")
        return
    await query.edit_message_text(k8s_menu_text(), reply_markup=k8s_menu_markup())


def _save_kubeconfig(content: str):
    if "apiVersion:" not in content or "clusters:" not in content or "contexts:" not in content or "users:" not in content:
        raise ValueError("это не похоже на kubeconfig YAML")
    kubeconfig_path = _kubeconfig_path()
    kubeconfig_path.parent.mkdir(parents=True, exist_ok=True)
    kubeconfig_path.write_text(content, encoding="utf-8")
    os.chmod(kubeconfig_path, 0o600)
    os.environ["KUBECONFIG"] = str(kubeconfig_path)
    _load_k8s().VersionApi().get_code()


async def handle_k8s_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(f"⛔ Нет доступа. Ваш Telegram ID: {update.effective_user.id}")
        return False
    if ctx.user_data.get("mode") != "k8s_kubeconfig":
        return False
    doc = update.message.document
    if not doc:
        return False
    try:
        tg_file = await ctx.bot.get_file(doc.file_id)
        data = await tg_file.download_as_bytearray()
        _save_kubeconfig(bytes(data).decode("utf-8"))
    except Exception as e:
        await update.message.reply_text(f"❌ Не удалось подключить Kubernetes: {e}", reply_markup=k8s_menu_markup())
        ctx.user_data.pop("mode", None)
        return True
    ctx.user_data.pop("mode", None)
    await update.message.reply_text("✅ Kubernetes подключён. Можно тестировать кнопки меню.", reply_markup=k8s_menu_markup())
    return True


async def handle_k8s_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE, mode: str):
    text = (update.message.text or "").strip()
    try:
        if mode == "k8s_kubeconfig":
            _save_kubeconfig(text)
            msg = "✅ Kubernetes подключён. Можно тестировать кнопки меню."
        elif mode == "k8s_config":
            key, value = text.split(maxsplit=1)
            _set_config_value("config", key, value)
            msg = "✅ ConfigMap обновлён, deployment перезапущен."
        elif mode == "k8s_secret":
            key, value = text.split(maxsplit=1)
            _set_config_value("secret", key, value)
            msg = "✅ Secret обновлён, deployment перезапущен."
        elif mode == "k8s_image":
            if not text:
                raise ValueError("image не указан")
            _set_image(text)
            msg = "✅ Docker image deployment обновлён."
        else:
            return False
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Нажми ☸️ Kubernetes и выбери действие заново.")
        return True
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка Kubernetes: {e}", reply_markup=k8s_menu_markup())
        ctx.user_data.pop("mode", None)
        return True
    ctx.user_data.pop("mode", None)
    await update.message.reply_text(msg, reply_markup=k8s_menu_markup())
    return True


async def k8s_config_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(f"⛔ Нет доступа. Ваш Telegram ID: {update.effective_user.id}")
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Использование: /k8s_config KEY VALUE")
        return
    key, value = ctx.args[0], " ".join(ctx.args[1:])
    try:
        _set_config_value("config", key, value)
        await update.message.reply_text("✅ ConfigMap обновлён, deployment перезапущен.")
    except Exception as e:
        await update.message.reply_text(f"❌ Не удалось обновить ConfigMap: {e}")


async def k8s_secret_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(f"⛔ Нет доступа. Ваш Telegram ID: {update.effective_user.id}")
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Использование: /k8s_secret KEY VALUE")
        return
    key, value = ctx.args[0], " ".join(ctx.args[1:])
    try:
        _set_config_value("secret", key, value)
        await update.message.reply_text("✅ Secret обновлён, deployment перезапущен.")
    except Exception as e:
        await update.message.reply_text(f"❌ Не удалось обновить Secret: {e}")


async def k8s_image_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(f"⛔ Нет доступа. Ваш Telegram ID: {update.effective_user.id}")
        return
    if not ctx.args:
        await update.message.reply_text("Использование: /k8s_image IMAGE")
        return
    image = " ".join(ctx.args).strip()
    try:
        _set_image(image)
        await update.message.reply_text("✅ Образ deployment обновлён.")
    except Exception as e:
        await update.message.reply_text(f"❌ Не удалось обновить образ: {e}")
