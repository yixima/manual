#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PreToolUse フック：機械で守れる絶対要件を、モデルの判断に依存せず強制する。

対象（L1 §0-14「機械で検証できる条項はフックへ移す」）：
  A. §7-11 納品ファイル名の ASCII 安全性（^[A-Za-z0-9._-]+$）
  B. §8-5 不可逆操作の標準手順（退避なしの破壊的コマンドを止める）
  C. §0-4 反映と発行の場所の一本化（発行は指定された1つのセッションでのみ行う）

判定は保守的に行う。誤って作業を止めることは、それ自体がマニュアル違反
（§2-9 承認済み作業の非中断実行）であるため、対象を明確なものに限定する。
"""
import json, sys, re, os, pathlib

# A. ASCII 安全名を要求するディレクトリ（納品・共有物の置き場）
DELIVERY_DIRS = ('dist/', 'out/', 'deliverables/', 'share/')
SAFE_NAME = re.compile(r'^[A-Za-z0-9._-]+$')

# B. 退避なしでは通さない破壊的コマンド
DANGEROUS = [
    (re.compile(r'\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b'), 'rm -rf'),
    (re.compile(r'\bgit\s+push\b.*(--force(?!-with-lease)|(?<!-)\s-f\b)'), 'git push --force'),
    (re.compile(r'\bgit\s+reset\s+--hard\b'), 'git reset --hard'),
    (re.compile(r'\bshred\b|\bmkfs\b|>\s*/dev/sd'), '不可逆な破壊操作'),
]

# C. §0-4 反映と発行の場所の一本化
#    マニュアルの改訂・発行は、**ユーザーが指定した1つのセッションでのみ**行う。
#    作業セッションが行うのは**提案まで**である。手元で条文を直しても他のセッションには一切伝わらず、
#    **版が枝分かれするだけ**になる（§0-7）。発行は配布URLを差し替える不可逆・外向きの操作でもある。
#    「今回だけ」と判断して発行してしまう事故を、条文の強調ではなく機械で止める。
PUBLISH = [
    (re.compile(r'(^|[\s;&|])(\./)?tools/publish\.sh\b|(^|[\s;&|])\./publish\.sh\b'),
     '発行スクリプト `publish.sh` の実行'),
    (re.compile(r'\bgit\s+push\b[^\n]*\bmain\b'),
     '`main` への push（＝配布URLの差し替え）'),
]

def is_publishing_session(cwd):
    """このセッションが「発行してよいセッション」として明示されているか。

    既定は**発行しない**。明示された場合にだけ通す。印は次のどちらか。
      - `.claude/manual-session` というファイルが置いてある
      - 環境変数 `CLAUDE_MANUAL_SESSION=1`
    **黙って有効にしない。** 有効にするのはユーザーの指示があったときだけである。
    """
    if os.environ.get('CLAUDE_MANUAL_SESSION') == '1':
        return True
    try:
        return (pathlib.Path(cwd) / '.claude' / 'manual-session').exists()
    except Exception:
        return False

RE_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?\n.*?^\1\s*$", re.S | re.M)

def strip_heredocs(cmd):
    """ヒアドキュメントの中身を取り除く。

    ファイルに書き込む文字列の中に危険なコマンドの「文字列」が含まれていても、
    それは実行ではない。誤って作業を止めることは、それ自体がマニュアル違反である
    （§2-9 承認済み作業の非中断実行）。実行される位置にあるものだけを判定する。
    （背景）テストスクリプトを書き込むヒアドキュメントの中に一時ディレクトリの
    再帰削除コマンドの文字列が含まれていたため、本フックが誤って作業を拒否した
    （2026-08・L2 記録参照）。
    """
    return RE_HEREDOC.sub('<<HEREDOC_BODY_REMOVED>>', cmd)

def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}, ensure_ascii=False))
    sys.exit(0)

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}

    # A. 納品物のファイル名検証
    path = ti.get("file_path") or ti.get("path") or ""
    if tool in ("Write", "Edit", "NotebookEdit") and path:
        norm = str(path).replace(os.sep, '/')
        if any(d in norm for d in DELIVERY_DIRS):
            name = norm.rsplit('/', 1)[-1]
            if not SAFE_NAME.match(name):
                deny(f"§7-11 違反：納品ディレクトリのファイル名 `{name}` が "
                     f"^[A-Za-z0-9._-]+$ に適合しません。半角英数・ハイフン・アンダースコア・"
                     f"ドットのみの名前へ変更してください（日本語タイトルはファイル内部かキャプションへ）。")

    # B. 不可逆操作
    if tool == "Bash":
        cmd = strip_heredocs(ti.get("command", "") or "")
        for rx, label in DANGEROUS:
            if rx.search(cmd):
                deny(f"§8-5 違反：`{label}` を含む不可逆操作を検出しました。"
                     f"手順は「①退避 → ②件数・サイズの照合検証 → ③一致した範囲のみ復元可能な削除」です。"
                     f"退避と照合を先に行ってください。完全消去はユーザー自身が実行します。"
                     f"（意図的に必要な場合は、その旨をユーザーに確認してから進めてください。）")

        # C. 発行は、発行してよいと明示されたセッションでのみ
        if not is_publishing_session(data.get("cwd") or os.getcwd()):
            for rx, label in PUBLISH:
                if rx.search(cmd):
                    deny(f"§0-4 違反：{label} を検出しました。"
                         f"**マニュアルの改訂・発行は、ユーザーが指定した1つのセッションでのみ行います。**"
                         f"このセッションが行うのは**提案まで**です——手元で条文を直しても他のセッションには"
                         f"一切伝わらず、**版が枝分かれするだけ**になります（§0-7）。"
                         f"追記提案（①どの条項に ②どの文言で ③なぜ）を書いて、"
                         f"発行を担当するセッションへ渡してください。"
                         f"このセッションが発行担当である場合に限り、ユーザーの指示のもとで "
                         f"`.claude/manual-session` を作成するか `CLAUDE_MANUAL_SESSION=1` を設定してください。"
                         f"**自分の判断で有効にしないこと。**")
    sys.exit(0)

if __name__ == '__main__':
    main()
