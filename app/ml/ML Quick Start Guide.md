# ML Quick Start Guide

## Быстрый старт за 5 шагов

### 1. Установка зависимостей

```bash
# Минимальный набор для ML
pip install pandas numpy scikit-learn matplotlib

# Или все сразу из файла
pip install -r requirements-ml.txt
```

### 2. Создание структуры

```bash
# Создайте директории
mkdir -p app/ml
mkdir -p ml_data/models
mkdir -p ml_data/archive

# Скопируйте файлы модулей
# - app/ml/ml_data_collector.py
# - app/ml/ml_data_analyzer.py
# - app/ml/ml_model_training_example.py
# - app/service/ml_smart_controller.py
```

### 3. Интеграция сборщика данных в run.py

```python
# В run.py добавьте импорты
from app.ml.ml_data_collector import MLDataCollector, ml_collection_loop

# В функции main() добавьте:
ml_collector = MLDataCollector(
    csv_path=Path("ml_data/training_data.csv"),
    collect_interval=300,  # 5 минут
)

# Запустите как корутину
ml_task = asyncio.create_task(
    ml_collection_loop(ml_collector, dev_mgr, stop_event)
)

# В блоке finally добавьте остановку
ml_task.cancel()
await asyncio.gather(..., ml_task, return_exceptions=True)
```

### 4. Запуск сбора данных

```bash
# Запустите основное приложение
python run.py

# Проверьте, что данные собираются
ls -lh ml_data/training_data.csv

# Следите за логами
tail -f logs/important.log | grep ML
```

### 5. Первое обучение (после недели сбора)

```bash
# Анализ собранных данных
python app/ml/ml_data_analyzer.py stats

# Проверка пропусков
python app/ml/ml_data_analyzer.py gaps

# Визуализация
python app/ml/ml_data_analyzer.py plot

# Подготовка данных
python app/ml/ml_data_analyzer.py export

# Обучение моделей
python app/ml/ml_model_training_example.py all

# Тест предсказаний
python app/ml/ml_model_training_example.py test
```

---

## Проверка работы

### Проверка 1: Данные собираются?

```bash
# Размер файла растёт?
watch -n 60 'ls -lh ml_data/training_data.csv'

# Последние записи
tail -5 ml_data/training_data.csv

# Количество записей
wc -l ml_data/training_data.csv
```

**Ожидаемо:** +12 записей в час (каждые 5 минут)

### Проверка 2: Качество данных

```python
from app.ml.ml_data_analyzer import MLDataAnalyzer

analyzer = MLDataAnalyzer()
stats = analyzer.basic_statistics()

print(f"Records: {stats['total_records']}")
print(f"Days: {stats['date_range_days']}")
print(f"Missing values: {stats['missing_values']}")
```

**Ожидаемо:** 
- После 1 дня: ~288 записей
- После 1 недели: ~2016 записей
- Пропусков < 5%

### Проверка 3: Модели обучены?

```bash
ls -lh ml_data/models/

# Должны быть файлы:
# - pv_predictor.pkl
# - battery_optimizer.pkl
# - pump_controller.pkl (если есть насос)
```

---

## Типичные проблемы и решения

### Проблема: FileNotFoundError при обучении

```
FileNotFoundError: Training data not found: ml_data/train_data.csv
```

**Решение:**
```bash
# Сначала экспортируйте данные
python app/ml/ml_data_analyzer.py export

# Затем обучайте
python app/ml/ml_model_training_example.py all
```

### Проблема: Слишком мало данных

```
ValueError: train_test_split: n_samples=50 should be >= n_splits=5
```

**Решение:**
- Подождите, пока наберётся минимум **1 неделя** данных (~2000 записей)
- Или уменьшите test_split до 0.1 в ml_data_analyzer.py

### Проблема: Модель показывает низкую точность

**Пример:** Test R² = 0.3 (должно быть > 0.7)

**Решение:**
1. Соберите больше данных (минимум 1 месяц)
2. Проверьте пропуски: `python app/ml/ml_data_analyzer.py gaps`
3. Убедитесь в разнообразии сценариев (солнце, облачность, ночь, разные нагрузки)

### Проблема: ML-модели не загружаются в продакшене

```
RuntimeError: ML models not available
```

**Решение:**
```python
# В run.py используйте режим HYBRID (по умолчанию)
ml_smart_ctrl = MLSmartController(
    mode="HYBRID",  # не ML_ONLY!
    ...
)

# Проверьте наличие файлов
ls ml_data/models/*.pkl
```

---

## Мониторинг ML-системы

### Grafana Dashboard (пример запроса Loki)

```logql
# Все события ML-коллектора
{job="dessmonitor"} |= "MLDataCollector"

# Метрики устройств
{job="dessmonitor"} | logfmt | type="device_metrics"

# Метрики инвертора
{job="dessmonitor"} | logfmt | type="inverter"

# Решения ML-контроллера
{job="dessmonitor"} |= "MLController"
```

### Алерты на аномалии

```python
# В будущем можно добавить:
# app/ml/anomaly_detector.py

from sklearn.ensemble import IsolationForest

# Детекция аномальных значений PV/Battery/Load
detector = IsolationForest(contamination=0.05)
detector.fit(normal_data)

is_anomaly = detector.predict(current_data)
if is_anomaly == -1:
    logger.warning("⚠️ Anomaly detected!")
```

---

## Расширенные возможности

### Добавление новых признаков

```python
# В ml_data_analyzer.py, метод create_features()

# Пример: добавить индикатор "пик потребления"
df['is_peak_hours'] = df['hour'].isin([18, 19, 20]).astype(int)

# Пример: добавить "эффективность PV"
df['pv_efficiency'] = df['pv_total_power'] / (df['pv1_voltage'] + df['pv2_voltage'] + 1)
```

### Гиперпараметры моделей

```python
# В ml_model_training_example.py

# Для PV predictor
from sklearn.model_selection import GridSearchCV

param_grid = {
    'n_estimators': [50, 100, 200],
    'max_depth': [10, 15, 20],
    'min_samples_split': [2, 5, 10]
}

grid_search = GridSearchCV(
    RandomForestRegressor(),
    param_grid,
    cv=5,
    scoring='r2'
)

grid_search.fit(X_train, y_train)
best_model = grid_search.best_estimator_
```

### Ансамбль моделей

```python
# Комбинируйте несколько моделей для лучшей точности

from sklearn.ensemble import VotingRegressor

ensemble = VotingRegressor([
    ('rf', RandomForestRegressor(n_estimators=100)),
    ('gb', GradientBoostingRegressor(n_estimators=100)),
    ('xgb', XGBRegressor(n_estimators=100))
])

ensemble.fit(X_train, y_train)
```

---

## Производительность и оптимизация

### Размер файлов данных

**Пример расчёта:**
- 1 запись ≈ 2 KB (CSV)
- 288 записей/день × 2 KB = 576 KB/день
- 30 дней ≈ **17 MB**

**Рекомендации:**
- Архивируйте старые данные (>3 месяцев)
- Используйте JSONL для больших объёмов
- Рассмотрите parquet формат для долгосрочного хранения

### Ротация данных

```python
# Добавьте в ml_data_collector.py

def rotate_data(self, max_days: int = 90):
    """Архивирует данные старше max_days"""
    if not self.csv_path.exists():
        return
    
    df = pd.read_csv(self.csv_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    cutoff = datetime.now() - timedelta(days=max_days)
    old_data = df[df['timestamp'] < cutoff]
    new_data = df[df['timestamp'] >= cutoff]
    
    if len(old_data) > 0:
        archive_path = self.csv_path.parent / "archive" / f"data_{cutoff:%Y%m}.csv"
        archive_path.parent.mkdir(exist_ok=True)
        old_data.to_csv(archive_path, index=False)
        
        new_data.to_csv(self.csv_path, index=False)
        self.logger.info(f"Archived {len(old_data)} old records")
```

### Оптимизация сбора

```python
# Уменьшите частоту сбора ночью
import datetime

def smart_interval(self) -> int:
    hour = datetime.datetime.now().hour
    
    # Ночью (22-06) - реже
    if hour >= 22 or hour < 6:
        return self.collect_interval * 3  # каждые 15 минут
    
    # День - как обычно
    return self.collect_interval  # каждые 5 минут
```

---

## Roadmap развития ML-модуля

### Фаза 1: MVP ✅ (текущая)
- [x] Базовый сборщик данных
- [x] Анализ и визуализация
- [x] Простые модели (RF, GB)
- [x] Интеграция в run.py

### Фаза 2: Production (1-2 месяца)
- [ ] Сбор 1+ месяц качественных данных
- [ ] Обучение production-моделей
- [ ] A/B тестирование ML vs эвристика
- [ ] Мониторинг эффективности

### Фаза 3: Advanced (3-6 месяцев)
- [ ] LSTM для прогнозирования временных рядов
- [ ] Reinforcement Learning для оптимизации
- [ ] Детекция аномалий в реальном времени
- [ ] Автоматический retraining моделей

### Фаза 4: Ecosystem (6+ месяцев)
- [ ] API для внешних моделей
- [ ] Cloud inference (если нужно)
- [ ] Multi-site learning (обучение на данных с нескольких установок)
- [ ] Explainable AI (интерпретация решений модели)

---

## Best Practices

### 1. Версионирование моделей

```python
# Сохраняйте модели с версией и датой
model_name = f"pv_predictor_v1.2_{datetime.now():%Y%m%d}.pkl"

# Храните метаданные
metadata = {
    'version': '1.2',
    'trained_on': datetime.now().isoformat(),
    'train_samples': len(X_train),
    'test_r2': test_r2,
    'features': features,
    'hyperparameters': model.get_params()
}

with open('ml_data/models/metadata.json', 'w') as f:
    json.dump(metadata, f, indent=2)
```

### 2. Логирование предсказаний

```python
# Записывайте, что модель предсказала vs что произошло
prediction_log = {
    'timestamp': datetime.now().isoformat(),
    'predicted_pv': predicted_value,
    'actual_pv': actual_value,
    'error': abs(predicted_value - actual_value),
    'model_version': '1.2'
}

# Для последующего анализа дрейфа модели
```

### 3. Feature Store

```python
# Централизованное хранилище признаков
class FeatureStore:
    def __init__(self):
        self.features = {}
    
    def register_feature(self, name, func):
        """Регистрирует функцию-вычислитель признака"""
        self.features[name] = func
    
    def compute_all(self, raw_data):
        """Вычисляет все признаки"""
        result = raw_data.copy()
        for name, func in self.features.items():
            result[name] = func(raw_data)
        return result

# Использование
fs = FeatureStore()
fs.register_feature('pv_to_load_ratio', 
                   lambda df: df['pv_power'] / (df['output_power'] + 1))
```

### 4. Continuous Training

```bash
# Cron job для ежемесячного переобучения
0 2 1 * * cd /app && python app/ml/ml_model_training_example.py all >> logs/retraining.log 2>&1
```

---

## Полезные ссылки

### Документация
- [Scikit-learn User Guide](https://scikit-learn.org/stable/user_guide.html)
- [Pandas Documentation](https://pandas.pydata.org/docs/)
- [ML for Time Series](https://www.tensorflow.org/tutorials/structured_data/time_series)

### Дополнительные инструменты
- **MLflow** - tracking экспериментов
- **DVC** - версионирование данных
- **Weights & Biases** - мониторинг обучения
- **SHAP** - интерпретация моделей

---

## Контрольный чеклист

Перед запуском в production убедитесь:

- [ ] Собрано минимум **1 месяц** данных
- [ ] Пропусков в данных < 5%
- [ ] Модели обучены и показывают R² > 0.7
- [ ] Тестирование на отложенной выборке пройдено
- [ ] Логирование работает корректно
- [ ] Режим HYBRID настроен (fallback на эвристику)
- [ ] Мониторинг в Grafana/Loki настроен
- [ ] Документация обновлена
- [ ] Команда знает, как интерпретировать результаты

---

## Поддержка

При возникновении вопросов:

1. Проверьте логи: `logs/ml_controller.log`, `logs/important.log`
2. Запустите диагностику: `python app/ml/ml_data_analyzer.py stats`
3. Проверьте модели: `ls -lh ml_data/models/`
4. Посмотрите примеры в коде (docstrings и комментарии)

**Удачи в обучении моделей! 🚀🤖**