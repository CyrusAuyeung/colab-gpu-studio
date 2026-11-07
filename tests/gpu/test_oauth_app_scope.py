"""Account-scoped app changes must not affect other accounts or pending auth."""
from app.config import settings
from app.db import Store
from app.gpu.accounts import AccountService
from app.gpu.google import GoogleService
from app.gpu.repository import Repository
from app.security import encrypt_secret


def test_scoped_state_invalidation_preserves_other_pending_authorization(monkeypatch):
    repo = Repository(Store('sqlite:///:memory:'))
    for account in ('a', 'b'):
        repo.create('credentials', 'owner', {'purpose': '_oauth_state', 'account_id': account}, record_id='state-'+account)
    GoogleService(repo, 'owner', account_id='b').invalidate_oauth_states('owner', account_id='b')
    assert not repo.get('credentials', 'state-a', 'owner').get('used_at')
    assert repo.get('credentials', 'state-b', 'owner').get('used_at')


def test_explicit_default_app_overrides_old_shared_app(monkeypatch):
    monkeypatch.setattr(settings, 'encryption_key', 'X'*43+'=')
    repo = Repository(Store('sqlite:///:memory:'))
    account = AccountService.default_account_id('owner')
    repo.create('accounts', 'owner', {'legacy_default': True}, record_id=account)
    for rid, aid, cid in [('old-shared', None, 'old'), ('explicit', account, 'new')]:
        repo.create('credentials', 'owner', {'purpose': 'oauth_client_config', 'provider': 'google', 'account_id': aid, 'client_id': cid, 'encrypted_client_secret': encrypt_secret('secret-123')}, record_id=rid)
    assert GoogleService(repo, 'owner', account_id=account)._config()[0] == 'new'


def test_canonical_default_app_does_not_replace_legacy_shared_fallback(monkeypatch):
    from app.oauth_config_routes import config_id
    monkeypatch.setattr(settings, 'encryption_key', 'X'*43+'=')
    repo = Repository(Store('sqlite:///:memory:'))
    account = AccountService.default_account_id('owner')
    repo.create('accounts', 'owner', {'legacy_default': True}, record_id=account)
    repo.create('credentials', 'owner', {'purpose': 'oauth_client_config', 'provider': 'google', 'account_id': account, 'client_id': 'old', 'encrypted_client_secret': encrypt_secret('secret-123')}, record_id='old-migrated-app')
    repo.create('credentials', 'owner', {'purpose': 'oauth_client_config', 'provider': 'google', 'account_id': account, 'client_id': 'new', 'encrypted_client_secret': encrypt_secret('secret-456')}, record_id=config_id('owner', account))
    assert GoogleService(repo, 'owner', account_id=account)._config()[0] == 'new'
    assert GoogleService(repo, 'owner', account_id='other')._config()[0] == 'old'


def test_unscoped_app_never_borrows_new_default_dedicated_app(monkeypatch):
    from app.oauth_config_routes import config_id
    monkeypatch.setattr(settings, 'encryption_key', 'X'*43+'=')
    repo = Repository(Store('sqlite:///:memory:'))
    aid = AccountService.default_account_id('owner')
    repo.create('accounts', 'owner', {'legacy_default': True}, record_id=aid)
    repo.create('credentials', 'owner', {'provider':'google','purpose':'oauth_client_config','account_id':aid,'client_id':'new','encrypted_client_secret':encrypt_secret('secret-123')}, record_id=config_id('owner', aid))
    assert GoogleService(repo, 'owner')._client_config() is None


def test_shared_change_invalidates_migrated_default_pending_state(monkeypatch):
    from app.oauth_config_routes import _invalidate_states_direct, config_id
    monkeypatch.setattr(settings, 'encryption_key', 'X'*43+'=')
    repo = Repository(Store('sqlite:///:memory:'))
    aid = AccountService.default_account_id('owner')
    repo.create('accounts', 'owner', {'legacy_default':True}, record_id=aid)
    app = repo.create('credentials', 'owner', {'provider':'google','purpose':'oauth_client_config','account_id':aid,'client_id':'old','encrypted_client_secret':encrypt_secret('secret-123')}, record_id=config_id('owner'))
    repo.create('credentials', 'owner', {'purpose':'_oauth_state','account_id':aid}, record_id='pending')
    _invalidate_states_direct(repo, 'owner', None, [app], True)
    assert repo.get('credentials','pending','owner').get('used_at')
