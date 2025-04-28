from datetime import datetime, time as dtime


def night_multiplier(now: datetime or None = None) -> int:
    """
    22:00–07:00 → возвращаем 5   (умножаем интервал ×5)
    остальное   → 1
    """
    now = now or datetime.now()
    if dtime(22, 0) <= now.time() or now.time() < dtime(7, 0):
        return 5          # можно 4 – по вкусу
    return 1