#!/bin/sh
set -eu

KEY="${ASSISTANT_API_KEY:-}"
cat > /usr/share/nginx/html/config.js <<EOF
window.ASSISTANT_API_KEY = $(printf '%s' "$KEY" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))');
EOF

exec nginx -g 'daemon off;'
