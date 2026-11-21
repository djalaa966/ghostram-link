#!/usr/bin/env bash
# Real-kernel experiment for ghostprofile (run inside a memory-limited container or cgroup):
#   docker run --rm --cap-add SYS_PTRACE --cap-add SYS_NICE --memory 96m --memory-swap 1g \
#     -v "$PWD:/src:ro" python:3.12-slim bash /src/tests/run_profile_experiment.sh [trials]
# Same workload, same access order: 1) record a profile, 2) baseline sweeps, 3) sweeps with replay.
set -u
N=${1:-5}
cd /tmp
W=/src/tests/swap_workload.py; G=/src/profiles/ghostprofile.py
wait_ready() { until grep -q ready "$1"; do sleep 0.2; done; }

echo "== record"
python3 $W --mb 192 --sweeps 1 --wait 1 > r.log & P=$!; wait_ready r.log
python3 $G record --pid $P --out prof.json --seconds 60 --interval 0.1 >/dev/null & R=$!
wait $P; wait $R; grep sweep r.log
for i in $(seq $N); do
  python3 $W --mb 192 --sweeps 1 --wait 1 > b.log & P=$!; wait $P; echo "baseline $i: $(grep sweep b.log)"
done
for ahead in 1024 4096; do
  for i in $(seq $N); do
    python3 $W --mb 192 --sweeps 1 --wait 1 > p.log & P=$!; wait_ready p.log
    python3 $G replay --pid $P --profile prof.json --ahead $ahead --interval 0.05 --seconds 60 > rp.log 2>&1 & R=$!
    wait $P; wait $R; echo "replay ahead=$ahead $i: $(grep sweep p.log) | $(tail -1 rp.log)"
  done
done
