import pytest
from app.instance_routes import RetentionInput
from pydantic import ValidationError


@pytest.mark.parametrize('body', [
    {'retention_mode': 'release'},
    {'retention_mode': 'retain', 'idle_minutes': 31},
])
def test_retention_rejects_unsupported_http_values(body):
    with pytest.raises(ValidationError):
        RetentionInput.model_validate(body)
