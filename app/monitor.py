import asyncio
import os
import time

import app.state as state
from app.executor import ssh_exec
from app.storage import get_servers
from app.k8s_handlers import k8s_health_alert

CHECK_INTERVAL = int(os.environ.get("SERVICE_MONITOR_INTERVAL", "60"))

CHECK_CMD = r'''
{
  if command -v docker >/dev/null 2>&1; then
    docker ps -a --format 'DOCKER	{{.Names}}	{{.State}}	{{.Status}}' 2>/dev/null | awk -F '\t' '$3 != "running" {print}'
  fi
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --failed --no-legend --plain 2>/dev/null | awk '{print "SYSTEMD\t"$1"\tfailed\t"$0}'
  fi
} | head -30
'''

_last_state = {}
_last_k8s_alert = None


def _failed_lines(output):
    return [line for line in (output or '').splitlines() if line.strip() and not line.startswith('(')]


def _failure_key(line):
    parts = line.split('\t')
    if len(parts) >= 3 and parts[0] in {'DOCKER', 'SYSTEMD'}:
        return tuple(parts[:3])
    return line


def _format_alert(server_name, output):
    lines = _failed_lines(output)
    if not lines:
        return None
    items = []
    for line in lines[:10]:
        parts = line.split('\t')
        if len(parts) >= 4 and parts[0] == 'DOCKER':
            items.append(f"• Docker: {parts[1]} — {parts[3]}")
        elif len(parts) >= 2 and parts[0] == 'SYSTEMD':
            items.append(f"• Systemd: {parts[1]} — failed")
        else:
            items.append(f"• {line[:120]}")
    more = '' if len(lines) <= 10 else f"\n…ещё: {len(lines) - 10}"
    return f"🚨 Обнаружено падение сервисов на сервере «{server_name}»:\n" + "\n".join(items) + more


async def _send_admins(bot, text):
    for admin_id in state.ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text[:3900])
        except Exception:
            pass


async def service_monitor(bot):
    await asyncio.sleep(10)
    while True:
        try:
            if not state.get_settings().get("service_monitor_enabled", True):
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            global _last_k8s_alert
            try:
                k8s_alert = await asyncio.to_thread(k8s_health_alert)
            except Exception:
                k8s_alert = None
            if k8s_alert != _last_k8s_alert:
                if k8s_alert:
                    await _send_admins(bot, k8s_alert)
                _last_k8s_alert = k8s_alert
            servers = get_servers()
            for name, cfg in servers.items():
                output = await asyncio.to_thread(ssh_exec, cfg, CHECK_CMD, True, None)
                failed = '' if output.strip() == '(нет вывода)' else output.strip()
                current = {_failure_key(line): line for line in _failed_lines(failed)}
                prev = _last_state.get(name)
                _last_state[name] = current

                # Первый проход только запоминает текущее состояние, чтобы не слать
                # уведомления по старым падениям, которые уже были до запуска бота.
                if prev is None:
                    continue

                fresh_keys = current.keys() - prev.keys()
                if fresh_keys:
                    msg = _format_alert(name, '\n'.join(current[key] for key in sorted(fresh_keys, key=str)))
                    if msg:
                        await _send_admins(bot, msg)
        except Exception:
            pass
        await asyncio.sleep(CHECK_INTERVAL)
