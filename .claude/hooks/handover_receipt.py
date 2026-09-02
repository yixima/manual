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
`handover/` に置かれた引き継ぎファイル（第1章を持つ .md）を探し、
セッション開始時に**受領確認をコンテキストへ流し込む**。ユーザーの操作は不要。
見つからなければ**何も出さない**（無関係なプロジェクトで騒がないため）。

**複数あるときは、勝手に選ばない**（§5.6 受け口の規定）。
名前と更新日時の一覧を出し、**どれを引き継ぐかを一つだけ質問する**。
以前は「最も新しいもの」を黙って選んでいたが、これは規定と矛盾していた——
**1つの作業が2つ以上のセッションへ枝分かれしたとき、黙って別の枝を引き継いでしまう**
（2026-09-02 に実測で発覚。L2 記録参照）。

置き場は環境変数 `CLAUDE_HANDOVER`（ファイルを直接指定）でも上書きできる。
"""
import json, sys, os, pathlib, subprocess

MARK = "## 1. 依頼の原文"        # 引き継ぎファイルであることの判定。README 等を誤って拾わないため


def find(cwd):
    """引き継ぎの候補を返す。戻り値＝(選んだ1本, 候補の全件)。

    候補が1本ならそれを選ぶ。**2本以上あるときは選ばない**（`None` を返す）。
    枝分かれした作業では、更新時刻の新しさは「自分宛て」であることを意味しない。
    """
    env = os.environ.get('CLAUDE_HANDOVER')
    if env:
        p = pathlib.Path(env).expanduser()
        return (p, [p]) if p.exists() else (None, [])
    d = pathlib.Path(cwd) / 'handover'
    if not d.is_dir():
        return None, []
    cands = [f for f in d.glob('*.md')
             if f.is_file() and MARK in f.read_text(encoding='utf-8', errors='replace')]
    cands.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    if not cands:
        return None, []
    if len(cands) == 1:
        return cands[0], cands
    return None, cands                 # **複数あるときは勝手に選ばない**


def lineage(path):
    """照合ブロックから、この引き継ぎの枝と親を読む。無ければ空。"""
    try:
        import re
        m = re.search(r'```handover-manifest\n(.*?)\n```',
                      path.read_text(encoding='utf-8', errors='replace'), re.S)
        d = json.loads(m.group(1)) if m else {}
        return d.get('case', ''), d.get('lane', ''), d.get('parent', '')
    except Exception:
        return '', '', ''


def case_from_name(name):
    """照合ブロックに案件名が無い（古い引き継ぎ）ときは、ファイル名から取る。"""
    for cut in ('_handover_', '_handover'):
        if cut in name:
            return name.split(cut)[0].split('.')[0]
    return pathlib.Path(name).stem.split('.')[0]


def existing_lanes(cwd):
    """受け口ですでに使われている枝名を集める。**同じ名前を提案させないため。**"""
    out = []
    d = pathlib.Path(cwd) / 'handover'
    if not d.is_dir():
        return out
    for f in d.glob('*.md'):
        try:
            _, lane, _ = lineage(f)
            if lane and lane not in out:
                out.append(lane)
        except Exception:
            continue
    return out


def section(text, head, limit=600):
    """引き継ぎの1章だけを切り出す。枝名を提案する材料にする。"""
    try:
        i = text.index(head)
    except ValueError:
        return ''
    j = text.find('\n## ', i + len(head))
    return text[i:j if j > 0 else len(text)][:limit].strip()


def own_file(cands, sid):
    """候補のうち、**このセッション自身が書いたもの**を返す。

    自分が書いた引き継ぎがあるなら、それが自分宛てであることは確定している。
    **確定しているものを質問してはいけない**（§2-5 自分で調べれば分かることは質問しない）。
    """
    import re
    for c in cands:
        try:
            m = re.search(r'```handover-manifest\n(.*?)\n```',
                          c.read_text(encoding='utf-8', errors='replace'), re.S)
            man = json.loads(m.group(1)) if m else {}
            if man.get('session') == sid:
                return c
        except Exception:
            continue
    return None


def my_lane(cwd, sid):
    """このセッションがすでに枝を持っているか。持っていれば再び質問しない。"""
    d = pathlib.Path(cwd) / 'handover'
    if not d.is_dir():
        return ''
    own = own_file(sorted(d.glob('*.md')), sid)
    if not own:
        return ''
    return lineage(own)[1]


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
        f, cands = find(cwd)
    except Exception:
        return 0                       # 読めなくてもセッションの開始を妨げない
    if not f and not cands:
        return 0
    if not f:
        # まず、**このセッション自身が書いた引き継ぎ**が候補にあれば、それが自分宛てである。
        # 確定しているものを質問しない（§2-5）。
        f = own_file(cands, sid)
    if not f:
        # **複数ある＝枝分かれしている。勝手に選ばない**（§5.6）。
        import datetime as _dt
        print("[引き継ぎの自動受領・§10-5] **受け口に引き継ぎが複数あります。"
              "どれを引き継ぐかは、勝手に決めません。**")
        print("\n| # | ファイル | 案件 | 枝 | 更新日時 |")
        print("|---|---|---|---|---|")
        for i, c in enumerate(cands[:10], 1):
            case, lane, _ = lineage(c)
            ts = _dt.datetime.fromtimestamp(c.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
            print(f"| {i} | `{c.name}` | {case or '—'} | {lane or '—'} | {ts} |")
        print("\n→ **この一覧をユーザーに示し、どれを引き継ぐかを"
              "「一つだけ」質問すること**（§2-4 質問は一度に一つ）。"
              "\n→ **更新日時が新しいものを自分で選ばない。** "
              "枝分かれした作業では、新しさは「自分宛て」であることを意味しない。"
              "\n→ 答えが返るまで、引き継ぎを前提とした作業を始めない（§1-7）。")
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
    case, lane, parent = lineage(f)
    head = f"対象：`{f}`"
    if case or lane:
        head += f"（案件：{case or '—'}／枝：{lane or '（枝分かれなし）'}）"
    if parent:
        head += f"\n分岐元：`{parent}` ——**この枝は途中から分かれたものである。"\
                f"分岐前の経緯は分岐元にしか無い。必要になったら分岐元を読む。**"
    print("[引き継ぎの自動受領・§10-5] このセッションは引き継ぎファイルを受け取っています。\n"
          f"{head}\n")
    if out.strip():
        print(out.strip())
    else:
        print("受領確認スクリプトを実行できなかった。ファイルを直接読んで、10章の欠落を自分で確認すること。")
    # ── 枝の名前を決める（セッション開始時に提案し、ユーザーが確定する）──
    # なぜここで決めるか：枝名が決まらないまま作業を進めると、最初の節目で
    # **保存できない**（別セッションの引き継ぎを枝名なしで上書きできないため）。
    # 決めるのを後回しにすると、**いちばん保存したい瞬間に手が止まる**。
    mine = my_lane(cwd, sid)
    if mine:
        print(f"\n→ このセッションの枝は `{mine}` である。**枝名の質問は不要。**"
              f"節目ごとに `--lane {mine}` を付けて保存する。")
    else:
        raw = f.read_text(encoding='utf-8', errors='replace')
        used = existing_lanes(cwd)
        print("\n【枝の名前を決める（§5.6 枝分かれ）】")
        print("**この引き継ぎから枝分かれして進むなら、枝の名前が要る。**"
              "名前が無いと、最初の節目で保存できない"
              "（別のセッションが書いた引き継ぎを、枝名なしでは上書きできないため）。")
        if used:
            print(f"→ **すでに使われている枝名：{'、'.join(f'`{x}`' for x in used)}"
                  f"（重複させない）**")
        for head, label in (('## 7. 未完了のタスク', '未完了'),
                            ('## 8. 次に最初に行うこと', '次の一手')):
            body = section(raw, head)
            if body:
                print(f"\n［{label}（枝名を考える材料）］\n{body}")
        print("\n→ **上を読んだうえで、枝の名前を2〜3個提案し、"
              "ユーザーに『一つだけ』質問すること**（§2-4）。")
        print("→ 名前は **半角英数とハイフンのみ**（`^[A-Za-z0-9._-]+$`。§7-11）。"
              "日本語の意味を1行添える（例：`survey`＝現地調査の枝）。")
        print("→ **勝手に決めない。** 一度決めた名前は変えられない"
              "（変えると次のセッションから見えなくなる）。")
        print("→ ユーザーが名前を指示したら、**その場で次を実行して最初の保存まで行う**："
              "\n```\npython3 tools/make_handover.py --auto handover/"
              f"{lineage(f)[0] or case_from_name(f.name)}_handover_latest.md \\\n"
              f"        --lane <指示された名前> --parent {f.name}\n```"
              "\n   （ファイル名は自動で `案件名.枝名_handover_latest.md` になる。"
              "その後 `--seal` → `--check` を通す。）")
        print("→ **枝分かれしない（この続きを1本で進める）とユーザーが答えたら、"
              "枝名は付けない。** その場合は保存時に上書きの門番が働く。")

    print("\n→ **作業に入る前に、このファイルを全章読むこと。**"
          "第1章（依頼の原文）と付録B（応答の原文）は要約ではなく原文である。要約で代用しない。\n"
          "→ 受領が完全であれば、**ユーザーに「理解できているか」を確かめる質問はしない。**"
          "上の照合がその確認である（§2-5 自分で調べれば分かることは質問しない）。\n"
          "→ 受領が不完全であれば、**作業に入る前に、不足している箇所を名指しで申告する**（§1-7）。\n"
          "→ そのうえで「8. 次に最初に行うこと」の1行目から始める。**前置き・要約・再説明は書かない**（§2-20）。")
    return 0


if __name__ == '__main__':
    sys.exit(main())
