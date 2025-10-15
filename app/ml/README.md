# ML Data Collection System

Система сбора данных для обучения ML-моделей управления солнечной системой и прудом.

## 📁 Структура данных

Данные собираются в 3 формата:
- **SQLite** (`ml_data/data.sqlite`) - персистентное хранилище
- **CSV** (`ml_data/training_data.csv`) - для pandas/scikit-learn
- **JSONL** (`ml_data/training_data.jsonl`) - для гибкой работы

---

## 🗂️ Описание полей данных

### 📅 Временные метки и календарь

| Поле | Тип | Описание | Пример |
|------|-----|----------|--------|
| `timestamp` | string | ISO 8601 timestamp | `2025-10-15T12:31:50.918453` |
| `unix_ts` | int | Unix timestamp (секунды) | `1760527910` |
| `hour` | int | Час дня (0-23) | `12` |
| `day_of_week` | int | День недели (0=Пн, 6=Вс) | `2` |
| `month` | int | Месяц (1-12) | `10` |
| `is_weekend` | int | Выходной день (0/1) | `0` |
| `is_daytime` | int | Дневное время 6-20ч (0/1) | `1` |
| `is_night` | int | Ночное время <6 или ≥22ч (0/1) | `0` |
| `season` | string | Сезон года | `autumn` |

**Использование:** Циклические признаки для ML (время суток, день недели), сезонность.

---

### 🔋 Инвертор и батарея

| Поле | Тип | Единицы | Описание | Пример |
|------|-----|---------|----------|--------|
| `battery_voltage` | float | V | Напряжение батареи | `25.7` |
| `battery_soc` | float | % | State of Charge (заряд) | `100.0` |
| `battery_current_chg` | float | A | Ток зарядки | `0.0` |
| `battery_current_dis` | float | A | Ток разрядки | `7.0` |

**Критично для:** Определения режима работы, защиты батареи, оптимизации заряда/разряда.

---

### ☀️ Солнечные панели (PV)

| Поле | Тип | Единицы | Описание | Пример |
|------|-----|---------|----------|--------|
| `pv1_voltage` | float | V | Напряжение панели 1 | `65.0` |
| `pv1_power` | float | W | Мощность панели 1 | `169.0` |
| `pv2_voltage` | float | V | Напряжение панели 2 | `null` |
| `pv2_power` | float | W | Мощность панели 2 | `null` |
| `pv_total_power` | float | W | Суммарная мощность PV | `169.0` |

**Использование:** Прогнозирование генерации, оптимизация потребления.

---

### ⚡ Выход инвертора

| Поле | Тип | Единицы | Описание | Пример |
|------|-----|---------|----------|--------|
| `output_voltage` | float | V | Напряжение на выходе | `230.1` |
| `output_power` | float | W | Активная мощность | `null` |
| `output_apparent_power` | float | VA | Полная мощность | `null` |
| `ac_output_load` | float | % | Нагрузка на инвертор | `10.0` |

---

### 🔌 Вход от сети

| Поле | Тип | Единицы | Описание | Пример |
|------|-----|---------|----------|--------|
| `ac_input_voltage` | float | V | Напряжение сети | `239.2` |
| `ac_input_frequency` | float | Hz | Частота сети | `50.0` |

---

### 🔄 Режимы работы

| Поле | Тип | Описание | Возможные значения |
|------|-----|----------|-------------------|
| `working_mode` | string | Текущий режим работы | `Invert Mode`, `Line Mode`, `Battery Mode`, `PV Mode` |
| `mains_status` | string | Статус сети | `Mains OK`, `Mains Discharge` |
| `inverter_on` | bool | Инвертор активен | `true`, `false` |

**Ключевые режимы:**
- `Invert Mode` - работа от солнца/батареи
- `Line Mode` - работа от сети
- `Battery Mode` - разряд батареи
- `PV Mode` - прямое питание от солнца

---

### 🌤️ Погода (текущее состояние)

| Поле | Тип | Единицы | Описание | Пример |
|------|-----|---------|----------|--------|
| `ambient_temp` | float | °C | Температура воздуха | `12.84` |
| `humidity` | float | % | Влажность воздуха | `82` |
| `pressure_hpa` | float | hPa | Атмосферное давление | `1029` |
| `wind_speed_mps` | float | m/s | Скорость ветра | `2.57` |

**Источник:** OpenWeatherMap API (обновление каждые 10 минут).

---

### 🌊 Вода в пруду

| Поле | Тип | Единицы | Описание | Пример |
|------|-----|---------|----------|--------|
| `water_temp` | float | °C | Температура воды | `12.8` |
| `temp_diff_air_water` | float | °C | Разница температур воздух-вода | `0.04` |
| `water_temp_trend` | string | - | Тренд температуры воды | `stable`, `warming`, `cooling` |
| `equivalent_cooling_index` | float | - | Индекс охлаждения (ветер × ΔT) | `18.4` |

**Формулы:**
```python
temp_diff_air_water = ambient_temp - water_temp
equivalent_cooling_index = max(0, 20 - ambient_temp) * wind_speed_mps
```

---

### 🔮 Прогноз погоды (следующий час)

| Поле | Тип | Единицы | Описание | Пример |
|------|-----|---------|----------|--------|
| `fc_source` | string | - | Источник прогноза | `OpenWeatherMap` |
| `fc_dt` | int | timestamp | Время прогноза | `1760529600` |
| `fc_temp_c` | float | °C | Прогноз температуры | `12.77` |
| `fc_clouds_pct` | float | % | Облачность | `100` |
| `fc_pop` | float | 0-1 | Вероятность осадков | `0` |
| `fc_rain_mm` | float | mm | Прогноз дождя | `0.0` |
| `fc_wind_mps` | float | m/s | Прогноз ветра | `3.21` |
| `fc_uvi` | float | - | УФ-индекс | `2.03` |
| `fc_solar_irradiance_wm2` | float | W/m² | Солнечная радиация | `null` |

---

### 📊 Агрегаты прогноза (3 и 6 часов)

| Поле | Тип | Единицы | Описание | Пример |
|------|-----|---------|----------|--------|
| `fc3h_temp_delta` | float | °C | Изменение температуры за 3ч | `0.40` |
| `fc6h_temp_delta` | float | °C | Изменение температуры за 6ч | `-0.79` |
| `fc3h_max_pop` | float | 0-1 | Макс. вероятность осадков (3ч) | `0` |
| `fc6h_max_pop` | float | 0-1 | Макс. вероятность осадков (6ч) | `0` |
| `fc3h_total_rain_mm` | float | mm | Суммарный дождь за 3ч | `0.0` |
| `fc6h_total_rain_mm` | float | mm | Суммарный дождь за 6ч | `0.0` |
| `fc3h_mean_clouds` | float | % | Средняя облачность (3ч) | `100.0` |
| `fc6h_mean_clouds` | float | % | Средняя облачность (6ч) | `92.17` |
| `will_rain_next_3h` | int | 0/1 | Будет дождь в ближ. 3ч | `0` |
| `will_rain_next_6h` | int | 0/1 | Будет дождь в ближ. 6ч | `0` |

**Использование:** Предсказание солнечной генерации, управление насосом перед дождём.

---

### 🏠 Устройства и нагрузка

| Поле | Тип | Единицы | Описание | Пример |
|------|-----|---------|----------|--------|
| `total_load_watt` | float | W | Общая нагрузка всех устройств | `20.0` |
| `devices_on_count` | int | шт | Количество включённых устройств | `1` |

---

### 💧 Насос пруда

| Поле | Тип | Единицы | Описание | Пример |
|------|-----|---------|----------|--------|
| `pump_speed` | int | % | Скорость насоса (0-100) | `10` |
| `pump_mode` | int | - | Режим работы насоса | `6` |
| `pump_uptime_today_sec` | int | сек | Суммарное время работы сегодня | `122` |
| `pump_current_uptime_sec` | int | сек | Время работы с последнего включения | `211` |

**Режимы насоса:**
- `6` - ручной режим
- `8` - автоматический режим

---

### ⚡ Энергетические потоки (дельты)

| Поле | Тип | Единицы | Описание | Пример |
|------|-----|---------|----------|--------|
| `energy_from_pv_wh` | float | Wh | Энергия от солнечных панелей | `1.41` |
| `energy_from_grid_wh` | float | Wh | Энергия от сети | `0.0` |
| `energy_to_load_wh` | float | Wh | Энергия в нагрузку | `0.0` |
| `energy_to_battery_wh` | float | Wh | Энергия в батарею (заряд) | `0.0` |
| `energy_from_battery_wh` | float | Wh | Энергия из батареи (разряд) | `1.50` |

**Расчёт:** Трапециевидная интеграция между двумя точками сбора.
```python
# Пример расчёта энергии от PV
time_delta_h = (current_ts - previous_ts) / 3600.0
pv_avg = (current_pv_power + previous_pv_power) / 2.0
energy_from_pv_wh = pv_avg * time_delta_h
```

---

### 🎯 Целевые переменные (для обучения)

| Поле | Тип | Единицы | Описание | Пример |
|------|-----|---------|----------|--------|
| `next_hour_pv_power` | float | W | PV генерация через час (для регрессии) | `null` |
| `optimal_pump_speed` | int | % | Оптимальная скорость насоса (для регрессии) | `null` |
| `should_charge_battery` | bool | - | Нужно заряжать батарею (для классификации) | `null` |

**Примечание:** Эти поля заполняются на этапе подготовки данных для обучения.

---

## 📂 Форматы файлов

### SQLite (`ml_data/data.sqlite`)

**Таблица:** `ml_points`
```sql
CREATE TABLE ml_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unix_ts INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    data_json TEXT NOT NULL  -- Весь JSON как строка
);

CREATE INDEX idx_points_ts ON ml_points(unix_ts);
```

**Преимущества:**
- ✅ Персистентность (переживает рестарты)
- ✅ Быстрые запросы по времени
- ✅ Компактное хранение
- ✅ WAL режим для надёжности

### CSV (`ml_data/training_data.csv`)

Стандартный CSV с заголовками, разделитель - запятая.

**Пример:**
```csv
timestamp,unix_ts,hour,battery_voltage,ambient_temp,water_temp,...
2025-10-15T12:31:50,1760527910,12,25.7,12.84,12.8,...
```

**Использование:**
```python
import pandas as pd
df = pd.read_csv('ml_data/training_data.csv')
```

### JSONL (`ml_data/training_data.jsonl`)

JSON Lines - каждая строка это отдельный JSON объект.

**Пример:**
```json
{"timestamp": "2025-10-15T12:31:50", "battery_voltage": 25.7, ...}
{"timestamp": "2025-10-15T12:32:20", "battery_voltage": 25.7, ...}
```

**Использование:**
```python
import json

data = []
with open('ml_data/training_data.jsonl') as f:
    for line in f:
        data.append(json.loads(line))
```

---

## 🔧 Настройка сбора данных

### Локальное тестирование
```bash
# 1. Скопируйте шаблон
cp local_test.sh.example local_test.sh

# 2. Отредактируйте
nano local_test.sh

# 3. Запустите
source local_test.sh && python run.py
```

### Интервалы сбора
```python
# В run.py
ml_collector = MLDataCollector(
    csv_export_enabled=True,
    jsonl_export_enabled=True,
    collect_interval=300,  # 5 минут (стандарт)
)
```

**Рекомендации:**
- **Тестирование:** 30-60 секунд
- **Продакшен:** 300 секунд (5 минут)
- **Детальный анализ:** 120 секунд (2 минуты)

---

## 📊 Использование данных

### Загрузка из SQLite
```python
import sqlite3
import json
import pandas as pd

conn = sqlite3.connect('ml_data/data.sqlite')
cur = conn.execute("SELECT data_json FROM ml_points ORDER BY unix_ts")

data = [json.loads(row[0]) for row in cur.fetchall()]
df = pd.DataFrame(data)

print(f"Loaded {len(df)} records")
print(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
```

### Загрузка из CSV
```python
import pandas as pd

df = pd.read_csv('ml_data/training_data.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'])
df = df.set_index('timestamp')

print(df.describe())
```

### Базовая визуализация
```python
import matplotlib.pyplot as plt

fig, axes = plt.subplots(3, 1, figsize=(15, 10))

# PV генерация
axes[0].plot(df.index, df['pv_total_power'], label='PV Power')
axes[0].set_ylabel('Power (W)')
axes[0].legend()

# Температуры
axes[1].plot(df.index, df['ambient_temp'], label='Air Temp')
axes[1].plot(df.index, df['water_temp'], label='Water Temp')
axes[1].set_ylabel('Temperature (°C)')
axes[1].legend()

# Насос
axes[2].plot(df.index, df['pump_speed'], label='Pump Speed')
axes[2].set_ylabel('Speed (%)')
axes[2].legend()

plt.tight_layout()
plt.savefig('ml_data_overview.png', dpi=150)
```

---

## 🎓 ML задачи

### 1. Прогнозирование генерации PV

**Цель:** Предсказать `pv_total_power` на следующий час

**Признаки:**
- Временные: `hour`, `day_of_week`, `month`, `season`
- Погода: `ambient_temp`, `fc_clouds_pct`, `fc_uvi`
- История: скользящие средние PV за 1ч, 3ч

**Модель:** RandomForestRegressor, GradientBoosting

### 2. Оптимизация управления насосом

**Цель:** Определить оптимальную скорость насоса

**Признаки:**
- Погода: `ambient_temp`, `wind_speed_mps`, `fc_rain_mm`
- Вода: `water_temp`, `temp_diff_air_water`
- Система: `battery_voltage`, `pv_total_power`, `working_mode`

**Модель:** GradientBoostingRegressor

### 3. Классификация режима работы

**Цель:** Предсказать оптимальный `working_mode`

**Признаки:**
- PV: `pv_total_power`, прогноз генерации
- Батарея: `battery_soc`, `battery_voltage`
- Нагрузка: `total_load_watt`, `devices_on_count`

**Модель:** RandomForestClassifier

---

## 📈 Анализ данных

### Статистика по режимам работы
```python
mode_stats = df.groupby('working_mode').agg({
    'pv_total_power': 'mean',
    'battery_soc': 'mean',
    'pump_speed': 'mean',
    'unix_ts': 'count'
}).rename(columns={'unix_ts': 'count'})

print(mode_stats)
```

### Корреляция температур
```python
import seaborn as sns

corr_data = df[['ambient_temp', 'water_temp', 'fc_temp_c', 'pump_speed']]
sns.heatmap(corr_data.corr(), annot=True, cmap='coolwarm')
plt.title('Temperature Correlations')
plt.savefig('temp_correlations.png')
```

### Дневной профиль PV
```python
hourly_pv = df.groupby('hour')['pv_total_power'].agg(['mean', 'std', 'max'])

plt.figure(figsize=(12, 6))
plt.plot(hourly_pv.index, hourly_pv['mean'], marker='o', label='Average')
plt.fill_between(
    hourly_pv.index,
    hourly_pv['mean'] - hourly_pv['std'],
    hourly_pv['mean'] + hourly_pv['std'],
    alpha=0.3
)
plt.xlabel('Hour of Day')
plt.ylabel('PV Power (W)')
plt.title('Daily PV Generation Profile')
plt.legend()
plt.grid(True)
plt.savefig('daily_pv_profile.png')
```

---

## 🔍 Качество данных

### Completeness Score

Каждая запись имеет метрику полноты:
```python
def get_completeness_score(record):
    """Процент заполненных полей (исключая временные метки)"""
    total_fields = 0
    filled_fields = 0
    
    for key, value in record.items():
        if key.startswith(('timestamp', 'unix_ts', 'hour', 'day_')):
            continue
        total_fields += 1
        if value is not None and value != '':
            filled_fields += 1
    
    return filled_fields / total_fields if total_fields > 0 else 0.0
```

### Проверка целостности
```python
# Пропущенные значения
missing = df.isnull().sum()
print("Missing values:")
print(missing[missing > 0])

# Выбросы в важных полях
for col in ['battery_voltage', 'pv_total_power', 'water_temp']:
    q1 = df[col].quantile(0.01)
    q99 = df[col].quantile(0.99)
    outliers = df[(df[col] < q1) | (df[col] > q99)]
    print(f"{col}: {len(outliers)} outliers ({len(outliers)/len(df)*100:.1f}%)")
```

---

## 🛠️ Maintenance

### Очистка старых данных
```python
# Удалить данные старше 90 дней
import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('ml_data/data.sqlite')
cutoff = int((datetime.now() - timedelta(days=90)).timestamp())

conn.execute("DELETE FROM ml_points WHERE unix_ts < ?", (cutoff,))
conn.execute("VACUUM")  # Оптимизация
conn.commit()
```

### Backup
```bash
# Бэкап SQLite
cp ml_data/data.sqlite ml_data/data.sqlite.backup_$(date +%Y%m%d)

# Экспорт в CSV
python app/ml/ml_data_analyzer.py export
```

---

## 📚 Дополнительные ресурсы

- **Обучение моделей:** `app/ml/ml_model_training_example.py`
- **Анализ данных:** `app/ml/ml_data_analyzer.py`
- **TimescaleDB queries:** См. документацию TimescaleDB

---

## 🆘 Troubleshooting

### Нет данных погоды
```bash
# Проверьте API ключ
echo $OPENWEATHER_API_KEY

# Проверьте логи
tail -f logs/full.log | grep -i weather
```

### CSV не создаётся

Убедитесь что флаги экспорта включены:
```python
ml_collector = MLDataCollector(
    csv_export_enabled=True,    # ← Включить!
    jsonl_export_enabled=True,  # ← Включить!
)
```

### Низкий completeness score

Подождите полной инициализации всех сервисов (~2 минуты):
- InverterMonitor
- Weather Service
- Device Status Updater

---

**Версия:** 1.0  
**Дата обновления:** 2025-10-15  
**Автор:** DessMonitor ML Team