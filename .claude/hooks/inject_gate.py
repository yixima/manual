#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UserPromptSubmit フック：毎ターン、次の2つをコンテキストへ注入する。

  ① 関門9項と出力契約（L1 §0-10②／§0-15）
     ——「長い会話で薄れる」を、記憶や気合ではなく機械的な再注入で潰す。
  ② 現在日時（L1 §3-7）
     ——セッションは自分がいつ動いているかを正確に知らないことがある。
       「今日」「現在」「最新」に依存する判断を、推測で行わせないために毎ターン与える。
  ③ セッション劣化の予兆警告（L1 §0-5）
     ——往復数・記録容量・生成物の大きさを実測し、しきい値を超えたら
       「ユーザーが不調を訴える前に」引き継ぎを提案するよう促す。

stdout はそのままコンテキストに入りトークンを消費するため、意図的に短く保つ。
長さの上限は L1 §0-14（条項の定員制）に従う。
"""
import json, sys, os, pathlib, datetime, zoneinfo

GATE = """[汎用マニュアル v17 / 関門（毎ターン自動注入・環境=Code）]
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
日時：「今日」「現在」「最新」に依存する記述は、上の現在日時を基準にし、必要なら基準日を本文に明記する。
迷ったら止める・弱める・質問する。「たぶん大丈夫」で送らない。"""

# しきい値（L1 §0-5）
MAX_TURNS, MAX_BYTES, MAX_FILE = 60, 2_000_000, 1_000_000

def degradation(data):
    warn = []
    tp = data.get('transcript_path') or ''
    try:
        p = pathlib.Path(tp)
        if p.exists():
            size = p.stat().st_size
            turns = sum(1 for _ in p.open(encoding='utf-8', errors='replace'))
            if size > MAX_BYTES:
                warn.append(f"会話の記録が {size/1_000_000:.1f}MB（しきい値 2MB）")
            if turns > MAX_TURNS:
                warn.append(f"往復が約 {turns} 回（しきい値 60）")
    except Exception:
        pass
    try:
        cwd = pathlib.Path(data.get('cwd') or os.getcwd())
        for d in ('dist', 'out', 'deliverables'):
            for f in (cwd / d).glob('*'):
                if f.is_file() and f.stat().st_size > MAX_FILE:
                    warn.append(f"{d}/{f.name} が {f.stat().st_size/1_000_000:.1f}MB（1MB 超はダウンロードが失敗しやすい）")
    except Exception:
        pass
    if not warn:
        return ""
    return ("\n[劣化の予兆・§0-5 自動検出] " + " ／ ".join(warn) +
            "\n→ ユーザーが不調を訴える前に、自分から申告し、引き継ぎファイル（§10-5 の10章）の作成を提案すること。"
            "\n→ 放置すると、応答が遅くなる・生成したファイルがダウンロードできなくなる・不正確な応答が混じる。")

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

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    print(now_line() + "\n\n" + GATE + degradation(data))

if __name__ == '__main__':
    main()
