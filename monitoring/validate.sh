#!/usr/bin/env bash
set -euo pipefail
OBS=$(cd "$(dirname "$0")" && pwd)
PROM=prom/prometheus@sha256:efd719c99d83b060d9daefdcf00360461adf279f45ef5391f8d111892118753e
AM=prom/alertmanager@sha256:e9733bafb1bdef9b00e25a21f8f99dc26a22224bf16641ad754d1649f4c3357a
ALLOY=grafana/alloy@sha256:2aa2099af76c0098d4af7a4d6e48f86cb66dc1a000222ad927a1c67c6542d13f
docker run --rm -v "$OBS:/config:ro" -w /config --entrypoint /bin/promtool "$PROM" test rules rules.test.yml
docker run --rm -v "$OBS:/etc/prometheus:ro" --entrypoint /bin/promtool "$PROM" check config --syntax-only /etc/prometheus/prometheus-local.yml
docker run --rm -v "$OBS/alertmanager-local.yml:/config.yml:ro" --entrypoint /bin/amtool "$AM" check-config /config.yml
TMP=$(mktemp -d)
# Only public rendered ConfigMaps are stored here; native tools run non-root.
chmod 755 "$TMP"
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/amtool" <<'ROUTES'
#!/usr/bin/env bash
exec docker run --rm -v "$OBS:$OBS:ro" -v "$TMP:$TMP:ro" --entrypoint /bin/amtool "$AM" "$@"
ROUTES
chmod 700 "$TMP/amtool"
export OBS AM TMP
AMTOOL="$TMP/amtool" python3 "$OBS/test-routing.py"
docker run --rm -v "$OBS/alloy-local.alloy:/config.alloy:ro" "$ALLOY" validate /config.alloy
# Validate the actual Helm configurations, including optional incident delivery.
mkdir -m 755 "$TMP/rendered"
python3 "$OBS/render-validation.py" "$TMP/rendered"
for config in "$TMP"/rendered/*; do
  docker run --rm -v "$config:/etc/prometheus:ro" --entrypoint /bin/promtool "$PROM" check config --syntax-only /etc/prometheus/prometheus.yml
  docker run --rm -v "$config:/config:ro" --entrypoint /bin/promtool "$PROM" check rules /config/rules.yml
  docker run --rm -v "$config:/config:ro" --entrypoint /bin/amtool "$AM" check-config /config/alertmanager.yml
  docker run --rm -v "$config/config.alloy:/config.alloy:ro" "$ALLOY" validate /config.alloy
  if [ -f "$config/shared-rules.test.yml" ]; then
    docker run --rm -v "$config:/config:ro" -w /config --entrypoint /bin/promtool "$PROM" test rules shared-rules.test.yml
  fi
  if [[ "$config" == *-incident ]]; then
    docker run --rm -v "$config:/config:ro" -w /config --entrypoint /bin/promtool "$PROM" test rules rules.test.yml
    AMTOOL="$TMP/amtool" python3 "$OBS/test-routing.py" --config "$config/alertmanager.yml"
  else
    AMTOOL="$TMP/amtool" python3 "$OBS/test-routing.py" --config "$config/alertmanager.yml" --slack-only
  fi
done
