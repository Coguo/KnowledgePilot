"""前端 JS 测试组:`node --test` 的 Python 入口。

**为什么不让 CI 直接跑 `node --test`:** 这套 Python 套件是「一条命令全绿」的
唯一入口。把 JS 测试留在外面,它就会变成「记得跑才跑」的东西,而前端有整整一类
**静默失败**(半帧缓冲被简化掉、先渲染后转义、少挂一个 script)是 pytest 完全
看不见的 —— 那些正是 `tests/js/` 里每一条用例对应的东西。

**为什么显式列文件而不是让 node 自己发现:** `node --test <目录>` 的发现规则
(`*.test.js` / `*-test.js` / `test-*.js` / `test/**`) 在 Node 各小版本之间有差异
(本机 v18.19.0 是较旧的一版)。在 Python 里 glob 出路径再逐个传,行为和 Node 版本
无关 —— 少一个文件是「测试没跑」,不是「测试通过」。

**为什么 node 不在就 skip:** 这是 Python 项目的 Python 测试套件。让没装 Node 的机器
上整套 pytest 变红,是把一件可选的事变成了阻塞。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
JS_TEST_DIR = REPO_ROOT / "tests" / "js"

# 时间上限。测试本身是毫秒级的(`node --test` 起停约占大头,本机约 1.5s);
# 给到 300s 是为了不让慢 CI 误报,而不是预期它会用到。
_TIMEOUT = 300


def _js_tests() -> list[Path]:
    """glob 出全部 `.test.mjs`,排序后返回绝对路径(不依赖 node 的目录发现规则)。"""
    return sorted(JS_TEST_DIR.glob("*.test.mjs"))


def test_js_test_files_exist():
    """至少要有测试文件 —— 防止「glob 出空列表 → 命令成功 → 假绿」。"""
    files = _js_tests()
    assert files, f"{JS_TEST_DIR} 下没有 *.test.mjs;这会让本模块的其余断言全部失去意义"
    # 四个核心纯函数组,缺一个都说明拆分回退了。
    names = {p.name for p in files}
    for required in ("sse.test.mjs", "markdown.test.mjs", "assembly.test.mjs", "store.test.mjs"):
        assert required in names, f"缺少 {required}"


def test_node_frontend_suite_passes():
    """按 index.html 的装配顺序加载前端并跑全部断言。"""
    node = shutil.which("node")
    if node is None:
        pytest.skip("本机没有 node;前端测试组跳过(不是失败)")

    files = [str(p) for p in _js_tests()]
    proc = subprocess.run(
        [node, "--test", *files],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_TIMEOUT,
        # Windows 上不加这个,`node` 会在一个新控制台窗口里跑,拿不到输出。
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    # 失败时把两段输出都带上 —— 只给 returncode 的话,定位要重跑一遍。
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"node --test 失败(exit {proc.returncode}):\n{out}"

    # `node --test` 在**没有跑到任何用例**时也可能返回 0(取决于版本与产物)。
    # 明确要求输出里出现通过计数,把「跑了但一条没执行」和真绿区分开。
    assert "fail 0" in out, f"输出里没有 `fail 0`,无法确认真的跑了用例:\n{out}"
