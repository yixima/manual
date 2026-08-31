#!/usr/bin/env bash
# 同梱スクリプトの起動検証（L1 §8-10）。正常系と異常系の両方を実際に発火させる。
set -uo pipefail
cd "$(dirname "$0")/.."
pass=0; fail=0
chk() { if [ "$2" = "$3" ]; then echo "  [ok] $1"; pass=$((pass+1)); else echo "  [NG] $1  期待=$2 実際=$3"; fail=$((fail+1)); fi; }
TMP=$(mktemp -d)

echo "── audit_activation.py ──"
python3 tools/audit_activation.py dist/L1_manual_v20.md --records dist/L2_records_v20.md > "$TMP/a.txt" 2>&1
chk "正常終了" 0 $?
grep -q "(100%)" "$TMP/a.txt" && chk "到達率100%" 0 0 || chk "到達率100%" 0 1
grep -q "孤立条項(0)" "$TMP/a.txt" && chk "孤立条項0件" 0 0 || chk "孤立条項0件" 0 1
grep -qE "失敗記録        : ([0-9]+) 件 / 捕捉 \1 件" "$TMP/a.txt" && chk "全記録が捕捉されている" 0 0 || chk "全記録が捕捉されている" 0 1

echo "── build_manual.py ──"
python3 tools/build_manual.py > "$TMP/b.txt" 2>&1; chk "正常終了" 0 $?
grep -q "欠落=なし" "$TMP/b.txt" && chk "条項の欠落なし（無省略保持）" 0 0 || chk "条項の欠落なし（無省略保持）" 0 1

echo "── build_dist.py ──"
python3 tools/build_dist.py > "$TMP/c.txt" 2>&1; chk "正常終了（不一致ゼロ）" 0 $?
cp dist/L0_core_card_v20.md "$TMP/bak.md"
printf '\n| わざと不一致にする行 | 検査が落ちることの確認 |\n' >> dist/L0_core_card_v20.md
python3 tools/build_dist.py > /dev/null 2>&1; chk "不一致があれば異常終了する（異常系）" 1 $?
cp "$TMP/bak.md" dist/L0_core_card_v20.md
python3 tools/build_dist.py > /dev/null 2>&1; chk "復元後は再び合格する" 0 $?

echo "── make_handover.py ──"
python3 tools/make_handover.py --new "$TMP/h.md" > /dev/null 2>&1; chk "雛形を生成できる" 0 $?
python3 tools/make_handover.py --check dist/handover_template_v20.md > /dev/null 2>&1; chk "未記入テンプレートは不合格（異常系）" 1 $?
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

echo "── build_mini.py ──"
python3 tools/build_mini.py > /dev/null 2>&1; chk "短縮版を生成できる" 0 $?
[ -f dist/L0_core_card_mini_v20.md ] && chk "短縮版が出力される" 0 0 || chk "短縮版が出力される" 0 1
grep -q "関門" dist/L0_core_card_mini_v20.md && chk "短縮版に関門が含まれる" 0 0 || chk "短縮版に関門が含まれる" 0 1

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
