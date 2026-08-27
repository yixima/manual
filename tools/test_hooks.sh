#!/usr/bin/env bash
# フックの起動検証（L1 §8-10）。正常系と異常系の両方を実際に発火させる。
set -uo pipefail
cd "$(dirname "$0")/.."
pass=0; fail=0
chk() { # chk <説明> <期待終了コード> <実際の終了コード>
  if [ "$2" = "$3" ]; then echo "  [ok] $1"; pass=$((pass+1));
  else echo "  [NG] $1  期待=$2 実際=$3"; fail=$((fail+1)); fi
}

echo "── inject_gate.sh ──"
out=$(./.claude/hooks/inject_gate.sh); rc=$?
chk "正常終了" 0 $rc
[ "$(echo "$out" | wc -l)" -ge 10 ] && chk "関門9項が出力される" 0 0 || chk "関門9項が出力される" 0 1

echo "── check_output.py ──"
run() { echo "$1" | python3 .claude/hooks/check_output.py >/dev/null 2>&1; echo $?; }
J() { python3 -c "import json,sys;print(json.dumps({'last_assistant_message':sys.argv[1],'cwd':'$PWD','session_id':'test'},ensure_ascii=False))" "$1"; }

chk "正常な応答は通す" 0 "$(run "$(J '調査の結果、対象は3件でした。— 状態：完了　次：不要')")"
chk "【型H】着手宣言で終わる応答は差し戻す" 2 "$(run "$(J 'まず全体を確認しました。これから実装に着手します。')")"
chk "【型A】出典なしの【確認済】は差し戻す" 2 "$(run "$(J '【確認済】この制度は2026年に改正されました。')")"
chk "【型A】出典ありの【確認済】は通す" 0 "$(run "$(J '【確認済】改正の事実を確認しました。出典：https://example.gov/x')")"
long=$(python3 -c "print('作業の詳細な説明。'*60 + 'ファイルを作成しました。')")
chk "【型B】長文の作業報告で状態行がなければ差し戻す" 2 "$(run "$(J "$long")")"
rm -f metrics/.stopguard-test
chk "空の応答は通す" 0 "$(run "$(J '')")"
chk "壊れた入力でも作業を止めない" 0 "$(echo 'not json' | python3 .claude/hooks/check_output.py >/dev/null 2>&1; echo $?)"
rm -f metrics/.stopguard-test
r1=$(run "$(J 'これから実装に着手します。')"); r2=$(run "$(J 'これから実装に着手します。')")
chk "同一応答の差し戻しは1回まで（無限ループ防止）" "2 0" "$r1 $r2"

echo "── guard_delivery.py ──"
g() { echo "$1" | python3 .claude/hooks/guard_delivery.py 2>/dev/null; }
d() { g "$1" | python3 -c "import json,sys;s=sys.stdin.read();print(json.loads(s)['hookSpecificOutput']['permissionDecision'] if s.strip() else 'allow')"; }
chk "§7-11 納品物の非ASCII名を拒否" "deny" "$(d '{"tool_name":"Write","tool_input":{"file_path":"dist/提案書_v1.md"}}')"
chk "§7-11 ASCII安全名は許可" "allow" "$(d '{"tool_name":"Write","tool_input":{"file_path":"dist/proposal_v1.md"}}')"
chk "納品外の日本語名は許可（過剰検知しない）" "allow" "$(d '{"tool_name":"Write","tool_input":{"file_path":"docs/メモ.md"}}')"
chk "§8-5 rm -rf を拒否" "deny" "$(d '{"tool_name":"Bash","tool_input":{"command":"rm -rf /tmp/x"}}')"
chk "§8-5 git push --force を拒否" "deny" "$(d '{"tool_name":"Bash","tool_input":{"command":"git push --force origin main"}}')"
chk "--force-with-lease は許可" "allow" "$(d '{"tool_name":"Bash","tool_input":{"command":"git push --force-with-lease origin x"}}')"
chk "通常のコマンドは許可" "allow" "$(d '{"tool_name":"Bash","tool_input":{"command":"ls -la"}}')"

echo "────────────────────────────"
echo "合格 $pass 件 / 不合格 $fail 件"
rm -f metrics/.stopguard-test
[ "$fail" -eq 0 ]
