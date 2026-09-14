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

echo "── 鏡（jsDelivr）のキャッシュを即時パージ ──"
# なぜ必要か（実測 2026-09-14）：鏡は放置すると最大12時間前の版を返す。パージAPIを叩けば数秒で最新になる。
# パージの失敗は公開の失敗ではない（鏡は自然にも更新される）ため、警告に留めて続行する。
for f in latest.json L0_core_card.md manual_all_in_one.md; do
  pst=$(curl -s --max-time 20 "https://purge.jsdelivr.net/gh/yixima/manual@main/latest/$f" \
    | python3 -c "import json,sys;print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo failed)
  echo "  purge $f → $pst"
done

echo "── 配布URLの実測（両経路・?t= 付き） ──"
sleep 3
for f in latest.json L0_core_card.md manual_all_in_one.md; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 \
    "https://raw.githubusercontent.com/yixima/manual/main/latest/$f?t=$(date +%s%N)")
  echo "  $f → HTTP $code"
  [ "$code" = "200" ] || { echo "[中止] 配布URLが取得できない" >&2; exit 1; }
done
# 配布URLは CDN（配信網）を経由するため、公開直後は古い版を返すことがある。
# **公開したはずの版が実際に配られるまで待って確認する。** 待たずに報告すると、
# 「公開した」と言いながら古い版を配っている状態を見逃す（§3-4 検証ファースト）。
# 照合には必ず ?t=（毎回変わる値）を付ける——付けない照合は古いキャッシュ側に留まり、
# 反映済みなのに「まだ」と誤判定する（実測 2026-09-14：?t= 無しは数分遅れた）。
WANT=$(python3 -c "import json;print(json.load(open('latest/latest.json'))['version'])")
ok_main=""; ok_mirror=""
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  [ -z "$ok_main" ] && {
    GOT=$(curl -s --max-time 20 "https://raw.githubusercontent.com/yixima/manual/main/latest/latest.json?t=$(date +%s%N)" \
      | python3 -c "import json,sys;print(json.load(sys.stdin).get('version',''))" 2>/dev/null || true)
    [ "$GOT" = "$WANT" ] && ok_main=$i || echo "  主経路はまだ「$GOT」（期待 $WANT）"
  }
  [ -z "$ok_mirror" ] && {
    GOTM=$(curl -s --max-time 20 "https://cdn.jsdelivr.net/gh/yixima/manual@main/latest/latest.json?t=$(date +%s%N)" \
      | python3 -c "import json,sys;print(json.load(sys.stdin).get('version',''))" 2>/dev/null || true)
    [ "$GOTM" = "$WANT" ] && ok_mirror=$i || echo "  鏡はまだ「$GOTM」（期待 $WANT）"
  }
  if [ -n "$ok_main" ] && [ -n "$ok_mirror" ]; then
    echo "  配布の版: $WANT（主=${ok_main}回目・鏡=${ok_mirror}回目の確認で一致）"
    exit 0
  fi
  sleep 15
done
echo "[中止] 配布が $WANT を返さない（主=${ok_main:-未}・鏡=${ok_mirror:-未}）。CDN の反映が遅れているか、公開に失敗している。" >&2
exit 1
