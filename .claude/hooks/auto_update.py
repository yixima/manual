#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SessionStart フック：セッション開始時にマニュアルを自動で最新化する。

狙い：**利用者が何もしなくても、常に最新版で動く。**
  版を上げるたびに手で貼り直す作業を、Claude Code 側では完全になくす。

やること（すべて失敗しても黙って通す。作業を止めないことを最優先する。§2-9）：
  1. マニュアルのリポジトリを取得し直す（git pull）。ネットワークが無ければ何もしない。
  2. コアカードが変わっていたら、~/.claude/CLAUDE.md の該当部分だけを差し替える。
  3. **検査プログラム（フック）本体も、配布元の最新に差し替える。**
  4. 更新があったときだけ、1行だけ知らせる（無ければ何も出さない）。

なぜ3が要るか：v25〜v28 の修正は**すべてフック本体の修正**だった。
コアカードだけを自動更新しても、**検査の中身は古いまま**である。
それに気づかないまま「自動で最新になります」と説明していた（L2 記録参照）。

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

# 自動更新するフックは、この一覧に**明示したものだけ**である。
# 配布元に置かれた任意のファイルを取り込むことはしない（取り込む対象を固定する）。
HOOKS = ('inject_gate.py', 'check_output.py', 'guard_delivery.py',
         'auto_update.py', 'manual_sync.py', 'handover_receipt.py')


def update_hooks(repo):
    """導入済みのフック本体を、配布元（origin/main）の最新へ差し替える。

    安全のため（§8-5 不可逆操作の標準手順）：
      - 取り込む対象は上の HOOKS に**固定**する。配布元の任意のファイルは取らない。
      - 書き込む前に **Python として構文が通るか検査**する。
        壊れたフックを入れると、以後**毎ターン**作業が止まるため、
        ここを通らないものは**入れない**（古いままのほうが安全である）。
      - 上書きの前に `.bak` へ退避する。
      - **何が失敗しても例外を外へ出さない。** セッションを止めないことを最優先する（§2-9）。

    新しいフックが増えた場合は、settings.json への登録が要るため、ここでは入れない。
    そのときは `python3 tools/install.py` を実行するよう促す（返り値で知らせる）。
    """
    hdir = pathlib.Path.home() / '.claude' / 'hooks' / 'manual'
    if not hdir.is_dir():
        return 0, []                      # 未導入の環境では何もしない
    changed, missing = [], []
    for name in HOOKS:
        dst = hdir / name
        if not dst.exists():
            missing.append(name)          # 未登録の新しいフック。install.py に任せる
            continue
        try:
            r = subprocess.run(['git', '-C', str(repo), 'show', f'origin/main:.claude/hooks/{name}'],
                               capture_output=True, text=True, timeout=15)
            if r.returncode != 0 or not r.stdout.strip():
                continue
            new = r.stdout
            if new == dst.read_text(encoding='utf-8'):
                continue                  # 変化なし
            compile(new, str(dst), 'exec')   # 壊れていたら例外→この1本は入れない
            dst.with_suffix('.py.bak').write_text(dst.read_text(encoding='utf-8'), encoding='utf-8')
            dst.write_text(new, encoding='utf-8')
            dst.chmod(0o755)
            changed.append(name)
        except Exception:
            continue                      # 1本の失敗で、他の更新やセッションを巻き込まない
    return len(changed), missing


def main():
    try:
        json.load(sys.stdin)
    except Exception:
        pass
    repo = repo_dir()
    if not repo:
        sys.exit(0)          # マニュアルの置き場が無い環境では何もしない

    # **カードが変わっていなくてもフックは更新する。**
    # v25〜v28 のように、カードは同じでフックだけが直る改訂があるため。
    n_hooks, missing_hooks = update_hooks(repo)

    card_rel = 'latest/L0_core_card.md'
    before = None
    try:
        before = (repo / card_rel).read_text(encoding='utf-8')
    except Exception:
        pass

    # 配布元は origin/main である。ローカルがどのブランチにいても、
    # **配布元から直接読む**ことで、ブランチの状態に依存しない。
    # （作業ブランチを削除しても壊れない。§3-15 原因を取り違えないための設計）
    after = None
    try:
        subprocess.run(['git', '-C', str(repo), 'fetch', '--quiet', 'origin', 'main'],
                       capture_output=True, timeout=25)
        r = subprocess.run(['git', '-C', str(repo), 'show', f'origin/main:{card_rel}'],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            after = r.stdout
    except Exception:
        pass

    if after is None:
        # 配布元から読めなければ、作業ツリーを更新して読む（従来の経路）
        try:
            subprocess.run(['git', '-C', str(repo), 'pull', '--quiet', '--ff-only'],
                           capture_output=True, timeout=25)
            after = (repo / card_rel).read_text(encoding='utf-8')
        except Exception:
            sys.exit(0)      # 取得できなくても止めない

    if after == before:
        if n_hooks:
            print(f"[汎用マニュアル] 検査プログラムを {n_hooks} 本、最新に更新しました。手作業は不要です。")
        if missing_hooks:
            print(f"[汎用マニュアル] 新しい検査プログラムがあります（{', '.join(missing_hooks)}）。"
                  f"登録が要るため `python3 tools/install.py` を1回だけ実行してください。")
        sys.exit(0)          # カードに変化なし
    try:
        (repo / card_rel).write_text(after, encoding='utf-8')
    except Exception:
        pass

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

    tail = f"（検査プログラムも {n_hooks} 本更新）" if n_hooks else ""
    print(f"[汎用マニュアル] 自動更新しました（{ver}）{tail}。"
          f"本セッションから最新版が適用されます。手作業は不要です。")
    if missing_hooks:
        print(f"[汎用マニュアル] 新しい検査プログラムがあります（{', '.join(missing_hooks)}）。"
              f"登録が要るため `python3 tools/install.py` を1回だけ実行してください。")

if __name__ == '__main__':
    main()
