#!/usr/bin/env bash
# フックの起動検証（L1 §8-10）。正常系と異常系の両方を実際に発火させる。
set -uo pipefail
cd "$(dirname "$0")/.."
pass=0; fail=0
chk() { # chk <説明> <期待終了コード> <実際の終了コード>
  if [ "$2" = "$3" ]; then echo "  [ok] $1"; pass=$((pass+1));
  else echo "  [NG] $1  期待=$2 実際=$3"; fail=$((fail+1)); fi
}

echo "── inject_gate.py ──"
out=$(echo "{\"cwd\":\"$PWD\",\"transcript_path\":\"/nonexistent\"}" | python3 .claude/hooks/inject_gate.py); rc=$?
chk "正常終了" 0 $rc
[ "$(echo "$out" | wc -l)" -ge 10 ] && chk "関門9項が出力される" 0 0 || chk "関門9項が出力される" 0 1
echo "$out" | grep -q "現在日時" && chk "現在日時が注入される" 0 0 || chk "現在日時が注入される" 0 1
echo "$out" | grep -qE "[0-9]{4}-[0-9]{2}-[0-9]{2}" && chk "実測した日付が入っている" 0 0 || chk "実測した日付が入っている" 0 1
chk "入力が空でも落ちない" 0 "$(echo '' | python3 .claude/hooks/inject_gate.py >/dev/null 2>&1; echo $?)"
big=$(mktemp -d)/t.jsonl; python3 -c "
import sys
open(sys.argv[1],'w').write('x'*2_100_000)" "$big"
echo "{\"cwd\":\"$PWD\",\"transcript_path\":\"$big\"}" | python3 .claude/hooks/inject_gate.py | grep -q "劣化の予兆" \
  && chk "記録2MB超で劣化警告が出る" 0 0 || chk "記録2MB超で劣化警告が出る" 0 1

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
rm -f metrics/.stopguard-test metrics/.terms-test
chk "【型I】未完了なのに中断理由が無ければ差し戻す" 2 "$(run "$(J '【この応答で完了したこと】調査。【未完了】実装。【次に最初に行うこと】実装の着手。')")"
rm -f metrics/.stopguard-test
chk "【型I】中断理由（承認待ち）が書いてあれば通す" 0 "$(run "$(J '【この応答で完了したこと】調査。【未完了】実装（承認待ちのため中断）。— 状態：入力待ち　次：ご承認ください')")"
rm -f metrics/.stopguard-test metrics/.terms-test
jarg=$(python3 -c "print('詳しい説明。'*60 + 'フックを使って強制します。出力契約も適用します。')")
chk "【型J】初出の専門用語に説明が無ければ差し戻す" 2 "$(run "$(J "$jarg")")"
rm -f metrics/.stopguard-test metrics/.terms-test
jok=$(python3 -c "print('詳しい説明。'*60 + 'フック（＝条件が満たされたら自動で動く小さなプログラム）を使います。')")
chk "【型J】説明を添えれば通す" 0 "$(run "$(J "$jok")")"
rm -f metrics/.stopguard-test metrics/.terms-test
tm=$(python3 -c "print('詳しい説明。'*60 + '本日の時点で最新の状況です。')")
chk "【型K】日時に依存する記述に基準日が無ければ差し戻す" 2 "$(run "$(J "$tm")")"
rm -f metrics/.stopguard-test
tmok=$(python3 -c "print('詳しい説明。'*60 + '本日（2026-08-27 JST）時点で最新の状況です。')")
chk "【型K】基準日を書けば通す" 0 "$(run "$(J "$tmok")")"
rm -f metrics/.stopguard-test metrics/.terms-test
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
# 回帰テスト：ヒアドキュメントの中身は「実行」ではないので許可する（2026-08 の誤検知）
hd=$(python3 -c 'import json;print(json.dumps({"tool_name":"Bash","tool_input":{"command":"cat > t.sh <<\x27EOF\x27\n" + "rm -rf" + " \"$TMP\"\nEOF\n"}}))')
chk "ヒアドキュメント内の危険コマンド文字列は許可（誤検知の回帰）" "allow" "$(d "$hd")"
hd2=$(python3 -c 'import json;print(json.dumps({"tool_name":"Bash","tool_input":{"command":"cat > t.sh <<\x27EOF\x27\nhello\nEOF\n" + "rm -rf" + " /tmp/y"}}))')
chk "ヒアドキュメントの後の実行は拒否" "deny" "$(d "$hd2")"

echo "────────────────────────────"
echo "合格 $pass 件 / 不合格 $fail 件"
rm -f metrics/.stopguard-test metrics/.terms-test
[ "$fail" -eq 0 ]
