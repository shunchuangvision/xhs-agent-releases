"""Current reference features on the packaged runtime; no real account or provider."""
import json
from pathlib import Path
from urllib.error import HTTPError


def configure(api):
    config = api('/reference-vision-config')
    saved = api('/reference-vision-config', 'PUT', dict(enabled=True, reuse_text=True,
        model='synthetic-vision', base_url='', api_key=None, revision=config['revision']))
    assert saved['configured'] and 'api_key' not in saved


def exercise(api, base, token, project_id):
    assert api('/reference-vision-config')['configured']
    account = api('/reference-account')
    assert {'available', 'opened', 'message', 'can_collect', 'browser_id', 'background_enabled'} <= set(account)
    assert not account['opened'] and account['background_enabled']
    settings = api('/reference-account/settings', 'PUT', {'browser': 'auto', 'background_enabled': False})
    assert not settings['background_enabled'] and not settings['can_collect']
    assert api('/reference-account')['background_enabled'] is False
    api('/reference-account/settings', 'PUT', {'browser': 'auto', 'background_enabled': True})
    schema = api('/openapi.json')
    assert '/api/reference-account/open' in schema['paths']
    assert '/api/reference-account/settings' in schema['paths']
    assert '/api/projects/{project_id}/reference-links/preview' in schema['paths']
    assert not any(p.startswith('/api/v3/media/') for p in schema['paths'])
    try:
        api('/v3/media/config')
    except HTTPError as exc:
        assert exc.code == 410
    else:
        raise AssertionError('Retired workbench is still exposed')
    try:
        api('/projects/' + project_id + '/reference-links/preview', 'POST',
            {'share_text': 'https://127.0.0.1/test', 'mode': 'public'})
    except HTTPError as exc:
        assert exc.code == 422
    else:
        raise AssertionError('Private URL was accepted')
    ref = api('/projects/' + project_id + '/references', 'POST',
        {'raw_text': 'Synthetic reference: open with a daily-life scene, explain the details, and invite discussion.',
         'source_url': 'https://example.com/synthetic-reference'})
    assert ref['selected']
    assert any(r['id'] == ref['id'] for r in api('/projects/' + project_id + '/workspace')['references'])
    # These external modules must also import under the actual frozen runtime.
    from app import account_browser, account_sessions, safari_capture, link_fetch
    from app._vendor.websocket import create_connection
    from app.reference_links import normalize_image
    from io import BytesIO
    from PIL import Image
    note_id = '000000000000000000000001'
    url = 'https://www.xiaohongshu.com/explore/' + note_id
    result = account_browser.extract({'url': url, 'title': 'Synthetic title',
        'desc': 'Synthetic body for a single note, with enough content for extraction.', 'images': []}, url)
    assert result['extraction'] == 'account_note'
    try:
        account_browser.extract({'url': url, 'blocked': True}, url)
    except link_fetch.LinkError:
        pass
    else:
        raise AssertionError('Verification challenge was not stopped')
    out = BytesIO(); Image.new('RGB', (160, 200), '#4c7865').save(out, 'PNG')
    assert normalize_image(out.getvalue())[2:] == (160, 200)
    return dict(reference_config_preserved=True, reference_import=True, account_routes=True,
        frozen_html_parser_and_websocket_imports=True, account_note_parser=True, browser_settings_persisted=True,
        challenge_stops=True, image_workbench_removed=True, real_account_login_tested=False,
        external_model_calls=0)
