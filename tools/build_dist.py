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
# 版はコアカードのファイル名から自動判定する（版を手で二重管理しない）
_cards = sorted(DIST.glob('L0_core_card_v*.md'))
if not _cards:
    print('[FAIL] dist/ に L0_core_card_v*.md が無い', file=sys.stderr); sys.exit(1)
VER = re.search(r'(v\d+)', _cards[-1].name).group(1)
FILES = {'L0': DIST / f'L0_core_card_{VER}.md',
         'L1': DIST / f'L1_manual_{VER}.md',
         'L2': DIST / f'L2_records_{VER}.md'}
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
missing, extra = t1 - t0, t0 - t1
check(not missing, 'L1 の自動発動表の行が L0 にすべてある', f'L0 に欠落: {sorted(missing)}')
check(not extra, 'L0 に L1 へ無い行が紛れていない（双方向の一致）', f'L0 に余分: {sorted(extra)}')

# 4. 版表記・発行日の一致
vers = {k: set(re.findall(r'v\d+\b', v[:1200])) for k, v in txt.items()}
dates = {k: set(re.findall(r'2026年\d+月\d+日', v[:1200])) for k, v in txt.items()}
check(all(VER in s for s in vers.values()), f'3ファイルすべてに {VER} の版表記がある', str(vers))
_d = set.intersection(*dates.values()) if all(dates.values()) else set()
check(bool(_d), '3ファイルの発行日が一致する', str(dates))

# 5. 短縮版が本体から再生成した内容と一致すること（手で書き写して版がずれる事故を防ぐ）
_mini = DIST / f'L0_core_card_mini_{VER}.md'
if _mini.exists():
    # 検査は状態を変えない。一時ファイルへ再生成して突き合わせるだけにする。
    import subprocess, tempfile, os
    _tmp = tempfile.NamedTemporaryFile(suffix='.md', delete=False)
    _tmp.close()
    subprocess.run([sys.executable, 'tools/build_mini.py', '--out', _tmp.name], capture_output=True)
    _same = pathlib.Path(_tmp.name).read_text(encoding='utf-8') == _mini.read_text(encoding='utf-8')
    os.unlink(_tmp.name)
    check(_same, '短縮版が本体と同期している',
          '再生成すると内容が変わる＝本体を直したあと python3 tools/build_mini.py を実行していない')
else:
    check(False, '短縮版が存在する', f'{_mini.name} が無い。python3 tools/build_mini.py で生成すること')

# 5.5 全部入り1ファイルが L0/L1/L2 と同期していること
_aio = DIST / f'manual_{VER}_all_in_one.md'
if _aio.exists():
    import subprocess as _sp, tempfile as _tf, os as _os
    _t = _tf.NamedTemporaryFile(suffix='.md', delete=False); _t.close()
    _sp.run([sys.executable, 'tools/build_allinone.py', '--out', _t.name], capture_output=True)
    _ok = pathlib.Path(_t.name).read_text(encoding='utf-8') == _aio.read_text(encoding='utf-8')
    _os.unlink(_t.name)
    check(_ok, '全部入り1ファイルが L0/L1/L2 と同期している',
          'L0/L1/L2 を直したあと python3 tools/build_allinone.py を実行していない')
else:
    check(False, '全部入り1ファイルが存在する', f'{_aio.name} が無い。python3 tools/build_allinone.py で生成すること')

# 5.7 latest/（版番号を含まない固定URL用）が最新版と一致していること
_lat = pathlib.Path('latest')
if (_lat / 'L0_core_card.md').exists():
    _same = (_lat / 'L0_core_card.md').read_text(encoding='utf-8') == txt['L0']
    check(_same, 'latest/L0_core_card.md が最新のコアカードと一致している',
          'コアカードを直したあと python3 tools/build_latest.py を実行していない')
    import json as _json
    try:
        _m = _json.loads((_lat / 'latest.json').read_text(encoding='utf-8'))
        check(_m.get('version') == VER, f'latest.json の版が {VER} を指している', f"version={_m.get('version')}")
    except Exception as _e:
        check(False, 'latest.json が読める', str(_e))
else:
    check(False, 'latest/ が存在する', 'python3 tools/build_latest.py で生成すること')

# 5.8 リポジトリの CLAUDE.md が内蔵するコアカードが、最新版と一致していること
#     （CLAUDE.md は `@` インポートが使えないため実体を内蔵する。手で写すと必ずいつか版がずれる）
_cmd = pathlib.Path('CLAUDE.md')
if _cmd.exists():
    _c = _cmd.read_text(encoding='utf-8')
    _i = _c.find('# 汎用マニュアル v')
    check(_i >= 0 and _c[_i:] == txt['L0'],
          'CLAUDE.md が内蔵するコアカードが最新版と一致している',
          'コアカードを直したあと python3 tools/build_latest.py を実行していない')
else:
    check(False, 'CLAUDE.md が存在する', 'リポジトリ直下に無い')

# 5. 旧版ファイルが dist/ に残っていないこと（版ずれの温床になる）
_stale = [f.name for f in DIST.glob('L[012]_*.md') if VER not in f.name]
check(not _stale, f'dist/ に旧版ファイルが残っていない', f'旧版: {_stale}')

# 7. ファイル名の ASCII 安全性
for p in DIST.glob('*'):
    check(bool(SAFE.match(p.name)), f'ファイル名 {p.name} が ASCII 安全', '非ASCIIを含む')

print('── 配布前検査（tools/build_dist.py）──')
for s in ok: print(f'  [ok] {s}')
for s in ng: print(f'  [NG] {s}')
print(f'合格 {len(ok)} 件 / 不合格 {len(ng)} 件')
if ng:
    print('\n不一致があるため配布しない（§0-7 発行前の照合）。修正してから再実行すること。', file=sys.stderr)
    sys.exit(1)

DIST.joinpath('DISTRIBUTION.md').write_text(f"""# 配布手順（この検査を通ったもののみ）

**配布は一方向である。** 単一ソース（本リポジトリ）→ この dist/ → 各配布先。
**配布先で直接編集しない。** 編集はリポジトリで行い、再生成して再配布する（§0-7 版ずれの構造的排除）。

## 【推奨】ブートローダー方式（貼るのは一度きり・更新時の貼り直しが不要）

**設定欄に貼るのは `bootloader.md`（54行）だけ。** 中身は固定URLから取得されるため、
**版を上げても貼り直す必要がない。**

| # | 配布先 | 貼るもの | 効く範囲 |
|---|---|---|---|
| 1 | claude.ai → 設定 →「Instructions for Claude」 | **`bootloader.md` の全文（一度だけ）** | すべての会話・すべてのプロジェクト |
| 2 | Cowork → 設定 → Cowork →「Global instructions」 | 同上（一度だけ） | すべての Cowork セッション |

**更新時にすることは、リポジトリを更新するだけ。** 各セッションは開始時に固定URLから最新を取得する。

- 固定URL（版番号を含まない・中身だけが変わる）
  - コアカード：`latest/L0_core_card.md`
  - 全部入り：`latest/manual_all_in_one.md`
  - 版の確認：`latest/latest.json`

**進行中のセッションを最新にするには、そのセッションで「マニュアル更新」と打つだけ。**
ファイルを添付し直す必要はない。

**Claude Code は完全に自動である。** SessionStart フックが毎回 `git pull` して、
コアカードが変わっていれば `~/.claude/CLAUDE.md` を自動で差し替える。**利用者の操作は不要。**

### 限界（隠さない）
- URLの取得ができない環境では、ブートローダーに内蔵したフォールバック（関門9項＋出力契約）だけが働く。
  **その場合は必ず申告される。**
- **URLを管理する者がルールを決める。** このURLは、必ず自分の管理下にあるものだけを指すこと。

---

## A. 全文を直接貼る方式（ブートローダーが使えない場合）

| # | 配布先 | 貼るもの | 効く範囲 |
|---|---|---|---|
| 1 | claude.ai → 左下のイニシャル → 設定 →「Instructions for Claude」 | `L0_core_card_{VER}.md` の全文（文字数で入らなければ `L0_core_card_mini_{VER}.md`） | **すべての会話・すべてのプロジェクト** |
| 2 | claude.ai → 各プロジェクト → プロジェクト指示 | 同上（案件固有の前提を追記可） | そのプロジェクト内の会話 |
| 3 | Cowork → 設定 → Cowork →「Global instructions」 | 同上 | **すべての Cowork セッション** |
| 4 | `~/.claude/CLAUDE.md` | 同上 | **Claude Code の全プロジェクト＋Cowork デスクトップ** |
| 5 | 各リポジトリの `CLAUDE.md` | 同上（プロジェクト固有の事項を追記可） | そのリポジトリ（web セッションを含む） |
| 6 | 各リポジトリの `.claude/` | 本リポジトリの `.claude/settings.json`・`.claude/hooks/`・`.claude/glossary.json` | そのリポジトリでの機械的強制（L3） |

**4・5・6 は1コマンドで済む**
```
python3 tools/install.py --dry-run   # 何が起きるか確認（何も書き換えない）
python3 tools/install.py             # 実行。既存ファイルは退避してから追記・統合する
```
残る手作業は **1（claude.ai）と 3（Cowork）の貼り付けだけ**。

## B. すでに開いているセッションに効かせる（そのつど）

**`manual_{VER}_all_in_one.md` を、そのセッションに添付するだけ。**
冒頭に取扱いの指示（最優先で適用・旧版は保管のみ・確認を求めずに適用する）を内蔵しているため、
**別途メッセージを書く必要はない。** L0・L1・L2 の3部がこの1ファイルに入っている。

**新しく始めるセッションには不要**（A で自動的に効く）。

## C. L1（本編）と L2（記録）の置き場

- claude.ai：プロジェクトナレッジに添付する。
- Claude Code：リポジトリに置き、`CLAUDE.md` から**パスで参照**する。
- **`@` インポートは Cowork でスキップされるため、コアカードは必ず実体で貼る。**

## D. 引き継ぎ（セッションを移るとき）

**引き継ぎは「書き写す」作業ではない。「記録から生成し、届いたことを照合する」作業である**（§10-5）。

### `[Code]`（記録が残るため、ほぼ自動）
```
python3 tools/make_handover.py --auto  handover/<ascii_name>.md   # 記録から生成（要約しない）
python3 tools/make_handover.py --check handover/<ascii_name>.md   # 渡せる状態かを検査
```
`--auto` が自動で埋めるのは **①依頼の原文 ④発行したファイル ⑤調整の経緯 ⑥失敗 ⑦未完了 ⑩使用したコマンド**。
残る `【要記入】` は **②決定の理由 ③却下した案 ⑧次の一手 ⑨前提条件**——**理由は記録に残らないため、機械には書けない。**
`【要記入】` が1つでも残っていれば `--check` は不合格になる。**検査に落ちた状態で引き継がない。**

**次のセッションは `handover/` を自動で受領する**（SessionStart フック）。手で確かめるときは：
```
python3 tools/make_handover.py --receipt handover/<ascii_name>.md
```
一致すれば、冒頭の確認作業はそれで完了とする。**「ちゃんと理解できていますか」と質問して確かめる必要はない。**

### `[Chat]` `[Cowork]`（記録が無いため、節目ごとに追記する）
`handover_template_{VER}.md` を使い、**一度に全部を思い出そうとせず、区切りのたびに同じファイルを作り直す**（§0-5）。
0章の件数表を実際に数えて埋め、受け取った側はそれと本文を突き合わせる。

## 注意（一次資料で確認済み）

- Cowork は、作業ディレクトリ外を指す `@` インポートをスキップする。
- クラウドセッション（claude.ai/code）はローカルの `~/.claude/settings.json` を読まない。フックはリポジトリ側に置く。
- 過去のセッションへ遡って反映することはできない。**新しいセッションから効く。**
""", encoding='utf-8')
print('  [ok] dist/DISTRIBUTION.md を更新した')
