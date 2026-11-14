#!/usr/bin/env python3
"""Import *candidate* alert definitions from a local checkout of awesome-prometheus-alerts.

This script does not register diagnostic tests or assert causal relationships. It preserves
upstream PromQL and provenance for review, without automatically activating rules.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import yaml

MATCH = ('redis', 'postgres', 'docker', 'host and hardware', 'systemd', 'blackbox', 'netdata', 'process exporter')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('rules', type=Path, help='Path to downloaded awesome-prometheus-alerts/_data/rules.yml')
    p.add_argument('--output', type=Path, default=Path('backend/diagnosis/generated/prometheus_rule_candidates.json'))
    p.add_argument('--upstream-revision', default='unrecorded', help='Pinned upstream commit hash, if known')
    a = p.parse_args()
    content = a.rules.read_bytes()
    data = yaml.safe_load(content)
    if not isinstance(data,dict) or not isinstance(data.get('groups'),list):
        raise SystemExit('Unexpected upstream rules.yml structure: expected groups[]')
    candidates=[]
    for group in data['groups']:
        if not isinstance(group,dict): continue
        for service in group.get('services',[]) or []:
            if not isinstance(service,dict): continue
            name=str(service.get('name',''))
            if not any(token in name.lower() for token in MATCH): continue
            for exporter in service.get('exporters',[]) or []:
                if not isinstance(exporter,dict): continue
                for rule in exporter.get('rules',[]) or []:
                    if not isinstance(rule,dict): continue
                    candidates.append({
                        'category':group.get('name'), 'service':name,
                        'exporter':exporter.get('slug') or exporter.get('name'),
                        'name':rule.get('name'), 'description':rule.get('description'),
                        'promql':rule.get('query'), 'severity':rule.get('severity'),
                        'for':rule.get('for'), 'review_status':'candidate_only',
                        'is_root_cause':False,
                        'netdata_executable':False,
                    })
    output={'schema_version':1,'origin':'https://github.com/samber/awesome-prometheus-alerts/blob/master/_data/rules.yml',
            'upstream_revision':a.upstream_revision,
            'upstream_sha256':hashlib.sha256(content).hexdigest(),
            'imported_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'status':'unreviewed_alert_condition_candidates',
            'note':'PromQL rules are conditions, not initiating fault hypotheses or directly executable Netdata tests.',
            'candidate_count':len(candidates),'candidates':candidates}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(output,indent=2)+'\n')
    print(f"Saved {len(candidates)} candidate conditions to {a.output} (none active)")

if __name__=='__main__':main()
