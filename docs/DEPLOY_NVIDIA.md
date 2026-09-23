# Развёртывание в NVIDIA Brev / DGX Cloud

## Что автоматизировано

Workflow `.github/workflows/ci-cd.yml` работает для pull request, push в `main`, тегов `v*` и ручного запуска. Python и frontend проверяются независимо. Контейнер публикуется в GHCR только после обоих успешных заданий; pull request лишь собирается и проверяется без публикации.

Теги образа:

- `latest` и `main` — последний успешный push в основную ветку;
- `sha-<короткий SHA>` — неизменяемая версия конкретного коммита;
- `v1.2.3` — версия при публикации одноимённого Git-тега.

Образ рассчитан на `linux/amd64`, типичную архитектуру NVIDIA Brev/DGX. Приложение не выполняет CUDA-вычисления, поэтому флаг `--gpus` не нужен. Данные находятся внутри образа; FastAPI доступен только внутри контейнера на `127.0.0.1:8000`, наружу опубликован Next.js на порту `3000`.

## Первый запуск на NVIDIA Brev

1. Создайте Brev instance. Достаточно CPU-инстанса, если правила хакатона не требуют GPU.
2. Подключитесь через `brev shell ИМЯ_ИНСТАНСА` или SSH.
3. Клонируйте репозиторий и создайте `.env`:

```bash
git clone https://github.com/BAITC-Hacks/hack-912ce0e6-aitushka.git
cd hack-912ce0e6-aitushka
cp .env.example .env
```

4. Заполните `.env` на сервере. Не добавляйте его в Git:

```dotenv
OPENAI_API_KEY=YOUR_KEY
OPENAI_MODEL=gpt-5.4-mini
AI_MAX_REQUESTS=50
AI_ALLOWED_ORIGINS=https://YOUR-BREV-TUNNEL.example
```

Для нескольких разрешённых адресов используйте запятую. Если доступ идёт только через `brev port-forward`, оставьте `AI_ALLOWED_ORIGINS=http://localhost:3000`.

5. Запустите опубликованный образ:

```bash
docker compose pull
docker compose up -d --no-build
docker compose ps
```

Статус должен стать `healthy`. Первичная проверка рассчитывает аналитику и может занимать несколько секунд. Логи: `docker compose logs --tail=200 dashboard`.

6. Выберите способ доступа:

- приватно: `brev port-forward ИМЯ_ИНСТАНСА --port 3000:3000`, затем `http://localhost:3000`;
- для команды: Brev Console → instance → Access → Using Tunnels → добавить порт `3000`, скопировать HTTPS URL и записать его в `AI_ALLOWED_ORIGINS`.

После изменения `.env` примените конфигурацию командой `docker compose up -d`.

Официальная документация NVIDIA описывает [Brev port forwarding и tunnels](https://docs.nvidia.com/brev/cli/connectivity) и [Docker Compose Launchables](https://docs.nvidia.com/brev/concepts/launchables).

## Доступ к образу

GitHub Container Registry может создать первый пакет приватным. В GitHub откройте Packages → `hack-912ce0e6-aitushka` → Package settings → Change visibility → Public. Тогда Brev сможет скачивать образ без токена.

Если пакет должен остаться приватным, создайте GitHub PAT с минимальным правом `read:packages` и на инстансе выполните:

```bash
docker login ghcr.io -u thedids10
```

Введите PAT через стандартный запрос Docker. Не записывайте токен в compose, `.env` проекта или команды CI.

## Обновление и откат

Обычное обновление до последнего успешного `main`:

```bash
git pull --ff-only
docker compose pull
docker compose up -d --no-build --remove-orphans
```

Для воспроизводимого релиза передайте версию или SHA-тег через `IMAGE_TAG`. Откат не требует изменения кода:

```bash
IMAGE_TAG=sha-ПРЕДЫДУЩИЙ_SHA docker compose pull
IMAGE_TAG=sha-ПРЕДЫДУЩИЙ_SHA docker compose up -d --no-build
```

## Проверка и диагностика

```bash
curl --fail http://127.0.0.1:3000/api/health
docker compose ps
docker compose logs --tail=200 dashboard
```

Если интерфейс доступен, а AI возвращает 403, проверьте точное значение публичного URL в `AI_ALLOWED_ORIGINS`: схема `https://` обязательна, завершающий `/` необязателен. Если образ не скачивается, сделайте пакет публичным или выполните `docker login ghcr.io`. Если контейнер unhealthy, смотрите логи — healthcheck проходит через Next.js к Python API и тем самым проверяет весь путь запроса.

## Секреты и границы CI/CD

`OPENAI_API_KEY` не участвует в сборке и не нужен GitHub Actions. Он передаётся только при запуске контейнера. `.dockerignore` исключает `.env`, локальные зависимости, логи и результаты разработки из build context.

CI/CD доставляет проверенный образ в реестр. Автоматическое подключение GitHub Actions по SSH к конкретному Brev-инстансу не включено: инстансы могут останавливаться и менять адрес, а хранение SSH-ключа расширяет доступ. На Brev обновление выполняется тремя командами из раздела выше или через Docker Compose Launchable по URL этого репозитория.
