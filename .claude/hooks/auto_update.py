#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SessionStart フック：セッション開始時にマニュアルを自動で最新化する。

狙い：**利用者が何もしなくても、常に最新版で動く。**
  版を上げるたびに手で貼り直す作業を、Claude Code 側では完全になくす。

やること（すべて失敗しても黙って通す。作業を止めないことを最優先する。§2-9）：
  1. マニュアルのリポジトリを取得し直す（git pull）。ネットワークが無ければ何もしない。
  2. コアカードが変わっていたら、~/.claude/CLAUDE.md の該当部分だけを差し替える。
  3. 更新があったときだけ、1行だけ知らせる（無ければ何も出さない）。

置き場所の探索順：環境変数 CLAUDE_MANUAL_REPO → ~/manual → ~/.claude/manual
"""
import json, sys, os, subprocess, pathlib, re

MARK_BEGIN = "<!-- BEGIN 汎用マニュアル コアカード（自動生成・直接編集しない） -->"
MARK_END = "<!-- END 汎用マニュアル コアカード -->"

def repo_dir():
    env = os.environ.get('CLAUDE_MANUAL_REPO')
    cands = ([pathlib.Path(env).expanduser()] if env else []) + [
        pathlib.Path.home() / 'manual', pathlib.Path.home() / '.claude' / 'manual']
    for c in cands:
        if (c / '.git').is_dir() and (c / 'latest').is_dir():
            return c
    return None

def main():
    try:
        json.load(sys.stdin)
    except Exception:
        pass
    repo = repo_dir()
    if not repo:
        sys.exit(0)          # マニュアルの置き場が無い環境では何もしない

    before = None
    card = repo / 'latest' / 'L0_core_card.md'
    try:
        before = card.read_text(encoding='utf-8')
    except Exception:
        pass

    try:
        subprocess.run(['git', '-C', str(repo), 'pull', '--quiet', '--ff-only'],
                       capture_output=True, timeout=25)
    except Exception:
        sys.exit(0)          # 取得できなくても止めない

    try:
        after = card.read_text(encoding='utf-8')
    except Exception:
        sys.exit(0)
    if after == before:
        sys.exit(0)          # 変化なし＝何も言わない

    ver = ''
    try:
        ver = json.loads((repo / 'latest' / 'latest.json').read_text(encoding='utf-8')).get('version', '')
    except Exception:
        pass

    # ~/.claude/CLAUDE.md のコアカード部分だけを差し替える（他の記述は触らない）
    dst = pathlib.Path.home() / '.claude' / 'CLAUDE.md'
    try:
        body = f"{MARK_BEGIN}\n\n{after.rstrip()}\n\n{MARK_END}\n"
        old = dst.read_text(encoding='utf-8') if dst.exists() else ""
        if MARK_BEGIN in old:
            new = re.sub(re.escape(MARK_BEGIN) + r'.*?' + re.escape(MARK_END) + r'\n?', body, old, flags=re.S)
        else:
            new = (old.rstrip() + "\n\n" if old.strip() else "") + body
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(new, encoding='utf-8')
    except Exception:
        sys.exit(0)

    print(f"[汎用マニュアル] 自動更新しました（{ver}）。"
          f"本セッションから最新版が適用されます。手作業は不要です。")

if __name__ == '__main__':
    main()
