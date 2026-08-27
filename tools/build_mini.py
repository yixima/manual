#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""コアカードの短縮版を、コアカード本体から機械的に生成する。

用途：claude.ai の「Claudeへの指示」欄など、文字数の上限で全文が入らない場合の代替。
**手で書き写さない。** 手で作ると必ず版がずれるため、本体から抜き出して作る。
抜き出すのは「毎ターン必ず効いていないと困る部分」だけ——出力契約・関門9項・作業の終わり方・限界。
自動発動表と判断フローは落とす（本編 L1 側に残るため、免除にはならない）。
"""
import re, sys, pathlib, argparse

def section(t, start, end=None):
    i = t.find(start)
    if i < 0:
        print(f'[FAIL] 見出しが見つからない: {start}', file=sys.stderr); sys.exit(1)
    j = t.find(end, i) if end else len(t)
    return t[i:j if j > 0 else len(t)].rstrip() + "\n"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', help='出力先を変える（検査用。既定は dist/ に書く）')
    a = ap.parse_args()
    src = sorted(pathlib.Path('dist').glob('L0_core_card_v*.md'))[-1]
    ver = re.search(r'(v\d+)', src.name).group(1)
    t = src.read_text(encoding='utf-8')
    head = t[:t.find('## 0. 環境プロファイル')].rstrip()
    out = (head.replace(f'コアカード（L0・常時発動）', f'コアカード短縮版（L0-mini・常時発動）')
           + "\n>\n> **本書は `" + src.name + "` の短縮版である。** 設定欄の文字数制限で全文が入らない場合にのみ用いる。"
             "\n> 落としたのは「作業種別の自動発動表」と「確認の判断フロー」の2つだけであり、**それらが免除されるわけではない**（本編 L1 に存在する）。\n\n"
           + "## 0. 環境プロファイル（最初に1回だけ判定する）\n\n"
             "**[Chat]**＝claude.ai：コード・コマンドは表示しない（結果のみ日本語で報告）／ファイルは共有機能で渡す／実行はユーザー／長時間処理は同一応答内で完了する範囲に限る。\n"
             "**[Cowork]**：コード表示は必要最小限／ファイルまたは作業フォルダ／実行は併用。\n"
             "**[Code]**＝Claude Code：コード表示は可（それが動作の本体）／コミットしてパスを明示／実行は自分／バックグラウンド可だが結果確認まで自分で行う。\n"
             "**判定できないときは [Chat] を既定とする（最も安全側）。環境で変わるのは手段だけで、要求水準は同一。**\n\n"
           + section(t, '## 1. 出力契約', '## 3. 作業種別')
           + section(t, '## 5.5 作業の終わり方', '## 6. このカードの限界')
           + section(t, '## 6. このカードの限界'))
    dst = pathlib.Path(a.out) if a.out else pathlib.Path('dist') / f'L0_core_card_mini_{ver}.md'
    dst.write_text(out, encoding='utf-8')
    n, c = len(out.splitlines()), len(out)
    print(f'{dst} を生成した（{n} 行 / {c} 文字。本体は {len(t.splitlines())} 行 / {len(t)} 文字）')
    return 0

if __name__ == '__main__':
    sys.exit(main())
