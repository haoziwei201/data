"""跳转引擎单元测试（作业要求"跳转逻辑测试"）。"""
from app.jump import match_condition, next_destination


def Q(rules):
    return {"jumpRules": rules}


class TestOptionCondition:
    def test_any_with_single_value(self):
        assert match_condition({"kind": "option_any", "optionKeys": ["a"]}, "a") is True
        assert match_condition({"kind": "option_any", "optionKeys": ["a"]}, "b") is False

    def test_any_with_list_value(self):
        m = {"kind": "option_any", "optionKeys": ["a", "b"]}
        assert match_condition(m, ["c", "a"]) is True
        assert match_condition(m, ["c", "d"]) is False

    def test_all_requires_every_key(self):
        m = {"kind": "option_all", "optionKeys": ["a", "b"]}
        assert match_condition(m, ["a", "b", "c"]) is True
        assert match_condition(m, ["a", "c"]) is False

    def test_no_value_never_matches(self):
        m = {"kind": "option_any", "optionKeys": ["a"]}
        assert match_condition(m, None) is False


class TestNumberCondition:
    def test_compare_ops(self):
        assert match_condition({"kind": "number", "op": "gte", "value": 18}, 18) is True
        assert match_condition({"kind": "number", "op": "lt", "value": 18}, 17.5) is True
        assert match_condition({"kind": "number", "op": "eq", "value": 5}, 5) is True
        assert match_condition({"kind": "number", "op": "ne", "value": 5}, 6) is True

    def test_between(self):
        m = {"kind": "number", "op": "between", "min": 0, "max": 120}
        assert match_condition(m, 0) is True
        assert match_condition(m, 120) is True
        assert match_condition(m, 121) is False
        assert match_condition(m, -1) is False

    def test_non_number_value(self):
        assert match_condition({"kind": "number", "op": "gte", "value": 18}, "18") is False


class TestNextDestination:
    def test_first_match_wins(self):
        rules = [
            {"match": {"kind": "option_any", "optionKeys": ["a"]}, "goto": "q5"},
            {"match": {"kind": "always"}, "goto": "q3"},
        ]
        assert next_destination(Q(rules), "a") == "q5"
        assert next_destination(Q(rules), "b") == "q3"

    def test_no_match_returns_none(self):
        rules = [{"match": {"kind": "option_any", "optionKeys": ["a"]}, "goto": "q5"}]
        assert next_destination(Q(rules), "b") is None

    def test_end(self):
        rules = [{"match": {"kind": "always"}, "goto": "end"}]
        assert next_destination(Q(rules), None) == "end"

    def test_empty_rules(self):
        assert next_destination(Q([]), "a") is None
        assert next_destination({}, "a") is None
