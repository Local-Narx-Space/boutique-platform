#!/usr/bin/env python3
"""Extract deployed Helm ConfigMaps for native monitoring-tool validation."""
import argparse
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[2] / 'boutique-gitops'
CONFIGS = {
    'boutique-prometheus': ('prometheus.yml', 'rules.yml'),
    'boutique-alertmanager': ('alertmanager.yml',),
    'boutique-alloy': ('config.alloy',),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='Temporary validation output directory')
    args = parser.parse_args()
    for profile in ('homelab', 'nonprod', 'production'):
        for incident in (False, True):
            name = profile + ('-incident' if incident else '-slack')
            output = args.directory / name
            output.mkdir(parents=True, exist_ok=True)
            output.chmod(0o755)
            rendered = subprocess.check_output([
                'helm', 'template', 'audit-' + name, str(ROOT / 'monitoring'),
                '-f', str(ROOT / 'monitoring/profiles' / profile / 'values.yaml'),
                '--set', 'incidentBridge.enabled=' + str(incident).lower(),
            ], text=True)
            configs = {d['metadata']['name']: d['data'] for d in yaml.safe_load_all(rendered)
                       if d and d['kind'] == 'ConfigMap'}
            for config, keys in CONFIGS.items():
                for key in keys:
                    (output / key).write_text(configs[config][key])
                    (output / key).chmod(0o644)
            fixture = yaml.safe_load(Path(__file__).with_name('rules.test.yml').read_text())
            fixture['rule_files'] = ['rules.yml']
            (output / 'rules.test.yml').write_text(yaml.safe_dump(fixture))
            (output / 'rules.test.yml').chmod(0o644)
            if profile == 'homelab':
                (output / 'shared-rules.test.yml').write_text(Path(__file__).with_name('shared-rules.test.yml').read_text())
                (output / 'shared-rules.test.yml').chmod(0o644)


if __name__ == '__main__':
    main()
