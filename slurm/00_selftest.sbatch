#!/bin/bash
#SBATCH --job-name=pulso-selftest
#SBATCH --partition=main
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=00:20:00
#SBATCH --output=logs/%x-%j.out
# Builds the env on the node, runs the offline test suite, then the live ping.
source "$HOME/pulso/slurm/_env.sh"
python -m pytest -q -p no:cacheprovider
python -m pulso.ping || echo "[selftest] ping failed (expected until config.json has credentials)"
