#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配布前の整合検査（L1 §0-7 の4点照合を機械化したもの）。

検査項目：
  1. L0 が200行以下であること（L1 §0-14 の物理上限）
  2. L0 の関門9項と、L1 §0-10② の関門9項が食い違っていないこと
  3. L0 の自動発動表の行と、L1 §0-11 の表の行が食い違っていないこと
  4. 版表記・発行日が L0／L1／L2 で一致していること
  5. 配布ファイル名が ^[A-Za-z0-9._-]+$ に適合すること（§7-11）

1つでも不合格なら異常終了する。**不一致のまま配布しない。**
"""
import re, sys, pathlib

DIST = pathlib.Path('dist')
FILES = {'L0': DIST / 'L0_core_card_v16.md',
         'L1': DIST / 'L1_manual_v16.md',
         'L2': DIST / 'L2_records_v16.md'}
SAFE = re.compile(r'^[A-Za-z0-9._-]+$')
ok, ng = [], []

def check(cond, label, detail=''):
    (ok if cond else ng).append(label + (f'  → {detail}' if detail and not cond else ''))

txt = {}
for k, p in FILES.items():
    if not p.exists():
        print(f'[FAIL] {p} が無い', file=sys.stderr); sys.exit(1)
    txt[k] = p.read_text(encoding='utf-8')

# 1. L0 の行数
n = len(txt['L0'].splitlines())
check(n <= 200, f'L0 の行数 {n} 行 ≦ 200 行（§0-14 の物理上限）', f'{n} 行あり超過')

# 2. 関門9項の一致（表現ではなく「各項が指す条項番号の集合」で照合する）
#    L0 は短縮表現を用いるが、指し示す条項は L1 と同一でなければならない。
def gate_items(t, anchor):
    """関門の各項が参照する条項番号の集合を、1〜9の順に返す。
    項の終わりは、次の番号付き項／見出し／表／トップレベル箇条書きのいずれかで判定する。"""
    k = t.find(anchor)
    if k < 0:
        return []
    items, cur, expect = [], None, 1
    for ln in t[k:].splitlines()[1:]:
        m = re.match(r'\s*(\d)\.\s', ln)
        if m and int(m.group(1)) == expect:
            if cur is not None:
                items.append(cur)
            cur, expect = set(), expect + 1
        elif cur is not None and (ln.startswith('## ') or ln.startswith('- **') or ln.startswith('|') or ln.startswith('---')):
            break
        if cur is not None:
            cur |= set(f'{a}-{b}' for a, b in re.findall(r'§\s?(\d+)-(\d+)', ln))
    if cur is not None:
        items.append(cur)
    return items[:9]

g0 = gate_items(txt['L0'], '## 2. 送信直前の関門')
g1 = gate_items(txt['L1'], '② 送信直前の必須ミニチェック')
check(len(g0) == 9, 'L0 の関門が9項ある', f'{len(g0)} 項')
check(len(g1) == 9, 'L1 の関門が9項ある', f'{len(g1)} 項')
diff = [i + 1 for i, (a, b) in enumerate(zip(g0, g1)) if a != b]
check(not diff and len(g0) == len(g1) == 9,
      'L0 と L1 の関門9項が同じ条項を指す',
      f'食い違う項: {diff}  L0={[sorted(g0[i-1]) for i in diff]}  L1={[sorted(g1[i-1]) for i in diff]}')

# 3. 自動発動表の行の一致（左欄の見出し語で照合する）
def table_left(t):
    rows = []
    for m in re.finditer(r'^\|\s*(?:\*\*)?([^|]{4,60}?)(?:\*\*)?\s*\|', t, re.M):
        s = re.sub(r'[\s*【】]', '', m.group(1))
        if s and not s.startswith('---') and '着手する作業' not in s and '論点' not in s:
            rows.append(s)
    return rows
t0 = set(table_left(txt['L0'][txt['L0'].find('自動発動'):]))
t1 = set(table_left(txt['L1'][txt['L1'].find('0-11.'):txt['L1'].find('0-12.')]))
missing = t1 - t0
check(not missing, 'L1 の自動発動表の行が L0 にすべてある', f'L0 に欠落: {sorted(missing)}')

# 4. 版表記・発行日の一致
vers = {k: set(re.findall(r'v1[0-9]\b', v[:1200])) for k, v in txt.items()}
dates = {k: set(re.findall(r'2026年\d+月\d+日', v[:1200])) for k, v in txt.items()}
check(all('v16' in s for s in vers.values()), '3ファイルすべてに v16 の版表記がある', str(vers))
check(all('2026年8月27日' in s for s in dates.values()), '3ファイルの発行日が一致する', str(dates))

# 5. ファイル名の ASCII 安全性
for p in DIST.glob('*'):
    check(bool(SAFE.match(p.name)), f'ファイル名 {p.name} が ASCII 安全', '非ASCIIを含む')

print('── 配布前検査（tools/build_dist.py）──')
for s in ok: print(f'  [ok] {s}')
for s in ng: print(f'  [NG] {s}')
print(f'合格 {len(ok)} 件 / 不合格 {len(ng)} 件')
if ng:
    print('\n不一致があるため配布しない（§0-7 発行前の照合）。修正してから再実行すること。', file=sys.stderr)
    sys.exit(1)

DIST.joinpath('DISTRIBUTION.md').write_text("""# 配布手順（この検査を通ったもののみ）

**配布は一方向である。** 単一ソース（本リポジトリ）→ この dist/ → 各配布先。
**配布先で直接編集しない。** 編集はリポジトリで行い、再生成して再配布する（§0-7 版ずれの構造的排除）。

| # | 配布先 | 貼るもの | 効く範囲 |
|---|---|---|---|
| 1 | claude.ai → 設定 →「Claudeへの指示」 | `L0_core_card_v16.md` の全文 | **すべての新しい会話** |
| 2 | claude.ai → 各プロジェクト → プロジェクト指示 | 同上（案件固有の前提を追記可） | そのプロジェクト内の会話 |
| 3 | Cowork → 設定 → Cowork → グローバル指示 | 同上 | **すべての Cowork セッション** |
| 4 | `~/.claude/CLAUDE.md` | 同上 | **Claude Code の全プロジェクト＋Cowork デスクトップ** |
| 5 | 各リポジトリの `CLAUDE.md` | 同上（プロジェクト固有の事項を追記可） | そのリポジトリ（web セッションを含む） |
| 6 | 各リポジトリの `.claude/` | 本リポジトリの `.claude/settings.json` と `.claude/hooks/` | そのリポジトリでの機械的強制（L3） |

**L1（本編）と L2（記録）の置き場**
- claude.ai：プロジェクトナレッジに添付する。
- Claude Code：リポジトリに置き、`CLAUDE.md` から**パスで参照**する（`@` インポートは Cowork でスキップされるため、コアカードは必ず実体で貼る）。

**注意（一次資料で確認済み）**
- Cowork は、作業ディレクトリ外を指す `@` インポートをスキップする。**コアカードを外部ファイル参照にしない。**
- クラウドセッション（claude.ai/code）はローカルの `~/.claude/settings.json` を読まない。**フックはリポジトリ側に置く。**
- 過去のセッションへ遡って反映することはできない。**新しいセッションから効く。**
""", encoding='utf-8')
print('  [ok] dist/DISTRIBUTION.md を更新した')
