from scout.api.app import create_app
from scout.api.schemas.events import ClientEvent, EventBatch, EventsAccepted, Rejection, SessionMeta


def test_every_operation_has_summary() -> None:
    schema = create_app().openapi()
    missing = [
        f"{method.upper()} {path}"
        for path, ops in schema["paths"].items()
        for method, op in ops.items()
        if not op.get("summary")
    ]
    assert missing == []


def test_dto_fields_are_described() -> None:
    for model in (SessionMeta, ClientEvent, EventBatch, Rejection, EventsAccepted):
        props = model.model_json_schema()["properties"]
        undocumented = [k for k, v in props.items() if not v.get("description")]
        assert undocumented == [], (model.__name__, undocumented)
