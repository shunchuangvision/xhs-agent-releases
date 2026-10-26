"""Synthetic-only image workbench checks for the packaged runtime."""
import base64
import json
import time
import uuid
from io import BytesIO
from urllib.request import Request, urlopen
from zipfile import ZipFile


def image_bytes():
    from PIL import Image
    out = BytesIO()
    Image.new('RGB', (32, 48), '#d7c6a2').save(out, 'PNG')
    return out.getvalue()


def provider_reply(path, raw):
    if '/images/' in path:
        assert path.endswith('/images/edits') and b'name="image[]"' in raw
        return {'data': [{'b64_json': base64.b64encode(image_bytes()).decode()}]}
    body = json.loads(raw)
    content = body['messages'][-1]['content']
    text = content[0]['text'] if isinstance(content, list) else content
    try:
        task = json.loads(text).get('task')
    except ValueError:
        task = None
    if task == 'understand_image':
        assert isinstance(content, list) and content[1]['image_url']['url'].startswith('data:image/jpeg;base64,')
        result = dict(summary='Synthetic image understanding', visible_text=[],
            visual_features=['Plain image'], composition='Centered', suggestions=['Soft daylight'],
            uncertainties=['Material cannot be confirmed from pixels'])
    elif task == 'visual_brief':
        result = dict(scene_analysis='Synthetic scene', prompt='Synthetic product image',
            negative_prompt='No unsupported facts', checks=['Check product appearance'], uncertainties=['Human review required'])
    else:
        result = {'ok': True}
    return {'choices': [{'message': {'content': json.dumps(result)}}], 'usage': {'total_tokens': 20}}


def configure(api):
    for kind in ('vision', 'image'):
        config = api('/v3/media/config')[kind]
        saved = api('/v3/media/config/' + kind, 'PUT', dict(enabled=True, reuse_text=True,
            model='synthetic-' + kind, base_url='', api_key=None, response_format='auto', revision=config['revision']))
        assert saved['configured'] and 'api_key' not in saved


def exercise(api, base, token, project_id):
    configs = api('/v3/media/config')
    assert all(configs[k]['configured'] for k in ('vision', 'image'))
    request = Request(base + '/api/v3/projects/' + project_id + '/assets?filename=test.png&source=synthetic-test&rights=true',
                      data=image_bytes(), headers={'Authorization': 'Bearer ' + token}, method='POST')
    with urlopen(request, timeout=20) as response:
        asset = json.load(response)
    def start(**changes):
        body = dict(kind='understanding', idempotency_key=str(uuid.uuid4()), asset_ids=[asset['id']],
                    model_route='studio-vision', max_retries=0, min_interval_seconds=1, reuse_knowledge=False)
        body.update(changes)
        job = api('/v3/media/projects/' + project_id + '/start', 'POST', body)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            detail = api('/v3/jobs/' + job['id'])
            if detail['job']['status'] not in ('queued', 'running'):
                assert detail['job']['status'] == 'succeeded', detail['job']['error']
                return detail
            time.sleep(.3)
        raise AssertionError('Image task did not finish')
    understanding = start()
    assert understanding['steps'][0]['outputs']['image_understanding']
    assert understanding['steps'][0]['outputs']['asset_id'] == asset['id']
    generation = start(kind='visual', generate_images=True, image_routes=['studio-image'],
                       scenes=['Synthetic daylight'], images_per_scene=2)
    assert generation['job']['image_progress'] == {'completed': 2, 'total': 2}
    ids = [s['outputs']['asset_id'] for s in generation['steps'] if s['tool'] == 'generate_image']
    assert len(set(ids)) == 2
    request = Request(base + '/api/v3/media/projects/' + project_id + '/download',
        data=json.dumps({'asset_ids': ids}).encode(), method='POST',
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urlopen(request, timeout=20) as response:
        with ZipFile(BytesIO(response.read())) as archive:
            assert len(archive.namelist()) == 3
            manifest = json.loads(archive.read('图片信息.json'))
            assert all(x['generation']['model'] == 'synthetic-image' for x in manifest)
    return dict(image_understanding=True, batch_image_generation=True, image_zip_download=True,
                image_configuration_preserved=True, external_model_calls=0)
