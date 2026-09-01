#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""引き継ぎファイルの作成・検査・受領を行う（L1 §10-5）。

  --auto OUT      セッションの記録から**中身まで**埋めた引き継ぎファイルを作る（`[Code]` 限定）
  --new  OUT      記録が無い環境向け。テンプレートを複製し、機械で分かる部分だけ埋める
  --check FILE    書き上げた引き継ぎファイルが、渡せる状態かを検査する
  --receipt FILE  受け取った引き継ぎファイルの完全性を照合し、受領確認を印字する

**設計の中心にある考え方**
「完全に引き継ぐ」の本体は、**次のセッションが全部を覚えていること**ではない。
それは確率的で保証できない。保証できるのは、**必要になった瞬間に原本を引けること**である。
よって本ツールは、要約を作らない。**原文をそのまま運び、索引と件数を付ける。**

**--auto が埋められるもの／埋められないもの（正直に区別する。§1-7）**
  埋められる：①依頼の原文 ④発行したファイル ⑤調整の経緯 ⑥失敗 ⑦未完了 ⑩使用したコマンド
             ——いずれもセッションの記録に事実として残っているため、記憶を介さずに写せる。
  埋められない：②なぜそう決めたか ③却下した案と理由 ⑧次の一手 ⑨前提条件
             ——**理由は記録に残らない。** これはセッション自身が書くしかない。
             未記入の箇所には `【要記入】` を置き、`--check` が不合格にする。
"""
import subprocess, sys, argparse, pathlib, re, datetime, json, hashlib, os

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import handover_extract as HX

SECTIONS = ["1. 依頼の原文", "2. 確定した事実と決定", "3. 却下した案", "4. 発行したすべてのファイル",
            "5. セッション中の調整・変更の経緯", "6. 失敗と、そこから得た改善", "7. 未完了のタスク",
            "8. 次に最初に行うこと", "9. 前提条件・数値前提", "10. 使用したコマンド・手順"]
SAFE = re.compile(r'^[A-Za-z0-9._-]+$')
TODO = '【要記入】'
MANIFEST_RE = re.compile(r'```handover-manifest\n(.*?)\n```', re.S)
PENDING = 'PENDING-SHA256'


def default_template():
    """同梱テンプレートのうち、版番号が最も新しいものを既定とする。
    版を上げるたびに既定値を書き換える必要をなくすため（§0-7 の版ずれ防止）。"""
    root = pathlib.Path(__file__).resolve().parent.parent
    c = sorted(root.glob('dist/handover_template_v*.md'),
               key=lambda p: int(re.search(r'v(\d+)', p.name).group(1)))
    return str(c[-1]) if c else 'dist/handover_template_v21.md'


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


def jst(ts):
    """記録の時刻（UTC 表記）を JST の短い形に直す。読めない値はそのまま返す。"""
    try:
        d = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
        import zoneinfo
        return f"{d.astimezone(zoneinfo.ZoneInfo('Asia/Tokyo')):%m-%d %H:%M}"
    except Exception:
        return ts or ''



def commits_in_session(d):
    """このセッションの期間中に積まれたコミットだけを返す。

    リポジトリの履歴全体は引き継ぎの対象ではない（次のセッションも `git log` で読める）。
    引き継ぐべきは「**このセッションで何を決めて何を積んだか**」である。
    """
    since = d.get('started')
    args = ['git', 'log', '--pretty=format:%h|%ad|%s', '--date=short']
    if since:
        args.append(f'--since={since}')
    else:
        args.append('-20')
    return sh(*args)


def files_in_session(d):
    """このセッションが作成・編集したファイルを (パス, 操作) の一覧で返す。

    出所は3つ。①記録に残るツール操作（Write/Edit）②期間中のコミットが触ったファイル
    ③まだコミットしていない変更。**Bash のリダイレクトで作ったファイルは①に残らない**ため、
    ②③で補う（記録だけを見ると取りこぼす。§3-4 実物を見る）。
    """
    seen, out = set(), []

    def add(path, how):
        if path and path not in seen:
            seen.add(path)
            out.append((path, how))

    for f in d.get('files', []):
        add(f['path'], f['tool'])
    since = d.get('started')
    if since:
        for ln in sh('git', 'log', f'--since={since}', '--name-only',
                     '--pretty=format:').splitlines():
            if ln.strip():
                add(ln.strip(), 'コミット済み')
    for ln in sh('git', 'status', '--porcelain').splitlines():
        m = re.match(r'^\s*\S{1,2}\s+(.*)$', ln)
        if not m:
            continue
        path = m.group(1).strip()
        if ' -> ' in path:            # 改名は改名後のパスを採る
            path = path.split(' -> ')[-1].strip()
        add(path.strip('"'), '未コミット')
    return out

# ── マニフェスト（受領確認ブロック）────────────────────────────
def stamp(text, manifest):
    """マニフェストを本文へ埋め込み、ファイル全体の指紋（sha256）を確定させる。

    指紋は「生成後に1文字も変わっていないか」を照合するためのものである。
    自分自身を含む値のため、いったん `PENDING-SHA256` を入れて全体を計算し、後から差し替える。
    """
    manifest['sha256'] = PENDING
    body = text.replace('__MANIFEST__', json.dumps(manifest, ensure_ascii=False, indent=2))
    digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
    return body.replace(PENDING, digest)


def read_manifest(text):
    m = MANIFEST_RE.search(text)
    if not m:
        return None, None
    try:
        d = json.loads(m.group(1))
    except Exception:
        return None, None
    got = d.get('sha256', '')
    recomputed = hashlib.sha256(text.replace(got, PENDING).encode('utf-8')).hexdigest() if got else ''
    return d, (got and got == recomputed)


def manifest_section():
    return """## 0. 受領確認ブロック（機械が検査する。削除しない）

> **次のセッションは、作業に入る前にこの1行だけを実行する。それで受領確認は完了する。**
>
> ```
> python3 tools/make_handover.py --receipt <このファイル>
> ```
>
> 出力された受領確認をそのまま報告すれば、**「本当に全部引き継げているか」をユーザーが質問して確かめる必要はない**。
> `[Chat]` `[Cowork]` ではコマンドを実行できないため、下の件数と本文の各章を目視で突き合わせ、
> **一致しない項目があればその場で申告する**（§1-7 分からないまま進めない）。

```handover-manifest
__MANIFEST__
```

---

"""


# ── ①記録から作る（[Code]）──────────────────────────────────
def auto(out, template, transcript=None, cwd=None, verbatim=True):
    cwd = cwd or os.getcwd()
    path = HX.find_transcript(cwd=cwd, explicit=transcript)
    if not path:
        print("セッションの記録が見つからない。", file=sys.stderr)
        print("`[Code]` 以外の環境には記録が無い。--new でテンプレートを作り、手で埋めること。", file=sys.stderr)
        return 1
    d = HX.parse(path)
    outp = pathlib.Path(out)
    if not SAFE.match(outp.name):
        print(f"ファイル名 `{outp.name}` が ^[A-Za-z0-9._-]+$ に適合しない（§7-11）。", file=sys.stderr)
        return 1

    def esc(t):
        """引用ブロックの中で原文を壊さないように整える。原文自体は書き換えない。"""
        return "\n".join('> ' + ln if ln.strip() else '>' for ln in t.splitlines())

    L = []
    L.append(f"# 引き継ぎファイル（{outp.stem}）\n")
    L.append("> **このファイルは、セッションの記録から機械的に生成した。会話・コマンド・ファイルは要約していない。**\n"
             "> 要約すると意図が失われるため、原文をそのまま運ぶ（L1 §10-5）。\n"
             f"> 生成元の記録：`{path}`\n")
    L.append(f"- **引き継ぎ元セッション**：`{d['session']}`（環境 `[Code]`／作業ディレクトリ `{d['cwd']}`／"
             f"ブランチ `{d['branch']}`）")
    L.append(f"- **作成日時**：{now()}")
    L.append(f"- **対象期間**：{jst(d['started'])} 〜 {jst(d['ended'])}（記録 {d['bytes']/1_000_000:.1f}MB・{d['turns']} 行）")
    L.append(f"- **この引き継ぎを作った理由**：{TODO}（劣化の予兆／区切り／ユーザー指示のいずれか。§0-5）\n")
    L.append("---\n")
    L.append(manifest_section().rstrip() + "\n")

    # 1. 依頼の原文 ── 記録から全文を写す。要約しない
    L.append("## 1. 依頼の原文\n")
    L.append("> **要約していない。ユーザーが述べた言葉をそのまま、時系列で全件載せている。**\n")
    if d['user_messages']:
        for i, m in enumerate(d['user_messages'], 1):
            L.append(f"### 1-{i}（{jst(m['ts'])}）\n")
            L.append(esc(m['text']) + "\n")
    else:
        L.append("（記録にユーザー発言が無い）\n")
    L.append("---\n")

    # 2. 決定と理由 ── 決定の候補は履歴から出せるが、理由は記録に無い
    L.append("## 2. 確定した事実と決定（＋なぜそう決めたか）\n")
    L.append("> **理由は記録に残らない。ここはセッション自身が書く。** 理由が無い決定は、次のセッションで善意によって覆される（§3-14）。\n")
    L.append("| # | 決定したこと | なぜそう決めたか | いつ |")
    L.append("|---|---|---|---|")
    rows = 0
    for ln in commits_in_session(d).splitlines():
        parts = ln.split('|', 2)
        if len(parts) == 3:
            rows += 1
            L.append(f"| {parts[0]} | {parts[2]} | {TODO} | {parts[1]} |")
    if not rows:
        L.append(f"| 1 | {TODO} | {TODO} | |")
    L.append("\n> 左の列は**このセッション中のコミット**から自動生成した"
             "（期間外の履歴は引き継ぎの対象ではないため含めない）。"
             "**「なぜそう決めたか」は履歴に無い。必ず埋めること。**\n")
    L.append("---\n")

    # 3. 却下した案 ── 記録から機械的には取り出せない
    L.append("## 3. 却下した案と、却下の理由\n")
    L.append("> **これが無いと、次のセッションは同じ議論を最初からやり直す。**\n")
    L.append("| # | 検討した案 | 採らなかった理由 |")
    L.append("|---|---|---|")
    L.append(f"| 1 | {TODO} | {TODO} |")
    L.append("\n> 却下の判断は記録に残らない。**この章だけは、記憶があるうちに書くこと。**\n")
    L.append("---\n")

    # 4. 発行したファイル ── 実際に書き換えたものを記録から取る
    L.append("## 4. 発行したすべてのファイル\n")
    L.append("> **一覧ではなく説明を書く。** 名前だけでは、次のセッションは中身を知らない。\n")
    L.append("| ファイル | 操作 | 何のために作ったか・中に何が書いてあるか |")
    L.append("|---|---|---|")
    touched = files_in_session(d)
    for path, how in touched:
        L.append(f"| `{path}` | {how} | {TODO} |")
    if not touched:
        L.append(f"| {TODO} | | {TODO} |")
    L.append("\n> このセッションが**実際に作成・編集した**ファイルだけを、記録と git の差分から自動生成した"
             "（リポジトリ全体の一覧ではない。一覧は `git ls-files` でいつでも取れるため、"
             "引き継ぐべきは「今回どれを触ったか」である）。**用途と内容は自分で埋めること。**\n")
    L.append("---\n")

    # 5. 調整の経緯 ── ユーザーが「変えてほしい」と述べた発言を原文で抜く
    L.append("## 5. セッション中の調整・変更の経緯\n")
    L.append("> ユーザーの発言のうち、訂正・変更・中止の合図を含むものを**原文のまま**抜き出した"
             "（機械判定のため取りこぼし・拾いすぎがある。**必ず目で確認すること**）。\n")
    if d['corrections']:
        for i, m in enumerate(d['corrections'], 1):
            L.append(f"**5-{i}（{jst(m['ts'])}）ユーザーの発言（原文）**\n")
            L.append(esc(m['text'][:1500]) + "\n")
            L.append(f"- **何をどう変えたか**：{TODO}（変える前 → 変えた後）\n")
    else:
        L.append(f"（訂正・調整の合図を含む発言は検出されなかった。**心当たりがあれば手で追加する**）{TODO}\n")
    L.append("---\n")

    # 6. 失敗 ── ツールの異常終了・フックの差し戻しを記録から取る
    L.append("## 6. 失敗と、そこから得た改善\n")
    L.append("> **隠さない。** 失敗の記録は、次のセッションが同じ失敗を繰り返さないための唯一の材料である（§10-4）。\n")
    L.append("| # | いつ | 何が起きたか（記録から） | 原因 | どう直したか |")
    L.append("|---|---|---|---|---|")
    if d['errors']:
        for i, e in enumerate(d['errors'], 1):
            det = e['detail'].replace('|', '\\|').replace('\n', ' ')[:200]
            L.append(f"| {i} | {jst(e['ts'])} | {e['kind']}：{det} | {TODO} | {TODO} |")
    else:
        L.append(f"| 1 | | 記録上の異常終了は無し。**それでも、指摘を受けた失敗があれば書く** | {TODO} | {TODO} |")
    L.append("")
    L.append("---\n")

    # 7. 未完了 ── 応答に残った【未完了】を拾う
    L.append("## 7. 未完了のタスク\n")
    L.append("> **着手済みで途中のものは「未実行」と明記する**（§8-3⑤）。「だいたい終わっている」と書かない。\n")
    L.append("| # | 残っている作業 | どこまで進んだか | 状態 |")
    L.append("|---|---|---|---|")
    if d['incomplete']:
        for i, m in enumerate(d['incomplete'], 1):
            L.append(f"| {i} | {m['text'].replace('|', '/')} | {TODO} | 未着手 / 途中（未実行） |")
    else:
        L.append(f"| 1 | {TODO} | {TODO} | 未着手 / 途中（未実行） |")
    L.append("")
    L.append("---\n")

    # 8. 次の一手
    L.append("## 8. 次に最初に行うこと\n")
    L.append("> **次のセッションが、前置き・要約・再説明なしに、この1行目から始められる形で書く**（§2-20）。\n")
    L.append(f"1. {TODO}\n")
    L.append("---\n")

    # 9. 前提条件
    L.append("## 9. 前提条件・数値前提\n")
    L.append("> レート・単価・期限・環境・権限・パス・依存関係。**「言わなくても分かる」ものこそ書く。**\n")
    L.append("| 項目 | 値 | 出典・根拠 | 確信度 |")
    L.append("|---|---|---|---|")
    L.append(f"| 作業ディレクトリ | `{d['cwd']}` | 記録 | 【確認済】 |")
    L.append(f"| ブランチ | `{d['branch']}` | 記録 | 【確認済】 |")
    L.append(f"| {TODO} | | | 【確認済】/【未確認・推測】/【不明】 |")
    L.append("")
    L.append("---\n")

    # 10. コマンド ── 実際に実行したものを全件、原文のまま
    L.append("## 10. 使用したコマンド・手順\n")
    L.append(f"> セッション中に**実際に実行した**コマンドを、重複を除いて時系列で全件載せた（{len(d['commands'])} 件）。"
             "推測ではなく実行記録である。\n")
    L.append(f"実行ディレクトリ：`{d['cwd']}`\n")
    L.append("```bash")
    for c in d['commands']:
        if c['why']:
            L.append(f"# {c['why']}")
        L.append(c['command'])
    if not d['commands']:
        L.append(f"# 実行したコマンドは記録されていない {TODO}")
    L.append("```\n")
    L.append("---\n")

    # ユーザーが提示した資料
    L.append("## 付録A. ユーザーが提示したファイル・データ\n")
    if d['attachments']:
        for a in d['attachments']:
            L.append(f"- {jst(a['ts'])}　{a['kind']}：`{a['name']}`")
    else:
        L.append("（記録に添付は無い。会話中に貼られた本文は「1. 依頼の原文」に原文で含まれている。）")
    L.append("\n---\n")

    # 会話の原文（応答側）
    if verbatim and d['assistant_texts']:
        L.append("## 付録B. こちらの応答の原文（要約なし）\n")
        L.append("> **なぜ載せるか**：決定の理由は、多くの場合ここに書かれている。"
                 "要約すると失われるため、原文のまま運ぶ。思考（内部の推論）は含まない。\n")
        for i, m in enumerate(d['assistant_texts'], 1):
            L.append(f"### B-{i}（{jst(m['ts'])}）\n")
            L.append(esc(m['text']) + "\n")
        L.append("---\n")

    L.append("""## 引き継ぎ先セッションへの指示（この文をそのまま残す）

このファイルを受け取ったら、**作業に入る前に**次を行う（§10-5）。

1. **受領確認を実行する**：`python3 tools/make_handover.py --receipt <このファイル>`。
   コマンドを実行できない環境では、「0. 受領確認ブロック」の件数と本文を目視で突き合わせる。
2. 本ファイルを全章読む（第1章と付録Bは**原文**である。要約で代用しない）。
3. 「4. 発行したすべてのファイル」に挙がっているファイルの**中身**を読む。
4. 並行する関連チャット・プロジェクト内の情報があれば把握する。
5. 上記を終えてから、「8. 次に最初に行うこと」の1行目を実行する。

**把握できなかったものがある場合は、作業に入る前にその旨を申告する。** 分からないまま進めない（§1-7）。
""")

    body = "\n".join(L)
    manifest = {
        "manifest_version": 1,
        "generated_at": now(),
        "source": "transcript",
        "session": d['session'],
        "cwd": d['cwd'],
        "branch": d['branch'],
        "counts": {
            "依頼の原文": len(d['user_messages']),
            "こちらの応答": len(d['assistant_texts']),
            "訂正・調整の候補": len(d['corrections']),
            "作成・編集したファイル": len(files_in_session(d)),
            "このセッションのコミット": len([x for x in commits_in_session(d).splitlines() if x.strip()]),
            "実行したコマンド": len(d['commands']),
            "記録された失敗": len(d['errors']),
            "未完了": len(d['incomplete']),
            "ユーザー提示の資料": len(d['attachments']),
            "記録の行数": d['turns'],
        },
        "chapters": SECTIONS,
        "sha256": PENDING,
    }
    outp.write_text(stamp(body, manifest), encoding='utf-8')

    print(f"{out} を作成した。")
    print(f"  記録から写した：依頼の原文 {len(d['user_messages'])} 件／実行したコマンド {len(d['commands'])} 件／"
          f"編集したファイル {len(d['files'])} 件／失敗 {len(d['errors'])} 件")
    todo = pathlib.Path(out).read_text(encoding='utf-8').count(TODO)
    print(f"  残りは {todo} 箇所の {TODO}（＝**理由**。記録に残らないため、機械には書けない）。")
    print("  埋め終えたら `--check` を通すこと。通らないうちは渡さない。")
    return 0


# ── ②テンプレートから作る（記録が無い環境）──────────────────
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
    print("`[Code]` で作業しているなら、代わりに `--auto` を使うこと。**会話の原文・実行コマンド・失敗まで自動で入る。**")
    print("依頼の原文・決定の理由・却下した案・調整の経緯・失敗と改善・未完了・次の一手・前提条件は、")
    print("**必ず自分で埋めること。** 埋め終えたら `--check` で検査する。")
    return 0


# ── ③検査 ──────────────────────────────────────────────────
def section_body(t, name):
    """指定した章の本文を返す。**見出し行として現れる箇所だけを章とみなす。**

    単純な文字列検索にすると、受領確認ブロックの中に章名が並んでいる箇所（`chapters`）を
    章そのものと取り違え、**中身を検査せずに合格させてしまう**（実測で発見した不具合）。
    """
    m = re.search(r'^#{1,3}\s*' + re.escape(name), t, re.M)
    if not m:
        return None
    i = m.start()
    j = t.find('\n## ', i + 1)
    return t[i:j if j > 0 else len(t)]


def norm(b):
    """比較用に正規化する。説明の引用文・罫線・空欄は本文とみなさない。"""
    b = re.sub(r'^>.*$', '', b, flags=re.M)
    b = re.sub(r'（[^）]*）', '', b)
    return re.sub(r'[|\s#\-`:_>*]', '', b)


def check(path, template=None):
    """必須10章が「テンプレートのまま」でないかを検査する。
    章の見出しがあるだけでは合格にしない。**中身が書き足されているか**を、
    テンプレートとの差分で判定する（雛形のまま渡す事故を防ぐため）。"""
    template = template or default_template()
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
        elif TODO in body:
            ng.append(f"{TODO} が残っている：{s}（{body.count(TODO)} 箇所）")
    man, ok = read_manifest(t)
    print('── 引き継ぎファイルの検査（L1 §10-5）──')
    if man is None:
        print("  [--] 受領確認ブロックが無い（--auto で作れば自動で入る。手書きなら省略してよい）")
    elif ok:
        print("  [ok] 受領確認ブロックがあり、指紋が本文と一致している")
    else:
        ng.append("受領確認ブロックの指紋が本文と一致しない（生成後に本文が書き換わっている）。"
                  "内容を確定させてから `--auto` で作り直すか、指紋の行を削ること")
    if ng:
        for x in ng:
            print(f"  [NG] {x}")
        print(f"\n不合格 {len(ng)} 件。**この状態で引き継ぐと、次のセッションは同じ状況を再現できない。**")
        print("埋めてから渡すこと。")
        return 1
    print("  [ok] 必須10章がすべて埋まっている")
    print(f"  [ok] {TODO} が残っていない")
    print("  [ok] ファイル名が ASCII 安全")
    print("\n最後に自分で検算すること：")
    print("  「このファイルだけを読んだ第三者が、いま自分がしている作業を続けられるか」")
    print("  答えが『いいえ』なら、まだ足りない。")
    return 0


# ── ④受領 ──────────────────────────────────────────────────
def receipt(path):
    """受け取った側が実行する。**冒頭の確認作業を、質問ではなく照合で終わらせる。**"""
    p = pathlib.Path(path)
    if not p.exists():
        print(f"{path} が無い。", file=sys.stderr)
        return 1
    t = p.read_text(encoding='utf-8')
    man, ok = read_manifest(t)
    print('── 引き継ぎの受領確認（L1 §10-5）──')
    print(f"  ファイル : {p.name}（{len(t):,} 字）")
    if man is None:
        print("  完全性   : 【不明】受領確認ブロックが無い。手書きの引き継ぎか、生成後に削られている。")
        print("             → **この場合、取りこぼしの有無は機械では確かめられない。**")
        print("               各章を読んだうえで、不足があればユーザーに申告すること。")
    elif ok:
        print(f"  完全性   : 【確認済】一致。生成時（{man.get('generated_at')}）から1文字も変わっていない")
        print(f"             sha256 {man.get('sha256','')[:32]}…")
    else:
        print("  完全性   : 【要注意】指紋が一致しない。**生成後に本文が変わっている。**")
        print("             → 途中で切れて貼られた可能性がある。元のファイルを取り直すこと。")
    if man:
        print(f"  引き継ぎ元: セッション {man.get('session')}／`{man.get('cwd')}`／ブランチ `{man.get('branch')}`")
        print("  受領内容 :")
        for k, v in (man.get('counts') or {}).items():
            print(f"             {k}: {v}")
    miss = [s for s in SECTIONS if section_body(t, s) is None]
    todo = t.count(TODO)
    print(f"  10章     : {'すべて存在する' if not miss else '欠落あり → ' + '／'.join(miss)}")
    if todo:
        print(f"  未記入   : {TODO} が {todo} 箇所残っている。**その箇所は引き継がれていない。**")
    nxt = section_body(t, "8. 次に最初に行うこと") or ''
    first = next((re.sub(r'^\s*(?:1\.|[-*])\s*', '', ln).strip() for ln in nxt.splitlines()
                  if re.match(r'^\s*(?:1\.|[-*])\s+\S', ln)
                  and not re.match(r'^\s*[-*\s]+$', ln) and TODO not in ln), '')
    print(f"  次の一手 : {first or '（第8章に記載が無い。ユーザーに確認すること）'}")
    print()
    if man and ok and not miss and not todo:
        print("  → **受領は完全である。冒頭の確認作業は、この照合をもって完了とする。**")
        print("    ユーザーに「理解できているか」を確かめる質問をする必要はない。")
        print("    そのうえで、第1章（依頼の原文）と付録B（応答の原文）を読んでから作業に入ること。")
        return 0
    print("  → **受領は不完全である。** 上の不足を、作業に入る前にユーザーへ申告すること（§1-7）。")
    return 1


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--auto', metavar='OUT', help='セッションの記録から中身まで埋めて作る（[Code] 限定）')
    g.add_argument('--new', metavar='OUT', help='テンプレートを複製する（記録が無い環境向け）')
    g.add_argument('--check', metavar='FILE', help='渡せる状態かを検査する')
    g.add_argument('--receipt', metavar='FILE', help='受け取った側が完全性を照合する')
    ap.add_argument('--template', default=None)
    ap.add_argument('--transcript', default=None, help='記録ファイルを明示する（既定は自動検出）')
    ap.add_argument('--no-verbatim', action='store_true', help='付録B（応答の原文）を含めない')
    a = ap.parse_args()
    tpl = a.template or default_template()
    if a.auto:
        return auto(a.auto, tpl, a.transcript, verbatim=not a.no_verbatim)
    if a.new:
        return new(a.new, tpl)
    if a.check:
        return check(a.check, tpl)
    return receipt(a.receipt)


if __name__ == '__main__':
    sys.exit(main())
