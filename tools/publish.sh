#!/usr/bin/env bash
# 配布の公開（作業ブランチと main の両方へ push する）。
#
# なぜ必要か：配布URLは main を指している。作業ブランチにだけ push すると、
# **手元は最新なのに配布URLは古いまま**という食い違いが起きる。
# 発行のたびに必ず本スクリプトを使い、片方だけ更新する事故を構造的に潰す。
set -euo pipefail
cd "$(dirname "$0")/.."
BR=$(git rev-parse --abbrev-ref HEAD)

echo "── 発行前の検査（1つでも落ちたら公開しない）──"
python3 tools/build_manual.py   > /dev/null
python3 tools/build_mini.py     > /dev/null
python3 tools/build_allinone.py > /dev/null
python3 tools/build_latest.py   > /dev/null
python3 tools/audit_activation.py dist/L1_manual_*.md --records dist/L2_records_*.md | grep -E "到達可能条項|失敗記録"
python3 tools/build_dist.py | tail -1
./tools/test_hooks.sh | tail -1
./tools/test_tools.sh | tail -1

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "[中止] 未コミットの変更がある。コミットしてから実行すること。" >&2
  exit 1
fi

echo "── 公開 ──"
git push -u origin "$BR"
git push origin "HEAD:refs/heads/main"
echo "  [ok] $BR と main の両方へ公開した"

echo "── 配布URLの実測 ──"
sleep 3
for f in latest.json L0_core_card.md manual_all_in_one.md; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 \
    "https://raw.githubusercontent.com/yixima/manual/main/latest/$f")
  echo "  $f → HTTP $code"
  [ "$code" = "200" ] || { echo "[中止] 配布URLが取得できない" >&2; exit 1; }
done
# 配布URLは CDN（配信網）を経由するため、公開直後は数十秒ほど古い版を返すことがある（実測 20〜40秒）。
# **公開したはずの版が実際に配られるまで待って確認する。** 待たずに報告すると、
# 「公開した」と言いながら古い版を配っている状態を見逃す（§3-4 検証ファースト）。
WANT=$(python3 -c "import json;print(json.load(open('latest/latest.json'))['version'])")
for i in 1 2 3 4 5 6 7 8 9 10; do
  GOT=$(curl -s --max-time 20 -H 'Cache-Control: no-cache' \
    https://raw.githubusercontent.com/yixima/manual/main/latest/latest.json \
    | python3 -c "import json,sys;print(json.load(sys.stdin).get('version',''))" 2>/dev/null || true)
  if [ "$GOT" = "$WANT" ]; then
    echo "  配布URLの版: $GOT（期待どおり。${i}回目の確認で一致）"
    exit 0
  fi
  echo "  配布URLはまだ「$GOT」（期待 $WANT）。CDN の反映を待つ…"
  sleep 15
done
echo "[中止] 配布URLが $WANT を返さない。CDN の反映が遅れているか、公開に失敗している。" >&2
exit 1
