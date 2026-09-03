#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UserPromptSubmit フック：毎ターン、次の2つをコンテキストへ注入する。

  ① 関門9項と出力契約（L1 §0-10②／§0-15）
     ——「長い会話で薄れる」を、記憶や気合ではなく機械的な再注入で潰す。
  ② 現在日時（L1 §3-7）
     ——セッションは自分がいつ動いているかを正確に知らないことがある。
       「今日」「現在」「最新」に依存する判断を、推測で行わせないために毎ターン与える。
  ③ 配布元が更新されていたら、**その場で新しいコアカードを流し込む**（L1 §0-4）
     ——`~/.claude/CLAUDE.md` はセッション開始時にしか読まれないため、
       進行中のセッションに新版を届ける経路はここしかない。
       取得そのものは manual_sync.py（非同期）が裏で済ませてあり、ここでは読むだけ。
  ④ セッション劣化の予兆警告（L1 §0-5）
     ——往復数・記録容量・生成物の大きさを実測し、しきい値を超えたら
       「ユーザーが不調を訴える前に」引き継ぎを提案するよう促す。

stdout はそのままコンテキストに入りトークンを消費するため、意図的に短く保つ。
長さの上限は L1 §0-14（条項の定員制）に従う。
"""
import json, sys, os, pathlib, datetime, zoneinfo

GATE = """[汎用マニュアル / 関門（毎ターン自動注入・環境=Code）]
送信前に9項。1つでも「未」なら送らない。埋めてから送る。
1 確かめれば分かることを確かめずに書いていないか（自問：あとで「本当に確認したのか」と問われて証拠を出せるか）
2 事実主張に確信度ラベル【確認済】【未確認・推測】【不明】を付けたか
3 できる/できない・制度・数値・期限・海外・固有名詞 → 検問を通し、必要なら裏取りを付けたか
4 相手の画面から「いまの状態」と「次にすべきこと」が分かるか
5 提示物の扱い（実行する・貼付先／読むだけ／参考）を書いたか
6 「これから〜します」で終わっていないか。指示された作業をやり切ったか。やり切っていないなら中断の理由を書いたか
7 ユーザーの直近指示より、自分の判断による作業を優先していないか
8 同じ失敗を方式を変えずに繰り返していないか（2回続いたら続行より先に申告）
9 自作した条件・仕様が互いに矛盾していないか／新しい指示・失敗は追記提案と記録をしたか
出力契約：該当したら必ず書く＝ラベル／末尾1行「— 状態：… 次：…」／未完了なら完了・未完了・次と中断理由／提示物の扱い／要裏取り1行／専門用語は初出に1行の意味。
やり切る：中断してよいのは①質問が必要②承認待ち③エラーで進めない④危険で確認が要る、の4つだけ。「区切りがよい」は理由にならない。
**ターンを終える＝ユーザーの画面では「停止」である。** 離れている間も進めたいなら、長い処理は `run_in_background` で走らせる（終了時に自分が呼び戻され、結果確認まで自分で行う）。
**「状態：実行中」と書いてよいのは、この瞬間も実際に何かが走っているときだけ。** 走っていないなら「入力待ち」か「完了」と書く——**待たせるだけの「実行中」は嘘である。**
日時：「今日」「現在」「最新」に依存する記述は、上の現在日時を基準にし、必要なら基準日を本文に明記する。
迷ったら止める・弱める・質問する。「たぶん大丈夫」で送らない。"""

# ── 負荷スコア（L1 §0-5）──────────────────────────────────
# **往復数は代理指標にすぎない。** 実際に効くのは「セッションが抱えた総データ量」であり、
# 中でも**バイナリ成果物（スライド・表計算・PDF・画像）は、テキストよりはるかに重い**。
# 理由＝圧縮された中身が展開されて読み込まれ、プレビュー生成や再読込で何度も文脈に載るため。
#
# 実測の基準点：
#   2026-09-02：**実往復56回**／会話の記録5.1MB／生成物0.7MB（すべてテキスト）
#     → スコア5.8。ユーザー報告「スライドやパワポなど容量のあるファイルを制作していないので
#       比較的まだ快調」。**注意水準8はこれより上にあり、正しく黙っていた。**
#   2026-08-28 の基準点（往復864回）は**記録ファイルの行数を往復数と取り違えていた**ため破棄した。
#     行数は、道具の呼び出し・その結果・思考の1つ1つが1行になる。上の実測では
#     **2153行に対して実際の往復は56回**——約38倍にずれる。
#     行数で「往復1200回」を超えたと判定していたが、**実際の往復は56回だった**（L2 記録参照）。
#
# 負荷スコア（MB相当）＝ 会話の記録(MB) + テキスト成果物(MB) + バイナリ成果物(MB)×重み
BINARY_EXT = {'.pptx', '.potx', '.xlsx', '.xlsm', '.docx', '.pdf', '.png', '.jpg', '.jpeg',
              '.gif', '.webp', '.mp4', '.mov', '.zip', '.key', '.numbers', '.pages'}

DEFAULTS = {
    # 負荷スコアのしきい値（主指標）
    "notice_score": 8.0,      # 注意水準：頭の片隅に置くだけ。申告も中断も不要
    "report_score": 20.0,     # 申告水準：申告する。ただし作業は止めない
    # バイナリ成果物の重み（テキストの何倍として数えるか）。実測に合わせて調整する
    "binary_weight": 3.0,
    # 往復数（**補助指標**。単独では申告水準に達しない。§0-5）
    # 実往復の数で数える（記録ファイルの行数ではない）。実測 56 回でスコア5.8だったため、
    # 注意水準は 200 回、申告への寄与はしない（下の判定で report には積まない）。
    "notice_turns": 200,
    # 単一ファイルの上限（ダウンロード失敗の防止）
    "max_single_file": 5_000_000,
    # 成果物を探す場所
    "output_dirs": ["dist", "out", "deliverables", "outputs", "artifacts", "slides", "docs"],
}

def thresholds(cwd):
    """.claude/manual-hooks.json の degradation セクションで上書きできる。"""
    t = dict(DEFAULTS)
    for d in (pathlib.Path(cwd) / '.claude', pathlib.Path.home() / '.claude'):
        try:
            cfg = json.loads((d / 'manual-hooks.json').read_text(encoding='utf-8'))
            t.update(cfg.get('degradation', {}))
            break
        except Exception:
            continue
    return t

def artifact_load(cwd, T):
    """成果物の量を測る。バイナリは重みを掛ける。戻り値＝(スコア寄与MB, 内訳, 大きすぎるファイル)"""
    text_mb = bin_mb = 0.0
    n_bin = 0
    oversize = []
    for d in T["output_dirs"]:
        base = pathlib.Path(cwd) / d
        if not base.is_dir():
            continue
        for f in base.rglob('*'):
            try:
                if not f.is_file():
                    continue
                size = f.stat().st_size
                if size > T["max_single_file"]:
                    oversize.append((f"{d}/{f.name}", size))
                if f.suffix.lower() in BINARY_EXT:
                    bin_mb += size / 1_000_000
                    n_bin += 1
                else:
                    text_mb += size / 1_000_000
            except Exception:
                continue
    return text_mb + bin_mb * T["binary_weight"], (text_mb, bin_mb, n_bin), oversize

def count_turns(path):
    """**実際の往復数**を数える。記録ファイルの行数ではない。

    行数を往復数として使っていたのは誤りである（2026-09-02 に実測で発覚）。
    記録には、道具の呼び出し・その結果・思考の1つ1つが**別々の行**として入る。
    実測では **2153行に対して実際の往復は56回**——約38倍にずれていた。
    その結果、まだ快調なセッションが「往復1200回超」として申告水準に達していた。

    ここで数えるのは「ユーザーが実際に発言した回数」である。
    道具の結果（tool_result）や下請けエージェントの発言は**往復ではない**。
    """
    n = 0
    try:
        for ln in path.open(encoding='utf-8', errors='replace'):
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if d.get('type') != 'user' or d.get('isSidechain'):
                continue
            c = d.get('message', {}).get('content')
            if isinstance(c, str):
                n += 1
            elif isinstance(c, list) and any(
                    isinstance(x, dict) and x.get('type') == 'text' for x in c):
                n += 1                    # 道具の結果だけの行は往復に数えない
    except Exception:
        pass
    return n


def degradation(data):
    """負荷スコアで判定する。往復数は補助指標であり、単独では申告水準に達しない。"""
    cwd = pathlib.Path(data.get('cwd') or os.getcwd())
    T = thresholds(cwd)

    talk_mb, turns = 0.0, 0
    try:
        p = pathlib.Path(data.get('transcript_path') or '')
        if p.exists():
            talk_mb = p.stat().st_size / 1_000_000
            turns = count_turns(p)
    except Exception:
        pass

    art_score, (text_mb, bin_mb, n_bin), oversize = artifact_load(cwd, T)
    score = talk_mb + art_score

    detail = f"負荷スコア {score:.1f}（会話 {talk_mb:.1f}MB"
    if text_mb:
        detail += f" ＋ テキスト成果物 {text_mb:.1f}MB"
    if bin_mb:
        detail += f" ＋ バイナリ成果物 {bin_mb:.1f}MB×{T['binary_weight']:g}＝{bin_mb * T['binary_weight']:.1f}（{n_bin}件）"
    detail += f"）／往復 約{turns} 回"

    report, notice = [], []
    if score >= T["report_score"]:
        report.append(f"{detail}　※申告水準 {T['report_score']:g}")
    elif score >= T["notice_score"]:
        notice.append(f"{detail}　※注意水準 {T['notice_score']:g}")
    # **往復数は補助指標である。単独では申告水準に達しない**（§0-5）。
    # ここで report に積まないのが要点——積むと、コアカードの
    # 「往復数は単独では判断しない」という規定と、実装が矛盾する（§3-14）。
    # 実際にその矛盾が起き、快調なセッションに申告を出し続けていた（L2 記録参照）。
    if turns >= T["notice_turns"] and not notice and not report:
        notice.append(f"往復 {turns} 回（補助指標。単独では申告水準に達しない）")
    for name, size in oversize[:3]:
        report.append(f"{name} が {size/1_000_000:.0f}MB"
                      f"（{T['max_single_file']/1_000_000:.0f}MB 超はダウンロードが失敗しやすい）")

    if report:
        return ("\n[劣化・§0-5 申告水準] " + " ／ ".join(report) +
                "\n→ ユーザーが不調を訴える前に、自分から申告し、引き継ぎファイル（§10-5 の10章）の作成を提案する。"
                "\n→ **作成は1コマンドで終わる。** 会話の原文・実行したコマンド・触ったファイル・失敗・未完了は、"
                "セッションの記録から要約せずに自動で入る："
                "\n     `python3 tools/make_handover.py --auto handover/<名前>_handover_<YYYYMMDD>_v1.md`"
                "\n   残るのは `【要記入】`＝**理由**だけ（理由は記録に残らないため機械には書けない）。"
                "埋めたら `--check` を通す。次のセッションは `handover/` を自動で受領する。"
                "\n→ **ただし、これは作業を止める理由にはならない（§2-9）。依頼された作業は続けたまま、申告だけを添える。**")
    if notice:
        return ("\n[劣化・§0-5 注意水準] " + " ／ ".join(notice) +
                "\n→ 頭の片隅に置くだけでよい。**申告も中断も不要。** 申告水準に達したら改めて通知される。")
    return ""

def now_line():
    """現在日時を毎ターン与える（L1 §3-7）。実行環境の時計を実測する。推測しない。"""
    try:
        utc = datetime.datetime.now(datetime.timezone.utc)
        try:
            jst = utc.astimezone(zoneinfo.ZoneInfo('Asia/Tokyo'))
            return (f"[現在日時・毎ターン自動注入] {jst:%Y-%m-%d %H:%M} JST"
                    f"（UTC {utc:%Y-%m-%d %H:%M}）／曜日：{'月火水木金土日'[jst.weekday()]}\n"
                    f"→ 「今日」「現在」「最新」「締切まで」等の日時に依存する判断は、記憶ではなくこの値を基準にする。"
                    f"実行環境の時計とユーザーの現地時刻が異なる場合があるため、重要な期限の判断ではユーザーに確認する。")
        except Exception:
            return (f"[現在日時・毎ターン自動注入] UTC {utc:%Y-%m-%d %H:%M}（タイムゾーン変換に失敗したため UTC で表示）\n"
                    f"→ 日時に依存する判断は、記憶ではなくこの値を基準にする。")
    except Exception:
        return "[現在日時] 取得に失敗した。日時に依存する断定をせず、必要ならユーザーに確認すること。"

def pending_update():
    """manual_sync.py が取得しておいた新版を、1回だけ流し込む。
    進行中のセッションへ更新を届ける唯一の経路である
    （~/.claude/CLAUDE.md はセッション開始時にしか読まれないため）。"""
    try:
        d = pathlib.Path(os.environ.get('CLAUDE_MANUAL_CACHE',
                                        pathlib.Path.home() / '.claude' / 'manual-cache'))
        mark = d / 'pending'
        if not mark.exists():
            return ""
        ver = mark.read_text(encoding='utf-8').strip()
        card = (d / 'L0_core_card.md').read_text(encoding='utf-8')
        mark.unlink()                      # 流し込むのは1回だけ
        return ("\n\n[汎用マニュアル・自動更新] 配布元が " + ver + " に更新されました。"
                "**以下が最新のコアカードです。本セッションでは、これを最上位の運用ルールとして"
                "ただちに適用してください**（以前に適用していた版よりこちらが優先します）。"
                "更新されたことを実況しない——適用の痕跡は出力契約を満たすことで示す。\n\n"
                + card)
    except Exception:
        return ""

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    print(now_line() + "\n\n" + GATE + degradation(data) + pending_update())

if __name__ == '__main__':
    main()
