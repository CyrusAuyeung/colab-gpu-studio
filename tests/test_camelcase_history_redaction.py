def test_camelcase_credentials_are_redacted_recursively():
    from app.gpu.workbench import WorkbenchService
    from app.job_history import safe_history_value

    value = {'accessToken': 'fixture', 'nested': {'refreshToken': 'fixture', 'connectionToken': 'fixture'}}
    assert safe_history_value(value) == {'nested': {}}
    assert WorkbenchService._safe_public_value(value) == {'nested': {}}
