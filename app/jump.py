"""跳转规则引擎：解释题目中的 jumpRules 数据，得出下一跳。

语义：规则按数组顺序匹配，命中第一条即返回其 goto；
全部不命中返回 None（由调用方按 order 顺序前进）。
"""
from app.utils import is_number


def _value_keys(value):
    """把答案值统一看成选项 key 的集合：单选是单个 key，多选是 key 数组。"""
    if isinstance(value, list):
        return set(v for v in value if isinstance(v, str))
    if isinstance(value, str) and value:
        return {value}
    return set()


def match_condition(m, value):
    kind = m.get("kind")
    if kind == "always":
        return True
    if kind in ("option_any", "option_all"):
        keys = set(m.get("optionKeys") or [])
        have = _value_keys(value)
        if not keys:
            return False
        return bool(keys & have) if kind == "option_any" else keys.issubset(have)
    if kind == "number":
        if not is_number(value):
            return False
        op = m.get("op")
        if op == "between":
            lo, hi = m.get("min"), m.get("max")
            if lo is not None and value < lo:
                return False
            if hi is not None and value > hi:
                return False
            return True
        t = m.get("value")
        return {
            "eq": value == t,
            "ne": value != t,
            "gt": value > t,
            "gte": value >= t,
            "lt": value < t,
            "lte": value <= t,
        }.get(op, False)
    return False


def next_destination(question, value):
    """返回下一跳：题目 _id 字符串 / "end" / None（按顺序下一题）。"""
    for rule in question.get("jumpRules") or []:
        if match_condition(rule.get("match") or {}, value):
            return rule.get("goto")
    return None
