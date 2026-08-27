#!/usr/bin/env bash
# 同梱スクリプトの起動検証（L1 §8-10）。正常系と異常系の両方を実際に発火させる。
set -uo pipefail
cd "$(dirname "$0")/.."
pass=0; fail=0
chk() { if [ "$2" = "$3" ]; then echo "  [ok] $1"; pass=$((pass+1)); else echo "  [NG] $1  期待=$2 実際=$3"; fail=$((fail+1)); fi; }
TMP=$(mktemp -d)

echo "── audit_activation.py ──"
python3 tools/audit_activation.py dist/L1_manual_v17.md --records dist/L2_records_v17.md > "$TMP/a.txt" 2>&1
chk "正常終了" 0 $?
grep -q "(100%)" "$TMP/a.txt" && chk "到達率100%" 0 0 || chk "到達率100%" 0 1
grep -q "孤立条項(0)" "$TMP/a.txt" && chk "孤立条項0件" 0 0 || chk "孤立条項0件" 0 1
grep -qE "失敗記録        : ([0-9]+) 件 / 捕捉 \1 件" "$TMP/a.txt" && chk "全記録が捕捉されている" 0 0 || chk "全記録が捕捉されている" 0 1

echo "── build_manual.py ──"
python3 tools/build_manual.py > "$TMP/b.txt" 2>&1; chk "正常終了" 0 $?
grep -q "欠落=なし" "$TMP/b.txt" && chk "条項の欠落なし（無省略保持）" 0 0 || chk "条項の欠落なし（無省略保持）" 0 1

echo "── build_dist.py ──"
python3 tools/build_dist.py > "$TMP/c.txt" 2>&1; chk "正常終了（不一致ゼロ）" 0 $?
cp dist/L0_core_card_v17.md "$TMP/bak.md"
printf '\n| わざと不一致にする行 | 検査が落ちることの確認 |\n' >> dist/L0_core_card_v17.md
python3 tools/build_dist.py > /dev/null 2>&1; chk "不一致があれば異常終了する（異常系）" 1 $?
cp "$TMP/bak.md" dist/L0_core_card_v17.md
python3 tools/build_dist.py > /dev/null 2>&1; chk "復元後は再び合格する" 0 $?

echo "── make_handover.py ──"
python3 tools/make_handover.py --new "$TMP/h.md" > /dev/null 2>&1; chk "雛形を生成できる" 0 $?
python3 tools/make_handover.py --check dist/handover_template_v17.md > /dev/null 2>&1; chk "未記入テンプレートは不合格（異常系）" 1 $?
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
