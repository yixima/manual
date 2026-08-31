#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UserPromptSubmit（非同期）：進行中のセッション中に、配布元の更新を裏で取りに行く。

**なぜ非同期なのか**：取得には通信が伴い、毎ターン同期で行うと応答が遅くなる。
`async: true` のフックは応答を待たせないため、体感の遅延がゼロになる。
取得した結果は次のターンで inject_gate.py が拾い、**その場でコンテキストへ流し込む**。

**なぜ必要なのか**：`~/.claude/CLAUDE.md` はセッション開始時にしか読み込まれない。
進行中のセッションに新しい版を届けるには、**毎ターンの注入経路に流し込むしかない**。

失敗しても黙って終わる（作業を止めない。§2-9）。
"""
import json, sys, os, pathlib, urllib.request, time

BASE = 'https://raw.githubusercontent.com/yixima/manual/main/latest'
CHECK_INTERVAL = 900          # 秒。これより短い間隔では取りに行かない（無駄な通信を避ける）

def cache_dir():
    d = pathlib.Path(os.environ.get('CLAUDE_MANUAL_CACHE',
                                    pathlib.Path.home() / '.claude' / 'manual-cache'))
    d.mkdir(parents=True, exist_ok=True)
    return d

def cfg(cwd):
    out = {"auto_sync": True, "rewake_on_update": False,
           "base_url": BASE, "check_interval": CHECK_INTERVAL}
    for d in (pathlib.Path(cwd) / '.claude', pathlib.Path.home() / '.claude'):
        try:
            out.update(json.loads((d / 'manual-hooks.json').read_text(encoding='utf-8')).get('sync', {}))
            break
        except Exception:
            continue
    return out

def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': 'manual-sync'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8')

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    c = cfg(data.get('cwd') or os.getcwd())
    if not c.get("auto_sync", True):
        sys.exit(0)

    d = cache_dir()
    stamp = d / 'last_check'
    try:
        if stamp.exists() and (time.time() - stamp.stat().st_mtime) < c["check_interval"]:
            sys.exit(0)                      # 直近に確認済み＝通信しない
    except Exception:
        pass
    try:
        stamp.write_text(str(time.time()))
    except Exception:
        pass

    try:
        meta = json.loads(fetch(f'{c["base_url"]}/latest.json'))
        ver = meta.get('version', '')
        if not ver:
            sys.exit(0)
    except Exception:
        sys.exit(0)                          # 取得できなくても止めない

    cur = ''
    try:
        cur = json.loads((d / 'latest.json').read_text(encoding='utf-8')).get('version', '')
    except Exception:
        pass
    if ver == cur:
        sys.exit(0)                          # 変化なし

    try:
        card = fetch(f'{c["base_url"]}/L0_core_card.md', timeout=25)
        if len(card) < 500:
            sys.exit(0)                      # 明らかに壊れた取得は採用しない
        (d / 'L0_core_card.md').write_text(card, encoding='utf-8')
        (d / 'latest.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        (d / 'pending').write_text(ver, encoding='utf-8')   # 次ターンで注入するための印
    except Exception:
        sys.exit(0)

    if c.get("rewake_on_update"):
        # 既定では使わない。作業中の割り込みは §2-9（承認済み作業の非中断実行）に反するため。
        print(f"[汎用マニュアル] 配布元が {ver} に更新されました。次の応答から新しい版が適用されます。",
              file=sys.stderr)
        sys.exit(2)
    sys.exit(0)

if __name__ == '__main__':
    main()
