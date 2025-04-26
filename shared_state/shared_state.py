from __future__ import annotations
from threading import RLock
from typing import (
    Any, Iterable, Mapping, MutableMapping, Tuple, Union,
)

_UpdateArg = Union[
    Mapping[str, Any],  # {'a':1}
    Iterable[Tuple[str, Any]],  # [('a',1)]
]


class _SharedState(dict):
    _instance: "_SharedState|None" = None
    _lock = RLock()

    def __new__(cls, *a, **kw):
        if cls._instance is None:
            cls._instance = super().__new__(cls)  # type: ignore[arg-type]
        return cls._instance

    # —————————————————————————————— set / get ————————————————————
    def __setitem__(self, k: str, v: Any) -> None:
        with self._lock:
            super().__setitem__(k, v)

    def __getitem__(self, k: str) -> Any:  # noqa: ANN401
        with self._lock:
            return super().__getitem__(k)

    # —————————————————————————————— update ——————————————————————
    def update(self, m: _UpdateArg = None, /, **kw: Any, ) -> None:
        """
        Полностью повторяет сигнатуру ``MutableMapping.update()`` —
        никаких «Unexpected argument» больше не будет.
        """
        with self._lock:
            if m is None:
                super().update(**kw)
            else:
                super().update(m, **kw)


shared_state: _SharedState = _SharedState()
