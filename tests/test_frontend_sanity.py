"""前端静态资源防回归检查。

背景（会话 9/10 修复的两类真实缺陷，HTTP 200 无法暴露）：
1. 页面内联脚本重复声明 common.js 的全局 const（如 TYPE_LABELS）→ 浏览器抛
   SyntaxError，整段内联脚本失效，所有按钮函数未注册；
2. 共享组件注入的表单 id 与页面静态 id 冲突（bank-form 的 n-title 撞上
   my.html 建问卷表单）→ getElementById 按文档序取错元素，提交数据错乱。

本文件用 Flask 测试客户端拉取页面与脚本做静态检查，锁住这两类回归；
页面交互行为仍由 docs/测试用例.md 的手工用例（H 系列）覆盖。
"""
import re

PAGES = ["index.html", "my.html", "edit.html", "fill.html", "stats.html",
         "bank.html", "question.html", "qstats.html"]
ASSETS = ["common.js", "bank-form.js", "style.css"]

# common.js 中的全局名：页面内联脚本再声明其中任何一个都会让整段脚本失效
COMMON_GLOBALS = {"TYPE_LABELS", "api", "esc", "showMsg", "logout", "fmtTime"}

EXTERNAL_SCRIPT = re.compile(r'<script[^>]*\bsrc="([^"]+)"[^>]*>\s*</script>')
INLINE_SCRIPTS = re.compile(r"<script>(.*?)</script>", re.S)
DECL = re.compile(r"(?:^|\n)\s*(?:const|let|function)\s+([A-Za-z_$][\w$]*)")
ID_DEF = re.compile(r'id="([\w-]+)"')
GET_ELEM = re.compile(r'getElementById\("([\w-]+)"\)')
JS_COMMENTS = re.compile(r"/\*.*?\*/", re.S)


def _inline_declarations(html):
    html = EXTERNAL_SCRIPT.sub("", html)
    names = []
    for block in INLINE_SCRIPTS.findall(html):
        names.extend(DECL.findall(block))
    return names


def _static_html(html):
    """去掉内联脚本，只留静态标记（JS 模板里的 id 是运行时按需渲染的）。"""
    return INLINE_SCRIPTS.sub("", EXTERNAL_SCRIPT.sub("", html))


class TestStaticAssetsServed:
    def test_all_pages_and_assets_served(self, client):
        for path in PAGES + ASSETS:
            r = client.get(f"/{path}")
            assert r.status_code == 200, f"{path} -> {r.status_code}"
            assert len(r.data) > 0

    def test_pages_reference_existing_scripts(self, client):
        for path in PAGES:
            html = client.get(f"/{path}").get_data(as_text=True)
            for src in EXTERNAL_SCRIPT.findall(html):
                r = client.get(src)
                assert r.status_code == 200, f"{path} 引用的 {src} 不可用"


class TestNoGlobalRedeclaration:
    def test_inline_scripts_do_not_redeclare_common_globals(self, client):
        """内联脚本声明 common.js 已有的全局名 = 浏览器整段脚本失效。"""
        for path in PAGES:
            html = client.get(f"/{path}").get_data(as_text=True)
            clash = set(_inline_declarations(html)) & COMMON_GLOBALS
            assert not clash, f"{path} 内联脚本重复声明了全局名: {clash}"


class TestIdIntegrity:
    def test_no_duplicate_static_ids_per_page(self, client):
        for path in PAGES:
            html = client.get(f"/{path}").get_data(as_text=True)
            ids = ID_DEF.findall(_static_html(html))
            dupes = {i for i in ids if ids.count(i) > 1}
            assert not dupes, f"{path} 存在重复静态 id: {dupes}"

    def test_bank_form_component_uses_isolated_ids(self, client):
        """共享组件的 id 必须全部 bf- 前缀，且不得引用页面的 n- 系列 id。"""
        js = JS_COMMENTS.sub("", client.get("/bank-form.js").get_data(as_text=True))
        created = ID_DEF.findall(js)
        assert created, "bank-form.js 未注入任何表单 id"
        non_bf = [i for i in created if not i.startswith("bf-")]
        assert not non_bf, f"组件存在非 bf- 前缀的注入 id（会与页面冲突）: {non_bf}"
        legacy = [i for i in GET_ELEM.findall(js) if not i.startswith("bf-")
                  and i not in ("bank-form", "bank-tbody")]
        # bank-form/bank-tbody 是组件的挂载点与页面列表容器，属约定契约
        assert not legacy, f"组件引用了非 bf- 的表单 id: {legacy}"

    def test_bank_form_pages_have_mount_point(self, client):
        """引入组件的页面必须恰好一个挂载点并调用 mountBankForm。"""
        for path in ("my.html", "bank.html"):
            html = client.get(f"/{path}").get_data(as_text=True)
            assert 'src="/bank-form.js"' in html, f"{path} 未引入 bank-form.js"
            assert html.count('id="bank-form"') == 1, f"{path} 缺少/重复挂载点"
            assert "mountBankForm(" in html, f"{path} 未调用 mountBankForm"


class TestPageContracts:
    def test_my_page_embeds_bank_card(self, client):
        """「我的问卷」页内嵌我的题库卡片（会话 9 的需求）。"""
        html = client.get("/my.html").get_data(as_text=True)
        for marker in ('id="bank-tbody"', "创建备选题", "我的题库"):
            assert marker in html, f"my.html 缺少题库卡片要素: {marker}"

    def test_edit_page_keeps_bank_picker(self, client):
        """编辑页保留「从题库选题」入口与题目升级操作。"""
        html = client.get("/edit.html").get_data(as_text=True)
        for marker in ('id="bank-picker"', "从题库选题", "upgradeQ"):
            assert marker in html, f"edit.html 缺少题库联动要素: {marker}"
