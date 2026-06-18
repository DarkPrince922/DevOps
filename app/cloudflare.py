import requests

import app.state as state
from app.proxy import requests_proxy

CF_API = "https://api.cloudflare.com/client/v4"


def _token():
    """Токен берётся из per-user файла cloudflare.json, иначе из .env."""
    try:
        cfg = state.load_json(state.CF_FILE, {})
    except Exception:
        cfg = {}
    token = (cfg.get("token") if isinstance(cfg, dict) else "") or state.CLOUDFLARE_API_TOKEN
    return (token or "").strip()


def has_token():
    return bool(_token())


def save_token(token):
    state.save_json(state.CF_FILE, {"token": str(token or "").strip()})


def _request(method, path, **kwargs):
    token = _token()
    if not token:
        return None, "токен не задан (CLOUDFLARE_API_TOKEN в .env или кнопка «Задать токен»)"
    try:
        r = requests.request(
            method,
            f"{CF_API}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            proxies=requests_proxy(),
            timeout=30,
            **kwargs,
        )
        data = r.json() if r.content else {}
    except Exception as e:
        return None, f"сетевая ошибка: {e}"
    if not isinstance(data, dict) or not data.get("success", False):
        errs = ""
        if isinstance(data, dict):
            errs = "; ".join(str(e.get("message", "")) for e in (data.get("errors") or []))
        return None, errs or f"HTTP {r.status_code}"
    return data.get("result"), ""


# ── Зоны ────────────────────────────────────────────────────────
def zones():
    """-> (list[dict], err)"""
    result, err = _request("GET", "/zones?per_page=50")
    return (result or []), err


def _resolve_zone(name_or_zone):
    """По имени записи (panel.example.com) или зоны (example.com) находит зону."""
    name = (name_or_zone or "").strip().lower().rstrip(".")
    if not name:
        return None, None, "не указано имя зоны/записи"
    zlist, err = zones()
    if err:
        return None, None, err
    best = None
    for z in zlist:
        zn = str(z.get("name", "")).lower()
        if name == zn or name.endswith("." + zn):
            if best is None or len(zn) > len(best[1]):
                best = (z["id"], zn)
    if not best:
        return None, None, f"зона для «{name_or_zone}» не найдена в аккаунте"
    return best[0], best[1], ""


# ── DNS ─────────────────────────────────────────────────────────
def dns_records(zone_id, name=None):
    """-> (list[dict], err) по id зоны (для меню)."""
    path = f"/zones/{zone_id}/dns_records?per_page=100"
    if name:
        path += f"&name={name.strip().lower()}"
    result, err = _request("GET", path)
    return (result or []), err


def format_records(zone_name, recs, err=""):
    if err:
        return f"❌ Cloudflare: {err}"
    if not recs:
        return f"DNS зоны {zone_name}: записей нет."
    lines = [f"DNS зоны {zone_name}:"]
    for r in recs[:40]:
        proxied = "🟠" if r.get("proxied") else "⚪"
        lines.append(f"{proxied} {r.get('type')} {r.get('name')} → {r.get('content')} (TTL {r.get('ttl')})")
    more = "" if len(recs) <= 40 else f"\n…ещё: {len(recs) - 40}"
    return "\n".join(lines)[:3500] + more


def list_zones():
    zlist, err = zones()
    if err:
        return f"❌ Cloudflare: {err}"
    if not zlist:
        return "Зоны не найдены или у токена нет доступа."
    return "Зоны Cloudflare:\n" + "\n".join(
        f"• {z.get('name')} — {z.get('status')}" for z in zlist
    )


def list_dns(zone, name=None):
    zid, zname, err = _resolve_zone(zone)
    if err:
        return f"❌ Cloudflare: {err}"
    recs, err = dns_records(zid, name)
    return format_records(zname, recs, err)


def set_dns(name, rtype="A", content="", proxied=None, ttl=1):
    """Создаёт или обновляет DNS-запись (upsert по type+name)."""
    name = (name or "").strip().lower().rstrip(".")
    rtype = (rtype or "A").strip().upper()
    content = str(content or "").strip()
    if not name or not content:
        return "❌ Cloudflare: нужно имя записи и значение (content)"
    zid, zname, err = _resolve_zone(name)
    if err:
        return f"❌ Cloudflare: {err}"
    existing, err = _request("GET", f"/zones/{zid}/dns_records?type={rtype}&name={name}")
    if err:
        return f"❌ Cloudflare: {err}"
    try:
        ttl = int(ttl) or 1
    except Exception:
        ttl = 1
    body = {"type": rtype, "name": name, "content": content, "ttl": ttl}
    if existing:
        body["proxied"] = bool(proxied) if proxied is not None else existing[0].get("proxied", False)
        _, err = _request("PUT", f"/zones/{zid}/dns_records/{existing[0]['id']}", json=body)
        action = "обновлена"
    else:
        body["proxied"] = bool(proxied) if proxied is not None else False
        _, err = _request("POST", f"/zones/{zid}/dns_records", json=body)
        action = "создана"
    if err:
        return f"❌ Cloudflare: {err}"
    cloud = "proxied 🟠" if body["proxied"] else "dns-only ⚪"
    return f"✅ Запись {action}: {rtype} {name} → {content} ({cloud})"


def delete_dns(name, rtype=None):
    name = (name or "").strip().lower().rstrip(".")
    if not name:
        return "❌ Cloudflare: не указано имя записи"
    zid, zname, err = _resolve_zone(name)
    if err:
        return f"❌ Cloudflare: {err}"
    path = f"/zones/{zid}/dns_records?name={name}"
    if rtype:
        path += f"&type={rtype.strip().upper()}"
    recs, err = _request("GET", path)
    if err:
        return f"❌ Cloudflare: {err}"
    if not recs:
        return f"Записи {name} не найдены."
    deleted = []
    for r in recs:
        _, err = _request("DELETE", f"/zones/{zid}/dns_records/{r['id']}")
        if not err:
            deleted.append(f"{r.get('type')} {r.get('name')}")
    if not deleted:
        return "❌ Cloudflare: не удалось удалить записи."
    return "✅ Удалено: " + "; ".join(deleted)


# ── Кэш ─────────────────────────────────────────────────────────
def purge_cache_id(zone_id, zone_name=""):
    _, err = _request("POST", f"/zones/{zone_id}/purge_cache", json={"purge_everything": True})
    if err:
        return f"❌ Cloudflare: {err}"
    return f"✅ Кэш зоны {zone_name or zone_id} очищен."


def purge_cache(zone):
    zid, zname, err = _resolve_zone(zone)
    if err:
        return f"❌ Cloudflare: {err}"
    return purge_cache_id(zid, zname)
