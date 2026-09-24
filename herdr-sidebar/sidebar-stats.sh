#!/bin/sh
# Output for herdr's custom sidebar section: one line per row, keep lines short.
load=$(sysctl -n vm.loadavg | awk '{print $2}')
mem_used=$(memory_pressure 2>/dev/null | awk -F': ' '/System-wide memory free percentage/ {print 100 - $2 "%"}')
agent_json=$(herdr agent list 2>/dev/null)
agents=$(printf '%s' "$agent_json" | grep -o '"agent_status"' | wc -l | tr -d ' ')
working=$(printf '%s' "$agent_json" | grep -o '"agent_status":"working"' | wc -l | tr -d ' ')

echo "load   $load"
echo "mem    ${mem_used:-?}"
echo "agents $agents ($working working)"
echo "time   $(date +%H:%M)"
