#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""マニュアルを、お使いの端末の Claude Code と Cowork（デスクトップ）へ一括導入する。

やること（この1コマンドで完結する）：
  1. ~/.claude/CLAUDE.md にコアカードを書き込む
     → Claude Code の**全プロジェクト**と、Cowork デスクトップに効く
  2. ~/.claude/hooks/manual/ にフックを置き、~/.claude/settings.json に登録する
     → **全プロジェクト**で、関門の毎ターン注入・出力契約の検査・危険操作の阻止が動く
  3. 残りの手作業（claude.ai と Cowork の設定欄への貼り付け）を画面に表示する

安全のため（L1 §8-5 不可逆操作の標準手順）：
  - 既存のファイルは**必ず退避（バックアップ）してから**触る
  - 既存の CLAUDE.md の中身は**消さない**。末尾に追記する
  - 既存の settings.json のフック設定は**消さない**。統合する
  - 何度実行しても二重登録にならない

使い方:
  python3 tools/install.py              # 導入する
  python3 tools/install.py --dry-run    # 何が起きるかだけ表示する（何も書き換えない）
"""
import json, sys, shutil, argparse, pathlib, datetime, re

MARK_BEGIN = "<!-- BEGIN 汎用マニュアル コアカード（自動生成・直接編集しない） -->"
MARK_END = "<!-- END 汎用マニュアル コアカード -->"

def stamp():
    return datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

def backup(path, dry):
    """退避してから触る。退避できなければ触らない。"""
    if not path.exists():
        return None
    bak = path.with_name(f"{path.name}.bak_{stamp()}")
    if not dry:
        shutil.copy2(path, bak)
        if bak.stat().st_size != path.stat().st_size:
            print(f"[中止] 退避の照合に失敗した: {bak}", file=sys.stderr)
            sys.exit(1)
    print(f"    退避: {bak.name}")
    return bak

def install_card(home, card, dry):
    dst = home / '.claude' / 'CLAUDE.md'
    body = f"{MARK_BEGIN}\n\n{card.read_text(encoding='utf-8').rstrip()}\n\n{MARK_END}\n"
    old = dst.read_text(encoding='utf-8') if dst.exists() else ""
    if MARK_BEGIN in old:
        new = re.sub(re.escape(MARK_BEGIN) + r'.*?' + re.escape(MARK_END) + r'\n?', body, old, flags=re.S)
        action = "更新（既存のコアカード部分だけを差し替え。他の記述は残す）"
    else:
        new = (old.rstrip() + "\n\n" if old.strip() else "") + body
        action = "追記（既存の記述は消さずに末尾へ追加）" if old.strip() else "新規作成"
    print(f"  1. {dst}  … {action}")
    backup(dst, dry)
    if not dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(new, encoding='utf-8')
    return dst

def install_hooks(home, repo, dry):
    hdir = home / '.claude' / 'hooks' / 'manual'
    print(f"  2. {hdir}/  … フック6本を配置")
    if not dry:
        hdir.mkdir(parents=True, exist_ok=True)
        for f in ('inject_gate.py', 'check_output.py', 'guard_delivery.py', 'auto_update.py',
                  'manual_sync.py', 'handover_receipt.py'):
            shutil.copy2(repo / '.claude' / 'hooks' / f, hdir / f)
            (hdir / f).chmod(0o755)
    for f in ('glossary.json', 'manual-hooks.json'):
        dst = home / '.claude' / f
        if dst.exists():
            print(f"     {dst.name} は既にあるため触らない（あなたの設定を尊重する）")
        else:
            print(f"     {dst.name} を新規作成")
            if not dry:
                shutil.copy2(repo / '.claude' / f, dst)

    sp = home / '.claude' / 'settings.json'
    cur = {}
    if sp.exists():
        try:
            cur = json.loads(sp.read_text(encoding='utf-8'))
        except Exception:
            print(f"[中止] {sp} が JSON として読めない。手で確認してから再実行すること。", file=sys.stderr)
            sys.exit(1)
    hooks = cur.setdefault('hooks', {})
    wanted = {
        'SessionStart': ('*', f'python3 {hdir}/auto_update.py'),
        'SessionStart#handover': ('*', f'python3 {hdir}/handover_receipt.py'),
        'UserPromptSubmit': ('*', f'python3 {hdir}/inject_gate.py'),
        'UserPromptSubmit#sync': ('*', f'python3 {hdir}/manual_sync.py'),
        'Stop': ('*', f'python3 {hdir}/check_output.py'),
        'PreToolUse': ('Write|Edit|NotebookEdit|Bash', f'python3 {hdir}/guard_delivery.py'),
    }
    added = 0
    for ev, (matcher, cmd) in wanted.items():
        ev = ev.split('#')[0]          # 同じイベントに複数のフックを登録するための表記
        groups = hooks.setdefault(ev, [])
        already = any(h.get('command', '').endswith(cmd.split('/')[-1])
                      for g in groups for h in g.get('hooks', []))
        if already:
            continue
        entry = {'type': 'command', 'command': cmd}
        if 'manual_sync' in cmd:
            entry['async'] = True      # 通信を伴うため、応答を待たせない
        groups.append({'matcher': matcher, 'hooks': [entry]})
        added += 1
    print(f"  3. {sp}  … フック登録 {added} 件を追加（既存の設定は保持）")
    backup(sp, dry)
    if not dry:
        sp.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + "\n", encoding='utf-8')
    return sp

SANDBOX_HELP = """
[中止] {path} に書き込めませんでした。

原因は Claude Code のサンドボックス（＝コマンドが触れてよい範囲を制限する安全機構）です。
`~/.claude/` は保護対象のため、既定では書き込みが拒否されます。**設定の誤りではありません。**

対処（どちらか一つ）:
  1. サンドボックスを外して、この導入コマンドだけを実行し直す。
  2. 対話型のターミナルで `claude` を起動し、`/sandbox` から `~/.claude/` への
     書き込みを許可してから、もう一度実行する。

**退避（バックアップ）は作成済みで、元のファイルは書き換わっていません。安全に再実行できます。**
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--home', default=str(pathlib.Path.home()), help='書き込み先のホーム（検証用に変更できる）')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    repo = pathlib.Path(__file__).resolve().parent.parent
    cards = sorted((repo / 'dist').glob('L0_core_card_v*.md'))
    if not cards:
        print('[中止] dist/ にコアカードが無い', file=sys.stderr); sys.exit(1)
    card = cards[-1]
    ver = re.search(r'(v\d+)', card.name).group(1)
    home = pathlib.Path(a.home).expanduser()

    print(f"── 汎用マニュアル {ver} の導入 {'（試行・何も書き換えない）' if a.dry_run else ''} ──")
    print(f"  配布元: {card}")
    try:
        install_card(home, card, a.dry_run)
        install_hooks(home, repo, a.dry_run)
    except PermissionError as e:
        print(SANDBOX_HELP.format(path=getattr(e, 'filename', '~/.claude/')), file=sys.stderr)
        return 1

    print(f"""
── ここまでで完了したこと ──
  Claude Code：**この端末の全プロジェクト**に効く（次に開くセッションから）
  Cowork（デスクトップ）：~/.claude/CLAUDE.md を読むため、これも効く

── あなたにしかできない残りの作業 ──
  **ブートローダー方式（推奨）を使っている場合は、ここで何もすることはありません。**
  設定欄に一度 `dist/bootloader.md` を貼ってあれば、以後は貼り直し不要です。
  （中身は固定URLから取得されるため、版を上げても設定欄は触らなくて構いません。）

  まだ設定欄に何も貼っていない場合だけ、次を一度だけ行ってください。
  A. claude.ai → 左下のイニシャル → 設定 →「Instructions for Claude」
     → {repo}/dist/bootloader.md の全文を貼る（一度だけ・以後の貼り直しは不要）
  B. Cowork（デスクトップ）→ 設定 → Cowork →「Global instructions」
     → 同じものを貼る（一度だけ）

  ※ A と B は、あなたのアカウントにログインした画面での操作です。
     私（アシスタント）はあなたのアカウントにログインできないため、代行できません（L1 §8-9）。

── 注意 ──
  ・**すでに開いているセッションには、確実には反映されません。** 新しいセッションから効きます。
    進行中のセッションを最新にしたいときは、そのセッションで「マニュアル更新」と打ってください。
  ・**次回からは、この install.py を実行する必要もありません。** SessionStart フックが
    セッション開始時に自動で git pull し、コアカードが変わっていれば差し替えます。
  ・クラウドのセッション（claude.ai/code）は ~/.claude/settings.json を読みません。
    そちらで機械的な検査も効かせたい場合は、対象リポジトリに .claude/ を置いてコミットしてください。
  ・過去のセッションに遡って反映することはできません（原理的に不可能）。
""")
    if a.dry_run:
        print("（試行モードのため、実際には何も書き換えていない）")
    return 0

if __name__ == '__main__':
    sys.exit(main())
