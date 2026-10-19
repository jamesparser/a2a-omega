#!/bin/sh
# probe e2a send capacity per account + agentverse reachability (no key values printed)
K1=$(grep -hE "^E2A_API_KEY=" REPLACE_ME 2>/dev/null | cut -d= -f2- | tr -d '\r"')
K2=$(grep -hE "^E2A2_API_KEY=" REPLACE_ME 2>/dev/null | cut -d= -f2- | tr -d '\r"')
UA="Mozilla/5.0"
send_probe() { # $1 key  $2 sender  $3 recipient
  local code
  code=$(curl -s -o /tmp/cap -w "%{http_code}" -X POST \
    -H "Authorization: Bearer $1" -H "Content-Type: application/json" -H "User-Agent: $UA" \
    -d "{\"to\":[\"$3\"],\"subject\":\"mesh-cap-probe\",\"text\":\"ignore - capacity probe 10-02\"}" \
    "https://api.e2a.dev/v1/agents/$2/messages")
  printf "  %s  HTTP %s :: %s\n" "$2 -> $3" "$code" "$(head -c 90 /tmp/cap | tr -d '\n')"
}
echo "=== k1 (LC acct, E2A_API_KEY) ==="
send_probe "$K1" "agent-three@agents.e2a.dev" "agent-one@agents.e2a.dev"
echo "=== k2 (LC/omega acct, E2A2_API_KEY) ==="
send_probe "$K2" "agent-one@agents.e2a.dev" "agent-two@agents.e2a.dev"
echo "=== agentverse reachability (real API path) ==="
curl -s -o /dev/null -w "agentverse /v2/agents/mailbox/submit (no-auth) = HTTP %{http_code}\n" "https://agentverse.ai/v2/agents/mailbox/submit"
curl -s -o /dev/null -w "agentverse /v1/agents (no-auth)            = HTTP %{http_code}\n" "https://agentverse.ai/v1/agents"
