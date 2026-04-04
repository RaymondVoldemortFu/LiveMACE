#!/usr/bin/env bash
# Aggregate tool cache hit/miss/set from logs inside a container.
# Usage: ./docker_cache_stats.sh <container_name_or_id>
# Optional: LOG_GLOB=/path/to/tool_cache.log* ./docker_cache_stats.sh mycontainer
set -euo pipefail

CONTAINER="${1:-}"
LOG_GLOB="${LOG_GLOB:-/app/logs/tool_cache.log*}"

usage() {
  echo "Usage: $0 <container_name_or_id>" >&2
  echo "Optional env: LOG_GLOB (default: /app/logs/tool_cache.log*)" >&2
  exit 1
}

[[ -n "$CONTAINER" ]] || usage

echo "Reading logs in container: $CONTAINER (glob: $LOG_GLOB)" >&2

docker exec -i -e LOG_GLOB="$LOG_GLOB" "$CONTAINER" bash -s <<'REMOTE_SCRIPT'
set -euo pipefail
LOG_GLOB="${LOG_GLOB:-/app/logs/tool_cache.log*}"
shopt -s nullglob
FILES=( $LOG_GLOB )
if ((${#FILES[@]} == 0)); then
  echo "Error: no files match: $LOG_GLOB" >&2
  exit 1
fi

TMP_BASE=/tmp/docker_cache_stats_$$
AWK_SRC=${TMP_BASE}.awk
TMP_OUT=${TMP_BASE}.out
TMP_DETAIL=${TMP_BASE}.detail
TMP_SUMMARY=${TMP_BASE}.summary
trap 'rm -f "$AWK_SRC" "$TMP_OUT" "$TMP_DETAIL" "$TMP_SUMMARY"' EXIT

cat <<'AWK_PROGRAM' >"$AWK_SRC"
function get_round(line,   r) {
  if (match(line, /round=[^ ]+/)) return substr(line, RSTART + 6, RLENGTH - 6)
  return ""
}
function get_num(line, key,   pat, s) {
  pat = key "=[0-9]+(\\.[0-9]+)?%?"
  if (match(line, pat)) {
    s = substr(line, RSTART, RLENGTH)
    sub("^" key "=", "", s)
    sub(/%$/, "", s)
    return s + 0
  }
  return -1
}
BEGIN {
  detail_total_h = detail_total_m = detail_total_s = 0
  summary_total_h = summary_total_m = summary_total_s = 0
}
{
  if ($0 ~ /Tool cache hit:/ || $0 ~ /Tool cache miss:/ || $0 ~ /Tool cache set:/) {
    r = get_round($0)
    if (r != "") {
      detail_rounds[r] = 1
      if ($0 ~ /Tool cache hit:/) {
        dh[r]++
        detail_total_h++
      } else if ($0 ~ /Tool cache miss:/) {
        dm[r]++
        detail_total_m++
      } else if ($0 ~ /Tool cache set:/) {
        ds[r]++
        detail_total_s++
      }
    }
  }
  if ($0 ~ /Tool cache round cleared:/) {
    r = get_round($0)
    if (r != "") {
      summary_rounds[r] = 1
      sh[r] = get_num($0, "hit")
      sm[r] = get_num($0, "miss")
      ss[r] = get_num($0, "set")
      summary_total_h += sh[r]
      summary_total_m += sm[r]
      summary_total_s += ss[r]
    }
  }
}
END {
  detail_total = detail_total_h + detail_total_m
  detail_rate = (detail_total > 0 ? detail_total_h * 100.0 / detail_total : 0)
  summary_total = summary_total_h + summary_total_m
  summary_rate = (summary_total > 0 ? summary_total_h * 100.0 / summary_total : 0)

  print "===== DETAIL LINES (per log line) ====="
  printf "hit=%d\nmiss=%d\nset=%d\nhit_rate=%.2f%%\n", detail_total_h, detail_total_m, detail_total_s, detail_rate
  print ""
  print "===== DETAIL PER ROUND ====="
  for (r in detail_rounds) {
    total_r = dh[r] + dm[r]
    rate_r = (total_r > 0 ? dh[r] * 100.0 / total_r : 0)
    printf "DETAIL\t%s\t%d\t%d\t%d\t%.2f\n", r, dh[r] + 0, dm[r] + 0, ds[r] + 0, rate_r
  }
  print ""
  print "===== ROUND CLEARED SUMMARY (from summary lines) ====="
  printf "hit=%d\nmiss=%d\nset=%d\nhit_rate=%.2f%%\n", summary_total_h, summary_total_m, summary_total_s, summary_rate
  print ""
  print "===== SUMMARY PER ROUND ====="
  for (r in summary_rounds) {
    total_r = sh[r] + sm[r]
    rate_r = (total_r > 0 ? sh[r] * 100.0 / total_r : 0)
    printf "SUMMARY\t%s\t%d\t%d\t%d\t%.2f\n", r, sh[r] + 0, sm[r] + 0, ss[r] + 0, rate_r
  }
}
AWK_PROGRAM

awk -f "$AWK_SRC" "${FILES[@]}" >"$TMP_OUT"

: >"$TMP_DETAIL"
: >"$TMP_SUMMARY"
while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in
    DETAIL$'\t'*) printf '%s\n' "$line" >>"$TMP_DETAIL" ;;
    SUMMARY$'\t'*) printf '%s\n' "$line" >>"$TMP_SUMMARY" ;;
    *) printf '%s\n' "$line" ;;
  esac
done <"$TMP_OUT"

fmt_rounds() {
  sort "$1" | awk -F '\t' '{ printf "%s  hit=%s  miss=%s  set=%s  hit_rate=%s%%\n", $2, $3, $4, $5, $6 }'
}

if [[ -s "$TMP_DETAIL" ]]; then
  echo ""
  echo "--- Detail rounds (sorted) ---"
  fmt_rounds "$TMP_DETAIL"
fi
if [[ -s "$TMP_SUMMARY" ]]; then
  echo ""
  echo "--- Summary rounds (sorted) ---"
  fmt_rounds "$TMP_SUMMARY"
fi
REMOTE_SCRIPT
