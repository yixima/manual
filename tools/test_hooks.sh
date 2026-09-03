#!/usr/bin/env bash
# フックの起動検証（L1 §8-10）。正常系と異常系の両方を実際に発火させる。
set -uo pipefail
cd "$(dirname "$0")/.."
pass=0; fail=0
# テストは本番の記録に触らない。隔離した一時ディレクトリへ向ける。
# （テストが実データを消すと、セッション中に説明済みの用語が「初出」に戻ってしまう）
export CLAUDE_MANUAL_METRICS="$(mktemp -d)/metrics"
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
# 劣化判定＝負荷スコア方式（往復数は補助指標）
W=$(mktemp -d); mkdir -p "$W/dist"
# 記録は「実際の往復（ユーザーの発言）」と「道具の呼び出し・結果」が混在する。
# 往復数は**前者だけ**を数えなければならない（v30 の修正。L2 記録参照）。
mkjs() { python3 -c "
import sys,pathlib,json
turns=int(sys.argv[2]); noise=int(sys.argv[3])
rows=[]
for i in range(turns):
    rows.append({'type':'user','message':{'role':'user','content':'依頼です'}})
    rows.append({'type':'assistant','message':{'role':'assistant','content':[{'type':'text','text':'はい'}]}})
for i in range(noise):
    rows.append({'type':'user','message':{'role':'user','content':[{'type':'tool_result','content':'結果'}]}})
pathlib.Path(sys.argv[1]).write_text(''.join(json.dumps(r,ensure_ascii=False)+chr(10) for r in rows),encoding='utf-8')" "$W/t.jsonl" "$1" "${2:-0}"; }
mkbin() { python3 -c "
import sys,pathlib
d=pathlib.Path(sys.argv[1])
for f in d.glob('*.pptx'): f.unlink()
for i in range(int(sys.argv[2])): (d/f'deck_{i}.pptx').write_bytes(b'x'*2_000_000)" "$W/dist" "$1"; }
gate() { echo "{\"cwd\":\"$W\",\"transcript_path\":\"$W/t.jsonl\"}" | python3 .claude/hooks/inject_gate.py; }

mkjs 20; mkbin 0
gate | grep -q "劣化" && chk "軽い作業では警告が出ない" 0 1 || chk "軽い作業では警告が出ない" 0 0
mkbin 6
gate | grep -q "§0-5 申告水準" && chk "スライド6本(12MB)は往復が少なくても申告水準" 0 0 || chk "スライド6本(12MB)は往復が少なくても申告水準" 0 1
gate | grep -q "バイナリ成果物" && chk "内訳にバイナリ成果物が表示される" 0 0 || chk "内訳にバイナリ成果物が表示される" 0 1
mkbin 0; mkjs 250
gate | grep -q "§0-5 注意水準" && chk "往復250回は補助指標として注意水準どまり" 0 0 || chk "往復250回は補助指標として注意水準どまり" 0 1
gate | grep -q "§0-5 申告水準" && chk "往復数だけでは申告水準に達しない（代理指標の降格）" 0 1 || chk "往復数だけでは申告水準に達しない（代理指標の降格）" 0 0
# 回帰（v30）：往復数は**どれだけ多くても**単独では申告水準に達しない。
# 以前は「1200回超なら申告」と実装しており、コアカードの規定と矛盾していた。
mkjs 5000
gate | grep -q "§0-5 申告水準" && chk "往復5000回でも単独では申告水準に達しない（回帰）" 0 1 || chk "往復5000回でも単独では申告水準に達しない（回帰）" 0 0
# 回帰（v30）：道具の結果の行を往復として数えない。
# 記録の行数を往復数と取り違えており、実測で約38倍にずれていた。
mkjs 10 3000
gate | grep -q "§0-5" && chk "道具の結果ばかりの記録では往復と数えない（回帰）" 0 1 || chk "道具の結果ばかりの記録では往復と数えない（回帰）" 0 0
mkjs 20; python3 -c "
import pathlib,sys;(pathlib.Path(sys.argv[1])/'big.pdf').write_bytes(b'x'*6_000_000)" "$W/dist"
gate | grep -q "ダウンロードが失敗しやすい" && chk "単一ファイル5MB超を検出する" 0 0 || chk "単一ファイル5MB超を検出する" 0 1
gate | grep -q "作業を止める理由にはならない" && chk "申告水準でも中断を促さない" 0 0 || chk "申告水準でも中断を促さない" 0 1
rm -r "$W"

echo "── check_output.py ──"
run() { echo "$1" | python3 .claude/hooks/check_output.py >/dev/null 2>&1; echo $?; }
# 差し戻しの本文（stderr）を読むためのもの。exit 2 でも止まらないようにする。
run_err() { echo "$1" | python3 .claude/hooks/check_output.py 2>&1 >/dev/null || true; }
J() { python3 -c "import json,sys;print(json.dumps({'last_assistant_message':sys.argv[1],'cwd':'$PWD','session_id':'test'},ensure_ascii=False))" "$1"; }

chk "正常な応答は通す" 0 "$(run "$(J '調査の結果、対象は3件でした。— 状態：完了　次：不要')")"
chk "【型H】着手宣言で終わる応答は差し戻す" 2 "$(run "$(J 'まず全体を確認しました。これから実装に着手します。')")"
chk "【型A】出典なしの【確認済】は差し戻す" 2 "$(run "$(J '【確認済】この制度は2026年に改正されました。')")"
chk "【型A】出典ありの【確認済】は通す" 0 "$(run "$(J '【確認済】改正の事実を確認しました。出典：https://example.gov/x')")"
long=$(python3 -c "print('作業の詳細な説明。'*60 + 'ファイルを作成しました。')")
chk "【型B】長文の作業報告で状態行がなければ差し戻す" 2 "$(run "$(J "$long")")"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
chk "空の応答は通す" 0 "$(run "$(J '')")"
chk "壊れた入力でも作業を止めない" 0 "$(echo 'not json' | python3 .claude/hooks/check_output.py >/dev/null 2>&1; echo $?)"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
chk "【型I】未完了なのに中断理由が無ければ差し戻す" 2 "$(run "$(J '【この応答で完了したこと】調査。【未完了】実装。【次に最初に行うこと】実装の着手。')")"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
# 回帰テスト：一覧に「未完了」の語があるだけでは差し戻さない（2026-09-01 の誤検知）
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test
list=$(python3 -c "print('必須の項目は次のとおりです。'*20 + '①作成理由 ②重要な決定 ③却下案 ④主な成果物 ⑤未完了 ⑥次の一手。未完了はありません。')")
chk "【型I】一覧に「未完了」の語があるだけでは通す（誤検知の回帰）" 0 "$(run "$(J "$list")")"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test
chk "【型I】実際に未完了が残っていれば差し戻す" 2 "$(run "$(J '調査は終わりました。未完了が残っています。')")"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test
chk "【型I】中断理由（承認待ち）が書いてあれば通す" 0 "$(run "$(J '【この応答で完了したこと】調査。【未完了】実装（承認待ちのため中断）。— 状態：入力待ち　次：ご承認ください')")"
# 回帰テスト（v27）：規則そのものを説明した文で発火しない（2026-09-01 の誤検知2件目）
# 引用・鉤括弧・コードの中身は、この応答が報告している作業ではない。
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test
desc=$(python3 -c "print('判定の仕組みを説明します。'*20 + '判定を「未完了という語が出たか」から「実際に未完了が残っていると述べているか」へ絞りました。— 状態：完了　次：不要')")
chk "【型I】規則を説明した文（鉤括弧の中）では発火しない（誤検知の回帰）" 0 "$(run "$(J "$desc")")"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test
quoted=$(python3 -c "print('報告します。'*30 + chr(10) + '> 残りの作業があります' + chr(10) + '作業は全部終わりました。— 状態：完了　次：不要')")
chk "【型I】引用ブロックの中の未完了では発火しない（誤検知の回帰）" 0 "$(run "$(J "$quoted")")"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test
done_line=$(python3 -c "print('作業の報告です。'*30 + '状態：完了　次：不要')")
chk "【型I】状態行が「完了」なら本人の宣言を優先して通す" 0 "$(run "$(J "$done_line")")"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
# 回帰テスト（v28）：差し戻しは「全文の再送」を求めない（2026-09-02 の指摘）
# 直前の応答はすでに画面に出ている。同じ内容を書き直すと同じ答えが2回届く。
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
msg_out=$(run_err "$(J '調査は終わりました。残りの作業が残っています。')")
echo "$msg_out" | grep -q "全文の再送はしない" && chk "差し戻しは全文の再送を求めない" 0 0 || chk "差し戻しは全文の再送を求めない" 0 1
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
msg_out2=$(run_err "$(J '調査は終わりました。残りの作業が残っています。')")
echo "$msg_out2" | grep -q "同じ応答を出し直して" && chk "「同じ応答を出し直せ」と言わない（回帰）" 0 1 || chk "「同じ応答を出し直せ」と言わない（回帰）" 0 0
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
jarg=$(python3 -c "print('詳しい説明。'*60 + 'フックを使って強制します。出力契約も適用します。')")
chk "【型J】初出の専門用語に説明が無ければ差し戻す" 2 "$(run "$(J "$jarg")")"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
jok=$(python3 -c "print('詳しい説明。'*60 + 'フック（＝条件が満たされたら自動で動く小さなプログラム）を使います。')")
chk "【型J】説明を添えれば通す" 0 "$(run "$(J "$jok")")"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
tm=$(python3 -c "print('詳しい説明。'*60 + '本日の時点で最新の状況です。')")
chk "【型K】日時に依存する記述に基準日が無ければ差し戻す" 2 "$(run "$(J "$tm")")"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
tmok=$(python3 -c "print('詳しい説明。'*60 + '本日（2026-08-27 JST）時点で最新の状況です。')")
chk "【型K】基準日を書けば通す" 0 "$(run "$(J "$tmok")")"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
r1=$(run "$(J 'これから実装に着手します。')"); r2=$(run "$(J 'これから実装に着手します。')")
chk "同一応答の差し戻しは1回まで（無限ループ防止）" "2 0" "$r1 $r2"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
# 【型M】未確認の印を残したまま、不可逆・外向き操作の承認を求める応答は差し戻す（2026-09-01 の事案）
u1='▶要裏取り：別環境で動くか／確信度〈未確認・推測〉。発行してよろしいでしょうか。— 状態：入力待ち　次：ご返答ください'
chk "【型M】未確認のまま発行の承認を求めたら差し戻す" 2 "$(run "$(J "$u1")")"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
u2='【確認済】すべて実測しました。出典：`tools/build_dist.py`。発行してよろしいでしょうか。— 状態：入力待ち　次：ご返答ください'
chk "【型M】裏取り済みなら承認を求めてよい" 0 "$(run "$(J "$u2")")"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
u3='▶要裏取り：無関係の件／確信度〈未確認・推測〉。これは本件の可否には影響しない。発行してよろしいでしょうか。— 状態：入力待ち　次：ご返答ください'
chk "【型M】影響しない理由を明記すれば通す（黙って通さない）" 0 "$(run "$(J "$u3")")"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
u4='▶要裏取り：別環境で動くか／確信度〈未確認・推測〉。先に確かめます。— 状態：実行中　次：不要'
chk "【型M】承認を求めていなければ通す（過剰検知しない）" 0 "$(run "$(J "$u4")")"

echo "── handover_receipt.py（引き継ぎの自動受領）──"
HW=$(mktemp -d); mkdir -p "$HW/handover" "$HW/tools"
cp tools/make_handover.py tools/handover_extract.py "$HW/tools/"
hr() { echo "{\"cwd\":\"$HW\",\"session_id\":\"$1\"}" | python3 .claude/hooks/handover_receipt.py 2>&1; }

# 引き継ぎが無いプロジェクトでは、何も出さない（無関係な場所で騒がないこと）
[ -z "$(hr s0)" ] && chk "引き継ぎが無ければ何も出さない" 0 0 || chk "引き継ぎが無ければ何も出さない" 0 1
chk "引き継ぎが無くてもセッションを止めない" 0 "$(echo "{\"cwd\":\"$HW\",\"session_id\":\"s0b\"}" | python3 .claude/hooks/handover_receipt.py >/dev/null 2>&1; echo $?)"

# README のような、引き継ぎでない .md を誤って拾わないこと
printf '# 受け口の説明\n\nここは引き継ぎファイルではない。\n' > "$HW/handover/README.md"
[ -z "$(hr s1)" ] && chk "引き継ぎでない .md は拾わない（誤検知の防止）" 0 0 || chk "引き継ぎでない .md は拾わない（誤検知の防止）" 0 1

# 本物の引き継ぎを置けば、受領確認が出る
python3 - "$HW/t.jsonl" <<'PYH'
import json, sys
rows = [{"type":"user","isSidechain":False,"timestamp":"2026-09-01T00:00:00Z","sessionId":"h1",
         "cwd":"/tmp/x","gitBranch":"main","message":{"role":"user","content":"引き継ぎの見本となる依頼である。"}}]
open(sys.argv[1],"w",encoding="utf-8").write("\n".join(json.dumps(r,ensure_ascii=False) for r in rows)+"\n")
PYH
python3 tools/make_handover.py --auto "$HW/handover/x_handover_20260901_v1.md" --transcript "$HW/t.jsonl" >/dev/null 2>&1
out=$(hr s2)
echo "$out" | grep -q "引き継ぎの自動受領" && chk "引き継ぎがあれば受領確認を出す" 0 0 || chk "引き継ぎがあれば受領確認を出す" 0 1
echo "$out" | grep -q "一致。生成時" && chk "完全性の照合結果を出す" 0 0 || chk "完全性の照合結果を出す" 0 1
echo "$out" | grep -q "確かめる質問はしない" && chk "確認の往復をやめるよう指示する" 0 0 || chk "確認の往復をやめるよう指示する" 0 1
[ -z "$(hr s2)" ] && chk "同じセッションで二度は出さない" 0 0 || chk "同じセッションで二度は出さない" 0 1
[ -n "$(hr s3)" ] && chk "別のセッションでは改めて出す" 0 0 || chk "別のセッションでは改めて出す" 0 1
chk "壊れた入力でも止まらない（異常系）" 0 "$(echo 'not json' | python3 .claude/hooks/handover_receipt.py >/dev/null 2>&1; echo $?)"
rm -r "$HW"

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
# §0-4 発行の場所の一本化：指定されていないセッションからの発行を機械的に止める
dc() { g "$1" | python3 -c "import json,sys;s=sys.stdin.read();print(json.loads(s)['hookSpecificOutput']['permissionDecision'] if s.strip() else 'allow')"; }
J0() { python3 -c "import json,sys;print(json.dumps({'tool_name':'Bash','tool_input':{'command':sys.argv[1]},'cwd':sys.argv[2]}))" "$1" "$2"; }
UNMARKED=$(mktemp -d)
chk "§0-4 publish.sh を拒否（発行担当でないセッション）" "deny" "$(dc "$(J0 './tools/publish.sh' "$UNMARKED")")"
chk "§0-4 main への push を拒否" "deny" "$(dc "$(J0 'git push origin HEAD:refs/heads/main' "$UNMARKED")")"
chk "作業ブランチへの push は許可（過剰検知しない）" "allow" "$(dc "$(J0 'git push -u origin claude/my-work' "$UNMARKED")")"
mkdir -p "$UNMARKED/.claude" && touch "$UNMARKED/.claude/manual-session"
chk "発行担当と明示されていれば許可" "allow" "$(dc "$(J0 './tools/publish.sh' "$UNMARKED")")"
rm -r "$UNMARKED"

echo "── handover_receipt.py（枝分かれ）──"
HR=$(mktemp -d); mkdir -p "$HR/handover" "$HR/metrics"
mkhv() { python3 -c "
import sys,pathlib,json
p=pathlib.Path(sys.argv[1])
man={'manifest_version':1,'generated_at':'2026-09-02','source':'transcript','session':sys.argv[2],
     'cwd':'/w','branch':'','case':'tokyo','lane':sys.argv[3],'parent':sys.argv[4],
     'counts':{},'chapters':[],'sha256':'x'}
p.write_text('# h' + chr(10)*2 + '## 1. 依頼の原文' + chr(10)*2 + '本文' + chr(10)*2
             + '\`\`\`handover-manifest' + chr(10) + json.dumps(man,ensure_ascii=False)
             + chr(10) + '\`\`\`' + chr(10), encoding='utf-8')" "$1" "$2" "$3" "$4"; }
mkhv "$HR/handover/tokyo_handover_latest.md" sessP "" ""
recv() { echo "{\"cwd\":\"$HR\",\"session_id\":\"$1\"}" | CLAUDE_MANUAL_METRICS="$HR/metrics" python3 .claude/hooks/handover_receipt.py 2>&1; }
# パイプで受けない：grep -q は一致した時点でパイプを閉じるため、
# 出力が長いと書き手側が SIGPIPE で落ち、pipefail が grep の成否を上書きする。
o1=$(recv s1)
grep -q "対象" <<<"$o1" && chk "1本だけならそのまま受領する" 0 0 || chk "1本だけならそのまま受領する" 0 1
mkhv "$HR/handover/tokyo.survey_handover_latest.md" sessA survey tokyo_handover_latest.md
mkhv "$HR/handover/tokyo.design_handover_latest.md" sessB design tokyo_handover_latest.md
out_hr=$(recv s2)
grep -q "勝手に決めません" <<<"$out_hr" && chk "複数あれば勝手に選ばない（回帰）" 0 0 || chk "複数あれば勝手に選ばない（回帰）" 0 1
grep -q "survey" <<<"$out_hr" && grep -q "design" <<<"$out_hr" && chk "候補の一覧に枝名が出る" 0 0 || chk "候補の一覧に枝名が出る" 0 1
grep -q "一つだけ" <<<"$out_hr" && chk "一つだけ質問するよう促す" 0 0 || chk "一つだけ質問するよう促す" 0 1
grep -q "対象：" <<<"$out_hr" && chk "複数あるとき「対象」を決め打ちしない（回帰）" 0 1 || chk "複数あるとき「対象」を決め打ちしない（回帰）" 0 0
# v32：開始時に枝名を提案させる（ユーザーが確定してから最初の保存を行う）
HR2=$(mktemp -d); mkdir -p "$HR2/handover" "$HR2/metrics"
python3 -c "
import sys,pathlib,json
p=pathlib.Path(sys.argv[1])
man={'manifest_version':1,'generated_at':'2026-09-02','source':'transcript','session':'sessP',
     'cwd':'/w','branch':'','case':'tokyo','lane':'','parent':'','counts':{},'chapters':[],'sha256':'x'}
p.write_text('# h'+chr(10)*2+'## 1. 依頼の原文'+chr(10)*2+'本文'+chr(10)*2
  +'## 7. 未完了のタスク'+chr(10)*2+'現地調査が残っている'+chr(10)*2
  +'## 8. 次に最初に行うこと'+chr(10)*2+'調査から始める'+chr(10)*2
  +'\`\`\`handover-manifest'+chr(10)+json.dumps(man,ensure_ascii=False)+chr(10)+'\`\`\`'+chr(10),
  encoding='utf-8')" "$HR2/handover/tokyo_handover_latest.md"
recv2() { echo "{\"cwd\":\"$HR2\",\"session_id\":\"$1\"}" | CLAUDE_MANUAL_METRICS="$HR2/metrics" python3 .claude/hooks/handover_receipt.py 2>&1; }
o2=$(recv2 n1)
grep -q "枝の名前を決める" <<<"$o2" && chk "開始時に枝名を決めるよう促す" 0 0 || chk "開始時に枝名を決めるよう促す" 0 1
grep -q "2〜3個提案" <<<"$o2" && chk "枝名を提案させてから質問させる" 0 0 || chk "枝名を提案させてから質問させる" 0 1
grep -q "現地調査が残っている" <<<"$o2" && chk "枝名を考える材料（未完了）を渡す" 0 0 || chk "枝名を考える材料（未完了）を渡す" 0 1
grep -q -- "--name <承認された名前>" <<<"$o2" && chk "確定後すぐ保存できる命令を用意する" 0 0 || chk "確定後すぐ保存できる命令を用意する" 0 1
grep -q "kobo_anken_omatsuri\|tokyo_omatsuri\|_〈このセッションで何をするか〉" <<<"$o2" && chk "提案するのは完成したファイル名（枝名だけではない）" 0 0 || chk "提案するのは完成したファイル名（枝名だけではない）" 0 1
grep -q "handover/tokyo/" <<<"$o2" && chk "保存先が案件フォルダになっている" 0 0 || chk "保存先が案件フォルダになっている" 0 1
grep -q "tokyo_handover_latest.md" <<<"$o2" && chk "案件名を埋めた命令を出す" 0 0 || chk "案件名を埋めた命令を出す" 0 1
# すでに枝を持つセッションには、二度と質問させない
python3 -c "
import sys,pathlib,json
p=pathlib.Path(sys.argv[1])
man={'manifest_version':1,'generated_at':'2026-09-02','source':'transcript','session':'n2',
     'cwd':'/w','branch':'','case':'tokyo','lane':'survey','parent':'tokyo_handover_latest.md',
     'counts':{},'chapters':[],'sha256':'x'}
p.write_text('# h'+chr(10)*2+'## 1. 依頼の原文'+chr(10)*2+'本文'+chr(10)*2
  +'\`\`\`handover-manifest'+chr(10)+json.dumps(man,ensure_ascii=False)+chr(10)+'\`\`\`'+chr(10),
  encoding='utf-8')" "$HR2/handover/tokyo.survey_handover_latest.md"
o3=$(recv2 n2)
grep -q "枝は \`survey\`" <<<"$o3" && chk "すでに枝を持つセッションには質問させない" 0 0 || chk "すでに枝を持つセッションには質問させない" 0 1
rm -r "$HR2"
rm -r "$HR"

echo "── auto_update.py（フック本体の自動更新）──"
# v29 で追加。v25〜v28 の修正はすべてフック本体の修正であり、
# コアカードだけを自動更新しても検査の中身は古いままだった（L2 記録参照）。
AU=$(mktemp -d)
mkdir -p "$AU/repo/.claude/hooks" "$AU/repo/latest" "$AU/home/.claude/hooks/manual"
cp .claude/hooks/*.py "$AU/repo/.claude/hooks/"
cp latest/L0_core_card.md latest/latest.json "$AU/repo/latest/"
git -C "$AU/repo" init -q
git -C "$AU/repo" -c user.email=t@t -c user.name=t add -A >/dev/null 2>&1
git -C "$AU/repo" -c user.email=t@t -c user.name=t commit -qm t >/dev/null 2>&1
# origin/main を直接作る（ネットワークに依存させない）
git -C "$AU/repo" update-ref refs/remotes/origin/main HEAD
# 導入済みのフックを「古い状態」にしておく
for f in inject_gate check_output guard_delivery auto_update manual_sync handover_receipt; do
  printf '# ふるいフック\n' > "$AU/home/.claude/hooks/manual/$f.py"
done
out=$(echo '{}' | HOME="$AU/home" CLAUDE_MANUAL_REPO="$AU/repo" python3 .claude/hooks/auto_update.py 2>&1)
grep -q '検査プログラム' <<<"$out" && chk "フック本体が古ければ最新に差し替える" 0 0 || chk "フック本体が古ければ最新に差し替える" 0 1
grep -q 'RE_INCOMPLETE' "$AU/home/.claude/hooks/manual/check_output.py" && chk "差し替え後の中身が配布元と同じ" 0 0 || chk "差し替え後の中身が配布元と同じ" 0 1
[ -f "$AU/home/.claude/hooks/manual/check_output.py.bak" ] && chk "上書きの前に退避（.bak）を残す" 0 0 || chk "上書きの前に退避（.bak）を残す" 0 1
# 壊れたフックは入れない（入れると以後毎ターン作業が止まるため）
printf 'def broken(:\n' > "$AU/repo/.claude/hooks/check_output.py"
git -C "$AU/repo" -c user.email=t@t -c user.name=t commit -qam broken >/dev/null 2>&1
git -C "$AU/repo" update-ref refs/remotes/origin/main HEAD
echo '{}' | HOME="$AU/home" CLAUDE_MANUAL_REPO="$AU/repo" python3 .claude/hooks/auto_update.py >/dev/null 2>&1
grep -q 'RE_INCOMPLETE' "$AU/home/.claude/hooks/manual/check_output.py" && chk "構文が壊れたフックは入れない（異常系）" 0 0 || chk "構文が壊れたフックは入れない（異常系）" 0 1
# 未導入の環境では何もしない
AU2=$(mktemp -d); mkdir -p "$AU2/home"
echo '{}' | HOME="$AU2/home" CLAUDE_MANUAL_REPO="$AU/repo" python3 .claude/hooks/auto_update.py >/dev/null 2>&1
chk "未導入の環境では何もせず止まらない（異常系）" 0 $?
rm -r "$AU" "$AU2"

echo "────────────────────────────"
echo "合格 $pass 件 / 不合格 $fail 件"
rm -f "$CLAUDE_MANUAL_METRICS"/.stopguard-test "$CLAUDE_MANUAL_METRICS"/.terms-test
[ "$fail" -eq 0 ]
