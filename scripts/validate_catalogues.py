#!/usr/bin/env python3
"""Validate consistency of the InfraSage bootstrap catalogues. Run from any working directory."""
from pathlib import Path
import json
import math
import sys
import yaml

BASE = Path(__file__).resolve().parents[1] / 'backend' / 'diagnosis' / 'catalogues'

def validate(base=BASE):
    knowledge = yaml.safe_load((base / 'knowledge.yaml').read_text())
    tests = yaml.safe_load((base / 'tests.yaml').read_text())
    model = json.loads((base / 'probabilities.json').read_text())
    errors = []
    hs = set(knowledge['hypotheses'])
    ts = set(tests['tests'])
    if knowledge['schema_version'] != tests['schema_version'] or tests['schema_version'] != model['schema_version']:
        errors.append('Schema version mismatch')
    if hs != set(model['priors']): errors.append('Prior hypotheses mismatch')
    if ts != set(model['likelihoods']): errors.append('Test IDs mismatch with likelihood tables')
    if any(not 0 <= x <= 1 for x in model['priors'].values()) or not math.isclose(sum(model['priors'].values()),1,abs_tol=1e-6):
        errors.append('Priors do not form probability distribution')
    for test_id, definition in tests['tests'].items():
        outcomes = definition['outcomes']
        if len(outcomes) < 2 or len(outcomes) != len(set(outcomes)):
            errors.append(f'{test_id}: outcomes duplicate or too few')
        if definition['normal_outcome'] not in outcomes:
            errors.append(f'{test_id}: normal outcome not in vocabulary')
        if definition['availability']['live'] and definition['execution']['binding_status'] != 'verified':
            errors.append(f'{test_id}: live execution improperly enabled')
        if definition['execution']['live_binding'] is None and definition['availability']['live']:
            errors.append(f'{test_id}: missing live binding')
        distributions = model['likelihoods'].get(test_id, {})
        if set(distributions) != hs:
            errors.append(f'{test_id}: incomplete hypothesis distribution')
        for hid, probs in distributions.items():
            if set(probs) != set(outcomes):
                errors.append(f'{test_id}/{hid}: outcomes differ from test definition')
            if any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 or v>1 for v in probs.values()):
                errors.append(f'{test_id}/{hid}: invalid probability values')
            if not math.isclose(sum(probs.values()),1,abs_tol=1e-6):
                errors.append(f'{test_id}/{hid}: probabilities do not sum to one')
    for rel in knowledge['relationships']:
        tid=rel['test_id']
        if tid not in ts:
            errors.append(f'{tid}: unknown test relationship')
            continue
        for hid, labels in rel['hypothesis_outcomes'].items():
            if hid not in hs:
                errors.append(f'{tid}: unknown hypothesis {hid}')
            if not set(labels.values()).issubset(set(tests['tests'][tid]['outcomes'])):
                errors.append(f'{tid}/{hid}: relationship uses nonexistent outcome')
            if labels['supports'] == labels['contradicts']:
                errors.append(f'{tid}/{hid}: same outcome supports and contradicts')
    if model['status'] != 'synthetic_provisional_not_calibrated':
        errors.append('Model improperly labeled; this distribution is synthetic')
    if errors:
        for e in errors: print('ERROR:',e)
        raise SystemExit(1)
    print(f"PASS: {len(hs)} hypotheses, {len(ts)} tests, {len(knowledge['relationships'])} relationship groups, {len(hs)*len(ts)} complete synthetic likelihood distributions")
    print('WARNING: Live execution disabled; causal links and numbers not calibrated.')
    return knowledge, tests, model

if __name__ == '__main__': validate()
