"""API 层：SSE 端点冒烟测试（依赖注入覆盖，不联网）。

依赖 get_chat_deps 被覆盖为 Fake LLM + Stub 搜索，因此请求不会触达任何真实服务。
"""

import json
import re

import pytest
from httpx import ASGITransport, AsyncClient

from knowledge_pilot.api import main as api_main
from knowledge_pilot.api.main import ChatDeps, app, get_chat_deps
from knowledge_pilot.memory import create_memory_store
from knowledge_pilot.search.stub import StubSearchProvider

from tests.fakes import FakeChatClient


def _override_deps(script=None, *, mode="loop"):
    """覆盖依赖并显式指定 agent_mode（默认 loop：这些是 Phase 0-2 循环行为测试）。"""
    llm = FakeChatClient(script=script or [(["接口测试回答。"], [])])
    if mode == "graph":
        llm.complete_script = [
            '{"steps": [{"title": "A", "question": "子问题A", "purpose": "p"}]}',
            '{"sufficient": true, "reason": "够", "gap": ""}',
            "# 报告",
        ]
    app.dependency_overrides[get_chat_deps] = lambda: ChatDeps(
        llm=llm, search=StubSearchProvider()
    )
    return llm


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_index_served():
    # Phase 9 起 `/` 是**图谱主导的新主页**（原先的单页聊天被替换），所以断言从
    # "Research Chat" 换成新主页的标识与它依赖的前端契约。`/api/chat` 端点本身
    # 仍然存在（见下方 test_chat_*），页面退役 ≠ 端点退役。
    #
    # Phase 10 改动：前端拆成了多文件，`/api/learning/topics` 这个字符串从
    # index.html **搬进了 js/api.js**。断言因此改成**顺着引用走**：
    # 保持原有强度（"页面确实依赖那个端点"），只是把查找范围从「本文件」扩到
    # 「本文件 + 它引用的所有同源资源」。位置变了，契约没变。
    async with await _client() as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "KnowledgePilot" in resp.text
        blob = resp.text
        for ref in _local_refs(resp.text):
            sub = await client.get(ref)
            assert sub.status_code == 200, f"{ref} 应当可访问（引用它的是 /）"
            blob += sub.text
    assert "/api/learning/topics" in blob


async def test_index_is_offline_self_contained():
    """铁律：前端零依赖、离线可跑——页面不得引用任何外部资源。

    这条很容易被顺手破坏（加一个字体 CDN、一个图标库就废了整条约束），而损坏时
    页面在联网的机器上看起来完全正常，只在断网/内网环境才白屏。所以用测试钉住。

    Phase 10 改动：原断言 `"<link" not in body` / `not re.search(r"\\ssrc=", body)`
    针对的正是**被拆分这件事本身**（单文件里不该有外链）。现在有了 `css/` 与
    `js/`，这两个断言必然失效。**但不降级成「不检查」**——重写成「允许引用，但
    只允许同源相对资源」，并保留 `https?://` 与 `@import` 两条零容忍断言。
    逐文件递归检查见 `test_all_assets_offline_clean`。
    """
    async with await _client() as client:
        resp = await client.get("/")
    body = resp.text
    assert not re.search(r"https?://", body)        # 无任何绝对 URL
    assert "@import" not in body
    for ref in _refs(body):
        assert _is_local_ref(ref), f"{ref} 不是同源相对资源"


# ---- Phase 10：多文件前端的资源装配 ---------------------------------------
#
# 拆分引入了一类**运行时静默**的失败：文件名打错只会让页面白屏/少一块样式，
# 控制台里没有任何报错（浏览器对 404 的脚本只是不发警告）。所以这一组测试
# 的价值全在「把打错字变成红色断言」。

WEB_DIR = api_main.WEB_DIR


def _refs(html: str) -> list[str]:
    """index.html 里所有 `href=` / `src=` 的值，保持文档顺序。"""
    return re.findall(r'(?:href|src)\s*=\s*"([^"]+)"', html, re.IGNORECASE)


def _is_local_ref(ref: str) -> bool:
    """同源相对资源：`/static/...` 或 `js/x.js` 这类相对路径。

    明确排除 `//host`（协议相对）、`http:`/`https:`/`data:`/`mailto:`/`#`。
    """
    if ref.startswith("//") or ref.startswith("#"):
        return False
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", ref):   # 带 scheme 的一律拒绝
        return False
    return True


def _local_refs(html: str) -> list[str]:
    return [r for r in _refs(html) if _is_local_ref(r)]


async def test_index_assets_all_resolve():
    """每个被引用的 css/js 都必须能 200 拿到内容。

    这是拆分后**最该有**的一条：文件名打错不会有任何运行时错误。
    """
    async with await _client() as client:
        index = await client.get("/")
        refs = [r for r in _local_refs(index.text) if not r.startswith("/api")]
        assert refs, "index.html 没有引用任何资源？拆分被回退了？"
        for ref in refs:
            resp = await client.get(ref)
            assert resp.status_code == 200, f"{ref} 取不到（引用它的是 /）"
            assert resp.text.strip(), f"{ref} 是空文件"


async def test_index_refs_are_same_origin():
    """不许出现 `//cdn...`、`http:`、`data:` 这类引用。"""
    async with await _client() as client:
        index = await client.get("/")
    for ref in _refs(index.text):
        assert _is_local_ref(ref), f"{ref} 不是同源相对资源"
        assert not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", ref)


async def test_all_assets_offline_clean():
    """递归到每个 css/js，剥掉注释后按**引用语义**扫描外部依赖。

    先剥注释是必须的：文件头写一句「不要引 CDN：https://... 那样会…」就会被
    朴素的正则扫成违规，于是第一个写文档的人弄红整套测试。剥注释后仍按引用
    语义查（HTML 的 `src=`/`href=`、CSS 的 `@import`/`url(`、JS 里 fetch 的绝对
    URL 字面量），既不误伤文档，也不放过真的外链。
    """
    async with await _client() as client:
        index = await client.get("/")
        for ref in [r for r in _local_refs(index.text) if not r.startswith("/api")]:
            body = (await client.get(ref)).text
            clean = _strip_comments(body, ref)
            assert "@import" not in clean, f"{ref} 用了 @import"
            for url in re.findall(r"url\(\s*['\"]?([^'\")]+)", clean):
                if url.startswith("#"):        # `url(#arrow)` 是 SVG 内部引用，不是资源
                    continue
                assert _is_local_ref(url), f"{ref} 里 url({url}) 不是同源资源"
            for url in re.findall(r"""fetch\(\s*["']([^"']+)["']""", clean):
                assert _is_local_ref(url) and url.startswith("/"), (
                    f"{ref} 里 fetch('{url}') 不是站内绝对路径"
                )
            assert not re.search(r"https?://", clean), f"{ref} 含绝对 URL"


def _strip_comments(text: str, ref: str) -> str:
    """按扩展名剥注释。剥错的风险是「漏掉一条真外链」而非误报，所以宁可保守。"""
    if ref.endswith(".html"):
        return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    if ref.endswith(".css"):
        return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # JS：行注释 + 块注释。字符串里的 `//` 会被误剥，但那种误剥只会让扫描更宽松，
    # 不会产生假阳性——真外链不会恰好藏在被误剥的片段里。
    out = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", out)


async def test_index_declares_expected_modules():
    """index.html 的 `<script src>` 有序列表 == 预期列表。

    挡住的情况：改了 index.html 但没跑 JS 测试；或者（更常见）新加了一个 js 文件
    却忘了挂上去 —— 那种文件在源码树里存在、测试也「通过」，只有浏览器里少功能。
    """
    async with await _client() as client:
        index = await client.get("/")
    got = re.findall(r'<script\b[^>]*\bsrc\s*=\s*"([^"]+)"', index.text)
    assert got == [
        "/static/js/util.js",
        "/static/js/dom.js",
        "/static/js/icons.js",
        "/static/js/sse.js",
        "/static/js/markdown.js",
        "/static/js/store.js",
        "/static/js/chat.js",
        "/static/js/graph/layout.js",
        "/static/js/graph/canvas.js",
        "/static/js/api.js",
        "/static/js/resizer.js",
        "/static/js/views/shell.js",
        "/static/js/views/graph.js",
        "/static/js/views/inspector.js",
        "/static/js/views/assistant.js",
        "/static/js/views/chat.js",
        "/static/js/views/settings.js",
        "/static/js/app.js",
    ]


async def test_index_has_no_inline_script_or_style():
    """钉住「拆分已完成」，防回流成单文件。

    内联脚本/样式一旦回来，`tests/js/` 的 Node 沙箱就再也覆盖不到那部分代码，
    而它照样能在浏览器里跑 —— 于是测试覆盖率静默下降。
    """
    async with await _client() as client:
        index = await client.get("/")
    body = index.text
    assert "<style" not in body.lower()
    assert not re.search(r"<script(?![^>]*\bsrc=)", body, re.IGNORECASE)


async def test_chat_page_fills_the_middle_column():
    """「对话」页的中间列**不许再有固定宽度**,滚动在整列那一层,输入框吸底。

    这是一条**回退哨**。此前 `.research` 上写着 `max-width: 860px; margin: 0 auto`：
    宽屏上正文缩成中间一条、两侧大片空着，而滚动发生在消息框那一小块里。用户报的原话
    是「聊天框太小，并未占全整个中间的屏幕……网页下拉没有办法延续补充」。这两件事
    **不会让任何测试变红** —— CSS 不参与任何断言（没有浏览器自动化），所以只能扫源码
    把它钉住；与同文件里 `test_index_has_no_inline_script_or_style` 是同一类。

    它挡不住的：断点行为、具体像素、`position: sticky` 在某个浏览器里的真实表现 ——
    那些只有真的在浏览器里过一遍才算数（与前端其余部分一样）。
    """
    async with await _client() as client:
        css = _strip_comments((await client.get("/static/css/chat.css")).text, ".css")

    col = _css_rule(css, ".research")
    assert "max-width" not in col, "`.research` 又有固定宽度了 —— 中间列会被缩成中间一条"
    assert "overflow-y: auto" in col, "滚动回到别处去了 —— 要的是「整列一起滚下去接着读」"

    composer = _css_rule(css, ".r-composer")
    assert "position: sticky" in composer and "bottom: 0" in composer, (
        "输入框不再是吸底的 —— 内容一长它就被顶下去，而用户明确要的是吸在底部"
    )


async def test_assistant_is_docked_into_the_middle_column():
    """助手是**中间列底部的停靠条**，不是浮在左下角的卡片（走查反馈 ①）。

    回退哨。用户报的两件事都是 CSS 造成的，而 CSS 不参与任何断言：「学习对话框固定
    在左下角 会影响观感」（旧版 `position: fixed; left/bottom: 18px`）与「关闭后没有
    办法再次打开」（收起态是一颗漂在画布上、只写着节点名的小胶囊 —— 它读起来像个
    标签而不是按钮，用户找不到回来的路）。这两条都不会让任何测试变红。

    现在它是 `<main>` 这个 flex 列里的第二个孩子：画布 `flex: 1` 吃掉其余高度，
    它 `flex: 0 0 auto` —— 于是「画布让出一块地方给它」是布局本身保证的，不需要
    z-index，也不可能盖住任何东西。收起态则是横铺满整列的一条栏，右侧固定挂着
    「展开 ▲」。

    它挡不住的：具体高度、在真机上「4 成高」的手感 —— 与前端其余部分一样，只有
    真的在浏览器里过一遍才算数。
    """
    async with await _client() as client:
        css = _strip_comments((await client.get("/static/css/assistant.css")).text, ".css")

    host = _css_rule(css, "#assistant", where="assistant.css")
    assert "position: fixed" not in host, "助手又浮起来了 —— 它会重新压住侧栏与画布"
    assert "flex: 0 0 auto" in host, (
        "它必须是 `<main>` 这个 flex 列里的定高子项（画布 flex: 1 吃掉其余高度）"
    )
    # `display: flex` 会盖掉浏览器给 `[hidden]` 的默认 `display: none` ——
    # 少了这一条，`views/assistant.js` 在「对话」页写的那句 `el.hidden = true`
    # **完全不起作用**（页面照旧显示一条空横线）。
    assert "display: none" in _css_rule(css, "#assistant[hidden]", where="assistant.css"), (
        "`#assistant[hidden]` 的显示开关没了 —— 对话页上会留一条空横线"
    )
    bar = _css_rule(css, ".as-launch", where="assistant.css")
    assert "width: 100%" in bar, "收起态退回了「一颗胶囊」—— 用户会找不到重新打开的路"


async def test_panel_divider_is_one_line_on_the_column_edge():
    """拖拽命中区与用户看到的那条线**是同一条**（走查反馈 ②，第四轮补上后半条）。

    旧版：线只在悬停时出现，而 `#sidebar` / `#panel` 各自另有一条静态
    `border-right` / `border-left` —— 接缝上同时有两样东西。用户报的是「可调整的线
    与实际呈现的线存在一定的距离」，说的就是这两条对不上（画布自己的滚动条就在它
    左边 9px 处，那条灰条看起来更像「线」）。

    第三轮只修了**一半**，所以用户又报了一次（右栏那条偏左 9px）。这条几何是两级的，
    而当时只钉了第一级：

    1. **线居中在命中区里** —— `.col-resizer` 9px、`::after` 在 `left: 4px` + 1px 宽；
    2. **命中区居中在网格边界上** —— `#rz-side` 用 `left: var(--side-w)` 配
       `translateX(-50%)` 是对的；`#rz-insp` 用 `right: var(--insp-w)`（量的是**右边缘**
       到边界的距离）却抄了同一个 `-50%`，于是整个命中区连里面那条线一起多推了 9px。

    第二级现在**算出来**断言，不再靠肉眼核对：`left` 锚定要 `shift + hit/2 == 0`，
    `right` 锚定要 `shift - hit/2 == 0`。
    """
    async with await _client() as client:
        layout = _strip_comments((await client.get("/static/css/layout.css")).text, ".css")
        sidebar = _strip_comments((await client.get("/static/css/sidebar.css")).text, ".css")
        inspector = _strip_comments((await client.get("/static/css/inspector.css")).text, ".css")

    hit = _px(_css_rule(layout, ".col-resizer", where="layout.css"), "width")
    line = _css_rule(layout, ".col-resizer::after", where="layout.css")
    assert _px(line, "left") + _px(line, "width") / 2 == hit / 2, (
        "常驻的那条线没居中在命中区里 —— 它会与网格边界错开，就又是「两条线对不上」"
    )

    for selector, var in (("#rz-side", "--side-w"), ("#rz-insp", "--insp-w")):
        rule = _css_rule(layout, selector, where="layout.css")
        anchor = re.search(
            r"^\s*(left|right):\s*var\(" + re.escape(var) + r"\)", rule, re.MULTILINE
        )
        assert anchor, f"{selector} 不再贴着 `var({var})` 锚定 —— 这条几何断言失去前提了"
        shift = re.search(r"translateX\(\s*(-?[\d.]+)%\s*\)", rule)
        assert shift, f"{selector} 没有位移，命中区不会落在分界上"
        offset = float(shift.group(1)) / 100 * hit
        # `right` 锚定下盒子的右边缘贴着边界，所以偏移方向与 `left` 相反。
        center = offset + hit / 2 if anchor.group(1) == "left" else offset - hit / 2
        assert abs(center) < 0.01, (
            f"{selector} 的命中区中心离网格边界 {center}px ——"
            "「能拖的那条线」与「真正的分界」又错开了（第三轮就是漏了这半条）"
        )

    # 栏自己的静态边框撤掉了。**唯一的例外**是 ≤1000px：那一档右栏变成浮层、
    # `#rz-insp` 已隐藏，浮层左边缘需要它自己的一条边框（见 layout.css 末尾）。
    assert "border-right" not in _css_rule(sidebar, "#sidebar", where="sidebar.css")
    assert "border-left" not in _css_rule(inspector, "#panel", where="inspector.css")


async def test_inspector_status_section_is_centered():
    """右栏「学习状态」段是居中的（走查反馈 ②的后半句：「贴齐左边 不符合居中」）。

    这一段只有一行状态徽标加一个按钮，左边齐平会让它读成「没写完的表单」。
    居中写在 `.sec-status` 上（子项继承），按钮则铺满整段宽度。
    """
    async with await _client() as client:
        css = _strip_comments((await client.get("/static/css/inspector.css")).text, ".css")
    status = _css_rule(css, ".sec-status", where="inspector.css")
    assert "text-align: center" in status, "「学习状态」段又贴齐左边了"
    assert "width: 100%" in _css_rule(css, ".sec-status .btn", where="inspector.css")


def _css_rule(css: str, selector: str, *, where: str = "css") -> str:
    """取一条 CSS 规则的声明块。**不支持嵌套规则** —— 这几条都没有嵌套，够用。

    注意 `[^}]*` 的含义：`@media` 里的规则照样能取到（外层块的 `}` 在更后面），
    但取到的**永远是第一个**匹配 —— 所以断言写在 `_strip_comments` 之后的文本上时，
    文件顺序就是语义的一部分。
    """
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"{where} 里找不到 `{selector}` 规则（改名了？那这条回退哨就空了）"
    return m.group(1)


def _px(block: str, prop: str) -> float:
    """取一条声明里的 px 数。找不到就**当场失败**而不是返回 0 —— 返回 0 会让几何断言
    在解析失败时"恰好"通过或给出一个看不懂的差值。"""
    m = re.search(r"(?<![\w-])" + re.escape(prop) + r":\s*(-?[\d.]+)px", block)
    assert m, f"`{prop}` 不是 px 值（或改名了），解析器该跟着改：{block!r}"
    return float(m.group(1))


async def test_static_asset_content_types():
    """css → text/css；js → 某个 JS MIME。

    两种 JS MIME 都放行，是因为 mime 映射来自运行环境的 `mimetypes`（Starlette 的
    `guess_type`），本机实测给的是 `application/javascript`，而部分平台/配置给
    `text/javascript`。两者都会被浏览器当脚本执行，钉死一个等于让测试依赖机器。
    真正要挡的是「js 被当成 text/plain 或 application/octet-stream 下发」——
    那在浏览器里是**静默不执行**（控制台一条 MIME 报错，页面永远停在初始化前）。
    """
    async with await _client() as client:
        css = await client.get("/static/css/tokens.css")
        js = await client.get("/static/js/util.js")
    assert css.headers["content-type"].startswith("text/css")
    assert js.headers["content-type"].split(";")[0] in (
        "text/javascript", "application/javascript",
    )


async def test_static_mount_does_not_expose_repo():
    """静态挂载只暴露 `web/`——绝不能顺着 `..` 摸到项目根。

    挂错一层（例如 `StaticFiles(directory=REPO_ROOT)`）会把 `.env`（含 API key）
    和 `data/learning.db`（全部学习记录）变成可下载的 URL。这是本 Phase 里唯一
    一条「出错即泄漏」的改动，所以用路径穿越用例正面钉它。
    """
    async with await _client() as client:
        for path in ("/static/%2e%2e%2f.env", "/static/..%2f.env",
                     "/static/%2e%2e%2fdata/learning.db",
                     "/static/%2e%2e%2fknowledge_pilot%2fconfig.py"):
            resp = await client.get(path)
            assert resp.status_code != 200, f"{path} 竟然可访问（静态挂载越界）"


async def test_chat_streams_events(monkeypatch):
    monkeypatch.setattr(api_main.settings, "agent_mode", "loop")
    _override_deps()
    try:
        async with await _client() as client:
            async with client.stream(
                "POST", "/api/chat", json={"message": "你好"}
            ) as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")

                frames = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        frames.append("[DONE]" if data == "[DONE]" else json.loads(data))
    finally:
        app.dependency_overrides.clear()

    assert frames[-1] == "[DONE]"
    types = [f["type"] for f in frames[:-1]]
    assert types == ["token", "done"]
    content = "".join(
        f["content"] for f in frames[:-1] if f.get("type") == "token"
    )
    assert "接口测试回答" in content


async def test_chat_streams_tool_events(monkeypatch):
    monkeypatch.setattr(api_main.settings, "agent_mode", "loop")
    llm = _override_deps(script=[
        ([], [{"name": "search_web", "arguments": '{"query": "测试"}'}]),
        (["基于搜索的答案。"], []),
    ])
    try:
        async with await _client() as client:
            async with client.stream(
                "POST", "/api/chat", json={"message": "搜索一下"}
            ) as resp:
                assert resp.status_code == 200
                frames = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        frames.append("[DONE]" if data == "[DONE]" else json.loads(data))
    finally:
        app.dependency_overrides.clear()

    types = [f["type"] for f in frames if f != "[DONE]"]
    assert types == ["tool_call", "tool_result", "token", "done"]
    assert llm.calls == 2


async def test_chat_closes_rag_after_stream(monkeypatch):
    """流结束后 RAGPipeline.close() 被调用（清理 task_{uuid} 临时知识库）。"""
    monkeypatch.setattr(api_main.settings, "agent_mode", "loop")
    closed = []

    class _FakeRag:
        def close(self):
            closed.append(True)

    llm = FakeChatClient(script=[(["接口测试回答。"], [])])
    app.dependency_overrides[get_chat_deps] = lambda: ChatDeps(
        llm=llm, search=StubSearchProvider(), rag=_FakeRag()
    )
    try:
        async with await _client() as client:
            async with client.stream(
                "POST", "/api/chat", json={"message": "你好"}
            ) as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    pass
    finally:
        app.dependency_overrides.clear()

    assert closed == [True]


async def test_chat_requires_api_key(monkeypatch):
    """未配置密钥时返回清晰错误，而不是神秘的 500。"""
    monkeypatch.setattr(api_main.settings, "deepseek_api_key", "")
    async with await _client() as client:
        resp = await client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 500
    assert "DEEPSEEK_API_KEY" in resp.json()["detail"]


async def test_chat_graph_mode_streams_plan_eval_done(monkeypatch):
    """默认 graph 模式：SSE 帧含 plan / eval / done（LangGraph 编排路径）。"""
    monkeypatch.setattr(api_main.settings, "agent_mode", "graph")
    llm = _override_deps(mode="graph")
    try:
        async with await _client() as client:
            async with client.stream(
                "POST", "/api/chat", json={"message": "研究 RAG chunking"}
            ) as resp:
                assert resp.status_code == 200
                frames = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        frames.append("[DONE]" if data == "[DONE]" else json.loads(data))
    finally:
        app.dependency_overrides.clear()

    assert frames[-1] == "[DONE]"
    types = [f["type"] for f in frames[:-1]]
    assert "plan" in types
    assert "eval" in types
    assert types[-1] == "done"
    # 综合报告作为 done 帧内容
    assert frames[-2]["content"] == "# 报告"
    # planner + evaluate + synthesize = 3 次非流式调用
    assert llm.complete_calls == 3


def test_sse_frame_maps_memory_event():
    """_sse_frame 正确编码 MemoryEvent（前端依赖该协议）。"""
    from knowledge_pilot.agent.events import MemoryEvent
    from knowledge_pilot.api.main import _sse_frame

    frame = _sse_frame(MemoryEvent(found=2))
    assert json.loads(frame[len("data: "):]) == {"type": "memory", "found": 2}


def test_sse_frame_maps_kg_event():
    """_sse_frame 正确编码 KgEvent（前端依赖该协议）。"""
    from knowledge_pilot.agent.events import KgEvent
    from knowledge_pilot.api.main import _sse_frame

    frame = _sse_frame(KgEvent(entities=3, relations=2, found_triples=1))
    assert json.loads(frame[len("data: "):]) == {
        "type": "kg", "entities": 3, "relations": 2, "found_triples": 1,
    }


async def test_chat_graph_kg_event_frame(monkeypatch):
    """KG 启用时：graph 模式 SSE 流含 kg 帧。"""
    monkeypatch.setattr(api_main.settings, "agent_mode", "graph")
    monkeypatch.setattr(api_main.settings, "kg_enabled", True)
    llm = FakeChatClient(script=[
        ([], [{"name": "search_web", "arguments": '{"query": "资料"}'}]),
        (["完成"], []),
    ])
    llm.complete_script = [
        '{"steps": [{"title": "A", "question": "子问题A", "purpose": "p"}]}',
        '{"sufficient": true, "reason": "够", "gap": ""}',
        '{"entities": [{"name": "RAG", "type": "concept"}], "relations": []}',
        "# 报告",
    ]
    app.dependency_overrides[get_chat_deps] = lambda: ChatDeps(
        llm=llm, search=StubSearchProvider()
    )
    try:
        async with await _client() as client:
            async with client.stream(
                "POST", "/api/chat", json={"message": "RAG"}
            ) as resp:
                assert resp.status_code == 200
                frames = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        frames.append("[DONE]" if data == "[DONE]" else json.loads(data))
    finally:
        app.dependency_overrides.clear()

    kg_frames = [f for f in frames if f != "[DONE]" and f.get("type") == "kg"]
    assert kg_frames
    assert kg_frames[0]["entities"] == 1
    assert kg_frames[0]["relations"] == 0
    assert kg_frames[0]["found_triples"] == 0  # 1 实体 0 关系 → 无三元组
    assert frames[-1] == "[DONE]"


async def test_chat_graph_memory_event_frame(monkeypatch, tmp_path):
    """Memory 启用且召回历史时：SSE 流含 memory 帧。"""
    monkeypatch.setattr(api_main.settings, "agent_mode", "graph")
    monkeypatch.setattr(
        api_main.settings, "memory_checkpoint_db_path", str(tmp_path / "graph.db")
    )
    store = create_memory_store(str(tmp_path / "memory.db"))
    store.save_run("chunking 策略", plan=[], evidence=[], report="fixed 与 recursive 对比", sources=[])

    llm = FakeChatClient(script=[(["完成"], [])])
    llm.complete_script = [
        '{"steps": [{"title": "A", "question": "子问题A", "purpose": "p"}]}',
        '{"sufficient": true, "reason": "够", "gap": ""}',
        "# 报告",
    ]
    app.dependency_overrides[get_chat_deps] = lambda: ChatDeps(
        llm=llm, search=StubSearchProvider(), memory=store
    )
    try:
        async with await _client() as client:
            async with client.stream(
                "POST", "/api/chat", json={"message": "比较 chunking 策略"}
            ) as resp:
                assert resp.status_code == 200
                frames = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        frames.append("[DONE]" if data == "[DONE]" else json.loads(data))
    finally:
        app.dependency_overrides.clear()
        store.close()

    types = [f["type"] for f in frames if f != "[DONE]"]
    assert "memory" in types
    assert frames[-1] == "[DONE]"


async def test_chat_graph_mcp_mode_streams_tool_and_notes(monkeypatch, tmp_path):
    """MCP 启用（graph 模式）：真实 stdio 子进程网关；search_memory 的 tool_call 帧
    出现在 SSE 流、其文本结果经 notes 落进 synthesize 输入。需要 mcp 已装（随 base）。

    search_papers（arXiv）会联网，故这里只驱动只读本地库的 search_memory——
    papers 的 schema / 真实调用分别由 test_mcp_servers_stdio 与手工冒烟覆盖。
    """
    pytest.importorskip("mcp")  # B 轨：缺 mcp 依赖时干净跳过（不联网、需 langgraph）
    monkeypatch.setattr(api_main.settings, "agent_mode", "graph")
    monkeypatch.setattr(api_main.settings, "mcp_enabled", True)
    monkeypatch.setattr(api_main.settings, "kg_enabled", False)
    monkeypatch.setattr(api_main.settings, "memory_enabled", False)

    # 预置一份研究历史，供真实 memory server 的 search_memory 命中（只读本地库）。
    db_path = str(tmp_path / "memory.db")
    store = create_memory_store(db_path)
    store.save_run("RAG chunking 策略", report="fixed 与 recursive 对比", sources=[])
    store.close()
    monkeypatch.setattr(api_main.settings, "memory_db_path", db_path)

    llm = FakeChatClient(script=[
        ([], [{"name": "search_memory", "arguments": '{"query": "RAG chunking"}'}]),
        (["完成"], []),
    ])
    llm.complete_script = [
        '{"steps": [{"title": "A", "question": "子问题A", "purpose": "p"}]}',
        '{"sufficient": true, "reason": "够", "gap": ""}',
        "# 报告",
    ]
    app.dependency_overrides[get_chat_deps] = lambda: ChatDeps(
        llm=llm, search=StubSearchProvider()
    )
    try:
        async with await _client() as client:
            async with client.stream(
                "POST", "/api/chat", json={"message": "研究 RAG chunking"}
            ) as resp:
                assert resp.status_code == 200
                frames = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        frames.append("[DONE]" if data == "[DONE]" else json.loads(data))
    finally:
        app.dependency_overrides.clear()
        # Phase 8：网关常驻进程级单例——测试必须显式复位，否则子进程跨用例存活、
        # 在 Windows 上常驻 tmp db 文件句柄（pytest 清理 tmp_path 会 PermissionError）。
        from knowledge_pilot.mcp.runtime import get_mcp_runtime

        await get_mcp_runtime().reset()

    types = [f["type"] for f in frames if f != "[DONE]"]
    assert "tool_call" in types
    tool_calls = [f for f in frames if f != "[DONE]" and f.get("type") == "tool_call"]
    assert any(tc.get("name") == "search_memory" for tc in tool_calls)
    assert frames[-1] == "[DONE]"

    # MCP 文本结果经 notes 进 synthesize 输入（done 内容是脚本固定的 REPORT）
    synthesize_msg = next(
        msg for msg in llm.seen_messages
        if msg[0]["role"] == "system" and "研究报告撰写员" in msg[0]["content"]
    )
    content = synthesize_msg[1]["content"]
    assert "工具补充资料（MCP，非网页搜索来源，仅供补充参考，不需要时可不引用）" in content
    assert "[工具 search_memory]" in content


async def test_chat_survives_mcp_runtime_failure(monkeypatch):
    """常驻网关启动失败（ensure 抛错）→ 退回无 MCP 路径，研究照常完成（不 500）。"""
    model = pytest.importorskip("langgraph")  # graph 路径需 langgraph（B 轨）
    del model
    monkeypatch.setattr(api_main.settings, "agent_mode", "graph")
    monkeypatch.setattr(api_main.settings, "mcp_enabled", True)

    import knowledge_pilot.mcp.runtime as runtime_mod

    class _BoomRuntime:
        async def ensure(self, specs):  # noqa: ANN001
            raise RuntimeError("常驻网关启动失败")

        async def refresh(self):
            raise AssertionError("ensure 失败时不应 refresh")

    monkeypatch.setattr(runtime_mod, "get_mcp_runtime", lambda: _BoomRuntime())

    llm = _override_deps(mode="graph")
    try:
        async with await _client() as client:
            async with client.stream(
                "POST", "/api/chat", json={"message": "研究 RAG"}
            ) as resp:
                assert resp.status_code == 200
                frames = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        frames.append("[DONE]" if data == "[DONE]" else json.loads(data))
    finally:
        app.dependency_overrides.clear()

    assert frames[-1] == "[DONE]"
    types = [f["type"] for f in frames[:-1]]
    assert "plan" in types and types[-1] == "done"  # 无 MCP 也走完 graph 流程
