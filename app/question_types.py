"""题型注册表（策略模式）。

每种题型实现三个静态方法：
  normalize(payload)        -> dict        规范化题目配置（options/config），非法则抛 ApiError
  check_answer(q, v)        -> str | None  校验填写值，返回错误信息或 None
  normalize_value(q, v)     -> v           把填写值规范成入库形态

新增题型 = 新增一个类并注册到 QUESTION_TYPES，数据库结构零改动。
"""
from bson import ObjectId

from app.errors import ApiError
from app.utils import is_number

NUMBER_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "between"}


def _check_options(options):
    if not isinstance(options, list) or len(options) < 2:
        raise ApiError("选择题至少需要两个选项")
    seen, out = set(), []
    for o in options:
        if not isinstance(o, dict):
            raise ApiError("选项格式不正确")
        key = str(o.get("key") or "").strip()
        text = str(o.get("text") or "").strip()
        if not key or not text:
            raise ApiError("选项的 key 与文本都不能为空")
        if key in seen:
            raise ApiError(f"选项 key 重复：{key}")
        seen.add(key)
        out.append({"key": key, "text": text})
    return out


def _int_cfg(config, name, minimum=None):
    """读取可选的整数配置项；返回 None 表示未设置。"""
    v = (config or {}).get(name)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ApiError(f"{name} 必须是整数")
    if minimum is not None and v < minimum:
        raise ApiError(f"{name} 不能小于 {minimum}")
    return v


def _num_cfg(config, name):
    v = (config or {}).get(name)
    if v is None:
        return None
    if not is_number(v):
        raise ApiError(f"{name} 必须是数字")
    return v


class SingleChoice:
    @staticmethod
    def normalize(payload):
        return {"options": _check_options(payload.get("options")), "config": {}}

    @staticmethod
    def check_answer(q, value):
        if value is None:
            return "本题必答" if q.get("required") else None
        keys = {o["key"] for o in q.get("options") or []}
        if not isinstance(value, str) or value not in keys:
            return "选择了无效的选项"
        return None

    @staticmethod
    def normalize_value(q, v):
        return v


class MultiChoice:
    @staticmethod
    def normalize(payload):
        config = payload.get("config") or {}
        out = {}
        for name in ("minSelect", "maxSelect"):
            out[name] = _int_cfg(config, name, minimum=1)
        if out["minSelect"] is not None and out["maxSelect"] is not None \
                and out["minSelect"] > out["maxSelect"]:
            raise ApiError("minSelect 不能大于 maxSelect")
        return {"options": _check_options(payload.get("options")), "config": out}

    @staticmethod
    def check_answer(q, value):
        if value is None:
            return "本题必答" if q.get("required") else None
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            return "答案格式不正确（应为选项数组）"
        if len(set(value)) != len(value):
            return "选项不能重复"
        keys = {o["key"] for o in q.get("options") or []}
        if not set(value) <= keys:
            return "包含了无效的选项"
        if not value and q.get("required"):
            return "本题必答"
        cfg = q.get("config") or {}
        if value:
            if cfg.get("minSelect") and len(value) < cfg["minSelect"]:
                return f"至少需要选择 {cfg['minSelect']} 项"
            if cfg.get("maxSelect") and len(value) > cfg["maxSelect"]:
                return f"最多只能选择 {cfg['maxSelect']} 项"
        return None

    @staticmethod
    def normalize_value(q, v):
        return list(v)


class TextBlank:
    @staticmethod
    def normalize(payload):
        config = payload.get("config") or {}
        out = {
            "minLength": _int_cfg(config, "minLength", minimum=0),
            "maxLength": _int_cfg(config, "maxLength", minimum=0),
        }
        if out["minLength"] is not None and out["maxLength"] is not None \
                and out["minLength"] > out["maxLength"]:
            raise ApiError("minLength 不能大于 maxLength")
        return {"config": out}

    @staticmethod
    def check_answer(q, value):
        if value is None:
            return "本题必答" if q.get("required") else None
        if not isinstance(value, str):
            return "答案必须是文本"
        s = value.strip()
        if q.get("required") and not s:
            return "本题必答"
        cfg = q.get("config") or {}
        if s:
            if cfg.get("minLength") is not None and len(s) < cfg["minLength"]:
                return f"至少需要输入 {cfg['minLength']} 个字"
            if cfg.get("maxLength") is not None and len(s) > cfg["maxLength"]:
                return f"最多只能输入 {cfg['maxLength']} 个字"
        return None

    @staticmethod
    def normalize_value(q, v):
        return v.strip()


class NumberBlank:
    @staticmethod
    def normalize(payload):
        config = payload.get("config") or {}
        out = {
            "min": _num_cfg(config, "min"),
            "max": _num_cfg(config, "max"),
            "integer": bool(config.get("integer", False)),
        }
        if out["min"] is not None and out["max"] is not None and out["min"] > out["max"]:
            raise ApiError("min 不能大于 max")
        return {"config": out}

    @staticmethod
    def check_answer(q, value):
        if value is None:
            return "本题必答" if q.get("required") else None
        if not is_number(value):
            return "答案必须是数字"
        cfg = q.get("config") or {}
        if cfg.get("integer") and float(value) != int(value):
            return "必须是整数"
        if cfg.get("min") is not None and value < cfg["min"]:
            return f"不能小于 {cfg['min']}"
        if cfg.get("max") is not None and value > cfg["max"]:
            return f"不能大于 {cfg['max']}"
        return None

    @staticmethod
    def normalize_value(q, v):
        if (q.get("config") or {}).get("integer"):
            return int(v)
        return float(v)


QUESTION_TYPES = {
    "single_choice": SingleChoice,
    "multi_choice": MultiChoice,
    "text": TextBlank,
    "number": NumberBlank,
}


def get_handler(qtype):
    h = QUESTION_TYPES.get(qtype)
    if h is None:
        raise ApiError(f"不支持的题型：{qtype}")
    return h


def validate_question_payload(payload):
    """校验并规范化整份题目字段（新增/编辑共用）。"""
    qtype = payload.get("type")
    h = get_handler(qtype)
    title = str(payload.get("title") or "").strip()
    if not (1 <= len(title) <= 500):
        raise ApiError("题目标题长度需在 1~500 之间")
    fields = h.normalize(payload)
    rules, err = normalize_jump_rules(payload.get("jumpRules"))
    if err:
        raise ApiError(err)
    return {
        "type": qtype,
        "title": title,
        "required": bool(payload.get("required", False)),
        **fields,
        "jumpRules": rules,
    }


def normalize_jump_rules(rules):
    """校验跳转规则：统一 match(goto) 结构（见设计文档 2.3.3）。"""
    if rules in (None, []):
        return [], None
    if not isinstance(rules, list):
        return None, "跳转规则格式不正确"
    out = []
    for r in rules:
        if not isinstance(r, dict):
            return None, "跳转规则格式不正确"
        m = r.get("match") or {}
        goto = r.get("goto")
        if goto != "end" and not (isinstance(goto, str) and ObjectId.is_valid(goto)):
            return None, "跳转目标不正确（应为题目 ID 或 end）"
        kind = m.get("kind")
        nm = {"kind": kind}
        if kind in ("option_any", "option_all"):
            keys = m.get("optionKeys")
            if not isinstance(keys, list) or not keys or \
                    not all(isinstance(k, str) and k for k in keys):
                return None, "选项跳转需要至少选择一个选项"
            nm["optionKeys"] = keys
        elif kind == "number":
            op = m.get("op")
            if op not in NUMBER_OPS:
                return None, "数字跳转的比较方式不正确"
            nm["op"] = op
            if op == "between":
                for f in ("min", "max"):
                    v = m.get(f)
                    if v is not None and not is_number(v):
                        return None, "数字跳转的边界必须是数字"
                    if v is not None:
                        nm[f] = v
            else:
                v = m.get("value")
                if not is_number(v):
                    return None, "数字跳转的阈值必须是数字"
                nm["value"] = v
        elif kind == "always":
            pass
        else:
            return None, f"未知的跳转条件类型：{kind}"
        out.append({"match": nm, "goto": goto})
    return out, None
