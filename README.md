# DevOps

Telegram AI-агент для администрирования серверов: выполняет команды по SSH и локально, управляет Docker и Kubernetes, ведёт мониторинг и работает с файлами проекта — через LLM (любой OpenAI-совместимый API).

Исходники и полная документация — в [`extracted/app/`](extracted/app/README.md).

## Быстрый старт

```bash
cd extracted/app
./install.sh                      # создаст .env и data/ из примеров
# заполните .env (TELEGRAM_TOKEN, AI_API_KEY, ADMIN_IDS)
docker compose up -d --build
```

Подробности по переменным окружения, безопасности и Kubernetes — в [README проекта](extracted/app/README.md).
