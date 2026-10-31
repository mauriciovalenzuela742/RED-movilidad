#!/bin/bash
#SBATCH --job-name=pulso-quality
#SBATCH --partition=main
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=48G
#SBATCH --time=01:30:00
#SBATCH --output=logs/%x-%j.out
# Usage: sbatch slurm/02_quality.sbatch 2026-10-07 2026-10-10 2026-10-11
source "$HOME/pulso/slurm/_env.sh"
python -m pulso.quality --dates "$@" --out outputs/phase0/quality \
    --tmp-dir "$PULSO_TMP" --memory-limit 40GB
