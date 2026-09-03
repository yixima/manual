#!/usr/bin/env bash
# 同梱スクリプトの起動検証（L1 §8-10）。正常系と異常系の両方を実際に発火させる。
set -uo pipefail
cd "$(dirname "$0")/.."
pass=0; fail=0
chk() { if [ "$2" = "$3" ]; then echo "  [ok] $1"; pass=$((pass+1)); else echo "  [NG] $1  期待=$2 実際=$3"; fail=$((fail+1)); fi; }
TMP=$(mktemp -d)

echo "── audit_activation.py ──"
python3 tools/audit_activation.py dist/L1_manual_v*.md --records dist/L2_records_v*.md > "$TMP/a.txt" 2>&1
chk "正常終了" 0 $?
grep -q "(100%)" "$TMP/a.txt" && chk "到達率100%" 0 0 || chk "到達率100%" 0 1
grep -q "孤立条項(0)" "$TMP/a.txt" && chk "孤立条項0件" 0 0 || chk "孤立条項0件" 0 1
grep -qE "失敗記録        : ([0-9]+) 件 / 捕捉 \1 件" "$TMP/a.txt" && chk "全記録が捕捉されている" 0 0 || chk "全記録が捕捉されている" 0 1

echo "── build_manual.py ──"
python3 tools/build_manual.py > "$TMP/b.txt" 2>&1; chk "正常終了" 0 $?
grep -q "欠落=なし" "$TMP/b.txt" && chk "条項の欠落なし（無省略保持）" 0 0 || chk "条項の欠落なし（無省略保持）" 0 1

echo "── build_dist.py ──"
python3 tools/build_dist.py > "$TMP/c.txt" 2>&1; chk "正常終了（不一致ゼロ）" 0 $?
CARD=$(ls dist/L0_core_card_v[0-9]*.md | tail -1)
cp "$CARD" "$TMP/bak.md"
printf '\n| わざと不一致にする行 | 検査が落ちることの確認 |\n' >> "$CARD"
python3 tools/build_dist.py > /dev/null 2>&1; chk "不一致があれば異常終了する（異常系）" 1 $?
cp "$TMP/bak.md" "$CARD"
python3 tools/build_dist.py > /dev/null 2>&1; chk "復元後は再び合格する" 0 $?

echo "── make_handover.py ──"
python3 tools/make_handover.py --new "$TMP/h.md" > /dev/null 2>&1; chk "雛形を生成できる" 0 $?
python3 tools/make_handover.py --check dist/handover_template_v40.md > /dev/null 2>&1; chk "未記入テンプレートは不合格（異常系）" 1 $?
python3 - "$TMP/h.md" "$TMP/h2.md" <<'PY'
import pathlib, sys
t = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
for s in ["1. 依頼の原文","3. 却下した案","5. セッション中の調整・変更の経緯","6. 失敗と、そこから得た改善",
          "7. 未完了のタスク","8. 次に最初に行うこと","9. 前提条件・数値前提","10. 使用したコマンド・手順"]:
    i = t.find(s); j = t.find('\n## ', i)
    t = t[:j] + "\n実際に記入した中身をここに書いた。十分な分量の記述である。\n" + t[j:]
pathlib.Path(sys.argv[2]).write_text(t, encoding='utf-8')
PY
python3 tools/make_handover.py --check "$TMP/h2.md" > /dev/null 2>&1; chk "全章を埋めれば合格する" 0 $?

# ── 記録からの自動生成（v22 の中核）──
# 本物の記録と同じ形（1行1レコードの JSONL）の見本を作って通す。
# **見本で通ることは、本番で通ることを保証しない**ため、形式は実際の記録から採寸してある。
python3 - "$TMP/t.jsonl" <<'PYT'
import json, sys
rows = [
 {"type":"user","isSidechain":False,"timestamp":"2026-09-01T00:00:00Z","sessionId":"s1",
  "cwd":"/tmp/x","gitBranch":"main","message":{"role":"user","content":"最初の依頼です。仕様はこうしてください。"}},
 {"type":"assistant","isSidechain":False,"timestamp":"2026-09-01T00:01:00Z","sessionId":"s1",
  "message":{"role":"assistant","content":[{"type":"thinking","thinking":"THINKING_MUST_NOT_APPEAR"},
                                            {"type":"text","text":"承知しました。実装します。"}]}},
 {"type":"assistant","isSidechain":False,"timestamp":"2026-09-01T00:02:00Z","sessionId":"s1",
  "message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash",
                                            "input":{"command":"echo hello","description":"挨拶を出す"}}]}},
 {"type":"assistant","isSidechain":False,"timestamp":"2026-09-01T00:03:00Z","sessionId":"s1",
  "message":{"role":"assistant","content":[{"type":"tool_use","name":"Write",
                                            "input":{"file_path":"out/a.py"}}]}},
 {"type":"user","isSidechain":False,"timestamp":"2026-09-01T00:04:00Z","sessionId":"s1",
  "message":{"role":"user","content":[{"type":"tool_result","is_error":True,"content":"コマンドが失敗した"}]}},
 {"type":"user","isSidechain":False,"timestamp":"2026-09-01T00:05:00Z","sessionId":"s1",
  "message":{"role":"user","content":"そこは違います。やめて別の方法にしてください。<system-reminder>自動注記</system-reminder>"}},
 {"type":"assistant","isSidechain":True,"timestamp":"2026-09-01T00:06:00Z","sessionId":"s1",
  "message":{"role":"assistant","content":[{"type":"text","text":"下請けの発言。引き継ぎに混ぜてはいけない。"}]}},
]
open(sys.argv[1],"w",encoding="utf-8").write("\n".join(json.dumps(r,ensure_ascii=False) for r in rows)+"\n")
PYT
python3 tools/make_handover.py --auto "$TMP/auto.md" --transcript "$TMP/t.jsonl" > /dev/null 2>&1
chk "記録から引き継ぎを生成できる" 0 $?
grep -q "最初の依頼です。仕様はこうしてください。" "$TMP/auto.md" && chk "依頼の原文が要約されず入る" 0 0 || chk "依頼の原文が要約されず入る" 0 1
grep -q "承知しました。実装します。" "$TMP/auto.md" && chk "こちらの応答の原文が入る" 0 0 || chk "こちらの応答の原文が入る" 0 1
grep -q "THINKING_MUST_NOT_APPEAR" "$TMP/auto.md" && chk "思考は載せない（ユーザーに示していないもの）" 0 1 || chk "思考は載せない（ユーザーに示していないもの）" 0 0
grep -q "下請けの発言" "$TMP/auto.md" && chk "下請けエージェントの発言を混ぜない" 0 1 || chk "下請けエージェントの発言を混ぜない" 0 0
grep -q "自動注記" "$TMP/auto.md" && chk "自動で差し込まれた注記を原文に混ぜない" 0 1 || chk "自動で差し込まれた注記を原文に混ぜない" 0 0
grep -q "echo hello" "$TMP/auto.md" && chk "実行したコマンドが入る" 0 0 || chk "実行したコマンドが入る" 0 1
grep -q "out/a.py" "$TMP/auto.md" && chk "編集したファイルが入る" 0 0 || chk "編集したファイルが入る" 0 1
grep -q "コマンドが失敗した" "$TMP/auto.md" && chk "記録された失敗が入る" 0 0 || chk "記録された失敗が入る" 0 1
grep -q "やめて別の方法に" "$TMP/auto.md" && chk "訂正・調整の発言が原文で入る" 0 0 || chk "訂正・調整の発言が原文で入る" 0 1
grep -q "【要記入】" "$TMP/auto.md" && chk "理由の欄に【要記入】が置かれる" 0 0 || chk "理由の欄に【要記入】が置かれる" 0 1
python3 tools/make_handover.py --check "$TMP/auto.md" > /dev/null 2>&1
chk "【要記入】が残っていれば検査に落ちる（異常系）" 1 $?

# --- 回帰（v40）：理由を書き足すと指紋が外れる。--seal で封をし直せば --check が通ること ---
# 実測で見つけた設計の矛盾。「理由を埋めよ」と「指紋を保て」が同時に成立していなかった。
python3 - "$TMP/auto.md" "$TMP/sealed.md" <<'PYT'
import pathlib, sys
t = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
pathlib.Path(sys.argv[2]).write_text(t.replace('【要記入】', '理由をここに書いた。十分な分量の記述である。'), encoding='utf-8')
PYT
python3 tools/make_handover.py --check "$TMP/sealed.md" > "$TMP/sc1.txt" 2>&1
chk "理由を埋めただけでは指紋が外れて落ちる（異常系）" 1 $?
grep -q -- "--seal" "$TMP/sc1.txt" && chk "落ちたとき --seal を実行せよと案内する" 0 0 || chk "落ちたとき --seal を実行せよと案内する" 0 1
python3 tools/make_handover.py --seal "$TMP/sealed.md" > /dev/null 2>&1
chk "--seal で封をし直せる" 0 $?
python3 tools/make_handover.py --check "$TMP/sealed.md" > /dev/null 2>&1
chk "封をし直せば --check が通る（回帰）" 0 $?
python3 tools/make_handover.py --receipt "$TMP/sealed.md" > "$TMP/sr.txt" 2>&1
chk "封をし直した引き継ぎは受領も完全になる" 0 $?
grep -q "一致。生成時" "$TMP/sr.txt" && chk "封のあとも指紋一致として報告する" 0 0 || chk "封のあとも指紋一致として報告する" 0 1
python3 tools/make_handover.py --seal "$TMP/sealed.md" > "$TMP/sr2.txt" 2>&1
grep -q "封をし直す必要は無い" "$TMP/sr2.txt" && chk "一致しているファイルへの --seal は何もしない" 0 0 || chk "一致しているファイルへの --seal は何もしない" 0 1

# --- 案件名の機械的な正規化（v40）：2026-09-02 の事案 ---
# ユーザーが「kobo anken」と指定したのに、別のセッションが
# `kobo_anken_hikitsugi_20260902_v1.md` を作った（語を足し、固定名を作らなかった）。
mkj3() { python3 -c "
import sys,pathlib,json
rows=[{'type':'user','sessionId':'sN','timestamp':'2026-09-02T00:00:00Z','cwd':'/w','message':{'role':'user','content':'依頼です'}},
      {'type':'assistant','sessionId':'sN','timestamp':'2026-09-02T00:01:00Z','message':{'role':'assistant','content':[{'type':'text','text':'承知'}]}}]
pathlib.Path(sys.argv[1]).write_text(''.join(json.dumps(r,ensure_ascii=False)+chr(10) for r in rows),encoding='utf-8')" "$1"; }
mkj3 "$TMP/nm.jsonl"
mkdir -p "$TMP/nm"
python3 tools/make_handover.py --auto "$TMP/nm/kobo anken_handover_latest.md" --transcript "$TMP/nm.jsonl" > "$TMP/nm.txt" 2>&1
chk "空白を含む案件名でも保存できる" 0 $?
[ -f "$TMP/nm/kobo_anken_handover_latest.md" ] && chk "空白は _ に直る（§7-11）" 0 0 || chk "空白は _ に直る（§7-11）" 0 1
[ -f "$TMP/nm/kobo_anken_handover_20260902_v1.md" ] || ls "$TMP/nm"/kobo_anken_handover_2*_v1.md >/dev/null 2>&1
chk "日付版も同時に残る（履歴が消えない）" 0 $?
grep -q "語は足していない" "$TMP/nm.txt" && chk "語を足さないと明記する" 0 0 || chk "語を足さないと明記する" 0 1
grep -q "hikitsugi" "$TMP/nm.txt" && chk "規格外の語を勝手に足さない（回帰）" 0 1 || chk "規格外の語を勝手に足さない（回帰）" 0 0
python3 -c "
import sys,pathlib
sys.path.insert(0,'tools')
import make_handover as M
ok = (M.normalize_name('kobo anken')=='kobo_anken'
      and M.normalize_name('東京 案件')==''
      and M.normalize_name('a  b')=='a_b'
      and M.paths_for('kobo_anken')[0]=='kobo_anken_handover_latest.md'
      and M.paths_for('kobo_anken','survey')[0]=='kobo_anken.survey_handover_latest.md')
sys.exit(0 if ok else 1)"
chk "正規化と命名の規則が仕様どおり" 0 $?
# 回帰（v40）：原文の中に現れた「【要記入】」を記入欄と数えない。
# 実測：検査の合格出力「[ok] 【要記入】 が残っていない」が記録に取り込まれ、未記入1件として差し戻された。
python3 -c "
import sys; sys.path.insert(0,'tools')
import make_handover as M
ok = (M.todo_count('| 1 | 【要記入】 | 【要記入】 |')==2
      and M.todo_count('1. 【要記入】')==1
      and M.todo_count('- **理由**：【要記入】（説明）')==1
      and M.todo_count('| 18 | 09-02 | [ok] 【要記入】 が残っていない [ok] 完了 | 〔任意〕 |')==0
      and M.todo_count('検査で 【要記入】 が3件見つかった。')==0)
sys.exit(0 if ok else 1)"
chk "原文の中の「【要記入】」を記入欄と数えない（回帰）" 0 $?
python3 tools/make_handover.py --auto "$TMP/nm/東京案件_handover_latest.md" --transcript "$TMP/nm.jsonl" > "$TMP/nm2.txt" 2>&1
chk "英数を含まない案件名では勝手に名前を付けず止まる（異常系）" 1 $?
grep -q "一つだけ質問" "$TMP/nm2.txt" && chk "止めたとき質問するよう促す" 0 0 || chk "止めたとき質問するよう促す" 0 1

# --- 承認された名前で作る／案件フォルダで整理する（v40）---
mkdir -p "$TMP/rc/handover"
mkj3 "$TMP/rc.jsonl"
python3 tools/make_handover.py --auto "$TMP/rc/handover/dummy.md" --name "kobo anken omatsuri" --case kobo_anken --transcript "$TMP/rc.jsonl" > "$TMP/rc.txt" 2>&1
chk "承認された名前で保存できる" 0 $?
head -30 "$TMP/rc/handover/kobo_anken/kobo_anken_omatsuri_handover_latest.md" > "$TMP/hd.txt"
grep -q "受け取ったセッションが、最初にすること" "$TMP/hd.txt" && chk "引き継ぎの先頭に「最初にすること」が入る（v40）" 0 0 || chk "引き継ぎの先頭に「最初にすること」が入る（v40）" 0 1
grep -q "他の作業に着手しない" "$TMP/hd.txt" && chk "他の作業より先だと明記する（順序）" 0 0 || chk "他の作業より先だと明記する（順序）" 0 1
grep -q "他の質問と束ねない" "$TMP/hd.txt" && chk "他の質問と束ねないと明記する（回帰）" 0 0 || chk "他の質問と束ねないと明記する（回帰）" 0 1
grep -q "候補を出すのがこちらの仕事" "$TMP/hd.txt" && chk "丸投げを禁じている" 0 0 || chk "丸投げを禁じている" 0 1
[ -f "$TMP/rc/handover/kobo_anken/kobo_anken_omatsuri_handover_latest.md" ] && chk "案件フォルダの中に固定名で入る" 0 0 || chk "案件フォルダの中に固定名で入る" 0 1
ls "$TMP/rc/handover/kobo_anken"/kobo_anken_omatsuri_handover_2*_v1.md >/dev/null 2>&1
chk "枝にも自分の日付版ができる（親のと混ざらない）" 0 $?
grep -q "語は足していない" "$TMP/rc.txt" && chk "承認名の調整でも語を足さないと明記する" 0 0 || chk "承認名の調整でも語を足さないと明記する" 0 1
python3 tools/make_handover.py --auto "$TMP/rc/handover/dummy.md" --name "kobo_anken_setsubi" --case kobo_anken --transcript "$TMP/rc.jsonl" > /dev/null 2>&1
[ -f "$TMP/rc/handover/kobo_anken/kobo_anken_setsubi_handover_latest.md" ] && chk "別の枝は別ファイルになる（上書きしない）" 0 0 || chk "別の枝は別ファイルになる（上書きしない）" 0 1
[ -f "$TMP/rc/handover/kobo_anken/kobo_anken_omatsuri_handover_latest.md" ] && chk "先の枝が消えていない（回帰）" 0 0 || chk "先の枝が消えていない（回帰）" 0 1
# 片付け（移動のみ・消さない）
mkdir -p "$TMP/td/handover"
for f in a_handover_latest.md a_handover_20260902_v1.md b_handover_latest.md; do echo '# h' > "$TMP/td/handover/$f"; done
python3 tools/make_handover.py --tidy "$TMP/td/handover" > "$TMP/td.txt" 2>&1
chk "受け口を案件ごとに片付けられる" 0 $?
[ -f "$TMP/td/handover/a/a_handover_latest.md" ] && [ -f "$TMP/td/handover/b/b_handover_latest.md" ] && chk "案件ごとのフォルダへ移る" 0 0 || chk "案件ごとのフォルダへ移る" 0 1
grep -q "1件も消していない" "$TMP/td.txt" && chk "件数を照合して消していないと報告する" 0 0 || chk "件数を照合して消していないと報告する" 0 1
n_td=$(find "$TMP/td/handover" -type f | wc -l)
[ "$n_td" = "3" ] && chk "片付けで件数が変わらない（§8-5）" 0 0 || chk "片付けで件数が変わらない（§8-5）" 0 1


# --- 枝分かれ（v40）：1つの作業が2つ以上のセッションへ分かれるとき ---
mkj2() { python3 -c "
import sys,pathlib,json
sid=sys.argv[2]
rows=[{'type':'user','sessionId':sid,'timestamp':'2026-09-02T00:00:00Z','cwd':'/w','message':{'role':'user','content':'依頼です'}},
      {'type':'assistant','sessionId':sid,'timestamp':'2026-09-02T00:01:00Z','message':{'role':'assistant','content':[{'type':'text','text':'承知'}]}}]
pathlib.Path(sys.argv[1]).write_text(''.join(json.dumps(r,ensure_ascii=False)+chr(10) for r in rows),encoding='utf-8')" "$1" "$2"; }
mkj2 "$TMP/la.jsonl" sessA
mkj2 "$TMP/lb.jsonl" sessB
python3 tools/make_handover.py --auto "$TMP/case_handover_latest.md" --transcript "$TMP/la.jsonl" >/dev/null 2>&1
chk "親の引き継ぎを作れる" 0 $?
python3 tools/make_handover.py --auto "$TMP/case_handover_latest.md" --transcript "$TMP/lb.jsonl" > "$TMP/g.txt" 2>&1
chk "別セッションが枝名なしで上書きしようとすると止まる（異常系）" 1 $?
grep -q -- "--lane" "$TMP/g.txt" && chk "止めたとき --lane の使い方を案内する" 0 0 || chk "止めたとき --lane の使い方を案内する" 0 1
python3 - "$TMP/case_handover_latest.md" <<'PYT'
import pathlib,sys,re,json
t=pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
d=json.loads(re.search(r'```handover-manifest\n(.*?)\n```',t,re.S).group(1))
sys.exit(0 if d.get('session')=='sessA' else 1)
PYT
chk "先に保存された引き継ぎが消えていない（回帰）" 0 $?
python3 tools/make_handover.py --auto "$TMP/case_handover_latest.md" --transcript "$TMP/lb.jsonl" --lane design --parent case_handover_latest.md >/dev/null 2>&1
chk "枝名を付ければ保存できる" 0 $?
[ -f "$TMP/case.design_handover_latest.md" ] && chk "枝ごとに別のファイル名になる" 0 0 || chk "枝ごとに別のファイル名になる" 0 1
python3 tools/make_handover.py --auto "$TMP/case_handover_latest.md" --transcript "$TMP/la.jsonl" --lane survey --parent case_handover_latest.md >/dev/null 2>&1
python3 tools/make_handover.py --merge "$TMP/case_merged.md" --from "$TMP/case.survey_handover_latest.md" "$TMP/case.design_handover_latest.md" >/dev/null 2>&1
chk "枝を合流できる" 0 $?
grep -q "どの枝が何を持っているか" "$TMP/case_merged.md" && chk "合流ファイルに枝の一覧が入る" 0 0 || chk "合流ファイルに枝の一覧が入る" 0 1
grep -q "survey" "$TMP/case_merged.md" && grep -q "design" "$TMP/case_merged.md" && chk "合流で両方の枝の全文が残る（要約しない）" 0 0 || chk "合流で両方の枝の全文が残る（要約しない）" 0 1
python3 tools/make_handover.py --merge "$TMP/x.md" --from "$TMP/case.survey_handover_latest.md" >/dev/null 2>&1
chk "枝が1本だけなら合流しない（異常系）" 1 $?

echo "── 受領確認（--receipt）──"
python3 tools/make_handover.py --receipt "$TMP/auto.md" > "$TMP/r.txt" 2>&1
chk "未記入が残る引き継ぎは受領不完全になる（異常系）" 1 $?
grep -q "一致。生成時" "$TMP/r.txt" && chk "指紋が一致していることを報告する" 0 0 || chk "指紋が一致していることを報告する" 0 1
grep -q "依頼の原文: 2" "$TMP/r.txt" && chk "受領した件数を数えて報告する" 0 0 || chk "受領した件数を数えて報告する" 0 1
python3 - "$TMP/auto.md" "$TMP/tampered.md" <<'PYT'
import pathlib, sys
t = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
pathlib.Path(sys.argv[2]).write_text(t.replace('最初の依頼です', '書き換えられた依頼です'), encoding='utf-8')
PYT
# パイプで受けない：--receipt は不合格時に 1 で終わるため、pipefail が grep の成否を上書きしてしまう
python3 tools/make_handover.py --receipt "$TMP/tampered.md" > "$TMP/rt.txt" 2>&1
grep -q "本文は生成時から変わっている" "$TMP/rt.txt" && chk "生成後に書き換えられたら検出する" 0 0 || chk "生成後に書き換えられたら検出する" 0 1
# **整形と書き換えは機械で区別できない。** 区別できるかのように報告しないこと
grep -q "区別することはできない" "$TMP/rt.txt" && chk "整形と書き換えを区別できないと明記する" 0 0 || chk "整形と書き換えを区別できないと明記する" 0 1
# 【要記入】を埋めれば受領が完全になること
python3 - "$TMP/auto.md" "$TMP/filled.md" <<'PYT'
import pathlib, sys, re, hashlib
t = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
old = re.search(r'"sha256": "([0-9a-f]{64})"', t).group(1)
t = t.replace('【要記入】', '理由をここに書いた。十分な分量の記述である。')
t = t.replace(old, 'PENDING-SHA256')
t = t.replace('PENDING-SHA256', hashlib.sha256(t.encode('utf-8')).hexdigest())
pathlib.Path(sys.argv[2]).write_text(t, encoding='utf-8')
PYT
python3 tools/make_handover.py --receipt "$TMP/filled.md" > /dev/null 2>&1
chk "全部埋まっていれば受領が完全になる" 0 $?
python3 tools/make_handover.py --receipt "$TMP/none.md" > /dev/null 2>&1
chk "ファイルが無ければ異常終了（異常系）" 1 $?

# ── 検査は「記入欄」だけを見る（原文は検査しない）──
# 原本主義（§10-5）で原文をそのまま運ぶ以上、原文の中に検査用の目印が現れることはありうる。
# それを未記入と数えると、**記入済みのファイルがいつまでも合格しない**（実測で発見した不具合）。
python3 - "$TMP/filled.md" "$TMP/verbatim.md" <<'PYT'
import pathlib, sys, re, hashlib
t = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
old = re.search(r'"sha256": "([0-9a-f]{64})"', t).group(1)
# ①引用（原文）の中 ②コードブロック（実行したコマンド）の中 ③行内コード（その言葉について書いた文）
t = t.replace('## 1. 依頼の原文\n', '## 1. 依頼の原文\n\n> ユーザーの発言に【要記入】と書いてあった。\n')
t = t.replace('## 10. 使用したコマンド・手順\n',
              '## 10. 使用したコマンド・手順\n\n````bash\necho "【要記入】"\n```\necho "内側の3個で閉じない"\n````\n')
t = t.replace('## 6. 失敗と、そこから得た改善\n',
              '## 6. 失敗と、そこから得た改善\n\n`【要記入】` が42箇所残っていた、という失敗の記録である。\n')
t = t.replace(old, 'PENDING-SHA256')
t = t.replace('PENDING-SHA256', hashlib.sha256(t.encode('utf-8')).hexdigest())
pathlib.Path(sys.argv[2]).write_text(t, encoding='utf-8')
PYT
python3 tools/make_handover.py --check "$TMP/verbatim.md" > "$TMP/v.txt" 2>&1
chk "原文・コード・行内コードの中の目印を未記入と数えない" 0 $?
grep -q "章の中身がほとんど無い" "$TMP/v.txt" && chk "引用だけで構成された章を空と判定しない" 0 1 || chk "引用だけで構成された章を空と判定しない" 0 0
# 記入欄が本当に空なら、ちゃんと落ちること（見逃しの回帰テスト）
python3 - "$TMP/filled.md" "$TMP/blank.md" <<'PYT'
import pathlib, sys, re, hashlib
t = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
old = re.search(r'"sha256": "([0-9a-f]{64})"', t).group(1)
t = t.replace('## 8. 次に最初に行うこと\n', '## 8. 次に最初に行うこと\n\n1. 【要記入】\n')
t = t.replace(old, 'PENDING-SHA256')
t = t.replace('PENDING-SHA256', hashlib.sha256(t.encode('utf-8')).hexdigest())
pathlib.Path(sys.argv[2]).write_text(t, encoding='utf-8')
PYT
python3 tools/make_handover.py --check "$TMP/blank.md" > /dev/null 2>&1
chk "記入欄が空なら落ちる（見逃しの回帰）" 1 $?

echo "── build_mini.py ──"
python3 tools/build_mini.py > /dev/null 2>&1; chk "短縮版を生成できる" 0 $?
[ -f "$(ls dist/L0_core_card_mini_v[0-9]*.md 2>/dev/null | tail -1)" ] && chk "短縮版が出力される" 0 0 || chk "短縮版が出力される" 0 1
grep -q "関門" "$(ls dist/L0_core_card_mini_v[0-9]*.md 2>/dev/null | tail -1)" && chk "短縮版に関門が含まれる" 0 0 || chk "短縮版に関門が含まれる" 0 1

echo "── install.py ──"
FH="$TMP/fakehome"; mkdir -p "$FH/.claude"
printf '# 既存メモ\n\n消えてはいけない内容。\n' > "$FH/.claude/CLAUDE.md"
printf '{"permissions":{"allow":["Bash(ls:*)"]},"hooks":{"Stop":[{"matcher":"*","hooks":[{"type":"command","command":"echo mine"}]}]}}' > "$FH/.claude/settings.json"
python3 tools/install.py --home "$FH" --dry-run > /dev/null 2>&1; chk "試行モードが動く" 0 $?
grep -q '消えてはいけない内容' "$FH/.claude/CLAUDE.md" && chk "試行モードは何も書き換えない" 0 0 || chk "試行モードは何も書き換えない" 0 1
python3 tools/install.py --home "$FH" > /dev/null 2>&1; chk "本実行が動く" 0 $?
grep -q '消えてはいけない内容' "$FH/.claude/CLAUDE.md" && chk "既存の CLAUDE.md を消さない" 0 0 || chk "既存の CLAUDE.md を消さない" 0 1
python3 - "$FH" <<'PYX'
import json, sys, pathlib
d = json.load(open(pathlib.Path(sys.argv[1]) / '.claude' / 'settings.json'))
ok = ('allow' in d.get('permissions', {})
      and any(h['command'] == 'echo mine' for g in d['hooks']['Stop'] for h in g['hooks'])
      and sorted(d['hooks']) == ['PreToolUse', 'SessionStart', 'Stop', 'UserPromptSubmit'])
sys.exit(0 if ok else 1)
PYX
chk "既存 settings を保持しつつフックを登録する" 0 $?
python3 tools/install.py --home "$FH" > /dev/null 2>&1
python3 - "$FH" <<'PYX'
import json, sys, pathlib
h = pathlib.Path(sys.argv[1]) / '.claude'
d = json.load(open(h / 'settings.json'))
n = sum(len(g['hooks']) for g in d['hooks']['Stop'])
dup = (h / 'CLAUDE.md').read_text(encoding='utf-8').count('BEGIN 汎用マニュアル')
sys.exit(0 if (n == 2 and dup == 1) else 1)
PYX
chk "2回実行しても二重登録されない（冪等性）" 0 $?
[ -x "$FH/.claude/hooks/manual/check_output.py" ] && chk "フックが実行可能な形で配置される" 0 0 || chk "フックが実行可能な形で配置される" 0 1

echo "── build_latest.py（固定URL用）──"
python3 tools/build_latest.py > /dev/null 2>&1; chk "生成できる" 0 $?
[ -f latest/L0_core_card.md ] && chk "版番号を含まないコアカードが出る" 0 0 || chk "版番号を含まないコアカードが出る" 0 1
[ -f dist/bootloader.md ] && chk "ブートローダーが出る" 0 0 || chk "ブートローダーが出る" 0 1
python3 -c "
import json,pathlib,sys
m=json.loads(pathlib.Path('latest/latest.json').read_text(encoding='utf-8'))
sys.exit(0 if m['version'].startswith('v') and m['core_card'].startswith('https://') else 1)"
chk "latest.json に版と取得先が入っている" 0 $?
grep -q "マニュアル更新" dist/bootloader.md && chk "更新用の発動キーワードが載っている" 0 0 || chk "更新用の発動キーワードが載っている" 0 1
grep -q "関門" dist/bootloader.md && chk "取得失敗時のフォールバックが載っている" 0 0 || chk "取得失敗時のフォールバックが載っている" 0 1
[ "$(wc -l < dist/bootloader.md)" -lt 80 ] && chk "ブートローダーが80行未満（貼りやすさ）" 0 0 || chk "ブートローダーが80行未満（貼りやすさ）" 0 1

echo "── auto_update.py（自動更新フック）──"
grep -q "origin/main" .claude/hooks/auto_update.py && chk "配布元（origin/main）から直接読む設計になっている" 0 0 || chk "配布元（origin/main）から直接読む設計になっている" 0 1
grep -q "SANDBOX_HELP" tools/install.py && chk "サンドボックス拒否に案内を出す" 0 0 || chk "サンドボックス拒否に案内を出す" 0 1
python3 -c "
import ast,sys
for f in ('.claude/hooks/auto_update.py','tools/install.py'):
    ast.parse(open(f,encoding='utf-8').read())" && chk "両ファイルの構文が妥当" 0 0 || chk "両ファイルの構文が妥当" 0 1
echo '{}' | CLAUDE_MANUAL_REPO=/nonexistent python3 .claude/hooks/auto_update.py > "$TMP/au.txt" 2>&1
chk "置き場が無くても止まらない（異常系）" 0 $?
[ ! -s "$TMP/au.txt" ] && chk "置き場が無いときは何も出さない" 0 0 || chk "置き場が無いときは何も出さない" 0 1
echo 'not json' | python3 .claude/hooks/auto_update.py > /dev/null 2>&1; chk "壊れた入力でも止まらない（異常系）" 0 $?

echo "── manual_sync.py（進行中セッションへの自動反映）──"
MC="$TMP/mcache"; mkdir -p "$MC"
echo "{\"cwd\":\"$PWD\"}" | CLAUDE_MANUAL_CACHE="$MC" python3 .claude/hooks/manual_sync.py > /dev/null 2>&1
chk "配布元から取得できる" 0 $?
[ -f "$MC/latest.json" ] && chk "版の情報を保存する" 0 0 || chk "版の情報を保存する" 0 1
[ -f "$MC/pending" ] && chk "次ターンで流し込む印を残す" 0 0 || chk "次ターンで流し込む印を残す" 0 1
n1=$(echo "{\"cwd\":\"$PWD\",\"transcript_path\":\"/x\"}" | CLAUDE_MANUAL_CACHE="$MC" python3 .claude/hooks/inject_gate.py | grep -c "自動更新")
[ "$n1" = "1" ] && chk "更新があれば新しいコアカードを流し込む" 0 0 || chk "更新があれば新しいコアカードを流し込む" 0 1
n2=$(echo "{\"cwd\":\"$PWD\",\"transcript_path\":\"/x\"}" | CLAUDE_MANUAL_CACHE="$MC" python3 .claude/hooks/inject_gate.py | grep -c "自動更新")
[ "$n2" = "0" ] && chk "流し込みは1回だけ（重複しない）" 0 0 || chk "流し込みは1回だけ（重複しない）" 0 1
echo "{\"cwd\":\"$PWD\"}" | CLAUDE_MANUAL_CACHE="$MC" python3 .claude/hooks/manual_sync.py > /dev/null 2>&1
chk "変化がなければ何もしない" 0 $?
echo "{\"cwd\":\"$PWD\"}" | CLAUDE_MANUAL_CACHE="$MC" CLAUDE_MANUAL_SYNC_OFF=1 python3 .claude/hooks/manual_sync.py > /dev/null 2>&1
chk "通信できなくても止まらない（異常系）" 0 $?
grep -q "1-2. 更新の取り込み" dist/bootloader.md && chk "ブートローダーに照合規則が載っている" 0 0 || chk "ブートローダーに照合規則が載っている" 0 1

echo "── score_session.py ──"
python3 tools/score_session.py "$TMP/none.jsonl" > /dev/null 2>&1; chk "記録が無ければ異常終了（異常系）" 1 $?
printf '{"ts":"t","session":"a","contract":{"has_label":true,"has_state_line":true,"has_backcheck":false},"violations":[]}\n' > "$TMP/m.jsonl"
python3 tools/score_session.py "$TMP/m.jsonl" > /dev/null 2>&1; chk "記録があれば集計できる" 0 $?

echo "── make_audit_package.py ──"
printf '応答1。\n---\n連絡先 a@b.com パス /home/user/x\n' > "$TMP/s.txt"
python3 tools/make_audit_package.py --text "$TMP/s.txt" -o "$TMP/o.md" > /dev/null 2>&1; chk "サンプルを切り出せる" 0 $?
grep -q '<メールアドレス>' "$TMP/o.md" && chk "メールアドレスを匿名化する" 0 0 || chk "メールアドレスを匿名化する" 0 1
grep -q '/home/<ユーザー>' "$TMP/o.md" && chk "絶対パスを匿名化する" 0 0 || chk "絶対パスを匿名化する" 0 1

echo "────────────────────────────"
echo "合格 $pass 件 / 不合格 $fail 件"
rm -r "$TMP"
[ "$fail" -eq 0 ]
