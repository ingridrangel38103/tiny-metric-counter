# tests/test_core.py
# Run: PYTHONPATH=src python -m unittest discover -s tests

import math
import unittest

from tiny_metric_counter import (
    DEFAULT_LIMIT,
    MetricRegistry,
)
from tiny_metric_counter.core import Counter, Histogram, CardinalityLimitExceeded


class TestCounter(unittest.TestCase):
    def test_inc_from_zero(self):
        c = Counter("c", ("method",), 10, "raise", "__overflow__")
        c.inc(method="GET")
        self.assertEqual(c.get(method="GET"), 1.0)

    def test_inc_by_amount(self):
        c = Counter("c", ("method",), 10, "raise", "__overflow__")
        c.inc(5.0, method="POST")
        c.inc(2.0, method="POST")
        self.assertEqual(c.get(method="POST"), 7.0)

    def test_inc_default_amount_is_one(self):
        c = Counter("c", ("method",), 10, "raise", "__overflow__")
        c.inc(method="GET")
        self.assertEqual(c.get(method="GET"), 1.0)

    def test_get_unset_is_zero(self):
        c = Counter("c", ("method",), 10, "raise", "__overflow__")
        self.assertEqual(c.get(method="GET"), 0.0)

    def test_negative_amount_raises(self):
        c = Counter("c", ("method",), 10, "raise", "__overflow__")
        with self.assertRaises(ValueError):
            c.inc(-1.0, method="GET")

    def test_wrong_labels_raises(self):
        c = Counter("c", ("method",), 10, "raise", "__overflow__")
        with self.assertRaises(ValueError):
            c.inc(path="/")

    def test_missing_labels_raises(self):
        c = Counter("c", ("method", "path"), 10, "raise", "__overflow__")
        with self.assertRaises(ValueError):
            c.inc(method="GET")

    def test_extra_labels_raises(self):
        c = Counter("c", ("method",), 10, "raise", "__overflow__")
        with self.assertRaises(ValueError):
            c.inc(method="GET", path="/")

    def test_cardinality_raise(self):
        c = Counter("c", ("id",), 2, "raise", "__overflow__")
        c.inc(id="a")
        c.inc(id="b")
        with self.assertRaises(CardinalityLimitExceeded):
            c.inc(id="c")

    def test_cardinality_overflow_single_label(self):
        c = Counter("c", ("id",), 2, "overflow", "__overflow__")
        c.inc(id="a")
        c.inc(id="b")
        c.inc(id="c")
        c.inc(id="d")
        self.assertEqual(c.get(id="a"), 1.0)
        self.assertEqual(c.get(id="b"), 1.0)
        self.assertEqual(c.get(id="__overflow__"), 2.0)

    def test_cardinality_overflow_multi_label_is_raise(self):
        # multi-label overflow is not supported in this simple lib
        c = Counter("c", ("a", "b"), 1, "overflow", "__overflow__")
        c.inc(a="1", b="1")
        with self.assertRaises(CardinalityLimitExceeded):
            c.inc(a="1", b="2")

    def test_snapshot(self):
        c = Counter("c", ("method",), 10, "raise", "__overflow__")
        c.inc(3.0, method="GET")
        snap = c.snapshot()
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0]["labels"], {"method": "GET"})
        self.assertEqual(snap[0]["value"], 3.0)

    def test_no_labels(self):
        c = Counter("c", (), 1, "raise", "__overflow__")
        c.inc()
        c.inc()
        self.assertEqual(c.get(), 2.0)


class TestHistogram(unittest.TestCase):
    def test_observe_basic(self):
        h = Histogram("h", ("route",), (0.1, 0.5, 1.0), 10, "raise", "__overflow__")
        h.observe(0.05, route="/")
        h.observe(0.4, route="/")
        h.observe(0.9, route="/")
        s = h.get(route="/")
        self.assertEqual(s["buckets"], {0.1: 1, 0.5: 2, 1.0: 3})
        self.assertEqual(s["count"], 3)
        self.assertAlmostEqual(s["sum"], 1.35, places=6)

    def test_observe_exact_bucket_boundary(self):
        h = Histogram("h", (), (1.0,), 10, "raise", "__overflow__")
        h.observe(1.0)
        s = h.get()
        self.assertEqual(s["buckets"], {1.0: 1})
        self.assertEqual(s["count"], 1)

    def test_observe_above_all_buckets(self):
        h = Histogram("h", (), (1.0, 2.0), 10, "raise", "__overflow__")
        h.observe(100.0)
        s = h.get()
        self.assertEqual(s["buckets"], {1.0: 0, 2.0: 0})
        self.assertEqual(s["count"], 1)
        self.assertEqual(s["sum"], 100.0)

    def test_get_unset_is_empty(self):
        h = Histogram("h", ("route",), (1.0,), 10, "raise", "__overflow__")
        s = h.get(route="/")
        self.assertEqual(s["count"], 0)
        self.assertEqual(s["buckets"], {1.0: 0})
        self.assertEqual(s["sum"], 0.0)

    def test_wrong_labels_raises(self):
        h = Histogram("h", ("route",), (1.0,), 10, "raise", "__overflow__")
        with self.assertRaises(ValueError):
            h.observe(1.0)

    def test_buckets_sorted(self):
        h = Histogram("h", (), (2.0, 1.0, 0.5), 10, "raise", "__overflow__")
        self.assertEqual(h.buckets, (0.5, 1.0, 2.0))

    def test_cardinality_raise(self):
        h = Histogram("h", ("id",), (1.0,), 1, "raise", "__overflow__")
        h.observe(0.5, id="a")
        with self.assertRaises(CardinalityLimitExceeded):
            h.observe(0.5, id="b")

    def test_cardinality_overflow(self):
        h = Histogram("h", ("id",), (1.0,), 1, "overflow", "__overflow__")
        h.observe(0.5, id="a")
        h.observe(0.5, id="b")
        s = h.get(id="__overflow__")
        self.assertEqual(s["count"], 1)
        self.assertEqual(s["sum"], 0.5)

    def test_snapshot(self):
        h = Histogram("h", ("route",), (1.0,), 10, "raise", "__overflow__")
        h.observe(0.5, route="/")
        snap = h.snapshot()
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0]["labels"], {"route": "/"})
        self.assertEqual(snap[0]["buckets"], {1.0: 1})
        self.assertEqual(snap[0]["count"], 1)


class TestRegistry(unittest.TestCase):
    def test_counter_create_and_reuse(self):
        r = MetricRegistry()
        c1 = r.counter("reqs", ("method",), max_labels=5)
        c2 = r.counter("reqs", ("method",), max_labels=5)
        self.assertIs(c1, c2)

    def test_histogram_create_and_reuse(self):
        r = MetricRegistry()
        h1 = r.histogram("lat", ("route",), buckets=(0.1, 0.5))
        h2 = r.histogram("lat", ("route",), buckets=(0.1, 0.5))
        self.assertIs(h1, h2)

    def test_collect_empty(self):
        r = MetricRegistry()
        self.assertEqual(r.collect(), {"counters": {}, "histograms": {}})

    def test_collect_counters_and_histograms(self):
        r = MetricRegistry()
        c = r.counter("reqs", ("method",), max_labels=10)
        h = r.histogram("lat", ("route",), buckets=(0.1,), max_labels=10)
        c.inc(method="GET")
        h.observe(0.05, route="/")
        out = r.collect()
        self.assertEqual(len(out["counters"]), 1)
        self.assertEqual(len(out["histograms"]), 1)
        c_snap = out["counters"]["reqs"]
        self.assertEqual(c_snap[0]["labels"], {"method": "GET"})
        self.assertEqual(c_snap[0]["value"], 1.0)
        h_snap = out["histograms"]["lat"]
        self.assertEqual(h_snap[0]["labels"], {"route": "/"})
        self.assertEqual(h_snap[0]["buckets"], {0.1: 1})

    def test_invalid_on_limit_raises(self):
        with self.assertRaises(ValueError):
            MetricRegistry(on_limit="other")

    def test_default_limit_constant(self):
        self.assertEqual(DEFAULT_LIMIT, 128)

    def test_cardinality_default_limit(self):
        r = MetricRegistry()
        c = r.counter("c", ("id",))
        for i in range(DEFAULT_LIMIT):
            c.inc(id="id%d" % i)
        self.assertEqual(len(c.snapshot()), DEFAULT_LIMIT)
        with self.assertRaises(CardinalityLimitExceeded):
            c.inc(id="over")


class TestNoLabels(unittest.TestCase):
    def test_histogram_no_labels(self):
        h = Histogram("h", (), (1.0, 2.0), 1, "raise", "__overflow__")
        h.observe(0.5)
        h.observe(1.5)
        s = h.get()
        self.assertEqual(s["buckets"], {1.0: 1, 2.0: 2})
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["sum"], 2.0)

    def test_histogram_no_labels_cardinality(self):
        h = Histogram("h", (), (1.0,), 1, "raise", "__overflow__")
        h.observe(0.5)
        # no-label histogram can only have one series; observing again is fine
        h.observe(1.5)
        s = h.get()
        self.assertEqual(s["count"], 2)


if __name__ == "__main__":
    unittest.main()
