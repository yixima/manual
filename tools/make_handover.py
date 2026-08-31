#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""引き継ぎファイルの作成を補助する（L1 §10-5）。

できること：
  --new    テンプレートを複製し、機械で分かる部分（日時・発行ファイル一覧・
           コミット履歴＝決定の経緯）を自動で埋めた雛形を作る。
  --check  書き上げた引き継ぎファイルに、必須10章がすべて埋まっているかを検査する。

**このスクリプトは「機械で分かる部分」しか埋められない。**
依頼の原文・決定の理由・却下した案・失敗の経緯は、人（またはセッション）が書く。
それが引き継ぎの本体である。
"""
import subprocess, sys, argparse, pathlib, re, datetime

SECTIONS = ["1. 依頼の原文", "2. 確定した事実と決定", "3. 却下した案", "4. 発行したすべてのファイル",
            "5. セッション中の調整・変更の経緯", "6. 失敗と、そこから得た改善", "7. 未完了のタスク",
            "8. 次に最初に行うこと", "9. 前提条件・数値前提", "10. 使用したコマンド・手順"]
SAFE = re.compile(r'^[A-Za-z0-9._-]+$')

def sh(*a):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""

def now():
    utc = datetime.datetime.now(datetime.timezone.utc)
    try:
        import zoneinfo
        return f"{utc.astimezone(zoneinfo.ZoneInfo('Asia/Tokyo')):%Y-%m-%d %H:%M} JST（UTC {utc:%Y-%m-%d %H:%M}）"
    except Exception:
        return f"UTC {utc:%Y-%m-%d %H:%M}"

def new(out, template):
    t = pathlib.Path(template).read_text(encoding='utf-8')
    t = t.replace('（YYYY-MM-DD HH:MM JST。実測値を書く。推測しない。§3-7）', now())

    files = []
    for ln in sh('git', 'ls-files').splitlines():
        p = pathlib.Path(ln)
        if p.is_file():
            files.append(f"| `{p.name}` | `{p.parent}/` | （何のために作ったか） | （中に何が書いてあるか） |")
    if files:
        t = t.replace("| ファイル名 | 置き場所 | 何のために作ったか | 中に何が書いてあるか |\n|---|---|---|---|\n| | | | |",
                      "| ファイル名 | 置き場所 | 何のために作ったか | 中に何が書いてあるか |\n|---|---|---|---|\n"
                      + "\n".join(files)
                      + "\n\n> 上の一覧は `git ls-files` から自動生成した。**「何のために」「中に何が」は自分で埋めること。**"
                        "一覧だけでは引き継げない。")

    log = sh('git', 'log', '--pretty=format:%h|%ad|%s', '--date=short', '-40')
    if log:
        rows = []
        for ln in log.splitlines():
            parts = ln.split('|', 2)
            if len(parts) == 3:
                rows.append(f"| {parts[0]} | {parts[2]} | （なぜそう決めたか） | {parts[1]} |")
        t = t.replace("| # | 決定したこと | なぜそう決めたか | いつ |\n|---|---|---|---|\n| 1 | | | |",
                      "| # | 決定したこと | なぜそう決めたか | いつ |\n|---|---|---|---|\n" + "\n".join(rows)
                      + "\n\n> 上はコミット履歴から自動生成した。**「なぜそう決めたか」は履歴に無い。自分で埋めること。**")
    pathlib.Path(out).write_text(t, encoding='utf-8')
    print(f"{out} を作成した。")
    print("自動で埋めたのは、日時・ファイル一覧・コミット履歴だけである。")
    print("依頼の原文・決定の理由・却下した案・調整の経緯・失敗と改善・未完了・次の一手・前提条件は、")
    print("**必ず自分で埋めること。** 埋め終えたら `--check` で検査する。")
    return 0

def section_body(t, name):
    i = t.find(name)
    if i < 0:
        return None
    j = t.find('\n## ', i)
    return t[i:j if j > 0 else len(t)]

def norm(b):
    """比較用に正規化する。説明の引用文・罫線・空欄は本文とみなさない。"""
    b = re.sub(r'^>.*$', '', b, flags=re.M)
    b = re.sub(r'（[^）]*）', '', b)
    return re.sub(r'[|\s#\-`:_>*]', '', b)

def check(path, template='dist/handover_template_v19.md'):
    """必須10章が「テンプレートのまま」でないかを検査する。
    章の見出しがあるだけでは合格にしない。**中身が書き足されているか**を、
    テンプレートとの差分で判定する（雛形のまま渡す事故を防ぐため）。"""
    p = pathlib.Path(path)
    t = p.read_text(encoding='utf-8')
    try:
        tpl = pathlib.Path(template).read_text(encoding='utf-8')
    except Exception:
        tpl = ''
    ng = []
    if not SAFE.match(p.name):
        ng.append(f"ファイル名 `{p.name}` が ^[A-Za-z0-9._-]+$ に適合しない（§7-11）")
    for s in SECTIONS:
        body = section_body(t, s)
        if body is None:
            ng.append(f"章が無い：{s}")
            continue
        tb = section_body(tpl, s) if tpl else None
        nb = norm(body)
        if tb is not None and nb == norm(tb):
            ng.append(f"章がテンプレートのまま（未記入）：{s}")
        elif len(nb.replace(re.sub(r'[|\s#\-`:_>*]', '', s), '')) < 12:
            ng.append(f"章の中身がほとんど無い：{s}")
    print('── 引き継ぎファイルの検査（L1 §10-5）──')
    if ng:
        for x in ng:
            print(f"  [NG] {x}")
        print(f"\n不合格 {len(ng)} 件。**この状態で引き継ぐと、次のセッションは同じ状況を再現できない。**")
        print("埋めてから渡すこと。")
        return 1
    print("  [ok] 必須10章がすべて埋まっている")
    print("  [ok] ファイル名が ASCII 安全")
    print("\n最後に自分で検算すること：")
    print("  「このファイルだけを読んだ第三者が、いま自分がしている作業を続けられるか」")
    print("  答えが『いいえ』なら、まだ足りない。")
    return 0

def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--new', metavar='OUT')
    g.add_argument('--check', metavar='FILE')
    ap.add_argument('--template', default='dist/handover_template_v19.md')
    a = ap.parse_args()
    return new(a.new, a.template) if a.new else check(a.check, a.template)

if __name__ == '__main__':
    sys.exit(main())
