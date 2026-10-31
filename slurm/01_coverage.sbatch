#!/bin/bash
#SBATCH --job-name=pulso-coverage
#SBATCH --partition=main
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=02:00:00
#SBATCH --output=logs/%x-%j.out
# Usage: sbatch slurm/01_coverage.sbatch [--stage1-only | --days N]
source "$HOME/pulso/slurm/_env.sh"
python -m pulso.coverage --out outputs/phase0 "$@"
