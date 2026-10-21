from pydantic import BaseModel, ValidationError

from scout.shared import describe_exception


class Doc(BaseModel):
    title: int


def test_validation_error_does_not_leak_input_value() -> None:
    try:
        Doc.model_validate({"title": "CONFIDENTIAL unpublished result"})
    except ValidationError as exc:
        text = describe_exception(exc)
    assert "CONFIDENTIAL" not in text and "title" in text and text.startswith("ValidationError")


def test_plain_exception_is_truncated() -> None:
    assert len(describe_exception(RuntimeError("x" * 1000))) == 200
