#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版上げを自動化する。手作業の置換（sed の手打ち）を廃止するため。

なぜ必要か（実測 2026-09-11）：手打ちの置換指定ミス（/g 落ち・行範囲指定）により、
配布中のカード冒頭が「記録＝L2_records_v55.md」と2版前を指したまま、v56・v57 の
2版にわたり配布された。手作業の儀式は、回数を重ねるほど必ず1回は失敗する。

使い方: python3 tools/bump_version.py v58 [発行日]（発行日省略時＝今日・JST）
やること:
  1. dist の6ファイルを git mv で新版名へ
  2. カード（手書き原本）の**冒頭8行だけ**を新版・新発行日に置換
     （本文中の「（v57で新設）」等の歴史表記は変えない——履歴の改竄になるため）
  3. tools/test_tools.sh・README.md の版表記を置換
  4. tools/build_manual.py の VER/DATE を更新
  5. 検証：カード冒頭8行に新版以外の版表記が残っていないこと（数える検査）
ビルドと発行前検査は行わない——従来どおり build 群と publish.sh を続けて実行すること。
"""
import re, subprocess, sys, pathlib, datetime


def sh(*a):
    subprocess.run(a, check=True)


def main():
    if len(sys.argv) < 2 or not re.fullmatch(r'v\d+', sys.argv[1]):
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    new = sys.argv[1]
    bm_path = pathlib.Path('tools/build_manual.py')
    bm = bm_path.read_text(encoding='utf-8')
    m = re.search(r"VER, DATE = '(v\d+)', '([^']+)'", bm)
    cur, cur_date = m.group(1), m.group(2)
    if cur == new:
        print(f'[FAIL] すでに {new} である', file=sys.stderr)
        sys.exit(1)
    jst = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(jst)
    today = sys.argv[2] if len(sys.argv) > 2 else f'{now.year}年{now.month}月{now.day}日'

    names = ['L0_core_card_{}.md', 'L0_core_card_mini_{}.md', 'L1_manual_{}.md',
             'L2_records_{}.md', 'handover_template_{}.md']
    srcs = [f'dist/{n.format(cur)}' for n in names] + [f'dist/manual_{cur}_all_in_one.md']
    dsts = [f'dist/{n.format(new)}' for n in names] + [f'dist/manual_{new}_all_in_one.md']
    for s, d in zip(srcs, dsts):
        sh('git', 'mv', s, d)
        print(f'  [mv] {s} → {d}')

    # カードの冒頭8行だけを置換（本文の歴史表記は保持する）
    card = pathlib.Path(dsts[0])
    lines = card.read_text(encoding='utf-8').splitlines(True)
    for i in range(min(8, len(lines))):
        lines[i] = lines[i].replace(cur, new).replace(cur_date, today)
    card.write_text(''.join(lines), encoding='utf-8')

    for p in ('tools/test_tools.sh', 'README.md'):
        f = pathlib.Path(p)
        f.write_text(f.read_text(encoding='utf-8').replace(cur, new), encoding='utf-8')

    bm = bm.replace(f"VER, DATE = '{cur}', '{cur_date}'",
                    f"VER, DATE = '{new}', '{today}'")
    bm_path.write_text(bm, encoding='utf-8')

    # 検証（§7-7②：あるべきでないものを数える）
    head = ''.join(card.read_text(encoding='utf-8').splitlines(True)[:8])
    others = sorted(set(re.findall(r'v\d+', head)) - {new})
    if others:
        print(f'[FAIL] カード冒頭8行に旧版表記が残存: {others}', file=sys.stderr)
        sys.exit(1)
    print(f'[ok] {cur} → {new}（発行日 {today}）。カード冒頭に旧版表記なし。'
          f'続けて build_manual → build_mini → build_allinone → build_latest → '
          f'audit_activation → build_dist → test_hooks/test_tools → publish.sh を実行すること。')


if __name__ == '__main__':
    main()
