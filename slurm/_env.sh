# Sourced by every job. Builds the Python env on node-local /tmp (home quota is nearly
# full) and reuses it if a previous job on the same node already built it.
# legacy-skylake Python runs on both Intel (general, largemem) and AMD (main, debug) nodes.
set -euo pipefail
module load python/3.12.3-legacy-skylake
export PULSO_ROOT="${PULSO_ROOT:-$HOME/pulso}"
export PULSO_ENV="/tmp/$USER/pulso-venv"
export PULSO_TMP="/tmp/$USER/pulso-tmp-${SLURM_JOB_ID:-manual}_${SLURM_ARRAY_TASK_ID:-0}"
mkdir -p "/tmp/$USER" "$PULSO_TMP"
(
  flock 9
  if [ ! -f "$PULSO_ENV/.ready" ]; then
    rm -rf "$PULSO_ENV"
    python -m venv "$PULSO_ENV"
    "$PULSO_ENV/bin/pip" install --no-cache-dir -q -r "$PULSO_ROOT/requirements-hpc.txt"
    touch "$PULSO_ENV/.ready"
  fi
) 9>"/tmp/$USER/pulso-venv.lock"
source "$PULSO_ENV/bin/activate"
trap 'rm -rf "$PULSO_TMP"' EXIT
cd "$PULSO_ROOT"
echo "[env] node=$(hostname) partition=${SLURM_JOB_PARTITION:-?} cpus=${SLURM_CPUS_PER_TASK:-1} python=$(python -V 2>&1)"
