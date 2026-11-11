"""g05.cli 的守护测试（issue #38 集成联调发现）。

cmd_seed 用 runpy.run_path 执行 seed_data.py。runpy 会透传当前 sys.argv，
"seed" 子命令名会变成 seed_data.py 的未知参数导致报错。
修复：cmd_seed 执行期间把 sys.argv 隔离为目标脚本视角，跑完恢复。

守护纪律：不仅测「能跑」，还要验证隔离与恢复都真的发生。
"""

from __future__ import annotations

from unittest import mock

from g05 import cli


class TestCmdSeedArgvIsolation:
    def test_argv_isolated_during_runpy_and_restored_after(self):
        """runpy 收到的 argv 必须是 seed_data.py 视角；跑完后 argv 恢复原样。"""
        captured: dict = {}

        def fake_run_path(path, *, run_name):  # noqa: ANN001
            import sys

            captured["path"] = path
            captured["argv_during"] = list(sys.argv)
            return None

        original_argv = ["prog", "seed", "--extra"]
        with (
            mock.patch.object(cli.sys, "argv", original_argv),
            mock.patch("runpy.run_path", side_effect=fake_run_path),
        ):
            assert cli.cmd_seed(None) == 0
            # cmd_seed 的 finally 恢复发生在 patch 上下文内，此时应已还原
            import sys

            assert sys.argv == original_argv

        assert captured["path"].endswith("seed_data.py")
        # 执行期间 argv 只剩目标脚本自身，不带 "seed"
        assert captured["argv_during"] == [captured["path"]]
