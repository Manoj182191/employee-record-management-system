from threading import RLock
from typing import Generic, TypeVar


K = TypeVar("K")
V = TypeVar("V")
_DELETED = object()


class LinearProbingMap(Generic[K, V]):
    """Resizable linear-probing hash table for algorithm comparison."""

    def __init__(self, capacity: int = 17, max_load_factor: float = 0.65) -> None:
        if capacity < 2:
            raise ValueError("Capacity must be at least 2.")
        if not 0 < max_load_factor < 1:
            raise ValueError("Maximum load factor must be in (0, 1).")
        self.capacity = capacity
        self.max_load_factor = max_load_factor
        self.keys: list[K | object | None] = [None] * capacity
        self.values: list[V | None] = [None] * capacity
        self.size = 0
        self.collisions = 0
        self.probes = 0
        self.resizes = 0
        self._lock = RLock()

    def _index(self, key: K, capacity: int | None = None) -> int:
        bucket_count = capacity or self.capacity
        value = 0
        for character in str(key):
            value = (value * 31 + ord(character)) % bucket_count
        return value

    def put(self, key: K, value: V) -> None:
        with self._lock:
            if (self.size + 1) / self.capacity > self.max_load_factor:
                self._resize(self.capacity * 2 + 1)
            index = self._index(key)
            first_deleted: int | None = None
            for _ in range(self.capacity):
                self.probes += 1
                current_key = self.keys[index]
                if current_key is None:
                    destination = first_deleted if first_deleted is not None else index
                    self.keys[destination] = key
                    self.values[destination] = value
                    self.size += 1
                    return
                if current_key is _DELETED:
                    if first_deleted is None:
                        first_deleted = index
                elif current_key == key:
                    self.values[index] = value
                    return
                else:
                    self.collisions += 1
                index = (index + 1) % self.capacity
            if first_deleted is not None:
                self.keys[first_deleted] = key
                self.values[first_deleted] = value
                self.size += 1
                return
            self._resize(self.capacity * 2 + 1)
            self.put(key, value)

    def get(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            index = self._index(key)
            for _ in range(self.capacity):
                self.probes += 1
                current_key = self.keys[index]
                if current_key is None:
                    return default
                if current_key is not _DELETED and current_key == key:
                    return self.values[index]
                index = (index + 1) % self.capacity
            return default

    def delete(self, key: K) -> bool:
        with self._lock:
            index = self._index(key)
            for _ in range(self.capacity):
                self.probes += 1
                current_key = self.keys[index]
                if current_key is None:
                    return False
                if current_key is not _DELETED and current_key == key:
                    self.keys[index] = _DELETED
                    self.values[index] = None
                    self.size -= 1
                    return True
                index = (index + 1) % self.capacity
            return False

    def _resize(self, new_capacity: int) -> None:
        entries = [
            (key, value)
            for key, value in zip(self.keys, self.values)
            if key is not None and key is not _DELETED
        ]
        self.capacity = new_capacity
        self.keys = [None] * new_capacity
        self.values = [None] * new_capacity
        self.size = 0
        for key, value in entries:
            index = self._index(key)
            while self.keys[index] is not None:
                index = (index + 1) % self.capacity
            self.keys[index] = key
            self.values[index] = value
            self.size += 1
        self.resizes += 1

    def slot_snapshot(self) -> list[dict[str, str | int | None]]:
        with self._lock:
            return [
                {
                    "index": index,
                    "state": (
                        "empty" if key is None
                        else "deleted" if key is _DELETED
                        else "occupied"
                    ),
                    "key": None if key is None or key is _DELETED else str(key),
                }
                for index, key in enumerate(self.keys)
            ]
