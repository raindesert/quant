"""实时行情监控。"""
import logging
import time
import threading
from data.fetcher import DataFetcher

logger = logging.getLogger("quant")


class RealtimeMonitor:
    """按固定间隔轮询实时行情，并分发给回调。"""

    def __init__(self, symbols: list, interval: int = 60):
        self.symbols = symbols
        self.interval = max(1, interval)
        self.fetcher = DataFetcher()
        self._running = threading.Event()
        self.callbacks = []
        self._prices_lock = threading.Lock()
        self.prices = {}
        self.thread = None

    def add_callback(self, callback):
        self.callbacks.append(callback)

    def start(self):
        if self._running.is_set():
            return

        self._running.set()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        print(f"实时监控已启动: {self.symbols}")

    def stop(self):
        self._running.clear()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=self.interval + 5)
        print("实时监控已停止")

    def _run(self):
        while self._running.is_set():
            try:
                data = self.fetcher.get_realtime_batch(self.symbols)
                if data:
                    with self._prices_lock:
                        self.prices.update(data)
                    for callback in self.callbacks:
                        for symbol, bar in data.items():
                            try:
                                callback(symbol, bar)
                            except Exception as exc:
                                logger.warning("回调执行失败 %s: %s", symbol, exc)
            except Exception as exc:
                logger.error("行情轮询异常: %s", exc)

            self._running.wait(timeout=self.interval)

    def get_price(self, symbol: str) -> float:
        with self._prices_lock:
            if symbol in self.prices:
                return self.prices[symbol].get("last_price", 0.0)
        return 0.0
