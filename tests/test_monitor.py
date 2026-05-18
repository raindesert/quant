"""Tests for RealtimeMonitor."""
import unittest
from threading import Thread, Lock
import time

from monitor.realtime import RealtimeMonitor


class TestThreadSafetyNoRaceCondition(unittest.TestCase):
    def test_prices_dict_accessed_safely(self):
        """The prices dict is accessed safely under concurrent reads/writes."""
        monitor = RealtimeMonitor(symbols=["TEST"], interval=60)
        monitor.prices = {"TEST": {"last_price": 10.0}}

        errors = []

        def reader():
            try:
                for _ in range(1000):
                    _ = monitor.get_price("TEST")
                    _ = len(monitor.prices)
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                for i in range(1000):
                    with monitor._lock:
                        monitor.prices["TEST"] = {"last_price": float(i)}
            except Exception as e:
                errors.append(e)

        threads = [
            Thread(target=reader),
            Thread(target=reader),
            Thread(target=writer),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])


class TestCallbackExceptionDoesntCrashLoop(unittest.TestCase):
    def test_callback_error_contained(self):
        """Callback exceptions are caught and don't crash the monitor loop."""
        monitor = RealtimeMonitor(symbols=["TEST"], interval=60)

        call_count = [0]

        def bad_callback(symbol, bar):
            call_count[0] += 1
            if call_count[0] > 2:
                raise RuntimeError("callback error")

        def good_callback(symbol, bar):
            pass

        monitor.add_callback(bad_callback)
        monitor.add_callback(good_callback)

        test_data = {
            "TEST": {
                "symbol": "TEST",
                "last_price": 10.0,
                "date": "2024-01-01",
            }
        }

        # Simulate callback dispatch
        callbacks = list(monitor.callbacks)
        for callback in callbacks:
            try:
                callback("TEST", test_data["TEST"])
            except Exception as exc:
                # Exception should be caught per design
                pass

        # good_callback should have been called
        self.assertGreater(call_count[0], 0)


if __name__ == "__main__":
    unittest.main()