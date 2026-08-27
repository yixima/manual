#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""発動構造の機械検査：条項の抽出／関門・自動発動表からの到達可能性／孤立条項／失敗記録の捕捉率。
使い方: python3 tools/audit_activation.py <manual.md> [<manual2.md> ...]
"""
import re, sys, json

CLAUSE_DEF = re.compile(r'^\*\*(\d+-\d+)\.')          # 本文中の条項定義 **0-10. …**
SEC_DEF    = re.compile(r'^## §(\d+)\.')              # 節見出し
REF        = re.compile(r'§\s?(\d+)-(\d+)')           # 参照 §3-4
REF_ALL    = re.compile(r'§\s?(\d+)\s*全項')          # 参照 §3 全項
REF_RANGE  = re.compile(r'§\s?(\d+)-(\d+)\s*[〜～]\s*(\d+)-(\d+)')  # §2-17〜2-21
REF_RANGE2 = re.compile(r'§\s?(\d+)-(\d+)\s*[〜～]\s*(\d+)(?!-)')   # §3-1〜§3-3 崩れ対策/§2-1〜2-5

def load(path):
    return open(path, encoding='utf-8').read().splitlines()

def clauses(lines):
    out = []
    for ln in lines:
        m = CLAUSE_DEF.match(ln.strip())
        if m: out.append(m.group(1))
    return out

def expand_refs(text, universe):
    """テキスト中の参照を、実在条項の集合へ展開する。"""
    found = set()
    for m in REF_RANGE.finditer(text):
        s, a, _, b = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
        for i in range(a, b + 1):
            c = f"{s}-{i}"
            if c in universe: found.add(c)
    for m in REF_RANGE2.finditer(text):
        s, a, b = m.group(1), int(m.group(2)), int(m.group(3))
        if b >= a:
            for i in range(a, b + 1):
                c = f"{s}-{i}"
                if c in universe: found.add(c)
    for m in REF_ALL.finditer(text):
        s = m.group(1)
        for c in universe:
            if c.split('-')[0] == s: found.add(c)
    for m in REF.finditer(text):
        c = f"{m.group(1)}-{m.group(2)}"
        if c in universe: found.add(c)
    return found

def block(lines, start_pat, end_pat):
    """start_pat の行から end_pat の行の直前までを返す。"""
    buf, on = [], False
    for ln in lines:
        if re.search(start_pat, ln): on = True
        elif on and re.search(end_pat, ln): break
        if on: buf.append(ln)
    return "\n".join(buf)

def documented_exclusions(lines):
    """§0-11 の「本表に載らない条項について」に、理由付きで明示除外された条項。"""
    txt = block(lines, r'本表に載らない条項について', r'^\*\*0-12\.|^---')
    out = set()
    for ln in txt.splitlines():
        # 各サブ箇条の先頭の太字トークンだけを除外対象とみなす（本文中の関連参照は拾わない）
        m = re.match(r'\s*-\s+\*\*((?:§\s?\d+-\d+[・、]?)+)\*\*', ln)
        if m:
            out |= set(f"{a}-{b}" for a, b in REF.findall(m.group(1)))
    return out

def failure_records(lines):
    """§10-4 の失敗記録（- **記録：…**）と、その再発防止に挙がる条項。"""
    recs = []
    for ln in lines:
        s = ln.strip()
        if s.startswith('- **記録：'):
            title = s.split('**')[1]
            recs.append((title, s))
    return recs

def audit(path):
    lines = load(path)
    text  = "\n".join(lines)
    univ  = clauses(lines)
    uset  = set(univ)

    gate  = block(lines, r'\*\*0-10\.', r'\*\*0-11\.')
    table = block(lines, r'\*\*0-11\.', r'\*\*0-12\.') or block(lines, r'\*\*0-11\.', r'^---')

    g = expand_refs(gate, uset)
    t = expand_refs(table, uset)
    reach = g | t
    orphans = [c for c in univ if c not in reach]

    excl = documented_exclusions(lines)
    orphans = [c for c in orphans if c not in excl]

    recs = failure_records(lines)
    if RECORDS_LINES:
        recs = failure_records(RECORDS_LINES)
    caught, missed = [], []
    for title, body in recs:
        # 「再発防止＝§X」以降を対象にする
        tail = body.split('再発防止')[-1] if '再発防止' in body else body
        need = expand_refs(tail, uset)
        if need and need <= reach: caught.append((title, sorted(need)))
        elif not need: missed.append((title, [], 'no-clause-cited'))
        else: missed.append((title, sorted(need - reach), 'unreachable'))

    return dict(path=path, n_clauses=len(univ), clauses=univ, excluded=sorted(excl),
                gate_reach=sorted(g), table_reach=sorted(t),
                n_reach=len(reach), reach=sorted(reach),
                orphans=orphans, n_records=len(recs),
                caught=len(caught), missed=missed,
                gate_only=sorted(g - t), table_only=sorted(t - g))

RECORDS_LINES = None

if __name__ == '__main__':
    args = sys.argv[1:]
    if '--records' in args:
        i = args.index('--records')
        RECORDS_LINES = load(args[i + 1])
        args = args[:i] + args[i + 2:]
    res = [audit(p) for p in args]
    for r in res:
        print("="*70)
        print(f"FILE: {r['path']}")
        print(f"  条項総数        : {r['n_clauses']}")
        print(f"  到達可能条項    : {r['n_reach']}  ({r['n_reach']*100//max(r['n_clauses'],1)}%)")
        print(f"  孤立条項({len(r['orphans'])}) : {', '.join(r['orphans']) if r['orphans'] else 'なし'}")
        print(f"  明示除外({len(r['excluded'])}) : {', '.join(r['excluded']) if r['excluded'] else 'なし'}（理由が本文に記載されたもの）")
        print(f"  失敗記録        : {r['n_records']} 件 / 捕捉 {r['caught']} 件")
        for t, cl, why in r['missed']:
            print(f"    - 未捕捉: {t} [{why}] {cl}")
    if len(res) == 2:
        a, b = res
        sa, sb = set(a['clauses']), set(b['clauses'])
        print("="*70)
        print(f"DIFF {a['path']} -> {b['path']}")
        print(f"  追加条項: {sorted(sb-sa) or 'なし'}")
        print(f"  削除条項: {sorted(sa-sb) or 'なし'}")
    json.dump(res, open('/tmp/claude-0/-home-user-manual/ee365d51-8050-5e71-a91f-89ed13214fae/scratchpad/audit.json','w'), ensure_ascii=False, indent=1)
