"""Offline binding acceptance for every shipped graph, plus an imported schema."""
import copy
import json
from pathlib import Path

import pytest
from app.gpu import builtin
from app.gpu.manifest import bind_parameters, resolve_parameters, validate_manifest

ROOT = Path(builtin.__file__).with_name('templates') / 'builtin'
ENTRIES = json.loads((ROOT / 'catalog.json').read_text())['workflows']


@pytest.mark.parametrize('entry', ENTRIES, ids=lambda value: value['file'])
def test_each_builtin_binds_defaults_and_reference_fields_without_mutating_graph(entry):
    bundle = json.loads((ROOT / entry['file']).read_text())
    original = copy.deepcopy(bundle['api_graph'])
    manifest = validate_manifest(bundle['manifest'], original, bundle['object_info'])
    supplied = {key: 'offline-reference' for key, spec in manifest['parameters'].items()
                if spec.get('type') in {'image', 'video', 'asset'} and spec.get('required')}
    supplied['seed'] = 42
    resolved = resolve_parameters(manifest, supplied)
    bound = bind_parameters(manifest, original, resolved)
    assert bound and original == bundle['api_graph']
    assert resolved['seed'] == 42
    assert set(resolved).issubset(manifest['parameters'])


def test_imported_custom_parameter_names_are_schema_driven():
    manifest = {'model_id': 'offline-custom', 'version': '1.0.0', 'parameters': {
        'unfamiliar_caption': {'type': 'string', 'default': 'hello', 'binding': {'node':'1','input':'text'}},
        'unfamiliar_iterations': {'type':'integer','default':7,'min':1,'max':10,
                                 'binding':{'node':'1','input':'steps'}}}}
    graph = {'1': {'class_type':'CustomNode', 'inputs':{'text':'old','steps':1}}}
    info = {'CustomNode': {'input':{'required':{'text':['STRING'],'steps':['INT',{'min':1,'max':10}]}},'output':['IMAGE']}}
    checked = validate_manifest(manifest, graph, info)
    resolved = resolve_parameters(checked, {'unfamiliar_caption':'imported','unfamiliar_iterations':9})
    bound = bind_parameters(checked, graph, resolved)
    assert bound['1']['inputs'] == {'text':'imported','steps':9}
    assert graph['1']['inputs'] == {'text':'old','steps':1}
