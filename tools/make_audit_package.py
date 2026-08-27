#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ChatGPT へ渡す監査パッケージを組み立てる。

用途1（既定）：Claude Code のトランスクリプト（JSONL）から、アシスタントの応答だけを抽出し、
              匿名化して盲検採点用のサンプル集を作る。
用途2：手元でコピーした応答をテキストファイルにまとめておき、区切り線で分割する。

匿名化：メールアドレス・ホームディレクトリの絶対パス・API キー様の文字列を伏せる。
       **URL は伏せない**（出典の有無が採点項目 L2 のため）。
使い方:
  python3 tools/make_audit_package.py --transcript <path.jsonl> [-n 20] -o audit_samples.md
  python3 tools/make_audit_package.py --text <path.txt> -o audit_samples.md
"""
import json, re, sys, argparse, pathlib

RE_MAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')
RE_HOME = re.compile(r'/(?:home|Users)/[^/\s"\']+')
RE_KEY = re.compile(r'\b(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{12,})\b')

def anon(s):
    s = RE_MAIL.sub('<メールアドレス>', s)
    s = RE_HOME.sub('/home/<ユーザー>', s)
    s = RE_KEY.sub('<資格情報>', s)
    return s

def from_transcript(path):
    out = []
    for ln in pathlib.Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            r = json.loads(ln)
        except Exception:
            continue
        msg = r.get('message') or {}
        if r.get('type') == 'assistant' or msg.get('role') == 'assistant':
            c = msg.get('content')
            if isinstance(c, list):
                txt = "".join(b.get('text', '') for b in c if isinstance(b, dict) and b.get('type') == 'text')
            else:
                txt = c if isinstance(c, str) else ''
            if txt and txt.strip():
                out.append(txt.strip())
    return out

def from_text(path):
    raw = pathlib.Path(path).read_text(encoding='utf-8', errors='replace')
    parts = [p.strip() for p in re.split(r'^-{3,}\s*$', raw, flags=re.M)]
    return [p for p in parts if p]

def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--transcript'); g.add_argument('--text')
    ap.add_argument('-n', type=int, default=20, help='末尾から取るサンプル数')
    ap.add_argument('-o', default='audit_samples.md')
    a = ap.parse_args()

    samples = from_transcript(a.transcript) if a.transcript else from_text(a.text)
    if not samples:
        print('応答を1件も抽出できなかった。ファイル形式を確認すること。', file=sys.stderr); return 1
    samples = samples[-a.n:]

    buf = ["# 採点対象サンプル",
           "",
           "> 匿名化済み（メールアドレス・絶対パス・資格情報を伏せた）。URL は採点項目 L2 のため残してある。",
           f"> 件数：{len(samples)}",
           "> **このファイルと rubric.md だけを ChatGPT に渡すこと。マニュアル本文は渡さない（盲検）。**",
           ""]
    for i, s in enumerate(samples, 1):
        buf += [f"## S-{i:03d}", "", anon(s), "", "---", ""]
    pathlib.Path(a.o).write_text("\n".join(buf), encoding='utf-8')
    print(f'{a.o} に {len(samples)} 件を書き出した。')
    print('次：chatgpt/prompt_02_blind_grader.md と chatgpt/rubric.md と本ファイルを ChatGPT へ貼る。')
    return 0

if __name__ == '__main__':
    sys.exit(main())
