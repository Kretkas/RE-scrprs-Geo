#!/bin/bash

# =============================================================
# НАСТРОЙКИ
# =============================================================

# Абсолютный путь к директории скрипта
PROJECT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# Путь к лог-файлу
LOG_FILE="$PROJECT_DIR/logs/app.log"

# Константы
CHANNEL="telegram"
TARGET_ID="244944702"
OPENCLAW_CMD="/opt/homebrew/bin/openclaw"

# =============================================================
# РОТАЦИЯ ЛОГОВ
# Делаем ДО exec, чтобы не писать в только что заархивированный файл
# =============================================================

# Удаляем архивные логи старше 30 дней
find "$(dirname "$LOG_FILE")" -name "app.log.*" -mtime +30 -delete 2>/dev/null

# Архивируем текущий лог если больше 10MB
LOG_SIZE=$(stat -f%z "$LOG_FILE" 2>/dev/null || stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)
if [ -f "$LOG_FILE" ] && [ "$LOG_SIZE" -gt 10485760 ]; then
 mv "$LOG_FILE" "$LOG_FILE.$(date +%Y%m%d_%H%M%S)"
fi

# Создаём директорию для логов (на случай первого запуска)
mkdir -p "$(dirname "$LOG_FILE")"

# =============================================================
# ФУНКЦИЯ УВЕДОМЛЕНИЙ
# Объявляем ДО exec и ДО первого возможного вызова
# =============================================================

notify() {
 echo "[NOTIFICATION] Sending: $1"
 $OPENCLAW_CMD message send --channel "$CHANNEL" --target "$TARGET_ID" --message "$1"
}

# =============================================================
# ПОИСК PYTHON
# Объявляем ДО exec, чтобы ошибка попала в лог
# =============================================================

export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH"

if command -v python3 &>/dev/null; then
 PYTHON_CMD=$(command -v python3)
elif command -v python &>/dev/null; then
 PYTHON_CMD=$(command -v python)
else
 notify "❌ КРИТИЧЕСКАЯ ОШИБКА: Python не найден в PATH. Выполнение остановлено."
 exit 1
fi

# =============================================================
# ПЕРЕНАПРАВЛЕНИЕ ВЫВОДА В ЛОГ
# Только после всех объявлений — теперь ВСЁ пишется в лог
# =============================================================

exec &> >(tee -a "$LOG_FILE")

# =============================================================
# ОСНОВНОЙ СКРИПТ
# =============================================================

echo "=== Запуск парсеров $(date) ==="
echo "Рабочая директория: $PROJECT_DIR"
echo "Python: $PYTHON_CMD ($($PYTHON_CMD --version 2>&1))"
echo "Файл лога: $LOG_FILE"

# Очищаем файл статистики перед новым запуском
> /tmp/scraper_stats.txt

# Уведомляем о старте
notify "✅ Плановая задача 'Daily Scrapers' запущена. Начинаю сбор данных."

# Переходим в директорию проекта (критично для cron — у него другой рабочий каталог)
cd "$PROJECT_DIR" || {
 notify "❌ Не удалось перейти в $PROJECT_DIR. Выполнение остановлено."
 exit 1
}

# Запускаем парсеры последовательно
ERRORS=0

for scraper in myhome_scraper.py ss_scraper.py korter_scraper.py; do
 echo "--> Запуск $scraper..."
 $PYTHON_CMD "$scraper"
 if [ $? -ne 0 ]; then
 notify "❌ ОШИБКА: парсер '$scraper' завершился с ошибкой."
 ERRORS=$((ERRORS + 1))
 fi
done

echo "=== Все парсеры завершены $(date) ==="

# Читаем статистику из файла (пишется самими парсерами в формате SOURCE:FOUND:429_ERRORS)
STATS_MSG=""
while IFS=: read -r source found errors_429; do
 case "$source" in
 MyHome) EMOJI="🟢" ;;
 SS) EMOJI="🩷" ;;
 Korter) EMOJI="🟠" ;;
 *) EMOJI="📊" ;;
 esac
 STATS_MSG="${STATS_MSG}${EMOJI} ${source}: ${found} кв. | 429×${errors_429}"$'\n'
done < /tmp/scraper_stats.txt

# Итоговое уведомление
if [ $ERRORS -eq 0 ]; then
 notify "🎉 Готово!
${STATS_MSG}"
else
 notify "⚠️ Завершено с ошибками (${ERRORS}/3).
${STATS_MSG}"
fi

# Ждём завершения дочернего процесса tee перед выходом
wait