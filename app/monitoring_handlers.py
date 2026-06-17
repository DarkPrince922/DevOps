import asyncio
import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from telegram import Update
from app.executor import ssh_exec as direct_ssh_exec, local_exec as direct_local_exec
from app.core import get_servers
import app.state as state

LOAD_GRAPH_CMD = """read _ u n sy i rest < /proc/stat; t=$((u+n+sy+i)); sleep 1; read _ u2 n2 sy2 i2 rest < /proc/stat; t2=$((u2+n2+sy2+i2)); awk -v dt=$((t2-t)) -v di=$((i2-i)) 'BEGIN{if(dt>0) printf \"cpu=%.1f\\n\", (dt-di)*100/dt; else print \"cpu=0\"}'; free -m | awk '/Mem:/ {printf \"ram=%.1f\\n\", $3*100/$2}'; df -P / | awk 'NR==2 {gsub(/%/,\"\",$5); print \"disk=\"$5}'; uptime | sed 's/.*load average: /load=/' | cut -d, -f1"""


def _parse_load_metrics(raw: str):
    data = {"cpu": None, "ram": None, "disk": None, "load": "n/a"}
    for line in (raw or "").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if k in ("cpu", "ram", "disk"):
            try:
                data[k] = max(0, min(100, float(v)))
            except Exception:
                pass
        elif k == "load":
            data[k] = v
    return data


async def send_load_graphs(update: Update):
    servers = get_servers()
    await update.message.reply_text("⏳ Собираю нагрузку и строю график...")
    rows = []
    uid = update.effective_user.id if update.effective_user else None
    if not servers:
        if not state.is_admin_id(uid):
            await update.message.reply_text("❌ Для мониторинга добавьте свой SSH-сервер в меню 🖥 Серверы.")
            return
        raw = await asyncio.to_thread(direct_local_exec, LOAD_GRAPH_CMD, False, uid)
        rows.append(("Локально", _parse_load_metrics(raw)))
    else:
        for name, cfg in servers.items():
            raw = await asyncio.to_thread(direct_ssh_exec, cfg, LOAD_GRAPH_CMD, False, uid)
            rows.append((name, _parse_load_metrics(raw)))

    names = [r[0] for r in rows]
    cpu = [r[1]["cpu"] or 0 for r in rows]
    ram = [r[1]["ram"] or 0 for r in rows]
    disk = [r[1]["disk"] or 0 for r in rows]
    x = range(len(names))
    width = 0.25

    fig_w = max(8, min(18, 1.2 * len(names) + 4))
    fig, ax = plt.subplots(figsize=(fig_w, 5.5), dpi=140)
    ax.bar([i - width for i in x], cpu, width, label="CPU %", color="#ef4444")
    ax.bar(list(x), ram, width, label="RAM %", color="#3b82f6")
    ax.bar([i + width for i in x], disk, width, label="Disk / %", color="#22c55e")
    ax.set_ylim(0, 100)
    ax.set_ylabel("Нагрузка, %")
    ax.set_title("Краткая нагрузка серверов")
    ax.set_xticks(list(x))
    ax.set_xticklabels(names, rotation=25, ha="right")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(loc="upper right")
    for i, (_, m) in enumerate(rows):
        ax.text(i, 103, f"load {m['load']}", ha="center", va="bottom", fontsize=8, rotation=25)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    await update.message.reply_photo(photo=buf, caption="📈 График нагрузки: CPU, RAM и диск по всем серверам")
