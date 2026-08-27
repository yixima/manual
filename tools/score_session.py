#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""実運用の遵守度を集計する（L1 §0-12 の外部指標①）。

入力：metrics/compliance.jsonl（Stop フックが全ターン自動記録したもの）
出力：出力契約の充足率と、違反の型別内訳。

**この数値が測るのは「出力契約を満たしたか」であって「内容が正しいか」ではない。**
内容の質は chatgpt/prompt_02_blind_grader.md による盲検採点で測る。両方を見ること。
"""
import json, sys, pathlib, collections

def main(path='metrics/compliance.jsonl', session=None):
    p = pathlib.Path(path)
    if not p.exists():
        print(f'{path} が無い。フックがまだ1度も動いていない可能性がある。')
        print('確認：.claude/settings.json の Stop フックが有効か、`/context` でフックが読まれているか。')
        return 1
    rows = []
    for ln in p.read_text(encoding='utf-8').splitlines():
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if session and r.get('session') != session:
            continue
        rows.append(r)
    if not rows:
        print('該当する記録が無い。'); return 1

    n = len(rows)
    viol = collections.Counter(t for r in rows for t in r.get('violations', []))
    clean = sum(1 for r in rows if not r.get('violations'))
    lab = sum(1 for r in rows if r.get('contract', {}).get('has_label'))
    st = sum(1 for r in rows if r.get('contract', {}).get('has_state_line'))
    bc = sum(1 for r in rows if r.get('contract', {}).get('has_backcheck'))
    sess = len({r.get('session') for r in rows})

    def pct(x): return f'{x*100/n:5.1f}%  ({x}/{n})'
    print('── 遵守度の集計（tools/score_session.py）──')
    print(f'  対象ターン数            : {n}（セッション {sess} 件）')
    print(f'  ① 出力契約の充足率      : {pct(clean)}   目標 95% 以上')
    print(f'     確信度ラベルを含む   : {pct(lab)}')
    print(f'     状態行を含む         : {pct(st)}')
    print(f'     裏取りを含む         : {pct(bc)}')
    print('  違反の型別内訳:')
    if viol:
        for t, c in viol.most_common():
            print(f'     {t}: {c} 件')
    else:
        print('     なし')
    print()
    print('  【この数値の限界】測っているのは形式の充足であって、内容の正しさではない。')
    print('  内容は chatgpt/prompt_02_blind_grader.md による盲検採点で測ること（指標②③）。')
    return 0

if __name__ == '__main__':
    a = sys.argv[1:]
    sys.exit(main(a[0] if a else 'metrics/compliance.jsonl', a[1] if len(a) > 1 else None))
