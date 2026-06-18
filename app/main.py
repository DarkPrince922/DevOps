from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from app.core import TOKEN, apply_settings, apply_active_provider, load_sessions, active_proxy_url, secure_data_files
from app.handlers import start, admin_command, backup_command, delete_server_callback, confirm_callback, continue_callback, admin_callback, agent_callback, provider_callback, proxy_callback, project_callback, server_callback, preapproved_callback, handle, handle_voice, handle_image, handle_text_file, handle_k8s_document
from app.monitor import service_monitor
from app.k8s_handlers import k8s_config_command, k8s_secret_command, k8s_image_command, k8s_callback
from app.cloudflare_handlers import cf_callback
from app.storage import prune_backups
import asyncio


async def post_init(application):
    asyncio.create_task(service_monitor(application.bot))


async def handle_document(update, ctx):
    if ctx.user_data.get("mode") == "k8s_kubeconfig":
        await handle_k8s_document(update, ctx)
        return
    await handle_text_file(update, ctx)


def main():
    secure_data_files()
    apply_active_provider()
    from app.gpt import reload_api_config
    reload_api_config()
    apply_settings()
    load_sessions()
    prune_backups()
    builder = Application.builder().token(TOKEN).post_init(post_init)
    proxy = active_proxy_url()
    if proxy:
        builder = builder.proxy_url(proxy).get_updates_proxy_url(proxy)
    builder = builder.concurrent_updates(True)
    app = builder.concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("k8s_config", k8s_config_command))
    app.add_handler(CommandHandler("k8s_secret", k8s_secret_command))
    app.add_handler(CommandHandler("k8s_image", k8s_image_command))
    app.add_handler(CallbackQueryHandler(delete_server_callback, pattern="^delete_server:"))
    app.add_handler(CallbackQueryHandler(confirm_callback, pattern="^confirm:"))
    app.add_handler(CallbackQueryHandler(continue_callback, pattern="^continue:"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin:"))
    app.add_handler(CallbackQueryHandler(agent_callback, pattern="^agent:"))
    app.add_handler(CallbackQueryHandler(provider_callback, pattern="^provider:"))
    app.add_handler(CallbackQueryHandler(proxy_callback, pattern="^proxy:"))
    app.add_handler(CallbackQueryHandler(k8s_callback, pattern="^k8s:"))
    app.add_handler(CallbackQueryHandler(cf_callback, pattern="^cf:"))
    app.add_handler(CallbackQueryHandler(project_callback, pattern="^project:"))
    app.add_handler(CallbackQueryHandler(server_callback, pattern="^server:"))
    app.add_handler(CallbackQueryHandler(preapproved_callback, pattern="^precmd:"))
    app.add_handler(CallbackQueryHandler(delete_server_callback, pattern="^delserver:"))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image))
    app.add_handler(MessageHandler(filters.Document.ALL & ~filters.Document.IMAGE, handle_document))
    app.add_handler(MessageHandler(filters.TEXT, handle))
    print("🚀 Agent started")
    app.run_polling()
