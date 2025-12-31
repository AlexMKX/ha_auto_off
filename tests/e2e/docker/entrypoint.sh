#!/bin/bash
set -e

echo "=== AutoQA Container Starting ==="
echo "Provisioning Home Assistant..."

# Run provisioning script
python /app/provisioning.py

echo "=== Provisioning Complete ==="
echo "Running command: $@"

# Execute the passed command (or default to shell)
if [ $# -eq 0 ]; then
    echo "No command specified, starting interactive shell..."
    exec /bin/bash
else
    exec "$@"
fi
