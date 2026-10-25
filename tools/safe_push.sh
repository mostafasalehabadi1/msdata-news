#!/usr/bin/env bash
# One push for every writer (kala, energy, kish; backend 1405-08-03).
# Usage: tools/safe_push.sh "<commit message>" <path>...
# A rebase conflict used to be aborted in silence: on 1405-08-03 every kala push from 20:22 failed that way and two hours
# of news stayed on the runner. Now a conflict is retried with this runner's copy winning (it holds the newest output),
# 3 times; a push that still fails prints PUSH FAILED, and the manager's Telegram watchdog alerts on a writer log that
# stops moving while its run is still in progress.
msg="$1"; shift
git add -A -- "$@" 2>/dev/null
git diff --cached --quiet || git commit -qm "$msg"
for i in 1 2 3; do
  if git pull -q --rebase --autostash 2>/dev/null || { git rebase --abort 2>/dev/null; git pull -q --rebase --autostash -X theirs; }; then
    git push -q && { rm -f /tmp/push_fail; exit 0; }
  else
    git rebase --abort 2>/dev/null
  fi
  sleep $((i * 10))
done
echo "PUSH FAILED: $msg ($(date -u +%H:%M) UTC)" | tee -a /tmp/push_fail
exit 1
