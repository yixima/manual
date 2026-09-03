#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""引き継ぎファイルの作成・検査・受領を行う（L1 §10-5）。

  --auto OUT      セッションの記録から**中身まで**埋めた引き継ぎファイルを作る（`[Code]` 限定）
  --new  OUT      記録が無い環境向け。テンプレートを複製し、機械で分かる部分だけ埋める
  --check FILE    書き上げた引き継ぎファイルが、渡せる状態かを検査する
  --seal FILE     理由を書き加えたあとに封（指紋）をし直す。--check の前に1回だけ
  --merge OUT     枝分かれした引き継ぎを1本にまとめる（--from に枝を並べる）
  --tidy DIR      受け口を案件ごとのフォルダへ片付ける（移動のみ・消さない）
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
TODO = '【要記入】'          # **必須**。ここが埋まらない限り渡せない。
OPT = '〔任意〕'             # **任意**。埋めれば精度は上がるが、検査は不合格にしない。
# なぜ2種類に分けたか（2026-09-01）：
#   すべての行に理由を要求した結果、1回の生成で 181 箇所の【要記入】が出た
#   （ファイル107件・コミット38件それぞれに理由を求めていた）。
#   **「必ず埋めよ」と「1行ごとに埋めよ」は同時に成立しない**——検査が現実に通らず、
#   引き継ぎが完成しない状態になっていた（§3-14 自作した要件の相互矛盾）。
#   よって理由を必須にする対象を、**重要な決定と大きな成果物**に絞った。
#   個々のコミット・個々のファイルは、記録から自動で入る事実だけで足りる。
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



FENCE = re.compile(r'^(`{3,})')


def fillable(text):
    """**記入欄だけ**を返す。原文（引用ブロック・コードブロック）は取り除く。

    なぜ必要か：引き継ぎファイルは原文をそのまま運ぶ（§10-5 原本主義）。
    その原文の中に `【要記入】` という文字列が現れることは普通にありうる——
    たとえば、この仕組み自体を作った作業の記録がそうであった。
    **原文に何が書いてあっても、それは記入欄ではない。** 検査対象から外す。
    （実測で発見した不具合。原文を検査対象に含めていたため、記入済みのファイルが
      いつまでも不合格になった。）

    コードブロックの終わりは、**開いたときと同じ数以上の ` で閉じられたとき**とする。
    3個で開いたブロックの中に3個の行があると、そこで閉じたと誤認するため、
    原文を載せる側は4個以上で開く。
    """
    out, fence = [], None
    for ln in text.splitlines():
        m = FENCE.match(ln)
        if fence is None:
            if m:
                fence = len(m.group(1))
                continue
            if ln.lstrip().startswith('>'):
                continue                      # 引用＝原文
            # 行内コード（`…`）の中は、**その言葉について書いた文**であって記入欄ではない。
            # 例：「`【要記入】` が42箇所残っていた」という失敗の記録は、未記入ではない。
            out.append(re.sub(r'`[^`]*`', '', ln))
        else:
            if m and len(m.group(1)) >= fence:
                fence = None
    return "\n".join(out)


SLOT_TABLE = re.compile(r'\|\s*' + re.escape(TODO) + r'\s*(?=\||$)')
SLOT_LINE = re.compile(r'(?:^|[：:]|^\s*[-*\d]+[.)]?\s*)\s*' + re.escape(TODO), re.M)


def todo_count(text):
    """**記入欄だけ**を数える。原文の中に現れた同じ文字列は数えない。

    `fillable()` は引用・コード・鉤括弧を落とすが、**表のセルに入った原文**は残る。
    記録から自動で入る「失敗の一覧」には、過去のコマンド出力がそのまま入るため、
    そこに `【要記入】` の語を含む行があると、**記入欄として数えられてしまう**。
    実際に起きた（2026-09-03）——検査の合格出力
    「[ok] 【要記入】 が残っていない」が記録に取り込まれ、**未記入1件として差し戻された**。

    記入欄は、次のどちらかの形をしている。
      - 表のセル**そのもの**（`| 【要記入】 |`）
      - 行頭・箇条書きの直後・`：` の直後
    **文の途中に現れたものは、記入欄ではない。**

    （これは v25・v27 の型I 誤検知、v22 の記入欄誤認と**同じ型**である。
      原文の中に何が書いてあっても、それはこの応答の記入欄ではない。4度目の再発。）
    """
    t = fillable(text)
    return len(SLOT_TABLE.findall(t)) + len(
        [m for m in SLOT_LINE.finditer(t) if '|' not in t[max(0, m.start() - 80):m.start()].split('\n')[-1]])

# ── ①記録から作る（[Code]）──────────────────────────────────
def case_from(out):
    """ファイル名から案件名を推定する。`案件名.枝名_handover_...md` の形を想定する。"""
    stem = pathlib.Path(out).name
    for cut in ('_handover_', '_handover'):
        if cut in stem:
            stem = stem.split(cut)[0]
            break
    return stem.split('.')[0]


SAFE = re.compile(r'^[A-Za-z0-9._-]+$')


def normalize_name(name):
    """ユーザーが言った案件名を、そのまま使える形に**機械的に**直す。

    **語を足さない。減らさない。使えない文字だけを置き換える。**
    なぜ機械でやるか：セッションごとに判断させると、**同じ案件名から違うファイル名が生まれる**。
    実際に起きた（2026-09-02）——ユーザーが「kobo anken」と指定したのに、
    別のセッションが `kobo_anken_hikitsugi_20260902_v1.md` を作った。
    空白を `_` にするのは §7-11 が要求するので正しい。
    だが **`hikitsugi` という語を足し、固定名（`_latest`）を作らなかったのは誤りである**——
    日付入りの名前しか無ければ、次のセッションは**日付を知らないと見つけられない**
    （§5.6「場所と名前が固定。探させない」）。
    """
    t = name.strip()
    t = re.sub(r'[\s\u3000]+', '_', t)          # 空白（全角も）→ _
    t = re.sub(r'[^A-Za-z0-9._-]', '-', t)      # 残る使えない文字 → -
    t = re.sub(r'-{2,}', '-', t).strip('-._')
    # **残るものが無ければ、勝手に名前を付けない。** 全部が日本語のときがこれである。
    # ここで `case` のような既定値を作ると、**別の案件と同じ名前になり、上書きし合う**。
    return t


def case_dir(receptacle, case):
    """案件ごとのフォルダ。**受け口にファイルが散乱しないようにする。**

    受け口の直下に全案件のファイルを並べると、案件が増えるほど探しにくくなり、
    §5.6 の「場所と名前が固定（探させない）」が実質的に成り立たなくなる。
    `<受け口>/<案件名>/` に入れれば、案件名さえ分かればフォルダごと辿れる。
    """
    return pathlib.Path(receptacle) / case


def paths_for(case, lane=''):
    """固定名（探させない）と日付版（履歴を残す）の2本を返す。

    **固定名は必ず作る。** 次のセッションはこの名前だけを頼りに探す。
    日付版は、節目ごとの上書きで消えないよう、履歴として別途残す（§5.6）。
    """
    stem = f"{case}.{lane}" if lane else case
    day = datetime.datetime.now().strftime('%Y%m%d')
    return f"{stem}_handover_latest.md", f"{stem}_handover_{day}_v1.md"


def lane_path(out, lane):
    """枝名を与えられたとき、枝ごとに別のファイル名にする。

    **同じ名前に上書きさせないことが、この関数の唯一の目的である。**
    `tokyo_handover_latest.md` ＋ 枝 `survey` → `tokyo.survey_handover_latest.md`
    """
    if not lane:
        return out
    p = pathlib.Path(out)
    name = p.name
    for cut in ('_handover_', '_handover'):
        if cut in name:
            head, rest = name.split(cut, 1)
            if '.' in head:               # すでに枝が付いていれば付け替える
                head = head.split('.')[0]
            return str(p.with_name(f"{head}.{lane}{cut}{rest}"))
    return str(p.with_name(f"{p.stem}.{lane}{p.suffix}"))


def auto(out, template, transcript=None, cwd=None, verbatim=True,
         case='', lane='', parent=''):
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
    # ── 受け取った側への指示を、**ファイルの先頭に載せる**。────────────
    # なぜここに載せるか（2026-09-03 の事案。L2 記録参照）：
    # 枝の名前を決める規則はコアカードに書いてあったが、**発動経路がカード1本しか無かった**。
    # `[Chat]` `[Cowork]` にはフックが無く、規則はカードの奥（§5.6）にあるため、
    # 実際の枝セッションは**候補の提案そのものは正しく行ったが、順序を守らなかった**——
    # 31コマンドを実行し成果物5点を作り終えてから、**他の3問と束ねて 4/4 番目に**質問した。
    # 規定は「セッションの開始時に決める」「質問は一度に一つ」である。
    # **守られなかったのは内容ではなく順序であった。** 順序は、書く場所で決まる。
    # 引き継ぎファイルは、どの環境でも**必ず読まれる**。指示は荷物と一緒に運ぶ。
    L.append('> ## 受け取ったセッションが、最初にすること\n>\n> **1. 枝の名前を決める。これが最初の応答である。他の作業に着手しない。**\n>\n> このファイルの **7章（未完了）と 8章（次に最初に行うこと）** を読み、\n> **このセッションの引き継ぎファイル名を2〜3個、意味を1行添えて提案し、\n> ユーザーに「一つだけ」質問する**（§2-4／§5.6）。\n>\n> - 形は `<親の案件名>_<このセッションで何をするか>`。例：`kobo_anken_omatsuri`（お祭り案件の申請作業）\n> - 半角英数・ハイフン・アンダースコアのみ（§7-11）。**日本語のままの名前は提案しない**\n> - **「名前を決めてください」と丸投げしない。候補を出すのがこちらの仕事である**\n> - **他の質問と束ねない。** 枝名の質問が先、それ以外はあと（§2-4 質問は一度に一つ）\n> - ユーザーが承認（または訂正）したら、**その場で最初の保存まで済ませる**。\n>   訂正された名前はそのまま使う（使えない文字だけを直し、**語を足さない**）\n>\n> **2. そのうえで、8章の1行目から作業を始める。** 前置き・要約・再説明は書かない（§2-20）。\n>\n> **枝の名前が決まるまで、受け口へ保存しない。** 名前なしで保存すると、\n> **別のセッションの引き継ぎを上書きして消す**（消えたことは照合では検出できない）。\n')
    L.append("---\n")
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
    L.append("**とくに重要な決定（3件以内）** ——ここは**必ず**埋める。"
             "次のセッションが方針を覆さないために、これだけは要る。\n")
    L.append("| # | 決定したこと | なぜそう決めたか |")
    L.append("|---|---|---|")
    for i in (1, 2, 3):
        L.append(f"| {i} | {TODO} | {TODO} |")
    L.append("\n**このセッション中の変更の履歴（自動）** ——事実は記録から入っている。"
             "理由の補足は任意であり、**空欄でも渡せる**。\n")
    L.append("| # | 変更したこと | 補足（任意） | いつ |")
    L.append("|---|---|---|---|")
    rows = 0
    for ln in commits_in_session(d).splitlines():
        parts = ln.split('|', 2)
        if len(parts) == 3:
            rows += 1
            L.append(f"| {parts[0]} | {parts[2]} | {OPT} | {parts[1]} |")
    if not rows:
        L.append(f"| 1 | （このセッション中の変更は記録されていない） | {OPT} | |")
    L.append("\n> 下の表は**このセッション中のコミット**から自動生成した"
             "（期間外の履歴は引き継ぎの対象ではないため含めない）。"
             "**変更の内容そのものは記録に残っているため、1件ずつ理由を書く必要はない。**"
             "書き残すべき理由は、上の「とくに重要な決定」に集約する。\n")
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
    L.append("**主な成果物（3件以内）** ——ここは**必ず**埋める。"
             "次のセッションが「何を渡されたのか」を知るために、これだけは要る。\n")
    L.append("| # | 成果物 | 何のために作ったか・中に何が書いてあるか |")
    L.append("|---|---|---|")
    for i in (1, 2, 3):
        L.append(f"| {i} | {TODO} | {TODO} |")
    touched = files_in_session(d)
    L.append(f"\n**触ったファイルの一覧（自動・{len(touched)}件）** ——事実は記録から入っている。"
             "個々の説明は任意であり、**空欄でも渡せる**。\n")
    L.append("| ファイル | 操作 | 補足（任意） |")
    L.append("|---|---|---|")
    for path, how in touched:
        L.append(f"| `{path}` | {how} | {OPT} |")
    if not touched:
        L.append(f"| （このセッションで作成・編集したファイルは記録されていない） | | {OPT} |")
    L.append("\n> このセッションが**実際に作成・編集した**ファイルだけを、記録と git の差分から自動生成した"
             "（リポジトリ全体の一覧ではない。一覧は `git ls-files` でいつでも取れるため、"
             "引き継ぐべきは「今回どれを触ったか」である）。"
             "**1件ずつ用途を書く必要はない。書くべきは、上の「主な成果物」だけである。**\n")
    L.append("---\n")

    # 5. 調整の経緯 ── ユーザーが「変えてほしい」と述べた発言を原文で抜く
    L.append("## 5. セッション中の調整・変更の経緯\n")
    L.append("> ユーザーの発言のうち、訂正・変更・中止の合図を含むものを**原文のまま**抜き出した"
             "（機械判定のため取りこぼし・拾いすぎがある。**必ず目で確認すること**）。\n")
    if d['corrections']:
        for i, m in enumerate(d['corrections'], 1):
            L.append(f"**5-{i}（{jst(m['ts'])}）ユーザーの発言（原文）**\n")
            L.append(esc(m['text'][:1500]) + "\n")
            L.append(f"- **何をどう変えたか**：{OPT}（変える前 → 変えた後）\n")
    else:
        L.append(f"（訂正・調整の合図を含む発言は検出されなかった。**心当たりがあれば手で追加する**）{OPT}\n")
    L.append("---\n")

    # 6. 失敗 ── ツールの異常終了・フックの差し戻しを記録から取る
    L.append("## 6. 失敗と、そこから得た改善\n")
    L.append("> **隠さない。** 失敗の記録は、次のセッションが同じ失敗を繰り返さないための唯一の材料である（§10-4）。\n")
    L.append("| # | いつ | 何が起きたか（記録から） | 原因 | どう直したか |")
    L.append("|---|---|---|---|---|")
    if d['errors']:
        for i, e in enumerate(d['errors'], 1):
            det = e['detail'].replace('|', '\\|').replace('\n', ' ')[:200]
            L.append(f"| {i} | {jst(e['ts'])} | {e['kind']}：{det} | {OPT} | {OPT} |")
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
            L.append(f"| {i} | {m['text'].replace('|', '/')} | {OPT} | 未着手 / 途中（未実行） |")
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
    L.append("````bash")
    for c in d['commands']:
        if c['why']:
            L.append(f"# {c['why']}")
        L.append(c['command'])
    if not d['commands']:
        L.append(f"# 実行したコマンドは記録されていない {TODO}")
    L.append("````\n")
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
        # ── 枝分かれ（1つの作業が2つ以上のセッションへ分かれるとき）──
        # case ＝ 案件名（枝が違っても同じ）／lane ＝ 枝の名前／parent ＝ 分岐元のファイル名
        # これが無いと、枝ごとの引き継ぎが**同じファイル名を奪い合って上書きし合う**。
        # 上書きされた側は、受領の照合（指紋・件数）では**検出できない**——
        # 届いたファイルが壊れていないことしか見ないからである（L2 記録参照）。
        "case": case or case_from(out),
        "lane": lane,
        "parent": parent,
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
    # ── 上書きの門番（枝分かれ対策）──────────────────────────
    # **別のセッションが書いた引き継ぎを、枝の名前を決めないまま上書きさせない。**
    # 上書きされた側は、受領の照合（指紋・件数）では**検出できない**。
    # 届いたファイルが壊れていないことしか見ないためである（L2 記録参照）。
    if outp.exists() and not lane:
        try:
            prev, _ = read_manifest(outp.read_text(encoding='utf-8'))
        except Exception:
            prev = None
        prev_sid = (prev or {}).get('session', '')
        if prev_sid and prev_sid != d['session']:
            print(f"[中止] {out} は**別のセッションが書いた引き継ぎ**である"
                  f"（既存: {prev_sid[:8]}… ／ いま: {d['session'][:8]}…）。", file=sys.stderr)
            print("  このまま書くと、**先に保存された引き継ぎが消える。**"
                  "しかも消えたことは、受領の照合では検出できない。", file=sys.stderr)
            print("  1つの作業が2つ以上のセッションへ**枝分かれ**しているなら、"
                  "枝の名前を付けて別のファイルにすること：", file=sys.stderr)
            print(f"    python3 tools/make_handover.py --auto {out} --lane <枝の名前> "
                  f"--parent {outp.name}", file=sys.stderr)
            print("  枝の名前は、**ユーザーに一つだけ質問して決める**"
                  "（勝手に付けない。名前が変わると次のセッションから見えなくなる）。", file=sys.stderr)
            return 1

    outp.write_text(stamp(body, manifest), encoding='utf-8')

    # **固定名だけでは履歴が残らない（節目ごとに上書きするため）。日付版も並べて残す**（§5.6）。
    try:
        # 日付版は、**実際の固定名から**導く。案件名から組み直すと、
        # 枝の引き継ぎが親の日付版と同じ名前になり、履歴が残らない。
        day = datetime.datetime.now().strftime('%Y%m%d')
        dated = outp.name.replace('_handover_latest.md', f'_handover_{day}_v1.md')
        dp = outp.with_name(dated)
        if outp.name.endswith('_handover_latest.md') and not dp.exists():
            dp.write_text(outp.read_text(encoding='utf-8'), encoding='utf-8')
            print(f"日付版も残した：{dp.name}（固定名が上書きされても履歴が消えないように）")
    except Exception:
        pass

    print(f"{out} を作成した。")
    print(f"  記録から写した：依頼の原文 {len(d['user_messages'])} 件／実行したコマンド {len(d['commands'])} 件／"
          f"編集したファイル {len(d['files'])} 件／失敗 {len(d['errors'])} 件")
    todo = todo_count(pathlib.Path(out).read_text(encoding='utf-8'))
    print(f"  残りは {todo} 箇所の {TODO}（＝**理由**。記録に残らないため、機械には書けない）。")
    print(f"  〔任意〕の欄は埋めなくても渡せる。**必ず要るのは、重要な決定3件と主な成果物3件の理由だけ。**")
    print("  埋め終えたら **`--seal` で封をし直してから** `--check` を通すこと。"
          "通らないうちは渡さない。")
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



def substance(body, tpl_body):
    """その章に、**説明文以外の中身**がどれだけあるかを数える。

    引用ブロックを一律に除くと、**原文だけで構成される章（1章・付録）が空と判定される**
    （実測で発見した不具合）。原本主義（§10-5）では引用こそが中身であるため、
    「テンプレートに元からある行」を引いた残りを中身として数える。
    """
    base = set(l.strip() for l in (tpl_body or '').splitlines())
    rest = [l for l in body.splitlines()
            if l.strip() not in base and not l.startswith('#')]
    return len(re.sub(r'[|\s#\-`:_>*]', '', "\n".join(rest)))

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
        elif substance(body, tb) < 12:
            ng.append(f"章の中身がほとんど無い：{s}")
        elif todo_count(body):
            ng.append(f"{TODO} が残っている：{s}（{todo_count(body)} 箇所）")
    man, ok = read_manifest(t)
    print('── 引き継ぎファイルの検査（L1 §10-5）──')
    if man is None:
        print("  [--] 受領確認ブロックが無い（--auto で作れば自動で入る。手書きなら省略してよい）")
    elif ok:
        print("  [ok] 受領確認ブロックがあり、指紋が本文と一致している")
    else:
        ng.append("受領確認ブロックの指紋が本文と一致しない（生成後に本文が書き換わっている）。"
                  "**理由を書き終えたのなら、これは正常である。** "
                  "`python3 tools/make_handover.py --seal <このファイル>` "
                  "で封をし直してから、もう一度 `--check` を通すこと")
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




RE_ESCAPED = re.compile(r'\\([#*`>|_\[\]~-])')


def unescape(text):
    """記号の退避（`\#` `\*` など）を戻す。**構造を読むときだけ使う。**

    経路によっては、見出しやコードブロックの記号が `\` で退避された姿で届く
    （実測：Google Drive の自然言語読み出し）。退避されたままだと
    **見出しも受領確認ブロックも見つからず、「全章が欠落」という誤った判定になる**。
    指紋の照合は退避を戻さない原文に対して行う（書式が変わったことは、それ自体が情報である）。
    """
    return RE_ESCAPED.sub(r'\1', text)

# ── 受領の3段階判定 ────────────────────────────────────────
# **なぜ3段階が要るか**：引き継ぎファイルは、環境によっては**そのままの姿では届かない**。
# 実測（2026-09-01）：Google Drive に text/markdown で保存したファイルを
#   ・download（生のまま取得）→ **バイト単位で完全一致**
#   ・read（自然言語表現として取得）→ **記号が `\` で退避され、連続する空白が詰められる**
# つまり、経路によっては**中身は全部届いているのに指紋だけが合わない**ことが起きる。
# ここで「不一致＝欠落」と断じると、正しく届いた引き継ぎを毎回はねてしまう（§3-11 代理指標による断定）。
# よって、**指紋（書式まで含めた同一性）と、件数（項目の欠落）を分けて判定する。**
RE_REQ = re.compile(r'^[\\#\s]*1-(\d+)\s*[（(]', re.M)     # 第1章の見出し「1-N（…）」
RE_ASST = re.compile(r'^[\\#\s]*B-(\d+)\s*[（(]', re.M)     # 付録Bの見出し「B-N（…）」


def recount(text):
    """本文から数え直す。**記号の退避や空白の詰まりに影響されない数え方**にする。"""
    return {"依頼の原文": len(RE_REQ.findall(text)),
            "こちらの応答": len(RE_ASST.findall(text))}

# ── ④受領 ──────────────────────────────────────────────────
def merge(out, sources):
    """枝分かれした引き継ぎを1本にまとめる（合流）。

    **要約しない。** 各枝の全文をそのまま連ねて残す（§10-5 原本主義）。
    合流で失われてよいものは何も無い——どちらの枝が正しかったかは、
    **あとから読む人が判断する**のであって、ここで選別してはならない。

    先頭に「どの枝が何を持っているか」の一覧を置き、
    そこだけを読めば、必要な枝の該当箇所へ行けるようにする。
    """
    srcs = [pathlib.Path(x) for x in sources]
    missing = [str(x) for x in srcs if not x.exists()]
    if missing:
        print("次のファイルが無い：" + "、".join(missing), file=sys.stderr)
        return 1
    if len(srcs) < 2:
        print("合流には2本以上の枝が要る。", file=sys.stderr)
        return 1

    L = [f"# 引き継ぎ（合流・{pathlib.Path(out).stem}）", "",
         f"> **{len(srcs)} 本の枝を1本にまとめたものである。作成 {now()}。**",
         "> **要約していない。** 各枝の全文をそのまま連ねてある。",
         "> どの枝が正しかったかは**ここでは決めていない**。読む人が判断する。", "",
         "## 0. どの枝が何を持っているか", "",
         "| # | 枝 | 案件 | 分岐元 | 生成時刻 | 依頼の原文 | 実行したコマンド |",
         "|---|---|---|---|---|---|---|"]
    for i, sp in enumerate(srcs, 1):
        t = sp.read_text(encoding='utf-8')
        man, _ = read_manifest(t)
        man = man or {}
        c = man.get('counts') or {}
        L.append(f"| {i} | {man.get('lane') or '（枝名なし）'} | {man.get('case') or '—'} | "
                 f"{man.get('parent') or '—'} | {man.get('generated_at', '—')} | "
                 f"{c.get('依頼の原文', '—')} | {c.get('実行したコマンド', '—')} |")
    L += ["",
          "> **矛盾があれば、それ自体が引き継ぐべき情報である。**"
          "どちらかを消して辻褄を合わせない（§1-9）。", "",
          "---", ""]
    for i, sp in enumerate(srcs, 1):
        t = sp.read_text(encoding='utf-8')
        man, ok = read_manifest(t)
        man = man or {}
        L += [f"# 枝 {i}／{man.get('lane') or sp.stem}（原本：`{sp.name}`）", "",
              f"> 受領の照合：{'指紋一致（1文字も変わっていない）' if ok else '指紋不一致（生成後に変更あり）'}",
              "", t.rstrip(), "", "---", ""]

    body = "\n".join(L)
    manifest = {
        "manifest_version": 1,
        "generated_at": now(),
        "source": "merge",
        "session": "", "cwd": str(pathlib.Path.cwd()), "branch": "",
        "case": case_from(out), "lane": "", "parent": "",
        "merged_from": [x.name for x in srcs],
        "counts": {}, "chapters": SECTIONS, "sha256": PENDING,
    }
    outp = pathlib.Path(out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(stamp(body + "\n\n" + manifest_section().replace(
        '__MANIFEST__', json.dumps(manifest, ensure_ascii=False, indent=2)), manifest),
        encoding='utf-8')
    print(f"{out} に {len(srcs)} 本の枝をまとめた（要約していない）。")
    for sp in srcs:
        print(f"  ← {sp.name}")
    print("  → 0章の一覧で、どの枝に何があるかを確かめること。")
    print("  → **枝の原本は消さない。** 合流後も、細部は原本にしか無い。")
    return 0


def tidy(receptacle):
    """受け口に散らばった引き継ぎを、案件ごとのフォルダへ片付ける。

    **移動するだけで、消さない**（§8-5 不可逆操作の標準手順）。
    件数を照合し、移動前後で1件も失われていないことを確かめてから報告する。
    """
    d = pathlib.Path(receptacle)
    if not d.is_dir():
        print(f"{receptacle} が無い。", file=sys.stderr)
        return 1
    loose = [f for f in d.glob('*.md')
             if f.is_file() and '_handover' in f.name]
    if not loose:
        print(f"{receptacle} の直下に、片付ける引き継ぎは無い。")
        return 0
    before = len(loose)
    moved, skipped = [], []
    for f in loose:
        case = case_from(f.name)
        if not case:
            skipped.append((f.name, '案件名を読み取れない'))
            continue
        dst = case_dir(d, case) / f.name
        if dst.exists():
            skipped.append((f.name, f'{case}/ に同名がすでにある'))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        f.rename(dst)
        moved.append((f.name, case))
    after = len(list(d.rglob('*_handover*.md')))
    print(f"── 受け口の片付け（{receptacle}）──")
    for name, case in moved:
        print(f"  [移動] {name} → {case}/")
    for name, why in skipped:
        print(f"  [据置] {name}（{why}。**消していない**）")
    print(f"  件数照合：片付け前 {before} 件 → 片付け後 {after} 件"
          f"（全体。**1件も消していない**）")
    if skipped:
        print("  → 据え置いたものは、名前が重複しているか案件名が読めない。"
              "**どちらもユーザーに確認してから動かすこと**（§8-5）。")
    return 0


def seal(path):
    """**理由を書き加えたあとに、封をし直す。**

    なぜ必要か（実測で見つけた設計の矛盾。L2 記録参照）：
    `--auto` は生成した瞬間の本文で指紋（sha256）を確定させる。ところがこの仕組みは、
    生成後に**人が理由を書き足すことを前提にしている**（機械には書けないため）。
    つまり「理由を埋めよ」と「指紋を保て」は**同時に成立しない**——
    理由を埋めた瞬間に指紋が外れ、`--check` が構造上ぜったいに通らなくなる。
    実際、v24 の引き継ぎは 17 箇所の未記入を残したまま、一度も検査を通っていなかった。

    直し方は「指紋の検査をやめる」ではない（それでは欠落を検知できなくなる）。
    **書き終えたことを人が宣言し、その時点の本文で封をし直す**手順を足す。
    件数も本文から数え直して入れ直すため、封のあとの `--receipt` は正しく働く。
    """
    p = pathlib.Path(path)
    if not p.exists():
        print(f"{path} が無い。", file=sys.stderr)
        return 1
    raw = p.read_text(encoding='utf-8')
    man, ok = read_manifest(raw)
    if man is None:
        print("  [--] 受領確認ブロックが無い。封をする対象が無い（手書きの引き継ぎ）。")
        return 0
    if ok:
        print("  [ok] すでに指紋は本文と一致している。封をし直す必要は無い。")
        return 0
    old = man.get('sha256', '')
    # 件数を本文から数え直す。理由を書き足しても件数は変わらないはずだが、
    # 章ごと削るような編集をしたときに、封が嘘をつかないようにする。
    got = recount(unescape(raw))
    for k in list((man.get('counts') or {}).keys()):
        if k in got:
            man['counts'][k] = got[k]
    man['sealed_at'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    man['sha256'] = PENDING
    body = MANIFEST_RE.sub(
        lambda m: m.group(0).replace(m.group(1), json.dumps(man, ensure_ascii=False, indent=2)),
        raw, count=1)
    digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
    p.write_text(body.replace(PENDING, digest), encoding='utf-8')
    print(f"{path} に封をし直した。")
    print(f"  指紋 : {old[:16]}… → {digest[:16]}…")
    print("  → もう一度 `--check` を通すこと。通ってはじめて渡せる。")
    return 0


def receipt(path):
    """受け取った側が実行する。**冒頭の確認作業を、質問ではなく照合で終わらせる。**

    判定は3段階。
      ① 指紋が一致        → 1文字も変わっていない。完全
      ② 指紋は不一致だが件数は一致 → **項目の欠落は無い。書式だけが変わっている**
                             （Drive の整形読み出し・チャットへの貼り付け等で起きる）
      ③ 件数も不一致      → **欠落がある。** 何が足りないかを名指しして申告する
    """
    p = pathlib.Path(path)
    if not p.exists():
        print(f"{path} が無い。", file=sys.stderr)
        return 1
    raw = p.read_text(encoding='utf-8')
    # 指紋は原文に対して、構造（章・件数・記入欄）は退避を戻した写しに対して見る。
    t = unescape(raw)
    man, ok = read_manifest(raw)
    if man is None:
        man, ok = read_manifest(t)[0], False
    got = recount(t)
    want = {k: v for k, v in (man.get('counts') or {}).items() if k in got} if man else {}
    short = {k: (want[k], got[k]) for k in want if got[k] < want[k]}

    print('── 引き継ぎの受領確認（L1 §10-5）──')
    print(f"  ファイル : {p.name}（{len(raw):,} 字）")
    if man is None:
        level = 'unknown'
        print("  完全性   : 【不明】受領確認ブロックが無い。手書きの引き継ぎか、生成後に削られている。")
        print("             → **この場合、取りこぼしの有無は機械では確かめられない。**")
        print("               各章を読んだうえで、不足があればユーザーに申告すること。")
    elif ok:
        level = 'exact'
        print(f"  完全性   : 【確認済】指紋が一致。生成時（{man.get('generated_at')}）から**1文字も変わっていない**")
        print(f"             sha256 {man.get('sha256','')[:32]}…")
    elif not short:
        level = 'reformatted'
        print("  完全性   : 【要注意】**本文は生成時から変わっている。ただし項目の数は揃っている。**")
        print("             指紋は一致しないが、本文から数え直した件数はマニフェストと一致する"
              "（＝**途中で切れて届いた形跡は無い**）。")
        print("             → 経路での整形（記号の退避・連続空白の詰まり）である可能性が高い。"
              "**ただし、整形と中身の書き換えを機械で区別することはできない。**")
        print("             → **生のまま取得し直せるなら、取り直すこと。** 取り直せないなら"
              "この状態で進めてよいが、**原文の細部は原本と異なりうる**ことを踏まえる。")
    else:
        level = 'missing'
        print("  完全性   : 【要注意】**欠落がある。** 件数が足りない：")
        for k, (w, g) in short.items():
            print(f"             {k}: マニフェスト {w} 件 → 本文に {g} 件（{w - g} 件不足）")
        print("             → 途中で切れて貼られた可能性が高い。**元のファイルを取り直すこと。**")

    if man:
        print(f"  引き継ぎ元: セッション {man.get('session')}／`{man.get('cwd')}`／ブランチ `{man.get('branch')}`")
        print("  受領内容 :")
        for k, v in (man.get('counts') or {}).items():
            mark = ''
            if k in got:
                mark = '  ← 本文で確認済' if got[k] >= v else f'  ← **本文には {got[k]} 件しかない**'
            print(f"             {k}: {v}{mark}")
    miss = [s for s in SECTIONS if section_body(t, s) is None]
    todo = todo_count(t)
    print(f"  10章     : {'すべて存在する' if not miss else '欠落あり → ' + '／'.join(miss)}")
    if todo:
        print(f"  未記入   : {TODO} が {todo} 箇所残っている。**その箇所は引き継がれていない。**")
    nxt = section_body(t, "8. 次に最初に行うこと") or ''
    first = next((re.sub(r'^\s*(?:1\.|[-*])\s*', '', ln).strip() for ln in nxt.splitlines()
                  if re.match(r'^\s*(?:1\.|[-*])\s+\S', ln)
                  and not re.match(r'^\s*[-*\s]+$', ln) and TODO not in ln), '')
    print(f"  次の一手 : {first or '（第8章に記載が無い。ユーザーに確認すること）'}")
    print()
    if level in ('exact', 'reformatted') and not miss and not todo:
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
    g.add_argument('--seal', metavar='FILE',
                   help='理由を書き加えたあとに封（指紋）をし直す。--check の前に1回')
    g.add_argument('--merge', metavar='OUT',
                   help='枝分かれした引き継ぎを1本にまとめる（--from に枝を並べる）')
    g.add_argument('--tidy', metavar='DIR',
                   help='受け口に散らばった引き継ぎを案件ごとのフォルダへ片付ける（移動のみ・消さない）')
    g.add_argument('--receipt', metavar='FILE', help='受け取った側が完全性を照合する')
    ap.add_argument('--template', default=None)
    ap.add_argument('--transcript', default=None, help='記録ファイルを明示する（既定は自動検出）')
    ap.add_argument('--no-verbatim', action='store_true', help='付録B（応答の原文）を含めない')
    # ── 枝分かれ ──
    ap.add_argument('--lane', default='',
                    help='枝の名前。1つの作業を2つ以上のセッションへ分けるときに付ける。'
                         'ファイル名が 案件名.枝名_handover_... になり、互いに上書きしなくなる')
    ap.add_argument('--parent', default='',
                    help='分岐元の引き継ぎファイル名。どこから分かれたかを記録する')
    ap.add_argument('--case', default='', help='案件名（既定はファイル名から推定）')
    ap.add_argument('--name', default='',
                    help='ユーザーが承認した引き継ぎファイルの名前（拡張子や _handover_latest は不要）。'
                         '例：`kobo_anken_omatsuri` → kobo_anken_omatsuri_handover_latest.md')
    ap.add_argument('--from', dest='sources', nargs='+', default=[],
                    help='--merge でまとめる枝のファイル（2本以上）')
    a = ap.parse_args()
    tpl = a.template or default_template()
    if a.auto:
        # ユーザーが承認した名前が渡されていれば、**それをそのまま使う**。
        # 直すのは使えない文字だけ。語を足さない（§7-11／2026-09-02 の事案）。
        if a.name:
            approved = normalize_name(a.name)
            if not approved:
                print(f"[中止] 名前 `{a.name}` は、ファイル名に使える文字を1つも含まない。",
                      file=sys.stderr)
                print("  半角英数の名前を**一つだけ質問して**決めること。", file=sys.stderr)
                return 1
            if approved != a.name:
                print(f"[名前の調整] `{a.name}` → `{approved}`"
                      "（使えない文字だけを直した。**語は足していない**。§7-11）")
            base = pathlib.Path(a.auto).parent
            case = normalize_name(a.case) if a.case else approved.split('.')[0]
            d = case_dir(base, case)
            d.mkdir(parents=True, exist_ok=True)
            a.auto = str(d / f"{approved}_handover_latest.md")
            a.case = case
            print(f"  → 保存先：{a.auto}")

        # 案件名に使えない文字があれば、**保存する前に**機械で直して報告する。
        raw_case = a.case or case_from(a.auto)
        norm_case = normalize_name(raw_case)
        norm_lane = normalize_name(a.lane) if a.lane else ''
        if norm_case != raw_case or (a.lane and norm_lane != a.lane):
            print(f"[案件名の調整] `{raw_case}`" + (f".{a.lane}" if a.lane else "")
                  + f" → `{norm_case}`" + (f".{norm_lane}" if norm_lane else ""))
            print("  ファイル名に使える文字は `^[A-Za-z0-9._-]+$` だけである（§7-11）。"
                  "**置き換えたのは使えない文字だけで、語は足していない。**")
            print("  この名前でよくなければ、いま言ってください。あとから変えると、"
                  "次のセッションから見えなくなります。")
        if not norm_case:
            print(f"[中止] 案件名 `{raw_case}` は、ファイル名に使える文字を1つも含まない。",
                  file=sys.stderr)
            print("  ファイル名に使えるのは半角英数・ハイフン・アンダースコア・ドットだけである"
                  "（§7-11）。日本語の案件名は、ファイル名にはできない。", file=sys.stderr)
            print("  **勝手に名前を付けない。** ユーザーに、半角英数の案件名を"
                  "**一つだけ質問して**決めること（例：`kobo_anken`）。", file=sys.stderr)
            print("  日本語の名称は、ファイルの中（表題）に書けばよい。", file=sys.stderr)
            return 1
        a.case, a.lane = norm_case, norm_lane
        out = lane_path(a.auto, a.lane)
        # ファイル名そのものに使えない文字が残るなら、**案件名から組み直す**。
        # ユーザーが言うのは案件名であって、ファイル名ではない（§2-13 相手に組み立てさせない）。
        if not SAFE.match(pathlib.Path(out).name):
            latest, _ = paths_for(norm_case, norm_lane)
            out = str(pathlib.Path(out).with_name(latest))
            print(f"  → 保存先：{pathlib.Path(out).name}")
        if a.lane and out != a.auto:
            print(f"枝 `{a.lane}` として書き出す：{out}")
            print("  （枝ごとに別のファイルにする。**同じ名前に上書きすると、"
                  "先に保存した枝の引き継ぎが消え、しかもそれは検出できない。**）")
        return auto(out, tpl, a.transcript, verbatim=not a.no_verbatim,
                    case=a.case, lane=a.lane, parent=a.parent)
    if a.new:
        return new(a.new, tpl)
    if a.check:
        return check(a.check, tpl)
    if a.seal:
        return seal(a.seal)
    if a.merge:
        return merge(a.merge, a.sources)
    if a.tidy:
        return tidy(a.tidy)
    return receipt(a.receipt)


if __name__ == '__main__':
    sys.exit(main())
