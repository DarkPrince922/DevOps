# DevOps — Telegram DevOps AI-бот

DevOps — это Telegram-бот для администрирования серверов с помощью AI. Бот принимает задачи в чате, обращается к API Codex и может выполнять команды локально в контейнере или на подключённых SSH-серверах.

## Что умеет бот

- Выполняет команды на главном сервере и удалённых SSH-серверах.
- Управляет списком серверов через Telegram.
- Показывает мониторинг CPU, RAM, диска, процессов, сети, аптайма и системной информации.
- Работает с Docker: контейнеры, логи, обслуживание и очистка с подтверждением.
- Может читать и создавать файлы в рабочей директории.
- Загружает веб-страницы и выполняет веб-поиск.
- Обрабатывает голосовые сообщения и изображения/скриншоты.
- Поддерживает SOCKS5-прокси для Telegram, API и поиска.
- Имеет админ-настройки: модель, лимиты, история, автопродолжение и сброс сессий.
- Содержит защиту от опасных действий и кнопку остановки текущей задачи.

## Требования

- Linux-сервер.
- Docker.
- Docker Compose plugin.
- Telegram Bot Token от BotFather.
- API key с сайта [codex.sale](https://codex.sale).

## Получение API key

1. Откройте сайт [codex.sale](https://codex.sale).
2. Войдите в аккаунт или зарегистрируйтесь.
3. Создайте или скопируйте API key в личном кабинете.
4. Укажите ключ в файле `.env` в переменной `CODEX_API_KEY`.

## Установка

Распакуйте архив и перейдите в папку проекта:

```bash
unzip DevOps.zip
cd DevOps
```

Запустите установочный скрипт:

```bash
./install.sh
```

Заполните файл `.env`:

```env
TELEGRAM_TOKEN=ваш_telegram_bot_token
CODEX_API_KEY=ваш_api_key_с_codex.sale
CODEX_BASE_URL=https://codex.sale/v1
MODEL=gpt-5.5
ADMIN_IDS=ваш_telegram_id
```

## Настройка серверов

Пример файла `data/servers.json`:

```json
{
  "My server": {
    "host": "203.0.113.10",
    "user": "root",
    "password": "CHANGE_ME",
    "port": 22
  }
}
```

Можно использовать пароль или SSH-ключи, если они поддерживаются вашей конфигурацией.

## Запуск

```bash
docker compose up -d --build
```

## Просмотр логов

```bash
docker compose logs -f --tail=100
```

## Остановка

```bash
docker compose down
```

## Обновление

```bash
git pull
docker compose up -d --build
```

Если проект установлен из архива, замените файлы проекта новой версией, сохраните свой `.env` и данные в `data/`, затем пересоберите контейнер.

## Важная безопасность

Не передавайте третьим лицам и не публикуйте:

- `.env`
- `data/servers.json`
- `data/proxies.json`
- `data/sessions.json`
- `data/preapproved_commands.json`
- `data/.ssh/`

В этих файлах могут находиться токены, API-ключи, пароли, прокси, история сессий и SSH-доступы.

Для распространения проекта используйте только примерные файлы `.env.example` и `data/*.example.json`.

## Kubernetes

Манифесты для запуска в Kubernetes находятся в `k8s/`.

1. Соберите и опубликуйте Docker-образ, затем укажите его в `k8s/deployment.yaml` вместо `ghcr.io/OWNER/REPOSITORY:latest`.
2. Скопируйте `k8s/secret.example.yaml` в `k8s/secret.yaml` и заполните реальные `TELEGRAM_TOKEN` и `AI_API_KEY`.
3. При необходимости измените `ADMIN_IDS` и другие параметры в `k8s/configmap.yaml`.
4. Добавьте `secret.yaml` в `k8s/kustomization.yaml` и примените:

```bash
kubectl apply -k k8s/
```

Данные бота сохраняются в PVC `devops-bot-data`. Для Telegram polling отдельный Service/Ingress не требуется.

Управление Kubernetes прямо из бота доступно администраторам через кнопку `☸️ Kubernetes` в главном меню или командами:

```text
/k8s_config KEY VALUE   # обновить ConfigMap и перезапустить deployment
/k8s_secret KEY VALUE   # обновить Secret и перезапустить deployment
/k8s_image IMAGE        # сменить Docker image deployment
```

По умолчанию используются namespace/deployment/configmap/secret из переменных `K8S_NAMESPACE`, `K8S_DEPLOYMENT`, `K8S_CONFIGMAP`, `K8S_SECRET`.

Интеграция Kubernetes с мониторингом:
- кнопка `☸️ Kubernetes` показывает статус deployment, поды, последние логи и события;
- фоновый мониторинг проверяет готовность deployment и отправляет админам алерт, если ready replicas меньше desired или поды в проблемном состоянии.

