"""打包:前端资源必须真的进 wheel。

**为什么值得一条测试:** 拆分前 `web/index.html` 是单文件,`web/**` 不在
`package-data` 里 —— 但开发时没人会注意到,因为大家跑的是**源码树里的** uvicorn,
`StaticFiles` 直接读磁盘。只有 `pip install` 出来的 wheel 才会**少一整个前端**,
表现是首页 404 或白屏,而在开发机上永远复现不了。

同时钉住 setuptools glob 的那个坑:写 `web/**/*` 时,部分实现要求 `**/` 至少
匹配一层,于是**恰好漏掉 `web/index.html`**。所以这里逐个模式检查覆盖,
而不是只看「有没有 package-data 这个键」。
"""

from __future__ import annotations

import tomllib
from pathlib import Path, PurePosixPath

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
PACKAGE_DIR = REPO_ROOT / "knowledge_pilot"
WEB_DIR = PACKAGE_DIR / "web"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _package_data_patterns() -> list[str]:
    data = _pyproject().get("tool", {}).get("setuptools", {}).get("package-data", {})
    patterns = data.get("knowledge_pilot")
    assert patterns, (
        "pyproject.toml 里没有 [tool.setuptools.package-data] 的 knowledge_pilot 条目;"
        "wheel 里会缺掉整个前端"
    )
    return list(patterns)


def _web_files() -> list[PurePosixPath]:
    """`web/` 下全部资源,相对**包目录**(= package-data 模式的基准)。"""
    return sorted(
        PurePosixPath(p.relative_to(PACKAGE_DIR).as_posix())
        for p in WEB_DIR.rglob("*")
        if p.is_file()
    )


def test_web_assets_exist():
    """前置:web/ 下确实有东西。空目录会让下面的覆盖断言变成永真。"""
    files = _web_files()
    assert len(files) >= 20, f"web/ 下只有 {len(files)} 个文件,拆分像是没落盘"
    assert PurePosixPath("web/index.html") in files


def test_index_html_is_covered_by_some_pattern():
    """`web/index.html` 必须被某个模式覆盖 —— 这正是 `web/**/*` 会漏掉的那个。"""
    target = PurePosixPath("web/index.html")
    patterns = _package_data_patterns()
    assert any(target.match(p) for p in patterns), (
        f"没有模式覆盖 web/index.html(现有:{patterns})。"
        "写 `web/**/*` 时部分 setuptools 实现要求 `**/` 至少匹配一层,index.html 会被漏掉;"
        "用显式多模式。"
    )


def test_every_web_asset_is_covered():
    """逐个文件检查覆盖 —— 这就是「打 wheel 后用 unzip -l 确认」的静态版本。"""
    patterns = _package_data_patterns()
    uncovered = [str(f) for f in _web_files() if not any(f.match(p) for p in patterns)]
    assert not uncovered, (
        "这些前端资源不会被放进 wheel:\n  "
        + "\n  ".join(uncovered)
        + f"\n现有模式:{patterns}"
    )


def test_patterns_are_not_overbroad():
    """模式不许宽到把包里的 Python 源码也裹进去(那是 data 与 code 的边界)。"""
    for p in _package_data_patterns():
        assert not p.startswith("*.py"), f"模式 {p} 会匹配 Python 源码"
        assert PurePosixPath("__init__.py").match(p) is False, f"模式 {p} 覆盖了 Python 源码"


@pytest.mark.parametrize("sub", ["css", "js", "js/views", "js/graph"])
def test_subdirectories_are_covered(sub):
    """嵌套目录也要有真实文件且被覆盖 —— 少一层模式就少一整层资源。"""
    files = [f for f in _web_files() if f.parent.as_posix() == f"web/{sub}"]
    assert files, f"web/{sub}/ 下没有文件"
    patterns = _package_data_patterns()
    missing = [str(f) for f in files if not any(f.match(p) for p in patterns)]
    assert not missing, f"web/{sub}/ 有文件没被模式覆盖:{missing}"
