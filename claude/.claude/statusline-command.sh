# MONTHLY / EXTRA USAGE via OAuth API
USAGE_CACHE="/tmp/claude-statusline-usage.json"
USAGE_CACHE_AGE=1800

fetch_usage() {
  local token tmp
  if [ "$(uname)" = "Darwin" ]; then
    token=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null | jq -r '.claudeAiOauth.accessToken // empty')
  else
    token=$(jq -r '.claudeAiOauth.accessToken // empty' ~/.claude/.credentials.json 2>/dev/null)
  fi
  [ -z "$token" ] && return 1
  tmp="${USAGE_CACHE}.tmp"
  curl -sf --max-time 3 "https://api.anthropic.com/api/oauth/usage" \
    -H "Authorization: Bearer $token" \
    -H "anthropic-beta: oauth-2025-04-20" \
    -H "Content-Type: application/json" > "$tmp" 2>/dev/null
  # only replace cache on a valid response, so a failed fetch doesn't
  # freeze a broken cache as "fresh" for the next 30 minutes
  if [ $? -eq 0 ] && jq -e '.extra_usage' "$tmp" >/dev/null 2>&1; then
    mv "$tmp" "$USAGE_CACHE"
  else
    rm -f "$tmp"
  fi
}

# Refresh cache if missing or stale
cache_age=9999
if [ -f "$USAGE_CACHE" ]; then
  if [ "$(uname)" = "Darwin" ]; then
    cache_age=$(( $(date +%s) - $(stat -f %m "$USAGE_CACHE") ))
  else
    cache_age=$(( $(date +%s) - $(stat -c %Y "$USAGE_CACHE") ))
  fi
fi
[ "$cache_age" -gt "$USAGE_CACHE_AGE" ] && fetch_usage

# Read from cache
monthly_limit=$(jq -r '.extra_usage.monthly_limit // empty' "$USAGE_CACHE" 2>/dev/null)
monthly_used=$(jq -r '.extra_usage.used_credits // empty' "$USAGE_CACHE" 2>/dev/null)

# UI
input=$(cat)

model=$(echo "$input" | jq -r '.model.display_name')
cwd=$(echo "$input" | jq -r '.workspace.current_dir')
used=$(echo "$input" | jq -r '.context_window.used_percentage // 0')
cost=$(echo "$input" | jq -r '.cost.total_cost_usd // empty')

# --- CONTEXT PROGRESS BAR GENERATOR ---
filled_chars=$(awk "BEGIN {print int(($used / 10) + 0.5)}")
[ $filled_chars -gt 10 ] && filled_chars=10
empty_chars=$((10 - filled_chars))

if [ $(printf '%.0f' "$used") -lt 50 ]; then
  bar_color="\033[32m"
elif [ $(printf '%.0f' "$used") -lt 80 ]; then
  bar_color="\033[33m"
else
  bar_color="\033[31m"
fi

bar_str=""
i=0; while [ $i -lt $filled_chars ]; do bar_str="${bar_str}█"; i=$((i+1)); done
i=0; while [ $i -lt $empty_chars ]; do bar_str="${bar_str}░"; i=$((i+1)); done
# --------------------------------------

branch=""
if git -C "$cwd" --no-optional-locks rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  branch=$(git -C "$cwd" --no-optional-locks symbolic-ref --short HEAD 2>/dev/null)
  [ -z "$branch" ] && branch=$(git -C "$cwd" --no-optional-locks rev-parse --short HEAD 2>/dev/null)
fi

parts=""

# 1. Model Name
if [ -n "$model" ]; then
  parts="$parts \033[36m[$model]\033[0m"
fi

# 2. Context Window Progress Bar & Percentage
if [ -n "$used" ]; then
  parts="$parts \033[35m[ctx:\033[0m${bar_color}${bar_str}\033[35m $(printf '%.0f' "$used")%]\033[0m"
fi

# 3. Branch
if [ -n "$branch" ]; then
  parts="$parts ⌥ ${branch}"
fi

# 4. Active Session Cost
if [ -n "$cost" ]; then
  parts="$parts \033[33m[\$$(printf '%.2f' "$cost")]\033[0m"
fi

# 5. Monthly spend (only appears if extra_usage is enabled on your plan)
if [ -n "$monthly_used" ] && [ -n "$monthly_limit" ]; then
  parts="$parts \033[32m[\$$(awk "BEGIN {printf \"%.2f\", $monthly_used/100}")/\$$(awk "BEGIN {printf \"%.0f\", $monthly_limit/100}")]\033[0m"
fi

printf "%b\n" "$parts"