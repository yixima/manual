#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown を、右側ウィンドウで確実に描画される HTML に変換する。

なぜ必要か（実測 2026-09-09）：SendUserFile で .md を「表示される形」として
送っても、ユーザーの画面の右側ウィンドウでは描画されなかった。表示用は
HTML・PDF・画像で出す（§7-12）。本スクリプトはその HTML 版を作る標準手段。

使い方: python3 tools/md2html.py 入力.md 出力.html [文書タイトル]
"""
import sys, html, pathlib

try:
    import markdown
except ImportError:
    print("[FAIL] python3 -m pip install markdown を先に実行すること", file=sys.stderr)
    sys.exit(1)

CSS = """
body { font-family: -apple-system, "Hiragino Sans", "Noto Sans JP", sans-serif;
       margin: 0; background: #f7f6f3; color: #222; line-height: 1.7; }
main { max-width: 60em; margin: 0 auto; padding: 1.5em 1.2em 4em; }
h1 { font-size: 1.35em; border-bottom: 3px solid #2a6f97; padding-bottom: .3em; }
h2 { font-size: 1.15em; border-left: 6px solid #2a6f97; padding-left: .5em; margin-top: 2em; }
h3 { font-size: 1.0em; margin-top: 1.5em; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; background: #fff;
        font-size: .92em; }
th, td { border: 1px solid #cdd5dd; padding: .45em .6em; text-align: left;
         vertical-align: top; }
th { background: #e8eff5; }
tr:nth-child(even) td { background: #fafbfc; }
blockquote { border-left: 4px solid #c9c3b8; margin: 1em 0; padding: .1em 1em;
             color: #555; background: #f0ede6; }
code { background: #eee9df; padding: .1em .3em; border-radius: 3px; font-size: .9em; }
pre code { display: block; padding: .8em; overflow-x: auto; }
.warn td, .warn { background: #fdeaea !important; }
strong { color: #143a52; }
"""

def convert(src: str, title: str) -> str:
    body = markdown.markdown(src, extensions=["tables", "fenced_code", "nl2br"])
    return (f"<!DOCTYPE html><html lang=\"ja\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
            f"<body><main>{body}</main></body></html>")

def main():
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr); sys.exit(1)
    inp, outp = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    title = sys.argv[3] if len(sys.argv) > 3 else inp.stem
    src = inp.read_text(encoding="utf-8")
    outp.write_text(convert(src, title), encoding="utf-8")
    # 検証（§7-7 の2方向）：①変換で落ちた見出し・表が無いか、件数で照合する
    n_h_md = sum(1 for l in src.splitlines() if l.startswith("#"))
    n_h_html = outp.read_text(encoding="utf-8").count("<h")
    n_tr = outp.read_text(encoding="utf-8").count("<tr>")
    n_rows_md = sum(1 for l in src.splitlines()
                    if l.lstrip().startswith("|") and "---" not in l)
    print(f"{outp} を生成した（見出し md:{n_h_md} → html:{n_h_html}／"
          f"表の行 md:{n_rows_md} → html:{n_tr}）")
    if n_h_html < n_h_md or n_tr < n_rows_md:
        print("[WARN] 変換で欠落の疑い。目視で確認すること", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
