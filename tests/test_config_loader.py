"""P1-5 配置预设（YAML/JSON）测试。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# 避免触发完整 main.py（其会 import matplotlib 等）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.loader import (
    SUPPORTED_FORMATS,
    detect_format,
    example_config,
    load_config,
    merge_config_with_args,
    save_args_as_config,
)

print("[DEBUG] test_config_loader.py TOP", file=sys.stderr)
# 提前 import 真实 yaml，避免其他测试的 stub 污染 sys.modules
# stub 版的 yaml 只有 safe_load，没 dump。
# 真版有 _yaml 模块属性（cython 编译的）。
_y = sys.modules.get("yaml")
print(f"[DEBUG] yaml in sys.modules: {_y is not None}", file=sys.stderr)
if _y is None:
    pass  # 还没人 import，让 loader 函数内 lazy import
elif not hasattr(_y, "dump"):
    print(f"[DEBUG] test_config_loader: 检测到 yaml stub，强制重 import", file=sys.stderr)
    del sys.modules["yaml"]
    import yaml as _real_yaml  # noqa: F401
else:
    print(f"[DEBUG] test_config_loader: yaml 已经是真库", file=sys.stderr)


class TestDetectFormat(unittest.TestCase):
    def test_yaml(self):
        self.assertEqual(detect_format("a.yaml"), "yaml")
        self.assertEqual(detect_format("a.yml"), "yml")

    def test_json(self):
        self.assertEqual(detect_format("a.json"), "json")

    def test_unknown(self):
        with self.assertRaises(ValueError):
            detect_format("a.txt")
        with self.assertRaises(ValueError):
            detect_format("a.csv")

    def test_supported_set(self):
        self.assertEqual(SUPPORTED_FORMATS, ("yaml", "yml", "json"))


class TestLoadConfig(unittest.TestCase):
    def test_yaml_basic(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "test.yaml"
            p.write_text(
                "mode: backtest\nstrategy: sma\ndays: 120\n",
                encoding="utf-8",
            )
            cfg = load_config(p)
        self.assertEqual(cfg["mode"], "backtest")
        self.assertEqual(cfg["strategy"], "sma")
        self.assertEqual(cfg["days"], 120)

    def test_json_basic(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "test.json"
            p.write_text(
                json.dumps({"mode": "optimize", "optimize_method": "bayesian"}),
                encoding="utf-8",
            )
            cfg = load_config(p)
        self.assertEqual(cfg["mode"], "optimize")
        self.assertEqual(cfg["optimize_method"], "bayesian")

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "empty.yaml"
            p.write_text("", encoding="utf-8")
            cfg = load_config(p)
        self.assertEqual(cfg, {})

    def test_nonexistent(self):
        with self.assertRaises(FileNotFoundError):
            load_config("/nonexistent/path.yaml")

    def test_non_dict_root(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.yaml"
            p.write_text("- 1\n- 2\n", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_config(p)
            self.assertIn("配置根必须是 dict", str(ctx.exception))


class TestMergeConfigWithArgs(unittest.TestCase):
    def test_fills_none_fields(self):
        args = argparse.Namespace(days=None, strategy=None, position_size=0.5)
        cfg = {"days": 250, "strategy": "sma"}
        out = merge_config_with_args(cfg, args)
        self.assertEqual(out.days, 250)
        self.assertEqual(out.strategy, "sma")
        # 已有值不被覆盖
        self.assertEqual(out.position_size, 0.5)

    def test_cli_overrides_yaml(self):
        """CLI 显式传（不是 None）就保留 CLI 值。"""
        args = argparse.Namespace(days=500, strategy=None)
        cfg = {"days": 250, "strategy": "sma"}
        out = merge_config_with_args(cfg, args)
        self.assertEqual(out.days, 500)  # CLI 优先
        self.assertEqual(out.strategy, "sma")  # YAML 填 None

    def test_empty_config(self):
        args = argparse.Namespace(days=None, strategy="rsi")
        out = merge_config_with_args({}, args)
        self.assertEqual(out.strategy, "rsi")
        self.assertIsNone(out.days)

    def test_unknown_key_skipped_silently(self):
        """未知键不报错（仅跳过）。"""
        args = argparse.Namespace(days=None)
        cfg = {"days": 120, "unknown_key_xyz": "ignored"}
        out = merge_config_with_args(cfg, args)
        self.assertEqual(out.days, 120)
        self.assertFalse(hasattr(out, "unknown_key_xyz"))

    def test_empty_list_replaced(self):
        args = argparse.Namespace(symbols=None)
        cfg = {"symbols": ["000001.SZ", "000002.SZ"]}
        out = merge_config_with_args(cfg, args)
        # symbols 是 list，args.symbols 默认是 None，应该被填
        self.assertEqual(out.symbols, ["000001.SZ", "000002.SZ"])


class TestSaveArgsAsConfig(unittest.TestCase):
    def test_basic_save_yaml(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.yaml"
            args = argparse.Namespace(
                mode="backtest", strategy="sma", days=250,
                stop_loss=None, position_size=1.0,
                config=None, save_config=None, no_t1=False,  # bool
                _private="x",
            )
            saved = save_args_as_config(args, p)
            self.assertEqual(saved, p)
            self.assertTrue(p.exists())
            import yaml
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            self.assertEqual(data["mode"], "backtest")
            self.assertEqual(data["strategy"], "sma")
            self.assertEqual(data["days"], 250)
            # None 和 False 不写
            self.assertNotIn("stop_loss", data)
            self.assertNotIn("no_t1", data)
            self.assertNotIn("config", data)
            self.assertNotIn("save_config", data)
            self.assertNotIn("_private", data)

    def test_save_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.json"
            args = argparse.Namespace(mode="optimize", optimize_method="bayesian")
            save_args_as_config(args, p)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["mode"], "optimize")

    def test_round_trip(self):
        """save → load → merge 应该能还原。"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rt.yaml"
            original = argparse.Namespace(
                mode="backtest", strategy="rsi", days=120,
                position_size=0.5, stop_loss=0.05,
                config=None, save_config=None, no_t1=False,
            )
            save_args_as_config(original, p)
            loaded = load_config(p)
            new_args = argparse.Namespace(
                mode=None, strategy=None, days=None,
                position_size=None, stop_loss=None,
                config=None, save_config=None, no_t1=False,
            )
            merged = merge_config_with_args(loaded, new_args)
            self.assertEqual(merged.strategy, "rsi")
            self.assertEqual(merged.days, 120)
            self.assertEqual(merged.position_size, 0.5)
            self.assertEqual(merged.stop_loss, 0.05)


class TestExampleConfig(unittest.TestCase):
    def test_example_has_expected_keys(self):
        text = example_config()
        self.assertIn("mode:", text)
        self.assertIn("strategy:", text)
        self.assertIn("days:", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
