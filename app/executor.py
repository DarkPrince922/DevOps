import os
import threading, subprocess, paramiko, shutil, time, re, shlex, requests, html, io, logging
from urllib.parse import quote_plus, unquote, urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import socks

import app.state as state
from app.proxy import parse_socks5, get_proxy_config, requests_proxy

logger = logging.getLogger(__name__)

# Файл known_hosts для TOFU-верификации SSH-хостов
_KNOWN_HOSTS_FILE = state.DATA_DIR / ".ssh" / "known_hosts"
_KNOWN_HOSTS_LOCK = threading.Lock()


class _TOFUPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, overwrite_changed=False):
        self.overwrite_changed = overwrite_changed

    """Trust On First Use.

    Первое подключение к хосту: сохраняет ключ в known_hosts и пропускает.
    Повторное подключение: сверяет с сохранённым ключом.
    Изменение ключа: выбрасывает исключение — возможный MITM.
    """

    def missing_host_key(self, client, hostname, key):
        with _KNOWN_HOSTS_LOCK:
            kh_path = _KNOWN_HOSTS_FILE
            kh_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(str(kh_path.parent), 0o700)
            except Exception:
                pass

            host_keys = paramiko.HostKeys()
            if kh_path.exists():
                try:
                    host_keys.load(str(kh_path))
                except Exception as e:
                    logger.warning("SSH known_hosts load error: %s", e)

            key_type = key.get_name()
            existing = host_keys.lookup(hostname)
            existing_key = existing.get(key_type) if existing else None

            if existing_key is None:
                host_keys.add(hostname, key_type, key)
                try:
                    host_keys.save(str(kh_path))
                except Exception as e:
                    logger.error("SSH TOFU: known_hosts save error: %s", e)
                try:
                    os.chmod(str(kh_path), 0o600)
                except Exception:
                    pass
                fp = key.get_fingerprint().hex(":")
                logger.info("SSH TOFU: новый хост %s [%s], fingerprint %s сохранён", hostname, key_type, fp)
            elif existing_key.get_fingerprint() != key.get_fingerprint():
                fp_old = existing_key.get_fingerprint().hex(":")
                fp_new = key.get_fingerprint().hex(":")
                if self.overwrite_changed:
                    host_keys.add(hostname, key_type, key)
                    try:
                        host_keys.save(str(kh_path))
                    except Exception as e:
                        logger.error("SSH TOFU: known_hosts save error: %s", e)
                    try:
                        os.chmod(str(kh_path), 0o600)
                    except Exception:
                        pass
                    logger.warning("SSH TOFU: fingerprint хоста %s [%s] перезаписан: %s -> %s", hostname, key_type, fp_old, fp_new)
                else:
                    raise paramiko.SSHException(
                        f"HOST_FINGERPRINT_CHANGED:{hostname}\n"
                        f"⚠️ SSH-ключ хоста {hostname} изменился!\n"
                        f"Было: {fp_old}\n"
                        f"Стало: {fp_new}\n"
                        f"Если сервер был переустановлен — подтверди перезапись fingerprint кнопкой."
                    )
            # Ключ совпадает — пропускаем без действий

def web_fetch(url):
    if not re.match(r"^https?://", url or "", re.I):
        return "❌ Укажи полный URL с http:// или https://"
    try:
        r = requests.get(url, timeout=20, proxies=requests_proxy(), headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        text = r.text
        if "html" in ctype.lower():
            text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:state.MAX_OUTPUT]
    except Exception as e:
        return f"❌ Не удалось загрузить страницу: {e}"

def _extract_search_links(raw):
    links = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', raw):
        href = html.unescape(href)
        if href.startswith('//duckduckgo.com/l/?'):
            href = 'https:' + href
        if 'duckduckgo.com/l/?' in href:
            qs = parse_qs(urlparse(href).query)
            href = unquote((qs.get('uddg') or [''])[0])
        if href.startswith('http') and 'duckduckgo.com' not in href and href not in links:
            links.append(href)
        if len(links) >= 6:
            break
    return links

def web_search(query, sources=5):
    try:
        sources = max(3, min(6, int(sources or 5)))
    except Exception:
        sources = 5
    try:
        queries = [query, f"{query} official docs", f"{query} troubleshooting"]
        links = []
        for q in queries:
            r = requests.get(
                'https://duckduckgo.com/html/?q=' + quote_plus(q),
                timeout=20,
                proxies=requests_proxy(),
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            r.raise_for_status()
            for link in _extract_search_links(r.text):
                if link not in links:
                    links.append(link)
                if len(links) >= sources:
                    break
            if len(links) >= sources:
                break
        links = links[:sources]
        if not links:
            return '❌ Поиск не нашёл подходящих источников'

        per_source_limit = max(1200, state.MAX_OUTPUT // len(links))
        results = []
        with ThreadPoolExecutor(max_workers=len(links)) as ex:
            futs = {ex.submit(web_fetch, url): url for url in links}
            for fut in as_completed(futs):
                url = futs[fut]
                try:
                    text = fut.result()[:per_source_limit]
                except Exception as e:
                    text = f'❌ {e}'
                results.append((url, text))

        out = [f'Найдено и прочитано источников: {len(results)}']
        for i, (url, text) in enumerate(results, 1):
            out.append(f'\nИсточник {i}: {url}\n{text}')
        return '\n'.join(out)[:state.MAX_OUTPUT]
    except Exception as e:
        return f'❌ Не удалось выполнить поиск: {e}'

def safe_path(path):
    p = Path(path)
    if not p.is_absolute():
        p = state.WORK_DIR / p
    p = p.resolve()
    if not str(p).startswith(str(state.WORK_DIR) + os.sep) and p != state.WORK_DIR:
        raise ValueError(f"путь вне разрешённой директории {state.WORK_DIR}")
    return p

def write_file(path, content):
    if len(content.encode()) > state.MAX_FILE_SIZE:
        return f"❌ Файл слишком большой, лимит {state.MAX_FILE_SIZE} байт"
    try:
        p = safe_path(path)
    except ValueError as e:
        return f"❌ Недопустимый путь: {e}"
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists(): shutil.copy(str(p), str(p) + ".bak")
    p.write_text(content)
    return f"файл обновлён: {p}"

def read_file(path):
    p = safe_path(path)
    if not p.exists(): return "файл не найден"
    return p.read_text(errors="replace")[:state.MAX_OUTPUT]

def is_dangerous(cmd):
    c = re.sub(r"\s+", " ", cmd.lower()).strip()
    if re.search(r"(^|[;&|])\s*(sudo\s+)?(?:rm|chmod|chown|mv|cp|dd)\b", c) and re.search(r"/(?:etc|root|boot|usr|var/lib/docker|proc|sys|dev)(?:/|\s|$)", c):
        return True
    if any(re.search(p, c) for p in state.DANGEROUS_PATTERNS): return True
    try:
        words = set(shlex.split(c))
    except Exception:
        words = set(c.split())
    return bool(words & state.DANGEROUS_WORDS)

def local_exec(cmd, allow_dangerous=False, uid=None):
    if uid is not None and not state.is_admin_id(uid):
        return "❌ Локальное выполнение доступно только администратору. Добавьте свой сервер и используйте SSH."
    if is_dangerous(cmd) and not allow_dangerous:
        return f"❌ Команда заблокирована: {cmd}"
    try:
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        if uid is not None:
            state.ACTIVE_PROCESSES[uid] = p
        start = time.time()
        while p.poll() is None:
            if uid is not None and state.STOP_FLAGS.get(uid):
                try: os.killpg(p.pid, 15)
                except Exception: p.kill()
                return "⏹ Остановлено."
            if time.time() - start > state.CMD_TIMEOUT:
                try: os.killpg(p.pid, 9)
                except Exception: p.kill()
                return f"❌ Команда превысила лимит {state.CMD_TIMEOUT}c. Для долгих установок запусти в фоне (nohup … >/tmp/log 2>&1 &) и проверяй прогресс отдельно."
            time.sleep(0.2)
        out, err = p.communicate()
        return ((out + err).strip() or "(нет вывода)")[:state.MAX_OUTPUT]
    except Exception as e:
        return f"❌ {e}"
    finally:
        if uid is not None:
            state.ACTIVE_PROCESSES.pop(uid, None)

def ssh_exec(cfg, cmd, allow_dangerous=False, uid=None, overwrite_host_key=False):
    if is_dangerous(cmd) and not allow_dangerous:
        return f"❌ Команда заблокирована: {cmd}"
    ssh = None
    try:
        ssh = paramiko.SSHClient()
        if _KNOWN_HOSTS_FILE.exists():
            ssh.load_host_keys(str(_KNOWN_HOSTS_FILE))
        ssh.set_missing_host_key_policy(_TOFUPolicy(overwrite_changed=overwrite_host_key))
        proxy_cfg = parse_socks5(get_proxy_config().get("current")) if get_proxy_config().get("enabled") else None
        sock = None
        if proxy_cfg:
            phost, pport, puser, ppass = proxy_cfg
            sock = socks.create_connection(
                (cfg["host"], cfg.get("port", 22)),
                timeout=state.SSH_CONNECT_TIMEOUT,
                proxy_type=socks.SOCKS5,
                proxy_addr=phost,
                proxy_port=pport,
                proxy_username=puser,
                proxy_password=ppass,
                proxy_rdns=True,
            )
        connect_kwargs = {
            "hostname": cfg["host"],
            "username": cfg["user"],
            "port": cfg.get("port", 22),
            "timeout": state.SSH_CONNECT_TIMEOUT,
            "sock": sock,
        }
        private_key = cfg.get("private_key")
        if private_key:
            key_text = private_key.replace("\\n", "\n")
            passphrase = cfg.get("passphrase") or cfg.get("key_passphrase") or cfg.get("password")
            key_obj = None
            last_err = None
            for key_cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey, paramiko.DSSKey):
                try:
                    key_obj = key_cls.from_private_key(io.StringIO(key_text), password=passphrase)
                    break
                except Exception as e:
                    last_err = e
            if not key_obj:
                return f"❌ SSH: не удалось прочитать private_key: {last_err}"
            connect_kwargs["pkey"] = key_obj
            if cfg.get("password") and passphrase != cfg.get("password"):
                connect_kwargs["password"] = cfg.get("password")
        else:
            connect_kwargs["password"] = cfg.get("password")
        ssh.connect(**connect_kwargs)
        _, out, err = ssh.exec_command(cmd, timeout=state.CMD_TIMEOUT)
        channel = out.channel
        if uid is not None:
            state.ACTIVE_CHANNELS[uid] = channel
        start = time.time()
        while not channel.exit_status_ready():
            if uid is not None and state.STOP_FLAGS.get(uid):
                channel.close()
                return "⏹ Остановлено."
            if time.time() - start > state.CMD_TIMEOUT:
                channel.close()
                return f"❌ SSH-команда превысила лимит {state.CMD_TIMEOUT}c. Для долгих установок запусти в фоне (nohup … >/tmp/log 2>&1 &) и проверяй прогресс отдельной командой."
            time.sleep(0.2)
        result = out.read().decode(errors="replace") + err.read().decode(errors="replace")
        return (result.strip() or "(нет вывода)")[:state.MAX_OUTPUT]
    except Exception as e:
        return f"❌ SSH: {e}"
    finally:
        if uid is not None:
            state.ACTIVE_CHANNELS.pop(uid, None)
        try:
            if ssh: ssh.close()
        except Exception:
            pass

