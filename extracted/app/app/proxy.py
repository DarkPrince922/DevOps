import re

from app.state import PROXIES_FILE, load_json, save_json

def get_proxy_config():
    cfg = load_json(PROXIES_FILE, {"enabled": False, "current": "", "items": []})
    cfg.setdefault("enabled", False)
    cfg.setdefault("current", "")
    cfg.setdefault("items", [])
    return cfg

def save_proxy_config(cfg):
    save_json(PROXIES_FILE, cfg)

def normalize_socks5(value):
    value = value.strip().replace("socks5://", "")

    # Поддерживаем оба формата:
    # user:pass@host:port и host:port:user:pass
    if "@" in value:
        if not re.fullmatch(r"[^:@\s]+:[^@\s]+@[^:\s]+:\d{1,5}", value):
            return None
        port = int(value.rsplit(":", 1)[1])
        return value if 1 <= port <= 65535 else None

    parts = value.split(":", 3)
    if len(parts) == 4:
        host, port, user, password = parts
        if not host or not user or not password or not port.isdigit():
            return None
        port = int(port)
        if not 1 <= port <= 65535:
            return None
        return f"{user}:{password}@{host}:{port}"

    return None

def parse_socks5(value):
    value = normalize_socks5(value or "")
    if not value:
        return None
    creds, hostport = value.rsplit("@", 1)
    user, password = creds.split(":", 1)
    host, port = hostport.rsplit(":", 1)
    return host, int(port), user, password

def active_proxy_url():
    cfg = get_proxy_config()
    if cfg.get("enabled") and cfg.get("current"):
        return "socks5://" + cfg["current"]
    return None

def requests_proxy():
    url = active_proxy_url()
    return {"http": url, "https": url} if url else None
