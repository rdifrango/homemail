#!/bin/sh
set -e
if [ ! -f /opt/homemail/Reports/index.html ]; then
    cp /opt/homemail/_default_index.html /opt/homemail/Reports/index.html
    echo "Seeded Reports/index.html into volume"
fi
exec python3 /opt/homemail/_pipeline/pipeline.py "$@"
