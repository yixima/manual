#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""セッションの記録（トランスクリプト）から、引き継ぎの材料を機械的に取り出す。

**なぜ必要か（L1 §10-5）**
引き継ぎファイルの10章のうち、①依頼の原文 ④発行したファイル ⑤調整の経緯
⑥失敗 ⑦未完了 ⑩使用したコマンド は、**すべてセッションの記録に事実として残っている**。
にもかかわらず、従来はこれを人（またはセッション）が記憶を頼りに書き写していた。
書き写しは、①手間がかかる ②劣化したセッションほど精度が落ちる ③要約によって原文が失われる——
という3つの欠陥を持つ。**記録があるなら、記憶ではなく記録から作る**（§3-4 検証ファースト）。

**この土台（`[Code]` 限定）**
Claude Code は会話の全履歴を JSONL（1行1レコードの記録形式）で保存している。
本モジュールはそれを読み、要約せずに取り出す。`[Chat]` `[Cowork]` にはこの記録が無いため、
本モジュールは使えない（その環境での扱いは L1 §10-5 の「常時更新」に従う）。

**取り出さないもの**：思考（thinking）ブロックは、ユーザーに示していない内部の推論であり、
引き継ぎの対象ではない。ツール実行の出力も、量が大きく再実行で得られるため取らない
（代わりに**実行したコマンドそのもの**を取る。§10-5 コマンドの記録）。
"""
import json, os, pathlib, re, sys

# ── 記録の場所 ──────────────────────────────────────────────
def project_slug(cwd):
    """Claude Code が記録を置くディレクトリ名。作業ディレクトリのパスから作られる。"""
    return re.sub(r'[^A-Za-z0-9]', '-', str(pathlib.Path(cwd).resolve()))

def find_transcript(cwd=None, explicit=None):
    """記録ファイルを探す。優先順＝明示指定 → 環境変数 → 作業ディレクトリに対応する置き場の最新。"""
    if explicit:
        p = pathlib.Path(explicit).expanduser()
        return p if p.exists() else None
    env = os.environ.get('CLAUDE_TRANSCRIPT')
    if env and pathlib.Path(env).exists():
        return pathlib.Path(env)
    base = pathlib.Path(os.environ.get('CLAUDE_PROJECTS_DIR',
                                       pathlib.Path.home() / '.claude' / 'projects'))
    d = base / project_slug(cwd or os.getcwd())
    if not d.is_dir():
        return None
    cands = sorted(d.glob('*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)
    return cands[0] if cands else None

# ── 本文の掃除 ──────────────────────────────────────────────
# 自動で差し込まれる注記は、ユーザーが書いた言葉ではない。原文に混ぜると引き継ぎが汚れる。
NOISE = [
    re.compile(r'<system-reminder>.*?</system-reminder>', re.S),
    re.compile(r'<local-command-stdout>.*?</local-command-stdout>', re.S),
    re.compile(r'<command-message>.*?</command-message>', re.S),
    re.compile(r'<command-args>.*?</command-args>', re.S),
]

def clean(text):
    for r in NOISE:
        text = r.sub('', text)
    return text.strip()

def blocks(msg):
    """message.content を、常にブロックの一覧として返す（文字列のこともあるため）。"""
    c = msg.get('content')
    if isinstance(c, str):
        return [{'type': 'text', 'text': c}]
    return c if isinstance(c, list) else []

def text_of(msg):
    return clean("\n".join(b.get('text', '') for b in blocks(msg) if b.get('type') == 'text'))

# ── 取り出し ────────────────────────────────────────────────
# ユーザーが「変えてほしい」と述べた合図。§10-5 の第5章（調整の経緯）の材料になる。
RE_CORRECTION = re.compile(
    r'(違う|ちがう|ではなく|じゃなく|やめて|中止|訂正|修正して|直して|変更して|変えて|'
    r'やり直|戻して|不要|要らない|いらない|間違|誤り|そうじゃ|ではない|おかしい|'
    r'できていない|されていない|抜けて|漏れて)')

RE_INCOMPLETE = re.compile(r'【未完了】(.+)')

# 成果物を作る・書き換えるツール。§10-5 の第4章（発行したファイル）の材料になる。
WRITE_TOOLS = {'Write', 'Edit', 'NotebookEdit'}

def parse(path):
    """記録を読み、引き継ぎに必要な事実だけを取り出して返す。

    戻り値の各項目は**要約していない**。原文・原コマンドをそのまま保持する。
    """
    out = {
        'session': None, 'cwd': None, 'branch': None,
        'started': None, 'ended': None,
        'user_messages': [],    # ユーザー発言（原文・時刻つき）
        'assistant_texts': [],  # こちらの応答本文（原文・時刻つき。思考は含まない）
        'corrections': [],      # そのうち、訂正・調整の合図を含むもの
        'commands': [],         # 実際に実行したコマンド
        'files': [],            # 作成・編集したファイル
        'attachments': [],      # ユーザーが提示したファイル・データ
        'errors': [],           # 失敗（ツールの異常終了・フックの差し戻し）
        'incomplete': [],       # 応答に残っていた【未完了】
        'turns': 0, 'bytes': 0,
    }
    p = pathlib.Path(path)
    out['bytes'] = p.stat().st_size
    seen_cmd, seen_file = set(), set()

    for line in p.open(encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue          # 壊れた行があっても止めない（記録は事後に読むものである）
        out['turns'] += 1
        if r.get('isSidechain'):
            continue          # 下請けエージェントの内部往復は、ユーザーとのやり取りではない
        out['session'] = out['session'] or r.get('sessionId')
        out['cwd'] = out['cwd'] or r.get('cwd')
        out['branch'] = out['branch'] or r.get('gitBranch')
        ts = r.get('timestamp')
        if ts:
            out['started'] = out['started'] or ts
            out['ended'] = ts
        typ = r.get('type')
        msg = r.get('message') or {}

        if typ == 'user':
            bs = blocks(msg)
            if any(b.get('type') == 'tool_result' for b in bs):
                for b in bs:                       # ツールの実行結果。失敗だけを拾う
                    if b.get('type') == 'tool_result' and b.get('is_error'):
                        c = b.get('content')
                        c = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                        out['errors'].append({'ts': ts, 'kind': 'ツール実行の失敗',
                                              'detail': c[:400]})
                continue
            t = text_of(msg)
            if not t:
                continue
            item = {'ts': ts, 'text': t}
            out['user_messages'].append(item)
            if RE_CORRECTION.search(t):
                out['corrections'].append(item)

        elif typ == 'assistant':
            for b in blocks(msg):
                if b.get('type') == 'text' and b.get('text', '').strip():
                    t = b['text']
                    out['assistant_texts'].append({'ts': ts, 'text': t})
                    for m in RE_INCOMPLETE.finditer(t):
                        out['incomplete'].append({'ts': ts, 'text': m.group(1).strip()[:300]})
                elif b.get('type') == 'tool_use':
                    name, inp = b.get('name'), b.get('input') or {}
                    if name == 'Bash' and inp.get('command'):
                        c = inp['command']
                        if c not in seen_cmd:
                            seen_cmd.add(c)
                            out['commands'].append({'ts': ts, 'command': c,
                                                    'why': inp.get('description', '')})
                    elif name in WRITE_TOOLS and inp.get('file_path'):
                        f = inp['file_path']
                        if f not in seen_file:
                            seen_file.add(f)
                            out['files'].append({'ts': ts, 'path': f, 'tool': name})

        elif typ == 'attachment':
            a = r.get('attachment') or {}
            k = a.get('type')
            if k in ('file', 'image', 'pasted_text', 'selected_lines', 'pdf'):
                out['attachments'].append({'ts': ts, 'kind': k,
                                           'name': a.get('filePath') or a.get('name') or '(名前なし)'})
            elif k in ('hook_error',) or (k == 'hook_success' and a.get('exitCode')):
                out['errors'].append({'ts': ts, 'kind': f"フックの差し戻し（{a.get('hookName','')}）",
                                      'detail': (a.get('stderr') or a.get('content') or '')[:400]})
    return out

def main():
    p = find_transcript(explicit=sys.argv[1] if len(sys.argv) > 1 else None)
    if not p:
        print('記録ファイルが見つからない。`[Code]` 以外の環境では取得できない。', file=sys.stderr)
        return 1
    d = parse(p)
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in d.items()},
                     ensure_ascii=False, indent=2))
    return 0

if __name__ == '__main__':
    sys.exit(main())
