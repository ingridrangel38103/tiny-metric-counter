"""Counters and histograms with bounded cardinality.

This module implements a tiny metrics library where each metric family has an
enforced cap on the number of distinct label-value combinations (cardinality).
This guards against accidental resource exhaustion when labels are derived
from unbounded input (user IDs, request paths, etc.).

Only the Python standard library is used; no third-party dependencies.
"""

import threading
from typing import Dict, List, Mapping, Optional, Sequence


class CardinalityLimitExceeded(Exception):
    """Raised by default when a metric exceeds its label-cardinality limit.

    A registry can be configured with ``on_limit='overflow'`` to silently
    remap excess combinations onto the pseudo-label ``"__overflow__"`` instead
    of raising.
    """

    pass


class MetricRegistry:
    """A registry that owns counters and histograms and enforces cardinality.

    Each metric family is created with a fixed set of label *names* and an
    upper bound (``max_labels``) on distinct label-value tuples. When the
    bound would be exceeded:
      * ``on_limit='raise'`` (default) -- raise CardinalityLimitExceeded.
      * ``on_limit='overflow'`` -- route the new tuple to ``__overflow__``.

    The registry is thread-safe; each metric uses its own lock.
    """

    DEFAULT_OVERFLOW_LABEL = "__overflow__"

    def __init__(self, on_limit: str = "raise") -> None:
        if on_limit not in ("raise", "overflow"):
            raise ValueError(
                "on_limit must be 'raise' or 'overflow', got: %r" % (on_limit,)
            )
        self._on_limit = on_limit
        self._counters: Dict[str, Counter] = {}
        self._histograms: Dict[str, Histogram] = {}
        self._lock = threading.Lock()

    def counter(
        self,
        name: str,
        labels: Sequence[str],
        max_labels: int = 128,
    ) -> "Counter":
        """Get or create a counter family.

        Re-call with the same ``name`` to retrieve an existing family; the
        ``labels`` and ``max_labels`` arguments are *ignored* for existing
        families (this is a deliberate simplicity trade-off).
        """
        with self._lock:
            if name in self._counters:
                return self._counters[name]
            c = Counter(
                name=name,
                label_names=tuple(labels),
                max_labels=max_labels,
                on_limit=self._on_limit,
                overflow_label=self.DEFAULT_OVERFLOW_LABEL,
            )
            self._counters[name] = c
            return c

    def histogram(
        self,
        name: str,
        labels: Sequence[str],
        buckets: Sequence[float],
        max_labels: int = 128,
    ) -> "Histogram":
        """Get or create a histogram family."""
        with self._lock:
            if name in self._histograms:
                return self._histograms[name]
            h = Histogram(
                name=name,
                label_names=tuple(labels),
                buckets=sorted(buckets),
                max_labels=max_labels,
                on_limit=self._on_limit,
                overflow_label=self.DEFAULT_OVERFLOW_LABEL,
            )
            self._histograms[name] = h
            return h

    def collect(self) -> Dict[str, object]:
        """Return a snapshot of all metric values (for testing/inspection).

        Format::
            {
              "counters": {
                  "http_requests_total": [
                      {"labels": {...}, "value": 5}, ...
                  ]
              },
              "histograms": {
                  "rpc_latency_seconds": [
                      {"labels": {...}, "sum": 1.0, "count": 3,
                       "buckets": {0.1: 2, 0.5: 3}}, ...
                  ]
              }
            }
        """
        with self._lock:
            counters = {n: c.snapshot() for n, c in self._counters.items()}
            histograms = {n: h.snapshot() for n, h in self._histograms.items()}
        return {"counters": counters, "histograms": histograms}


class Counter:
    """A counter metric family with bounded cardinality.

    Counters monotonically increase (via :meth:`inc`). Decrementing is not
    supported; if you need a gauge, this is the wrong library.
    """

    def __init__(
        self,
        name: str,
        label_names: Sequence[str],
        max_labels: int,
        on_limit: str,
        overflow_label: str,
    ) -> None:
        self.name = name
        self.label_names = tuple(label_names)
        self.max_labels = max_labels
        self._on_limit = on_limit
        self._overflow_label = overflow_label
        self._values: Dict[tuple, float] = {}
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        """Increment the counter identified by ``labels`` by ``amount``.

        Raises if labels don't match the declared set, or if amount < 0, or
        if cardinality would be exceeded (subject to ``on_limit``).
        """
        if amount < 0:
            raise ValueError("amount must be non-negative, got %r" % (amount,))
        key = self._resolve_key(labels)
        with self._lock:
            if key not in self._values:
                if len(self._values) >= self.max_labels:
                    key = self._handle_overflow()
                    if key is None:
                        raise CardinalityLimitExceeded(
                            "counter %r exceeded max_labels=%d" % (self.name, self.max_labels)
                        )
            self._values[key] = self._values.get(key, 0.0) + amount

    def get(self, **labels: str) -> float:
        """Return the current counter value for ``labels``."""
        key = self._resolve_key(labels)
        with self._lock:
            return self._values.get(key, 0.0)

    def snapshot(self) -> List[Dict[str, object]]:
        """Return all current label/value pairs as a list of dicts."""
        with self._lock:
            return [
                {
                    "labels": dict(zip(self.label_names, k)),
                    "value": v,
                }
                for k, v in self._values.items()
            ]

    def _resolve_key(self, labels: Mapping[str, str]) -> tuple:
        if set(labels.keys()) != set(self.label_names):
            raise ValueError(
                "label mismatch: expected %s, got %s"
                % (",".join(self.label_names) if self.label_names else "(none)",
                    ",".join(sorted(labels.keys())) if labels else "(none)")
            )
        return tuple(labels[n] for n in self.label_names)

    def _handle_overflow(self) -> Optional[tuple]:
        if self._on_limit != "overflow":
            return None
        overflow_key = (self._overflow_label,)
        if not self.label_names:
            return None
        return overflow_key if len(self.label_names) == 1 else None


class Histogram:
    """A histogram metric family with bounded cardinality.

    Observes a value into fixed buckets. Exposes per-bucket counts, total sum,
    and overall count via :meth:`snapshot`.
    """

    def __init__(
        self,
        name: str,
        label_names: Sequence[str],
        buckets: Sequence[float],
        max_labels: int,
        on_limit: str,
        overflow_label: str,
    ) -> None:
        self.name = name
        self.label_names = tuple(label_names)
        self.buckets = tuple(sorted(buckets))
        self.max_labels = max_labels
        self._on_limit = on_limit
        self._overflow_label = overflow_label
        self._series: Dict[tuple, Dict[str, object]] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        """Observe ``value`` into the histogram identified by ``labels``."""
        key = self._resolve_key(labels)
        with self._lock:
            if key not in self._series:
                if len(self._series) >= self.max_labels:
                    key = self._handle_overflow()
                    if key is None:
                        raise CardinalityLimitExceeded(
                            "histogram %r exceeded max_labels=%d"
                            % (self.name, self.max_labels)
                        )
                if key not in self._series:
                    self._series[key] = {
                        "buckets": {b: 0 for b in self.buckets},
                        "sum": 0.0,
                        "count": 0,
                    }
            s = self._series[key]
            b = s["buckets"]
            for edge in self.buckets:
                if value <= edge:
                    b[edge] += 1
            s["sum"] = float(s["sum"]) + value
            s["count"] = int(s["count"]) + 1

    def get(self, **labels: str) -> Dict[str, object]:
        """Return the current series for ``labels``."""
        key = self._resolve_key(labels)
        with self._lock:
            if key not in self._series:
                return {
                    "buckets": {b: 0 for b in self.buckets},
                    "sum": 0.0,
                    "count": 0,
                }
            s = self._series[key]
            return {
                "buckets": dict(s["buckets"]),
                "sum": float(s["sum"]),
                "count": int(s["count"]),
            }

    def snapshot(self) -> List[Dict[str, object]]:
        """Return all series as a list of dicts."""
        with self._lock:
            return [
                {
                    "labels": dict(zip(self.label_names, k)),
                    "buckets": dict(s["buckets"]),
                    "sum": float(s["sum"]),
                    "count": int(s["count"]),
                }
                for k, s in self._series.items()
            ]

    def _resolve_key(self, labels: Mapping[str, str]) -> tuple:
        if set(labels.keys()) != set(self.label_names):
            raise ValueError("label mismatch")
        return tuple(labels[n] for n in self.label_names)

    def _handle_overflow(self) -> Optional[tuple]:
        if self._on_limit != "overflow":
            return None
        if len(self.label_names) != 1:
            return None
        return (self._overflow_label,)


DEFAULT_LIMIT = 128
