#!/bin/bash
#SBATCH --job-name=pulso-days
#SBATCH --partition=main
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=logs/%x-%A_%a.out
# TEMPLATE for phases 1-2: one array task per local day listed in a text file.
# Usage: sbatch --array=1-$(wc -l < days.txt)%12 slurm/10_days_array.sbatch days.txt pulso.MODULE
# Each task downloads one day to node-local /tmp, processes it there and writes only the
# small aggregated result to outputs/. Tasks whose output already exists exit at once.
source "$HOME/pulso/slurm/_env.sh"
DAYS_FILE="$1"; MODULE="$2"
DAY=$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$DAYS_FILE")
python -m "$MODULE" --date "$DAY" --tmp-dir "$PULSO_TMP" --memory-limit 26GB
