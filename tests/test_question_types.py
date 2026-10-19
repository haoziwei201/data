"""题型注册表单元测试：校验规则（作业要求"校验测试"）。"""
import pytest

from app.errors import ApiError
from app.question_types import validate_question_payload


def q(qtype, required=False, options=None, config=None):
    return {
        "type": qtype,
        "title": "测试题",
        "required": required,
        "options": options,
        "config": config or {},
    }


class TestSingleChoice:
    def test_required_missing(self):
        from app.question_types import get_handler
        doc = q("single_choice", required=True, options=[{"key": "a", "text": "A"}, {"key": "b", "text": "B"}])
        assert get_handler("single_choice").check_answer(doc, None) == "本题必答"

    def test_invalid_key(self):
        from app.question_types import get_handler
        doc = q("single_choice", options=[{"key": "a", "text": "A"}, {"key": "b", "text": "B"}])
        assert "无效" in get_handler("single_choice").check_answer(doc, "zzz")

    def test_valid(self):
        from app.question_types import get_handler
        doc = q("single_choice", required=True, options=[{"key": "a", "text": "A"}, {"key": "b", "text": "B"}])
        assert get_handler("single_choice").check_answer(doc, "a") is None


class TestMultiChoice:
    DOC = q("multi_choice", required=True,
            options=[{"key": "a", "text": "A"}, {"key": "b", "text": "B"},
                     {"key": "c", "text": "C"}, {"key": "d", "text": "D"}],
            config={"minSelect": 2, "maxSelect": 3})

    def test_too_few(self):
        from app.question_types import get_handler
        assert "至少" in get_handler("multi_choice").check_answer(self.DOC, ["a"])

    def test_too_many(self):
        from app.question_types import get_handler
        assert "最多" in get_handler("multi_choice").check_answer(self.DOC, ["a", "b", "c", "d"])

    def test_duplicate_option(self):
        from app.question_types import get_handler
        assert "重复" in get_handler("multi_choice").check_answer(self.DOC, ["a", "a"])

    def test_exactly_required_count(self):
        from app.question_types import get_handler
        assert get_handler("multi_choice").check_answer(self.DOC, ["a", "b"]) is None


class TestText:
    DOC = q("text", required=True, config={"minLength": 2, "maxLength": 5})

    def test_too_short(self):
        from app.question_types import get_handler
        assert "至少" in get_handler("text").check_answer(self.DOC, "x")

    def test_too_long(self):
        from app.question_types import get_handler
        assert "最多" in get_handler("text").check_answer(self.DOC, "一二三四五六")

    def test_blank_required(self):
        from app.question_types import get_handler
        assert "必答" in get_handler("text").check_answer(self.DOC, "   ")


class TestNumber:
    DOC = q("number", required=True, config={"min": 0, "max": 120, "integer": True})

    def test_not_integer(self):
        from app.question_types import get_handler
        assert "整数" in get_handler("number").check_answer(self.DOC, 12.5)

    def test_below_min(self):
        from app.question_types import get_handler
        assert "小于" in get_handler("number").check_answer(self.DOC, -1)

    def test_above_max(self):
        from app.question_types import get_handler
        assert "大于" in get_handler("number").check_answer(self.DOC, 121)

    def test_valid_integer(self):
        from app.question_types import get_handler
        assert get_handler("number").check_answer(self.DOC, 20) is None

    def test_bool_rejected(self):
        from app.question_types import get_handler
        assert "数字" in get_handler("number").check_answer(self.DOC, True)


class TestPayloadValidation:
    def test_options_need_two(self):
        with pytest.raises(ApiError):
            validate_question_payload(q("single_choice", options=[{"key": "a", "text": "A"}]))

    def test_duplicate_option_key(self):
        with pytest.raises(ApiError):
            validate_question_payload(q("single_choice", options=[
                {"key": "a", "text": "A"}, {"key": "a", "text": "B"}]))

    def test_bad_jump_target(self):
        p = q("single_choice", options=[{"key": "a", "text": "A"}, {"key": "b", "text": "B"}])
        p["jumpRules"] = [{"match": {"kind": "always"}, "goto": "not-an-id"}]
        with pytest.raises(ApiError):
            validate_question_payload(p)

    def test_bad_minmax(self):
        with pytest.raises(ApiError):
            validate_question_payload(q("multi_choice", options=[
                {"key": "a", "text": "A"}, {"key": "b", "text": "B"}],
                config={"minSelect": 3, "maxSelect": 2}))

    def test_unknown_type(self):
        with pytest.raises(ApiError):
            validate_question_payload(q("magic"))
