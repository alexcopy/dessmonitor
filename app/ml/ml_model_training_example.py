# app/ml/ml_model_training_example.py
"""
Пример обучения ML-моделей на собранных данных.

Демонстрирует три задачи:
1. Прогнозирование генерации PV (regression)
2. Оптимизация управления батареей (classification)
3. Управление скоростью насоса (regression)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Tuple, List, Dict, Any
import pickle
import logging

from sklearn.model_selection import train_test_split, GridSearchCV
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier, GradientBoostingRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, r2_score, accuracy_score, classification_report

try:
    import matplotlib.pyplot as plt

    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    logging.warning("matplotlib not available. Install: pip install matplotlib")


class MLModelTrainer:
    """Класс для обучения моделей на собранных данных"""

    def __init__(self, data_path: Path = Path("ml_data/train_data.csv")):
        self.data_path = data_path
        self.df: pd.DataFrame = None
        self.scaler = StandardScaler()

        # Модели
        self.pv_predictor = None
        self.battery_optimizer = None
        self.pump_controller = None

        self.logger = logging.getLogger(__name__)

    def load_data(self):
        """Загружает обработанные данные"""
        if not self.data_path.exists():
            raise FileNotFoundError(
                f"Training data not found: {self.data_path}\n"
                "Run: python app/ml/ml_data_analyzer.py export"
            )

        self.df = pd.read_csv(self.data_path)
        print(f"Loaded {len(self.df)} training samples")
        return self.df

    # ======================================================================
    # Task 1: Прогнозирование генерации PV
    # ======================================================================

    def train_pv_predictor(self) -> dict:
        """
        Обучает модель прогнозирования генерации солнечных панелей.

        Цель: предсказать pv_total_power на следующий час
        на основе текущих условий и исторических данных.
        """
        print("\n" + "=" * 60)
        print("TASK 1: PV Power Prediction")
        print("=" * 60)

        if self.df is None:
            self.load_data()

        # Признаки
        features = [
            'hour', 'day_of_week', 'month', 'is_daytime',
            'pv_total_power', 'pv_total_power_ma_1h', 'pv_total_power_ma_3h',
            'ambient_temp', 'battery_soc', 'ac_output_load'
        ]

        # Целевая переменная
        target = 'target_pv_next_hour'

        # Проверяем наличие колонок
        available_features = [f for f in features if f in self.df.columns]
        if target not in self.df.columns:
            print(f"⚠️  Target column '{target}' not found. Skipping PV predictor.")
            return {}

        # Подготовка данных
        df_clean = self.df.dropna(subset=available_features + [target])
        X = df_clean[available_features]
        y = df_clean[target]

        print(f"Using {len(available_features)} features: {available_features}")

        # Разделение
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, shuffle=False
        )

        print(f"Training samples: {len(X_train)}, Test samples: {len(X_test)}")

        # Масштабирование
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)

        # Обучение модели
        print("\nTraining Random Forest...")
        model = RandomForestRegressor(
            n_estimators=100,
            max_depth=15,
            min_samples_split=5,
            random_state=42,
            n_jobs=-1
        )

        model.fit(X_train_scaled, y_train)

        # Предсказание
        y_pred_train = model.predict(X_train_scaled)
        y_pred_test = model.predict(X_test_scaled)

        # Метрики
        train_rmse = np.sqrt(mean_squared_error(y_train, y_pred_train))
        test_rmse = np.sqrt(mean_squared_error(y_test, y_pred_test))
        train_r2 = r2_score(y_train, y_pred_train)
        test_r2 = r2_score(y_test, y_pred_test)

        print(f"\n--- Results ---")
        print(f"Train RMSE: {train_rmse:.2f} W")
        print(f"Test RMSE:  {test_rmse:.2f} W")
        print(f"Train R²:   {train_r2:.4f}")
        print(f"Test R²:    {test_r2:.4f}")

        # Важность признаков
        feature_importance = pd.DataFrame({
            'feature': available_features,
            'importance': model.feature_importances_
        }).sort_values('importance', ascending=False)

        print(f"\n--- Top 5 Feature Importance ---")
        print(feature_importance.head())

        # Визуализация
        if MATPLOTLIB_AVAILABLE:
            self._plot_pv_prediction(y_test, y_pred_test)

        # Сохранение модели
        self.pv_predictor = {
            'model': model,
            'scaler': self.scaler,
            'features': available_features,
            'metrics': {
                'train_rmse': train_rmse,
                'test_rmse': test_rmse,
                'test_r2': test_r2
            }
        }

        self._save_model(self.pv_predictor, 'pv_predictor.pkl')

        return self.pv_predictor['metrics']

    def _plot_pv_prediction(self, y_true, y_pred):
        """Визуализация прогноза PV"""
        fig, axes = plt.subplots(1, 2, figsize=(15, 5))

        # Scatter plot
        axes[0].scatter(y_true, y_pred, alpha=0.3, s=1)
        axes[0].plot([y_true.min(), y_true.max()],
                     [y_true.min(), y_true.max()],
                     'r--', lw=2)
        axes[0].set_xlabel('Actual PV Power (W)')
        axes[0].set_ylabel('Predicted PV Power (W)')
        axes[0].set_title('PV Prediction: Actual vs Predicted')
        axes[0].grid(True)

        # Time series
        plot_samples = min(500, len(y_true))
        axes[1].plot(y_true.values[:plot_samples], label='Actual', alpha=0.7)
        axes[1].plot(y_pred[:plot_samples], label='Predicted', alpha=0.7)
        axes[1].set_xlabel('Sample')
        axes[1].set_ylabel('PV Power (W)')
        axes[1].set_title(f'PV Prediction: First {plot_samples} samples')
        axes[1].legend()
        axes[1].grid(True)

        plt.tight_layout()
        save_path = Path("ml_data/pv_prediction_plot.png")
        plt.savefig(save_path, dpi=150)
        print(f"\nPlot saved: {save_path}")
        plt.close()

    # ======================================================================
    # Task 2: Оптимизация управления батареей
    # ======================================================================

    def train_battery_optimizer(self) -> dict:
        """
        Обучает модель оптимизации управления батареей.

        Цель: классифицировать текущее состояние как оптимальное (1)
        или неоптимальное (0) на основе эвристических правил.
        """
        print("\n" + "=" * 60)
        print("TASK 2: Battery Management Optimization")
        print("=" * 60)

        if self.df is None:
            self.load_data()

        # Признаки
        features = [
            'hour', 'is_daytime', 'is_weekend',
            'battery_soc', 'battery_voltage', 'battery_current_chg', 'battery_current_dis',
            'pv_total_power', 'pv_total_power_ma_1h',
            'output_power', 'ac_output_load',
            'pv_to_load_ratio'
        ]

        target = 'target_is_optimal'

        # Проверяем наличие
        available_features = [f for f in features if f in self.df.columns]
        if target not in self.df.columns:
            print(f"⚠️  Target column '{target}' not found. Skipping battery optimizer.")
            return {}

        # Подготовка данных
        df_clean = self.df.dropna(subset=available_features + [target])
        X = df_clean[available_features]
        y = df_clean[target]

        print(f"Using {len(available_features)} features")

        # Проверка баланса классов
        class_counts = y.value_counts()
        print(f"\nClass distribution:")
        print(f"  Optimal (1):     {class_counts.get(1, 0)} ({class_counts.get(1, 0) / len(y) * 100:.1f}%)")
        print(f"  Not optimal (0): {class_counts.get(0, 0)} ({class_counts.get(0, 0) / len(y) * 100:.1f}%)")

        # Разделение
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, stratify=y, random_state=42
        )

        print(f"\nTraining samples: {len(X_train)}, Test samples: {len(X_test)}")

        # Обучение
        print("\nTraining Random Forest Classifier...")
        model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            min_samples_split=10,
            class_weight='balanced',
            random_state=42,
            n_jobs=-1
        )

        model.fit(X_train, y_train)

        # Предсказание
        y_pred_train = model.predict(X_train)
        y_pred_test = model.predict(X_test)

        # Метрики
        train_acc = accuracy_score(y_train, y_pred_train)
        test_acc = accuracy_score(y_test, y_pred_test)

        print(f"\n--- Results ---")
        print(f"Train Accuracy: {train_acc:.4f}")
        print(f"Test Accuracy:  {test_acc:.4f}")
        print(f"\n--- Classification Report ---")
        print(classification_report(y_test, y_pred_test,
                                    target_names=['Not Optimal', 'Optimal']))

        # Сохранение
        self.battery_optimizer = {
            'model': model,
            'features': available_features,
            'metrics': {
                'train_acc': train_acc,
                'test_acc': test_acc
            }
        }

        self._save_model(self.battery_optimizer, 'battery_optimizer.pkl')

        return self.battery_optimizer['metrics']

    # ======================================================================
    # Task 3: Управление скоростью насоса
    # ======================================================================

    def train_pump_controller(self) -> dict:
        """
        Обучает модель управления скоростью насоса.

        Цель: предсказать оптимальную скорость насоса (0-100)
        на основе состояния системы и окружающей среды.
        """
        print("\n" + "=" * 60)
        print("TASK 3: Pump Speed Control")
        print("=" * 60)

        if self.df is None:
            self.load_data()

        # Если насос есть в данных
        if 'pump_speed' not in self.df.columns:
            print("⚠️  No pump data available. Skipping pump controller training.")
            return {}

        # Признаки
        features = [
            'hour', 'is_daytime', 'ambient_temp',
            'battery_voltage', 'battery_soc',
            'pv_total_power', 'pv_total_power_ma_3h',
            'working_mode',
            'output_power', 'ac_output_load'
        ]

        target = 'pump_speed'

        # Проверяем наличие
        available_features = [f for f in features if f in self.df.columns]

        # Подготовка данных
        df_clean = self.df.dropna(subset=available_features + [target])

        # One-hot encoding для working_mode если есть
        if 'working_mode' in available_features:
            df_encoded = pd.get_dummies(df_clean, columns=['working_mode'], prefix='mode')
            # Обновляем список признаков
            mode_cols = [c for c in df_encoded.columns if c.startswith('mode_')]
            available_features = [f for f in available_features if f != 'working_mode'] + mode_cols
        else:
            df_encoded = df_clean

        X = df_encoded[available_features]
        y = df_encoded[target]

        print(f"Using {len(available_features)} features")

        # Разделение
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, shuffle=False
        )

        print(f"\nTraining samples: {len(X_train)}, Test samples: {len(X_test)}")

        # Обучение
        print("\nTraining Gradient Boosting Regressor...")
        model = GradientBoostingRegressor(
            n_estimators=100,
            max_depth=5,
            learning_rate=0.1,
            random_state=42
        )

        model.fit(X_train, y_train)

        # Предсказание
        y_pred_train = model.predict(X_train)
        y_pred_test = model.predict(X_test)

        # Метрики
        train_rmse = np.sqrt(mean_squared_error(y_train, y_pred_train))
        test_rmse = np.sqrt(mean_squared_error(y_test, y_pred_test))
        train_r2 = r2_score(y_train, y_pred_train)
        test_r2 = r2_score(y_test, y_pred_test)

        print(f"\n--- Results ---")
        print(f"Train RMSE: {train_rmse:.2f}%")
        print(f"Test RMSE:  {test_rmse:.2f}%")
        print(f"Train R²:   {train_r2:.4f}")
        print(f"Test R²:    {test_r2:.4f}")

        # Сохранение
        self.pump_controller = {
            'model': model,
            'features': available_features,
            'metrics': {
                'train_rmse': train_rmse,
                'test_rmse': test_rmse,
                'test_r2': test_r2
            }
        }

        self._save_model(self.pump_controller, 'pump_controller.pkl')

        return self.pump_controller['metrics']

    # ======================================================================
    # Утилиты
    # ======================================================================

    def _save_model(self, model_dict: dict, filename: str):
        """Сохраняет модель в pickle"""
        save_path = Path("ml_data/models") / filename
        save_path.parent.mkdir(parents=True, exist_ok=True)

        with open(save_path, 'wb') as f:
            pickle.dump(model_dict, f)

        print(f"\n✅ Model saved: {save_path}")

    def train_all(self) -> dict:
        """Обучает все модели последовательно"""
        results = {}

        try:
            results['pv_predictor'] = self.train_pv_predictor()
        except Exception as e:
            print(f"❌ PV predictor failed: {e}")
            self.logger.error(f"PV predictor training failed", exc_info=True)

        try:
            results['battery_optimizer'] = self.train_battery_optimizer()
        except Exception as e:
            print(f"❌ Battery optimizer failed: {e}")
            self.logger.error(f"Battery optimizer training failed", exc_info=True)

        try:
            results['pump_controller'] = self.train_pump_controller()
        except Exception as e:
            print(f"❌ Pump controller failed: {e}")
            self.logger.error(f"Pump controller training failed", exc_info=True)

        print("\n" + "=" * 60)
        print("TRAINING COMPLETE")
        print("=" * 60)
        for model_name, metrics in results.items():
            if metrics:
                print(f"\n{model_name}:")
                for metric, value in metrics.items():
                    print(f"  {metric}: {value}")

        return results


# ==============================================================================
# Класс для использования обученных моделей в продакшене
# ==============================================================================

class MLModelPredictor:
    """Использование обученных моделей для предсказаний"""

    def __init__(self, models_dir: Path = Path("ml_data/models")):
        self.models_dir = models_dir
        self.pv_predictor = None
        self.battery_optimizer = None
        self.pump_controller = None
        self.logger = logging.getLogger(__name__)

    def load_model(self, model_name: str) -> dict:
        """Загружает обученную модель из pickle"""
        model_path = self.models_dir / f"{model_name}.pkl"

        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")

        with open(model_path, 'rb') as f:
            model_dict = pickle.load(f)

        self.logger.info(f"Loaded model: {model_name}")
        return model_dict

    def predict_pv_next_hour(self, current_state: dict) -> float:
        """
        Прогнозирует генерацию PV на следующий час.

        Args:
            current_state: словарь с текущими значениями признаков

        Returns:
            Прогнозируемая мощность PV в Ваттах
        """
        if self.pv_predictor is None:
            self.pv_predictor = self.load_model('pv_predictor')

        model = self.pv_predictor['model']
        scaler = self.pv_predictor['scaler']
        features = self.pv_predictor['features']

        # Собираем признаки в правильном порядке
        X = np.array([[current_state.get(f, 0) for f in features]])

        # Масштабируем и предсказываем
        X_scaled = scaler.transform(X)
        prediction = model.predict(X_scaled)[0]

        return max(0, prediction)

    def is_state_optimal(self, current_state: dict) -> bool:
        """
        Определяет, оптимально ли текущее состояние системы.

        Returns:
            True если состояние оптимально, False если нет
        """
        if self.battery_optimizer is None:
            self.battery_optimizer = self.load_model('battery_optimizer')

        model = self.battery_optimizer['model']
        features = self.battery_optimizer['features']

        X = np.array([[current_state.get(f, 0) for f in features]])
        prediction = model.predict(X)[0]

        return bool(prediction)

    def predict_optimal_pump_speed(self, current_state: dict) -> int:
        """
        Предсказывает оптимальную скорость насоса.

        Returns:
            Скорость насоса от 0 до 100
        """
        if self.pump_controller is None:
            self.pump_controller = self.load_model('pump_controller')

        model = self.pump_controller['model']
        features = self.pump_controller['features']

        # One-hot encoding для working_mode
        state_encoded = current_state.copy()
        if 'working_mode' in state_encoded:
            mode = state_encoded.pop('working_mode')
            # Создаём все возможные mode_ колонки
            for mode_name in ['LINE MODE', 'BATTERY MODE', 'PV MODE']:
                state_encoded[f'mode_{mode_name}'] = int(mode == mode_name)

        X = np.array([[state_encoded.get(f, 0) for f in features]])
        prediction = model.predict(X)[0]

        return int(np.clip(prediction, 0, 100))


# ==============================================================================
# CLI для обучения моделей
# ==============================================================================

if __name__ == "__main__":
    import sys

    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    if len(sys.argv) > 1:
        command = sys.argv[1]

        trainer = MLModelTrainer()

        if command == "all":
            print("Training all models...")
            trainer.train_all()

        elif command == "pv":
            print("Training PV predictor...")
            trainer.train_pv_predictor()

        elif command == "battery":
            print("Training battery optimizer...")
            trainer.train_battery_optimizer()

        elif command == "pump":
            print("Training pump controller...")
            trainer.train_pump_controller()

        elif command == "test":
            # Тестирование предсказаний
            print("\nTesting predictions...")
            predictor = MLModelPredictor()

            # Пример состояния системы
            test_state = {
                'hour': 14,
                'day_of_week': 2,
                'month': 10,
                'is_daytime': 1,
                'is_weekend': 0,
                'pv_total_power': 1200.0,
                'pv_total_power_ma_1h': 1150.0,
                'pv_total_power_ma_3h': 1100.0,
                'ambient_temp': 18.5,
                'battery_voltage': 52.3,
                'battery_soc': 85.0,
                'battery_current_chg': 5.0,
                'battery_current_dis': 0.0,
                'output_power': 800.0,
                'ac_output_load': 35.0,
                'pv_to_load_ratio': 1.5,
                'working_mode': 'PV MODE'
            }

            try:
                pv_pred = predictor.predict_pv_next_hour(test_state)
                print(f"\nPV prediction for next hour: {pv_pred:.0f} W")
            except Exception as e:
                print(f"PV prediction failed: {e}")

            try:
                is_optimal = predictor.is_state_optimal(test_state)
                print(f"Current state is optimal: {is_optimal}")
            except Exception as e:
                print(f"Optimality check failed: {e}")

            try:
                pump_speed = predictor.predict_optimal_pump_speed(test_state)
                print(f"Optimal pump speed: {pump_speed}%")
            except Exception as e:
                print(f"Pump prediction failed: {e}")

        else:
            print(f"Unknown command: {command}")
            print("Available commands: all, pv, battery, pump, test")

    else:
        print("Usage: python ml_model_training_example.py [all|pv|battery|pump|test]")
        print("\nCommands:")
        print("  all      - Train all models")
        print("  pv       - Train PV predictor only")
        print("  battery  - Train battery optimizer only")
        print("  pump     - Train pump controller only")
        print("  test     - Test predictions with example data")