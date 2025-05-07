#!/usr/bin/env bash
# tmux_logs.sh  ─ «живой» обзор логов dessmonitor

# ─────────────────────────────────────────────────────────────
LOG_DIR=${1:-/srv/dessmonitor/logs}   # каталог с логами  (можно передать аргументом)
SESSION=dessmon                        # имя tmux‑сессии

# Группы лог‑файлов: "имя‑окна"  (список файлов)
declare -A WINDOWS=(
  [core]="device_status.log device_details.log"
  [business]="business_decisions.log pump_controller.log important.log"
  [inverter]="inverter.log"
)

# Файлы, которые *не* интересны
IGNORE="application.log dessmonitor.log"

# ─────────────────────────────────────────────────────────────
# helper: true  ↦  строка есть в списке; false ‑ нет
in_list() { [[ " $2 " =~ [[:space:]]$1[[:space:]] ]]; }

# 1. стартуем (или присоединяемся) к сессии
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux new-session -d -s "$SESSION" -n scratch      # пустое окно, потом удалим
fi

# 2. создаём/обновляем окна
for win in "${!WINDOWS[@]}"; do
  # если окно уже есть – пропускаем
  if tmux list-windows -t "$SESSION" -F '#W' | grep -qx "$win"; then
    continue
  fi

  # создаём окно и первую панель
  tmux new-window -t "$SESSION" -n "$win" -d
  pane=0
  for file in ${WINDOWS[$win]}; do
    # пропускаем игнорируемые
    if in_list "$file" "$IGNORE"; then
      continue
    fi

    full="$LOG_DIR/$file"
    # • первая панель: запускаем tail • остальные: сплит вертикально
    if [ $pane -eq 0 ]; then
      tmux send-keys -t "$SESSION:$win.$pane" "tail -F -n 50 $full" C-m
    else
      tmux split-window -t "$SESSION:$win" -v "tail -F -n 50 $full"
      tmux select-layout -t "$SESSION:$win" tiled >/dev/null
    fi
    ((pane++))
  done
done

# 3. убираем временное «scratch»‑окно, если оно осталось пустым
tmux list-panes -t "$SESSION:scratch" &>/dev/null && \
  tmux kill-window -t "$SESSION:scratch"

# 4. подключаемся (если уже внутри tmux – просто переключит)
tmux attach -t "$SESSION"
