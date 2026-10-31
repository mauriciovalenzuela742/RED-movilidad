#!/bin/bash
# Run from the laptop (Git Bash) in the project root: copies code to ~/pulso on Leftraru.
# Does not copy data, the venv, outputs or config.json (credentials are copied separately).
set -euo pipefail
cd "$(dirname "$0")/.."
tar czf - pulso tests slurm requirements-hpc.txt | ssh nlhpc 'mkdir -p ~/pulso/logs ~/pulso/outputs && tar xzf - -C ~/pulso && find ~/pulso/slurm -type f -exec sed -i "s/\r$//" {} +'
echo "synced to nlhpc:~/pulso"
