"""实时行情监控。"""
import time
from threading import Thread

from data.fetcher import DataFetcher


class RealtimeMonitor:
    """按固定间隔轮询实时行情，并分发给回调。"""

    def __init__(self, symbols: list, interval: int = 60):
        self.symbols = symbols
        self.interval = max(1, interval)
        self.fetcher = DataFetcher()
        self.running = False
        self.callbacks = []
        self.prices = {}
        self.thread = None

    def add_callback(self, callback):
        """注册行情回调。"""
        self.callbacks.append(callback)

    def start(self):
        """启动监控线程。"""
        if self.running:
            return

        self.running = True
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()
        print(f"实时监控已启动: {self.symbols}")

    def stop(self):
        """停止监控线程。"""
        self.running = False
        print("实时监控已停止")

    def _run(self):
        """持续拉取并广播实时行情。"""
        while self.running:
            data = self.fetcher.get_realtime_batch(self.symbols)
            if data:
                self.prices.update(data)
                for callback in self.callbacks:
                    for symbol, bar in data.items():
                        try:
                            callback(symbol, bar)
                        except Exception as exc:
                            print(f"回调执行失败 {symbol}: {exc}")

            time.sleep(self.interval)

    def get_price(self, symbol: str) -> float:
        """获取最新价格。"""
        if symbol in self.prices:
            return self.prices[symbol].get("last_price", 0.0)
        return 0.0
