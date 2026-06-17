import base64, copy, fcntl, hashlib, json, os, time, threading, contextvars
from pathlib import Path
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

TOKEN    = os.environ["TELEGRAM_TOKEN"]
API_KEY  = os.environ.get("AI_API_KEY") or os.environ.get("ROUTERAI_API_KEY") or os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
ENV_API_KEY = API_KEY
API_BASE = (os.environ.get("AI_BASE_URL") or os.environ.get("ROUTERAI_BASE_URL") or os.environ.get("CODEX_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://routerai.ru/api/v1").rstrip("/")
MODEL    = os.environ.get("AI_MODEL") or os.environ.get("MODEL") or "anthropic/claude-opus-4-7"

DATA_DIR     = Path(os.environ.get("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
SERVERS_FILE = DATA_DIR / "servers.json"
SESSIONS_FILE = DATA_DIR / "sessions.json"
PROXIES_FILE  = DATA_DIR / "proxies.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
PROJECTS_FILE = DATA_DIR / "projects.json"
ACTIVE_PROJECTS_FILE = DATA_DIR / "active_projects.json"
AI_AGENTS_FILE = DATA_DIR / "ai_agents.json"
ALLOWED_USERS_FILE = DATA_DIR / "allowed_users.json"
NOTIFIED_USERS_FILE = DATA_DIR / "notified_users.json"
AGENTS_FILE   = DATA_DIR / "agents.json"

STOP_FLAGS   = {}   # uid -> bool
STATUS_TEXT  = {}   # uid -> current action string
START_TIME   = {}   # uid -> timestamp
ADMIN_IDS    = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(",", " ").split() if x.strip().isdigit()}
PUBLIC_ACCESS = os.environ.get("PUBLIC_ACCESS", "0").lower() in {"1", "true", "yes", "on"}
ALLOWED_USERS_FILE = DATA_DIR / "allowed_users.json"
K8S_FILE = DATA_DIR / "k8s.json"
CURRENT_USER_ID = contextvars.ContextVar("CURRENT_USER_ID", default=None)
MAX_OUTPUT   = int(os.environ.get("MAX_OUTPUT", "4000"))
MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", "200000"))
WORK_DIR     = Path(os.environ.get("WORK_DIR", "/app")).resolve()
SESSION_HISTORY = {}
SESSION_UPDATED = {}
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "14"))
SESSION_MAX_USERS = int(os.environ.get("SESSION_MAX_USERS", "50"))
BACKUP_RETENTION = int(os.environ.get("BACKUP_RETENTION", "5"))
ACTIVE_TASKS = set()
RUNNING_TASKS = {}
ACTIVE_PROCESSES = {}
ACTIVE_CHANNELS = {}
PENDING_CONFIRM = {}
PENDING_CONTINUE = {}
PENDING_ADMIN_SETTING = {}
PENDING_HOSTKEY = {}
PENDING_CONTINUE = {}
CONFIRMED_TASKS = set()
MAX_HISTORY_CHARS = int(os.environ.get("MAX_HISTORY_CHARS", "60000"))
AUTO_CONTINUE_LIMIT = int(os.environ.get("AUTO_CONTINUE_LIMIT", "3"))

TELEGRAM_HTML_RULE = """
Для Telegram используй только поддерживаемые HTML-теги для отображения текста.

Поддерживаемые HTML теги:
• <b>жирный</b> или <strong></strong>
• <i>курсив</i> или <em></em>
• <u>подчёркнутый</u>
• <s>зачёркнутый</s>
• <code>моноширинный</code>
• <pre>блок кода</pre>
• <a href="url">ссылка</a>
• <blockquote>цитата</blockquote>
• <tg-spoiler>спойлер</tg-spoiler>
• <tg-emoji emoji-id="123">😀</tg-emoji>

Важные правила:
• Каждый открывающий тег должен быть закрыт
• Теги должны быть правильно вложены
• Атрибуты ссылок берите в кавычки
• В обычных ответах уместно используй HTML-теги для акцентов: выделяй <b>важное</b>, <code>команды</code>, <blockquote>заметки</blockquote>, <i>уточнения</i>
• Не превращай весь ответ в сплошную разметку: теги должны улучшать читаемость, а не мешать ей
• Если форматирование не нужно, не добавляй его без причины

Неправильно:
<b>жирный <i>курсив</b></i>

Правильно:
<b>жирный <i>курсив</i></b>
"""
MAX_AGENT_STEPS = int(os.environ.get("MAX_AGENT_STEPS", "20"))

DANGEROUS_WORDS = {"shutdown", "reboot", "halt", "poweroff", "mkfs", "dd", "init", "telinit", "wipefs", "fdisk", "parted", "visudo"}
DANGEROUS_PATTERNS = [
    r"rm\s+-[^;&|]*[rf][^;&|]*\s+/(?:\s|$)",
    r"rm\s+-[^;&|]*[rf][^;&|]*(?:\s+|=)(?:/|/root|/etc|/var|/usr|/home|/opt|/srv|/boot|/var/lib/docker)(?:\s|/|$)",
    r"docker\s+(?:compose\s+)?(?:down|kill|rm|stop|restart)\b",
    r"docker\s+(?:system|volume|network|builder)\s+prune\b",
    r"docker\s+volume\s+rm\b",
    r"rm\s+-",
    r"\bchmod\s+(?:-R\s+)?(?:777|666|ugo\+rwx|a\+rwx)\b",
    r"\b(?:chmod|chown|mv)\b[^;&|]*(?:\s|=)(?:/|/root|/etc|/var|/usr|/home|/opt|/srv|/boot|/var/lib/docker)(?:\s|/|$)",
    r"\bchown\s+-R\b",
    r"systemctl\s+(?:restart|stop|disable|mask)\b",
    r"(?:>|dd\s+.*of=)\s*/dev/(?:sd|vd|xvd|nvme)",
    r"\b(?:mkfs|wipefs|fdisk|parted)\b",
    r"bash\s+-c|sh\s+-c|eval\b|:(){",
]

SYSTEM_PROMPT = """Правила работы:
- Выполняй только текущий запрос пользователя; если данных достаточно — действуй.
- Используй доступные инструменты для чтения, поиска, команд, веб-страниц и файлов. Не отвечай планом вместо действия.
- Перед изменением продакшена, удалением данных, правами, БД, firewall, секретами или остановкой сервисов запроси подтверждение.
- После изменения кода проверь синтаксис изменённых файлов; перед перезапуском сервиса тоже проверь синтаксис.
- Не раскрывай секреты и не отправляй сырые логи, дампы или длинные списки.
- Итоговый ответ пиши кратко на русском: что сделано, где, результат или следующий шаг.
"""


SECRET_FIELDS = {"password", "passphrase", "key_passphrase"}
ENC_PREFIX = "enc::"

def _crypto_key():
    secret = os.environ.get("SERVER_PASSWORD_KEY") or os.environ.get("FERNET_KEY") or TOKEN
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)

def _fernet():
    return Fernet(_crypto_key())

def encrypt_secret(value):
    if not isinstance(value, str) or not value or value.startswith(ENC_PREFIX):
        return value
    return ENC_PREFIX + _fernet().encrypt(value.encode()).decode()

def decrypt_secret(value):
    if not isinstance(value, str) or not value.startswith(ENC_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(ENC_PREFIX):].encode()).decode()
    except Exception:
        return value

def _crypt_server_secrets(data, encrypt=True):
    data = copy.deepcopy(data)
    if not isinstance(data, dict):
        return data
    for cfg in data.values():
        if not isinstance(cfg, dict):
            continue
        for key in SECRET_FIELDS:
            if key in cfg:
                cfg[key] = encrypt_secret(cfg[key]) if encrypt else decrypt_secret(cfg[key])
    return data

USER_SCOPED_FILES = {"servers.json", "settings.json", "agents.json", "providers.json", "projects.json", "proxies.json", "k8s.json"}

def set_current_user_id(uid):
    try:
        CURRENT_USER_ID.set(int(uid) if uid is not None else None)
    except Exception:
        CURRENT_USER_ID.set(None)

def current_user_id():
    return CURRENT_USER_ID.get()

def is_admin_id(uid=None):
    if uid is None:
        uid = current_user_id()
    try:
        return int(uid) in ADMIN_IDS
    except Exception:
        return False

def scoped_data_path(path):
    p = Path(path)
    uid = current_user_id()
    if uid is not None and not is_admin_id(uid) and p.name in USER_SCOPED_FILES:
        return DATA_DIR / "users" / str(uid) / p.name
    return p

def get_allowed_users():
    data = load_json(ALLOWED_USERS_FILE, {"users": []})
    users = data.get("users", []) if isinstance(data, dict) else []
    return sorted({int(x) for x in users if str(x).lstrip("-").isdigit()})

def is_allowed_user(uid):
    try:
        return int(uid) in get_allowed_users()
    except Exception:
        return False

def add_allowed_user(uid):
    users = set(get_allowed_users())
    users.add(int(uid))
    save_json(ALLOWED_USERS_FILE, {"users": sorted(users)})

def remove_allowed_user(uid):
    users = set(get_allowed_users())
    users.discard(int(uid))
    save_json(ALLOWED_USERS_FILE, {"users": sorted(users)})

def is_user_authorized(uid):
    return PUBLIC_ACCESS or is_admin_id(uid) or is_allowed_user(uid)

def _secure_file(path):
    try: os.chmod(path, 0o600)
    except FileNotFoundError: pass

def secure_data_files():
    for f in [Path(".env"), SERVERS_FILE, SESSIONS_FILE, PROXIES_FILE, SETTINGS_FILE]:
        _secure_file(f)

def load_json(path, default):
    path = scoped_data_path(path)
    if path.exists():
        _secure_file(path)
        try:
            data = json.loads(path.read_text())
            if path.name == SERVERS_FILE.name:
                data = _crypt_server_secrets(data, encrypt=False)
            return data
        except Exception: return default
    return default

def save_json(path, data):
    path = scoped_data_path(path)
    if path.name == SERVERS_FILE.name:
        data = _crypt_server_secrets(data, encrypt=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_UN)


def default_settings():
    return {
        "model": MODEL,
        "max_output": MAX_OUTPUT,
        "max_history_chars": MAX_HISTORY_CHARS,
        "auto_continue_limit": AUTO_CONTINUE_LIMIT,
        "max_agent_steps": MAX_AGENT_STEPS,
        "max_agent_steps": MAX_AGENT_STEPS,
        "service_monitor_enabled": os.environ.get("SERVICE_MONITOR_ENABLED", "1").lower() not in {"0", "false", "no", "off"},
    }

def get_settings():
    cfg = load_json(SETTINGS_FILE, {})
    base = default_settings()
    # model всегда из .env, не переопределяем через settings.json
    base.update({k: v for k, v in cfg.items() if k in base and k != "model"})
    return base

_settings_lock = threading.Lock()

def apply_settings(cfg=None):
    global MODEL, MAX_OUTPUT, MAX_HISTORY_CHARS, AUTO_CONTINUE_LIMIT, MAX_AGENT_STEPS
    cfg = cfg or get_settings()
    with _settings_lock:
        MODEL = str(cfg.get("model", MODEL)).strip() or MODEL
        MAX_OUTPUT = max(500, min(20000, int(cfg.get("max_output", MAX_OUTPUT))))
        MAX_HISTORY_CHARS = max(5000, min(200000, int(cfg.get("max_history_chars", MAX_HISTORY_CHARS))))
        AUTO_CONTINUE_LIMIT = max(0, min(10, int(cfg.get("auto_continue_limit", AUTO_CONTINUE_LIMIT))))
        MAX_AGENT_STEPS = max(1, min(50, int(cfg.get("max_agent_steps", MAX_AGENT_STEPS))))
    save_json(SETTINGS_FILE, {
        # model не сохраняем — управляется только через .env
        "max_output": MAX_OUTPUT,
        "max_history_chars": MAX_HISTORY_CHARS,
        "auto_continue_limit": AUTO_CONTINUE_LIMIT,
        "service_monitor_enabled": bool(cfg.get("service_monitor_enabled", True)),
    })

def set_setting(key, value):
    cfg = get_settings()
    if key == "model":
        raise ValueError("модель управляется только через .env, измени MODEL там и перезапусти бота")
    if key in ("max_output", "max_history_chars", "auto_continue_limit", "max_agent_steps"):
        cfg[key] = int(str(value).strip())
    elif key == "service_monitor_enabled":
        if isinstance(value, bool):
            cfg[key] = value
        else:
            cfg[key] = str(value).strip().lower() in {"1", "true", "yes", "on", "да", "вкл", "включено"}
    else:
        raise ValueError("неизвестная настройка")
    apply_settings(cfg)


# ────────────────────────────────────────────────
# ИИ-агенты: переключение моделей и совместная работа
# ────────────────────────────────────────────────
_agents_lock = threading.Lock()

def default_agents():
    if current_user_id() is not None and not is_admin_id():
        return {"agents": [], "active": None, "collab": False, "collab_agents": []}
    # Набор агентов по умолчанию. Имя -> модель (routerai/openai-совместимая).
    return {
        "agents": [
            {"name": "Claude Opus", "model": "anthropic/claude-opus-4-7"},
            {"name": "GPT-4o", "model": "openai/gpt-4o"},
            {"name": "Gemini 2.5 Pro", "model": "google/gemini-2.5-pro"},
        ],
        "active": "Claude Opus",
        "collab": False,          # режим совместной работы агентов
        "collab_agents": ["GPT-4o", "Gemini 2.5 Pro"],  # советники для активного агента
    }

def get_agents():
    cfg = load_json(AGENTS_FILE, None)
    base = default_agents()
    if not isinstance(cfg, dict):
        return base
    agents = cfg.get("agents")
    if not isinstance(agents, list):
        agents = base["agents"]
    clean = []
    provider_names = [p["name"] for p in get_providers().get("providers", [])]
    default_provider = get_providers().get("active") or (provider_names[0] if provider_names else "")
    for a in agents:
        if isinstance(a, dict) and a.get("name") and a.get("model"):
            provider = str(a.get("provider") or default_provider).strip()
            if provider not in provider_names:
                provider = default_provider
            clean.append({"name": str(a["name"]).strip(), "model": str(a["model"]).strip(), "provider": provider})
    if not clean and is_admin_id():
        clean = base["agents"]
    active = cfg.get("active")
    names = [a["name"] for a in clean]
    if active not in names:
        active = clean[0]["name"] if clean else None

    collab_agents = cfg.get("collab_agents")
    if not isinstance(collab_agents, list):
        # миграция старого режима: все агенты, кроме активного, становятся советниками
        collab_agents = [n for n in names if n != active]
    collab_agents = [str(n) for n in collab_agents if str(n) in names]

    return {
        "agents": clean,
        "active": active,
        "collab": bool(cfg.get("collab", False)),
        "collab_agents": collab_agents,
    }

def save_agents(cfg):
    with _agents_lock:
        save_json(AGENTS_FILE, cfg)

def active_agent_model():
    a = active_agent()
    return a.get("model") if a else None

def active_agent():
    cfg = get_agents()
    for a in cfg["agents"]:
        if a["name"] == cfg["active"]:
            return a
    return cfg["agents"][0] if cfg.get("agents") else None

def set_active_agent(name):
    cfg = get_agents()
    if name in [a["name"] for a in cfg["agents"]]:
        cfg["active"] = name
        save_agents(cfg)
        return True
    return False

def toggle_collab_agent(name):
    cfg = get_agents()
    names = [a["name"] for a in cfg["agents"]]
    if name not in names:
        return False
    selected = list(cfg.get("collab_agents", []))
    if name in selected:
        selected.remove(name)
    else:
        selected.append(name)
    cfg["collab_agents"] = [n for n in selected if n in names]
    save_agents(cfg)
    return True

def selected_collab_agents():
    cfg = get_agents()
    selected = set(cfg.get("collab_agents", []))
    return [a for a in cfg["agents"] if a["name"] in selected]

def toggle_collab():
    cfg = get_agents()
    cfg["collab"] = not cfg.get("collab", False)
    save_agents(cfg)
    return cfg["collab"]

def add_agent(name, model, provider=None):
    cfg = get_agents()
    name = str(name).strip()
    model = str(model).strip()
    providers = get_providers()
    provider_names = [p["name"] for p in providers.get("providers", [])]
    provider = str(provider or providers.get("active") or (provider_names[0] if provider_names else "")).strip()
    if provider not in provider_names:
        provider = providers.get("active") or (provider_names[0] if provider_names else "")
    if not name or not model or not provider:
        return False
    cfg["agents"] = [a for a in cfg["agents"] if a["name"] != name]
    cfg["agents"].append({"name": name, "model": model, "provider": provider})
    save_agents(cfg)
    return True

def del_agent(name):
    cfg = get_agents()
    cfg["agents"] = [a for a in cfg["agents"] if a["name"] != name]
    cfg["collab_agents"] = [n for n in cfg.get("collab_agents", []) if n != name]
    if cfg["active"] not in [a["name"] for a in cfg["agents"]]:
        cfg["active"] = cfg["agents"][0]["name"] if cfg["agents"] else None
    save_agents(cfg)
    return True


# ────────────────────────────────────────────────
# Провайдеры / реселлеры OpenAI-совместимого API
# ────────────────────────────────────────────────
PROVIDERS_FILE = DATA_DIR / "providers.json"
_providers_lock = threading.Lock()

def default_providers():
    if current_user_id() is not None and not is_admin_id():
        return {"providers": [], "active": None}
    env_url = (API_BASE or "https://routerai.ru/api/v1").rstrip("/")
    return {
        "providers": [
            {"name": "Текущий .env", "base_url": env_url, "api_key": ENV_API_KEY},
            {"name": "Codex Sale", "base_url": "https://codex.sale/v1", "api_key": ""},
            {"name": "RouterAI", "base_url": "https://routerai.ru/api/v1", "api_key": ""},
            {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "api_key": ""},
        ],
        "active": "Текущий .env",
    }

def get_providers():
    cfg = load_json(PROVIDERS_FILE, None)
    base = default_providers()
    if not isinstance(cfg, dict):
        return base
    items = cfg.get("providers")
    if not isinstance(items, list):
        items = base["providers"]
    clean = []
    seen = set()
    for p in items:
        if isinstance(p, dict) and p.get("name") and p.get("base_url"):
            name = str(p["name"]).strip()
            url = str(p["base_url"]).strip().rstrip("/")
            if name and url and name not in seen:
                api_key = str(p.get("api_key", "")).strip()
                clean.append({"name": name, "base_url": url, "api_key": api_key})
                seen.add(name)
    if not clean and is_admin_id():
        clean = base["providers"]
    active = cfg.get("active")
    if active not in [p["name"] for p in clean]:
        active = clean[0]["name"] if clean else None
    return {"providers": clean, "active": active}

def save_providers(cfg):
    with _providers_lock:
        save_json(PROVIDERS_FILE, cfg)

def active_provider():
    cfg = get_providers()
    for p in cfg["providers"]:
        if p["name"] == cfg["active"]:
            return p
    return cfg["providers"][0] if cfg.get("providers") else {"base_url": API_BASE, "api_key": ENV_API_KEY}

def active_provider_url():
    return active_provider()["base_url"].rstrip("/")

def provider_by_name(name):
    cfg = get_providers()
    for p in cfg.get("providers", []):
        if p.get("name") == name:
            return p
    return active_provider()

def apply_active_provider():
    global API_BASE, API_KEY
    p = active_provider()
    API_BASE = p.get("base_url", API_BASE).rstrip("/")
    API_KEY = p.get("api_key") or ENV_API_KEY
    return API_BASE

def set_active_provider(name):
    cfg = get_providers()
    if name in [p["name"] for p in cfg["providers"]]:
        cfg["active"] = name
        save_providers(cfg)
        apply_active_provider()
        return True
    return False

def add_provider(name, base_url, api_key=""):
    cfg = get_providers()
    name = str(name).strip()
    base_url = str(base_url).strip().rstrip("/")
    api_key = str(api_key).strip()
    if not name or not base_url.startswith(("http://", "https://")):
        return False
    cfg["providers"] = [p for p in cfg["providers"] if p["name"] != name]
    cfg["providers"].append({"name": name, "base_url": base_url, "api_key": api_key})
    save_providers(cfg)
    return True

def del_provider(name):
    cfg = get_providers()
    cfg["providers"] = [p for p in cfg["providers"] if p["name"] != name]
    if cfg["active"] not in [p["name"] for p in cfg["providers"]]:
        cfg["active"] = cfg["providers"][0]["name"] if cfg["providers"] else None
    save_providers(cfg)
    apply_active_provider()
    return True


# ────────────────────────────────────────────────
# Проекты: сервер + рабочая директория + отдельные инструкции
# ────────────────────────────────────────────────
PROJECTS_FILE = DATA_DIR / "projects.json"
_projects_lock = threading.Lock()

def default_projects():
    return {"projects": [], "active": None}

def get_projects():
    cfg = load_json(PROJECTS_FILE, None)
    if not isinstance(cfg, dict):
        return default_projects()
    items = cfg.get("projects")
    if not isinstance(items, list):
        items = []
    clean = []
    seen = set()
    for p in items:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", "")).strip()
        server = str(p.get("server", "")).strip()
        path = str(p.get("path", "")).strip()
        prompt = str(p.get("prompt", "")).strip()
        extra = {}
        for key in ("docker_image", "build_cmd", "push_cmd", "deploy_cmd", "k8s_namespace", "k8s_deployment", "k8s_container"):
            extra[key] = str(p.get(key, "")).strip()
        if name and server and path and name not in seen:
            item = {"name": name, "server": server, "path": path, "prompt": prompt}
            item.update(extra)
            clean.append(item)
            seen.add(name)
    active = cfg.get("active")
    if active not in [p["name"] for p in clean]:
        active = None
    return {"projects": clean, "active": active}



def is_main_server_name(name):
    value = str(name or "").strip().casefold()
    return value in {"главный сервер", "главный", "local", "localhost", "контейнер", "main"}

def normalize_server_name(name, servers=None):
    """Канонизирует имя сервера одинаково для проектов и tool calls."""
    raw = str(name or "").strip().strip(' .,;:!?)\"\'')
    if is_main_server_name(raw):
        return "Главный сервер"
    servers = servers or {}
    if raw in servers:
        return raw
    folded = raw.casefold()
    for srv in servers.keys():
        if str(srv).casefold() == folded:
            return srv
    return raw

def save_projects(cfg):
    with _projects_lock:
        save_json(PROJECTS_FILE, cfg)

def active_project():
    cfg = get_projects()
    for p in cfg["projects"]:
        if p["name"] == cfg.get("active"):
            return p
    return None

def set_active_project(name):
    cfg = get_projects()
    if name is None or str(name).strip() in {"", "-", "none", "None"}:
        cfg["active"] = None
        save_projects(cfg)
        return True
    name = str(name).strip()
    if name in [p["name"] for p in cfg["projects"]]:
        cfg["active"] = name
        save_projects(cfg)
        return True
    return False

def add_project(name, server, path, prompt=""):
    cfg = get_projects()
    name = str(name).strip()
    server = normalize_server_name(server)
    path = str(path).strip()
    prompt = str(prompt).strip()
    if not name or not server or not path.startswith("/"):
        return False
    cfg["projects"] = [p for p in cfg["projects"] if p["name"] != name]
    cfg["projects"].append({"name": name, "server": server, "path": path, "prompt": prompt})
    if not cfg.get("active"):
        cfg["active"] = name
    save_projects(cfg)
    return True

def del_project(name):
    cfg = get_projects()
    name = str(name).strip()
    before = len(cfg["projects"])
    cfg["projects"] = [p for p in cfg["projects"] if p["name"] != name]
    if len(cfg["projects"]) == before:
        return False
    if cfg.get("active") == name:
        cfg["active"] = cfg["projects"][0]["name"] if cfg["projects"] else None
    save_projects(cfg)
    return True


def update_project_deploy(name, **fields):
    cfg = get_projects()
    name = str(name).strip()
    allowed = {"docker_image", "build_cmd", "push_cmd", "deploy_cmd", "k8s_namespace", "k8s_deployment", "k8s_container"}
    for p in cfg["projects"]:
        if p["name"] == name:
            for k, v in fields.items():
                if k in allowed:
                    p[k] = str(v or "").strip()
            save_projects(cfg)
            return True
    return False



def ensure_defaults_extra():
    # Создаём только отсутствующие файлы с безопасными дефолтами;
    # рабочие структуры проектов/агентов/провайдеров и их форматы обслуживают функции выше.
    for path, default in [
        (ALLOWED_USERS_FILE, {"users": []}),
        (NOTIFIED_USERS_FILE, {"users": []}),
    ]:
        if not path.exists():
            save_json(path, default)

ensure_defaults_extra()
