"""测试全局配置。

把自选股列表重定向到临时目录，避免测试读写用户真实的
~/.quant_watchlist.json（必须在 utils.watchlist 被导入前设置）。
"""
import os
import tempfile

if not os.environ.get("QUANT_WATCHLIST_PATH"):
    os.environ["QUANT_WATCHLIST_PATH"] = os.path.join(
        tempfile.gettempdir(), "quant_test_watchlist.json"
    )
