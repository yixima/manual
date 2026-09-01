#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SessionStart フック：引き継ぎファイルを自動で受領する（L1 §10-5）。

**なぜ必要か**
引き継いだ側は、これまで毎回「本当に全部引き継げているか」を確かめる作業から始めていた。
ユーザーが質問し、セッションが答え、その答えが正しいかをまた確かめる——**この往復自体がストレス**であり、
しかも**答えが正しい保証はどこにも無い**（確率的な応答だからである）。

そこで、確かめる対象を「セッションが理解しているか」から「**ファイルが完全に届いているか**」へ移す。
後者は機械で確かめられる。届いたことが確定していれば、あとは必要なときに原本を引けばよい。

**動き方**
`handover/` に置かれた引き継ぎファイル（第1章を持つ .md）のうち最も新しいものを見つけ、
セッション開始時に**受領確認をコンテキストへ流し込む**。ユーザーの操作は不要。
見つからなければ**何も出さない**（無関係なプロジェクトで騒がないため）。

置き場は環境変数 `CLAUDE_HANDOVER`（ファイルを直接指定）でも上書きできる。
"""
import json, sys, os, pathlib, subprocess

MARK = "## 1. 依頼の原文"        # 引き継ぎファイルであることの判定。README 等を誤って拾わないため


def find(cwd):
    env = os.environ.get('CLAUDE_HANDOVER')
    if env:
        p = pathlib.Path(env).expanduser()
        return p if p.exists() else None
    d = pathlib.Path(cwd) / 'handover'
    if not d.is_dir():
        return None
    cands = [f for f in d.glob('*.md')
             if f.is_file() and MARK in f.read_text(encoding='utf-8', errors='replace')]
    if not cands:
        return None
    return max(cands, key=lambda f: f.stat().st_mtime)


def already_done(cwd, sid):
    """同じセッションで二度流し込まない（開始のたびに再注入されると邪魔になるため）。"""
    d = pathlib.Path(os.environ.get('CLAUDE_MANUAL_METRICS',
                                    pathlib.Path(cwd) / 'metrics'))
    return d / f'.handover-{sid}'


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    cwd = data.get('cwd') or os.getcwd()
    sid = data.get('session_id') or 'unknown'
    try:
        f = find(cwd)
    except Exception:
        return 0                       # 読めなくてもセッションの開始を妨げない
    if not f:
        return 0
    mark = already_done(cwd, sid)
    try:
        if mark.exists():
            return 0
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(str(f), encoding='utf-8')
    except Exception:
        pass

    tool = pathlib.Path(cwd) / 'tools' / 'make_handover.py'
    out = ''
    if tool.exists():
        try:
            r = subprocess.run([sys.executable, str(tool), '--receipt', str(f)],
                               capture_output=True, text=True, timeout=30, cwd=cwd)
            out = (r.stdout or '') + (r.stderr or '')
        except Exception:
            out = ''
    print("[引き継ぎの自動受領・§10-5] このセッションは引き継ぎファイルを受け取っています。\n"
          f"対象：`{f}`\n")
    if out.strip():
        print(out.strip())
    else:
        print("受領確認スクリプトを実行できなかった。ファイルを直接読んで、10章の欠落を自分で確認すること。")
    print("\n→ **作業に入る前に、このファイルを全章読むこと。**"
          "第1章（依頼の原文）と付録B（応答の原文）は要約ではなく原文である。要約で代用しない。\n"
          "→ 受領が完全であれば、**ユーザーに「理解できているか」を確かめる質問はしない。**"
          "上の照合がその確認である（§2-5 自分で調べれば分かることは質問しない）。\n"
          "→ 受領が不完全であれば、**作業に入る前に、不足している箇所を名指しで申告する**（§1-7）。\n"
          "→ そのうえで「8. 次に最初に行うこと」の1行目から始める。**前置き・要約・再説明は書かない**（§2-20）。")
    return 0


if __name__ == '__main__':
    sys.exit(main())
