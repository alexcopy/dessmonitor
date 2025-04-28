# app/utils/time_utils.py
from datetime import datetime

def night_multiplier() -> int:
    """
    • 22:00 – 07:00  → коэффициент 5
    • иначе          → 1
    """
    h = datetime.now().hour
    return 5 if (h >= 22 or h < 7) else 1


async def smart_sleep(stop_event, base_sec: int) -> None:
    """
    Асинхронная «пауза с учётом ночи».

    • Ждёт min(base_sec * k, 1) секунд, где k = night_multiplier()
    • Прерывается мгновенно, если `stop_event.set()` вызывается
      при завершении приложения.
    """
    from asyncio import wait_for, TimeoutError
    k = night_multiplier()
    delay = max(1, base_sec * k)
    try:
        await wait_for(stop_event.wait(), timeout=delay)
    except TimeoutError:
        pass          # нормальный переход к следующему циклу
