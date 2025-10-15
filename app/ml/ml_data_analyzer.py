# app/ml/ml_data_analyzer.py
"""
Утилиты для анализа и подготовки собранных данных для обучения модели.
"""

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
import pandas as pd


class MLDataAnalyzer:
    """Анализ и подготовка данных для ML"""

    def __init__(self, csv_path: Path = Path("ml_data/training_data.csv")):
        self.csv_path = csv_path
        self.df: pd.DataFrame = None

    def load_data(self) -> pd.DataFrame:
        """Загружает данные из CSV"""
        if not self.csv_path.exists():
            raise FileNotFoundError(f"Data file not found: {self.csv_path}")

        self.df = pd.read_csv(self.csv_path)
        self.df['timestamp'] = pd.to_datetime(self.df['timestamp'])
        self.df = self.df.sort_values('timestamp')

        print(f"Loaded {len(self.df)} records")
        print(f"Date range: {self.df['timestamp'].min()} to {self.df['timestamp'].max()}")

        return self.df

    def basic_statistics(self) -> Dict:
        """Базовая статистика по собранным данным"""
        if self.df is None:
            self.load_data()

        stats = {
            "total_records": len(self.df),
            "date_range_days": (self.df['timestamp'].max() - self.df['timestamp'].min()).days,
            "missing_values": self.df.isnull().sum().to_dict(),
            "numeric_stats": self.df.describe().to_dict(),
        }

        # Режимы работы
        if 'working_mode' in self.df.columns:
            stats['working_mode_distribution'] = self.df['working_mode'].value_counts().to_dict()

        # Среднее по времени суток
        self.df['hour_of_day'] = self.df['timestamp'].dt.hour
        stats['avg_pv_by_hour'] = self.df.groupby('hour_of_day')['pv_total_power'].mean().to_dict()

        return stats

    def find_data_gaps(self, max_gap_minutes: int = 15) -> List[Tuple[datetime, datetime]]:
        """Находит пропуски в данных"""
        if self.df is None:
            self.load_data()

        gaps = []
        for i in range(1, len(self.df)):
            prev_time = self.df.iloc[i - 1]['timestamp']
            curr_time = self.df.iloc[i]['timestamp']
            gap = (curr_time - prev_time).total_seconds() / 60

            if gap > max_gap_minutes:
                gaps.append((prev_time, curr_time))

        return gaps

    def create_features(self) -> pd.DataFrame:
        """
        Создаёт дополнительные признаки для обучения модели:
        - Скользящие средние
        - Разности (дельты)
        - Временные признаки
        """
        if self.df is None:
            self.load_data()

        df = self.df.copy()

        # === Временные признаки ===
        df['hour'] = df['timestamp'].dt.hour
        df['day_of_week'] = df['timestamp'].dt.dayofweek
        df['month'] = df['timestamp'].dt.month
        df['is_weekend'] = df['day_of_week'].isin([5, 6]).astype(int)
        df['is_daytime'] = df['hour'].between(6, 20).astype(int)

        # === Скользящие средние для сглаживания ===
        for col in ['pv_total_power', 'battery_voltage', 'battery_soc', 'output_power']:
            if col in df.columns:
                # Среднее за последний час (12 точек по 5 минут)
                df[f'{col}_ma_1h'] = df[col].rolling(window=12, min_periods=1).mean()
                # Среднее за последние 3 часа
                df[f'{col}_ma_3h'] = df[col].rolling(window=36, min_periods=1).mean()

        # === Производные (скорости изменения) ===
        for col in ['battery_soc', 'pv_total_power']:
            if col in df.columns:
                df[f'{col}_delta'] = df[col].diff()
                df[f'{col}_delta_rate'] = df[col].diff() / df['unix_ts'].diff()

        # === Отношения ===
        if 'pv_total_power' in df.columns and 'output_power' in df.columns:
            df['pv_to_load_ratio'] = df['pv_total_power'] / (df['output_power'] + 1)

        if 'battery_current_chg' in df.columns and 'battery_current_dis' in df.columns:
            df['net_battery_current'] = df['battery_current_chg'] - df['battery_current_dis']

        # === Целевые переменные (labels) для supervised learning ===
        # Прогноз PV на следующий час (shift на 12 точек назад)
        if 'pv_total_power' in df.columns:
            df['target_pv_next_hour'] = df['pv_total_power'].shift(-12)

        # Оптимальность текущего состояния (эвристика)
        df['target_is_optimal'] = self._calculate_optimality(df)

        return df

    def _calculate_optimality(self, df: pd.DataFrame) -> pd.Series:
        """
        Эвристическая оценка оптимальности текущего состояния.
        1 = оптимально, 0 = не оптимально

        Критерии оптимальности:
        - Если есть избыток PV и батарея не заряжается → не оптимально
        - Если батарея разряжается при наличии PV → не оптимально
        - Если используется сеть при заряженной батарее → не оптимально
        """
        optimal = pd.Series(1, index=df.index)  # по умолчанию оптимально

        # Правило 1: PV избыток, но батарея не заряжается
        if all(col in df.columns for col in ['pv_total_power', 'output_power', 'battery_current_chg', 'battery_soc']):
            surplus = df['pv_total_power'] - df['output_power']
            not_charging = df['battery_current_chg'] < 1
            battery_not_full = df['battery_soc'] < 95

            optimal.loc[surplus > 100 & not_charging & battery_not_full] = 0

        # Правило 2: Разряд батареи при наличии солнца
        if all(col in df.columns for col in ['pv_total_power', 'battery_current_dis']):
            has_sun = df['pv_total_power'] > 100
            discharging = df['battery_current_dis'] > 1

            optimal.loc[has_sun & discharging] = 0

        # Правило 3: Использование сети при заряженной батарее
        if all(col in df.columns for col in ['working_mode', 'battery_soc']):
            on_grid = df['working_mode'] == 'LINE MODE'
            battery_ok = df['battery_soc'] > 30

            optimal.loc[on_grid & battery_ok] = 0

        return optimal

    def export_for_training(
            self,
            output_path: Path = Path("ml_data/processed_training_data.csv"),
            test_split: float = 0.2
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Экспортирует обработанные данные, разделённые на train/test

        Returns:
            (train_df, test_df)
        """
        df = self.create_features()

        # Удаляем строки с пропусками в критических столбцах
        critical_cols = ['battery_voltage', 'pv_total_power', 'output_power']
        df = df.dropna(subset=[c for c in critical_cols if c in df.columns])

        # Временное разделение (последние 20% - на тест)
        split_idx = int(len(df) * (1 - test_split))
        train_df = df.iloc[:split_idx]
        test_df = df.iloc[split_idx:]

        # Сохраняем
        train_path = output_path.parent / "train_data.csv"
        test_path = output_path.parent / "test_data.csv"

        train_df.to_csv(train_path, index=False)
        test_df.to_csv(test_path, index=False)

        print(f"Exported: Train={len(train_df)} rows, Test={len(test_df)} rows")
        print(f"Train: {train_path}")
        print(f"Test: {test_path}")

        return train_df, test_df

    def plot_overview(self, save_path: Path = None):
        """Создаёт обзорные графики собранных данных"""
        if self.df is None:
            self.load_data()

        fig, axes = plt.subplots(4, 1, figsize=(15, 12))

        # 1. PV generation
        axes[0].plot(self.df['timestamp'], self.df['pv_total_power'], label='PV Power')
        axes[0].set_ylabel('PV Power (W)')
        axes[0].set_title('Solar Generation')
        axes[0].legend()
        axes[0].grid(True)

        # 2. Battery
        ax2 = axes[1]
        ax2.plot(self.df['timestamp'], self.df['battery_voltage'], 'b-', label='Voltage')
        ax2.set_ylabel('Voltage (V)', color='b')
        ax2.tick_params(axis='y', labelcolor='b')

        ax2_twin = ax2.twinx()
        ax2_twin.plot(self.df['timestamp'], self.df['battery_soc'], 'r-', label='SOC')
        ax2_twin.set_ylabel('SOC (%)', color='r')
        ax2_twin.tick_params(axis='y', labelcolor='r')

        ax2.set_title('Battery State')
        ax2.grid(True)

        # 3. Load
        axes[2].plot(self.df['timestamp'], self.df['output_power'], label='Output Power')
        axes[2].plot(self.df['timestamp'], self.df['total_load_watt'], label='Device Load', alpha=0.7)
        axes[2].set_ylabel('Power (W)')
        axes[2].set_title('Load Consumption')
        axes[2].legend()
        axes[2].grid(True)

        # 4. Working mode
        if 'working_mode' in self.df.columns:
            mode_numeric = self.df['working_mode'].astype('category').cat.codes
            axes[3].scatter(self.df['timestamp'], mode_numeric, s=1, alpha=0.5)
            axes[3].set_ylabel('Mode')
            axes[3].set_title('Inverter Working Mode')
            axes[3].grid(True)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=150)
            print(f"Plot saved to {save_path}")
        else:
            plt.show()


# ============================================================================
# CLI для быстрого анализа
# ============================================================================

if __name__ == "__main__":
    import sys

    analyzer = MLDataAnalyzer()

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "stats":
            stats = analyzer.basic_statistics()
            print("\n=== STATISTICS ===")
            for key, value in stats.items():
                print(f"{key}: {value}")

        elif command == "gaps":
            gaps = analyzer.find_data_gaps()
            print(f"\n=== DATA GAPS (>{15} min) ===")
            for start, end in gaps:
                duration = (end - start).total_seconds() / 60
                print(f"{start} -> {end} ({duration:.1f} min)")

        elif command == "export":
            train, test = analyzer.export_for_training()
            print("Data exported successfully")

        elif command == "plot":
            save_path = Path("ml_data/overview_plot.png")
            analyzer.plot_overview(save_path)

        else:
            print(f"Unknown command: {command}")
            print("Available: stats, gaps, export, plot")

    else:
        print("Usage: python ml_data_analyzer.py [stats|gaps|export|plot]")