#!/usr/bin/env python3
"""Check receiver routing from Compose or rendered Helm config; sends no notifications."""
import argparse
import os
import subprocess
import yaml
from pathlib import Path

AMTOOL = os.environ.get('AMTOOL', 'amtool')
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--config', type=Path, default=Path(__file__).with_name('alertmanager-local.yml'))
parser.add_argument('--slack-only', action='store_true')
args = parser.parse_args()
path = args.config
scenarios = [
    ('slack', ['environment=production','severity=critical','service=incident-bridge','incident_delivery=disabled','alertname=IncidentDeliveryDeadLetters']),
    ('slack', ['environment=production','severity=critical','service=incident-bridge','incident_delivery=disabled','alertname=IncidentWorkerStalled']),
    ('servicenow,slack', ['environment=production','severity=critical','service=orders','alertname=BoutiqueServiceDown']),
    ('slack', ['environment=production','severity=warning','service=orders','alertname=BoutiqueHighLatency']),
    ('slack', ['environment=dev','severity=critical','service=orders','alertname=BoutiqueServiceDown']),
    ('slack', ['environment=staging','severity=warning','service=frontend','alertname=BoutiqueHighLatency']),
    ('slack', ['environment=platform','severity=critical','service=prometheus','alertname=MonitoringScrapeFailed']),
]
shared = 'slack-production' in {r['name'] for r in yaml.safe_load(path.read_text())['receivers']}
for receivers, labels in scenarios:
    if args.slack_only:
        receivers = 'slack'
        if shared:
            if 'environment=production' in labels:receivers = 'slack-production'
            elif any(label in labels for label in ('environment=dev','environment=staging')):receivers = 'slack-nonprod'
    subprocess.run([AMTOOL,'config','routes','test','--config.file='+str(path),'--verify.receivers='+receivers,*labels],check=True,stdout=subprocess.DEVNULL)
print('PASS: receiver routing matches ' + ('Slack-only default.' if args.slack_only else 'optional production incident delivery.'))
