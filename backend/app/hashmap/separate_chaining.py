from threading import RLock
from typing import Generic, TypeVar


K = TypeVar("K")
V = TypeVar("V")


class EmployeeHashMap(Generic[K, V]):
    """Resizable separate-chaining map keyed by employee ID."""

    def __init__(self, capacity: int = 17, max_load_factor: float = 0.75) -> None:
        if capacity < 2:
            raise ValueError("Capacity must be at least 2.")
        if not 0 < max_load_factor <= 1:
            raise ValueError("Maximum load factor must be in (0, 1].")
        self.capacity = capacity
        self.max_load_factor = max_load_factor
        self.buckets: list[list[tuple[K, V]]] = [[] for _ in range(capacity)]
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
            index = self._index(key)
            bucket = self.buckets[index]
            for position, (existing_key, _) in enumerate(bucket):
                self.probes += 1
                if existing_key == key:
                    bucket[position] = (key, value)
                    return
            if bucket:
                self.collisions += 1
            bucket.append((key, value))
            self.size += 1
            if self.size / self.capacity > self.max_load_factor:
                self._resize(self.capacity * 2 + 1)

    def get(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            for existing_key, value in self.buckets[self._index(key)]:
                self.probes += 1
                if existing_key == key:
                    return value
            return default

    def delete(self, key: K) -> bool:
        with self._lock:
            bucket = self.buckets[self._index(key)]
            for position, (existing_key, _) in enumerate(bucket):
                self.probes += 1
                if existing_key == key:
                    del bucket[position]
                    self.size -= 1
                    return True
            return False

    def _resize(self, new_capacity: int) -> None:
        entries = [entry for bucket in self.buckets for entry in bucket]
        self.capacity = new_capacity
        self.buckets = [[] for _ in range(new_capacity)]
        self.size = 0
        for key, value in entries:
            self.buckets[self._index(key)].append((key, value))
            self.size += 1
        self.resizes += 1

    def values(self) -> list[V]:
        with self._lock:
            return [value for bucket in self.buckets for _, value in bucket]

    def bucket_snapshot(self) -> list[list[tuple[K, V]]]:
        with self._lock:
            return [list(bucket) for bucket in self.buckets]

    def stats(self) -> dict[str, int | float]:
        with self._lock:
            lengths = [len(bucket) for bucket in self.buckets]
            return {
                "size": self.size,
                "capacity": self.capacity,
                "load_factor": round(self.size / self.capacity, 3),
                "collisions": self.collisions,
                "max_chain_length": max(lengths, default=0),
                "resizes": self.resizes,
            }
