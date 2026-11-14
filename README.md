# tiny-metric-counter

Counters and histograms with bounded cardinality. Each metric family has an enforced cap on the number of distinct label-value tuples, so you can expose metrics from code where labels come from unbounded input without risking resource exhaustion.

```python
from tiny_metric_counter import MetricRegistry

reg = MetricRegistry()

c = reg.counter("http_requests_total", labels=["method"], max_labels=16)
c.inc(method="GET")

h = reg.histogram("http_latency_seconds", labels=["route"], buckets=[0.1, 0.5, 1.0], max_labels=32)
h.observe(0.2, route="/api/users")

print(reg.collect())
```

## Why this exists

When you derive metric labels from request paths, user IDs, or other unbounded input, a single pathological caller can create millions of series. Prometheus calls this cardinality explosion; it degrades service performance and storage. This library prevents it at the source: every family gets a hard `max_labels` bound set at construction time.

The trade-off is fidelity. When the bound is reached you either raise an error (the default, so you notice during testing) or route subsequent observations to a synthetic `"__overflow__"` label. The overflow option loses per-label resolution for the excess data, which is the point: you keep operational visibility without unbounded cost.

## Edge cases

* A family with no labels (`labels=[]`) has exactly one series. Overflow does not apply to it; observations always land in the single bucket.
* The overflow bucket is only supported for single-label families. Multi-label overflow would require remapping a tuple onto a single key, which this library deliberately avoids to keep semantics simple. If a multi-label family exceeds its bound, it raises even under `on_limit="overflow"`.
* Re-calling `reg.counter(name, ...)` returns the existing family and ignores the `labels` and `max_labels` arguments you pass the second time. This is a deliberate simplicity trade-off: you always get back the family that was registered first under that name.
* Histogram buckets are sorted at construction. An observation lands in every bucket whose threshold is >= the value (cumulative buckets). Values above all buckets are counted in `sum` and `count` but in no bucket.

## Running the tests

```
PYTHONPATH=src python -m unittest discover -s tests
```

## Design notes

The window stores values eagerly rather than keeping running aggregates. Running
sums drift with floating point over long streams, and recomputing from a small
buffer is cheap enough that the drift is not worth the speed.

