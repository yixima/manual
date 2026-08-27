#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop フック：出力契約（L1 §0-15）の充足を機械的に検査し、記録する。

役割は2つある。
  ① 測定：全ターンの充足状況を metrics/compliance.jsonl へ追記する。
          これが「発動率」を測る唯一の一次データである（L1 §0-12 の外部指標①）。
  ② 強制：明白な違反は exit 2 で差し戻し、同じ応答内で修正させる。

設計上の注意：
  - 誤検知はユーザーの作業を妨げるため、判定は「明白なものだけ」に絞る。
  - 無限ループを避けるため、同一ターンでの差し戻しは1回までとする。
  - 設定は .claude/manual-hooks.json で上書きできる（enforce を false にすると記録のみ）。
"""
import json, sys, os, re, hashlib, datetime, pathlib

def _candidates(cwd, name):
    """設定・用語集を、プロジェクト内 → ユーザー共通（~/.claude）の順に探す。
    全プロジェクト共通で導入した場合、設定はホーム側に置かれるため。"""
    return [pathlib.Path(cwd) / '.claude' / name,
            pathlib.Path.home() / '.claude' / name]

def metrics_dir(cwd):
    """記録の置き場。環境変数 CLAUDE_MANUAL_METRICS で上書きできる。
    プロジェクト内に設定があればプロジェクトの metrics/、無ければ
    ~/.claude/manual-metrics/ に置く（共通導入時に各プロジェクトを汚さないため）。"""
    env = os.environ.get('CLAUDE_MANUAL_METRICS')
    if env:
        return pathlib.Path(env).expanduser()
    if (pathlib.Path(cwd) / '.claude' / 'manual-hooks.json').exists():
        return pathlib.Path(cwd) / 'metrics'
    return pathlib.Path.home() / '.claude' / 'manual-metrics'

def load_cfg(cwd):
    cfg = {"enforce": True, "rules": {"declaration_without_action": True,
                                      "missing_state_line": True,
                                      "unsourced_verified_label": True,
                                      "unexplained_incomplete": True,
                                      "undefined_jargon": True,
                                      "undated_time_reference": True}}
    for cand in _candidates(cwd, 'manual-hooks.json'):
        try:
            cfg.update(json.loads(cand.read_text(encoding='utf-8')))
            break
        except Exception:
            continue
    return cfg

# ── 判定ルール ──────────────────────────────────────────────
# R1【型H】実行を伴わない着手宣言で応答を終えている（L1 §2-17）
RE_DECL = re.compile(
    r'(これから|続けて|次に|引き続き|この後)[^。\n]{0,40}?'
    r'(します|着手します|実行します|進めます|作成します|開始します)[。．]?\s*$')

# R2【型B】作業を報告しているのに状態行がない（L1 §2-15／§0-15）
RE_WORK = re.compile(r'(完了|実行|作成|修正|追加|削除|コミット|生成|更新)し(た|ました)')
RE_STATE = re.compile(r'(—\s*状態[:：])|(【この応答で完了したこと】)|(状態[:：]\s*(完了|実行中|入力待ち|停止中))')

# R3【型A】【確認済】と書きながら出典がない（L1 §3-1「出典URLを併記する」）
RE_VERIFIED = re.compile(r'【確認済】')
RE_SOURCE = re.compile(r'(https?://)|(出典[:：])|(一次資料)|(`[^`]+\.(md|py|sh|json|ya?ml)`)')

# R4【型I】未完了で終わるのに中断の理由が書かれていない（L1 §2-9 完遂義務）
RE_INCOMPLETE = re.compile(r'(【未完了】|未完了|残りの作業|次に最初に行うこと|途中まで|一旦ここまで)')
RE_REASON = re.compile(r'(質問|お伺い|ご判断|判断が必要|承認|許可|エラー|失敗しました|進めません|進められません|'
                       r'危険|不可逆|確認が必要|確認させて|どちらに|ますか[？?]|でしょうか[？?])')

# R5【型J】専門用語を初出で説明していない（L1 §2-13）
def jargon_terms(cwd):
    for cand in _candidates(cwd, 'glossary.json'):
        try:
            g = json.loads(cand.read_text(encoding='utf-8'))
            return [t for t in g.get('terms', []) if t]
        except Exception:
            continue
    return []

def unexplained(msg, term):
    """その用語が、この応答の中で一度も説明されずに使われていれば True。"""
    for m in re.finditer(re.escape(term), msg):
        seg = msg[m.end():m.end() + 25]
        if seg.startswith('（') or seg.startswith('(') or seg.startswith('＝') or seg.startswith('とは'):
            return False
    return True

# R6【型K】日時に依存する記述に基準日が無い（L1 §3-7）
RE_TIMEREF = re.compile(r'(今日|本日|現在|最新|今月|今週|来週|来月|昨日|明日|締切|期限)')
RE_DATE = re.compile(r'(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}|\d{1,2}月\d{1,2}日|基準[:：]|JST|UTC)')

def evaluate(msg, cfg, cwd='.', session='x'):
    """違反の一覧と、契約の充足状況を返す。"""
    r = cfg.get("rules", {})
    tail = msg.rstrip()[-200:]
    viol = []
    if r.get("declaration_without_action", True) and RE_DECL.search(tail):
        viol.append(("型H", "着手宣言で応答が終わっている。宣言した作業を同じ応答内で実行するか、"
                            "実行できないなら【この応答で完了したこと】／【未完了】／【次に最初に行うこと】を書く（§2-17／§2-18）。"))
    if r.get("missing_state_line", True) and len(msg) > 400 and RE_WORK.search(msg) and not RE_STATE.search(msg):
        viol.append(("型B", "作業を報告しているが状態行がない。末尾に1行「— 状態：… 次：…」を付ける"
                            "（すべきことがなければ『次：不要』と明記する）（§2-15／§0-15）。"))
    if r.get("unsourced_verified_label", True) and RE_VERIFIED.search(msg) and not RE_SOURCE.search(msg):
        viol.append(("型A", "【確認済】と書いているが出典が併記されていない。出典を書けないなら"
                            "【未確認・推測】へ落とす（§3-1）。"))
    if r.get("unexplained_incomplete", True) and RE_INCOMPLETE.search(msg) and not RE_REASON.search(msg):
        viol.append(("型I", "作業に未完了が残っているのに、中断の理由が書かれていない。"
                            "続行を妨げる要因（①質問が必要 ②承認待ち ③エラーで進めない ④危険で確認が要る）が"
                            "無いなら、応答を終えずに最後までやり切る。あるなら、①〜④のどれかを明示する（§2-9）。"))
    if r.get("undefined_jargon", True) and len(msg) > 300:
        seen = seen_terms(cwd, session)
        new = [t for t in jargon_terms(cwd) if t in msg and t not in seen and unexplained(msg, t)]
        if new:
            add_seen(cwd, session, [t for t in jargon_terms(cwd) if t in msg])
            viol.append(("型J", "このセッションで初めて使う専門用語に、意味の説明が無い："
                                + "／".join(new[:5])
                                + "。初出時に1行で意味を書く。例「フック（＝条件が満たされたら自動で動く"
                                  "小さなプログラム）」（§2-13）。"))
        else:
            add_seen(cwd, session, [t for t in jargon_terms(cwd) if t in msg])
    if r.get("undated_time_reference", True) and RE_TIMEREF.search(msg) and not RE_DATE.search(msg) and len(msg) > 300:
        viol.append(("型K", "「今日」「現在」「最新」など日時に依存する記述があるが、基準となる日付が書かれていない。"
                            "毎ターン注入される現在日時を基準にし、本文に基準日を明記する（§3-7）。"))
    contract = {
        "has_label": bool(re.search(r'【(確認済|未確認・推測|不明)】', msg)),
        "has_state_line": bool(RE_STATE.search(msg)),
        "has_backcheck": bool(re.search(r'(要裏取り|要・裏取り)', msg)),
        "length": len(msg),
    }
    return viol, contract

def seen_terms(cwd, session):
    """このセッションで既に説明済みの用語（初出判定のため）。"""
    p = metrics_dir(cwd) / f'.terms-{session}'
    try:
        return set(p.read_text(encoding='utf-8').split())
    except Exception:
        return set()

def add_seen(cwd, session, terms):
    if not terms:
        return
    try:
        d = metrics_dir(cwd)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f'.terms-{session}'
        cur = seen_terms(cwd, session) | set(terms)
        p.write_text(" ".join(sorted(cur)), encoding='utf-8')
    except Exception:
        pass

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # 入力が読めないときは黙って通す（作業を止めない）
    msg = data.get("last_assistant_message") or ""
    cwd = data.get("cwd") or os.getcwd()
    sid = data.get("session_id") or "unknown"
    if not msg.strip():
        sys.exit(0)

    cfg = load_cfg(cwd)
    viol, contract = evaluate(msg, cfg, cwd, sid)

    # ① 測定：常に記録する
    try:
        mdir = metrics_dir(cwd)
        mdir.mkdir(parents=True, exist_ok=True)
        rec = {"ts": datetime.datetime.now().isoformat(timespec='seconds'),
               "session": sid, "contract": contract,
               "violations": [v[0] for v in viol]}
        with open(mdir / 'compliance.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

    if not viol or not cfg.get("enforce", True):
        sys.exit(0)

    # ② 強制：同一応答での差し戻しは1回まで（無限ループの防止）
    guard = metrics_dir(cwd) / f'.stopguard-{sid}'
    digest = hashlib.sha256(msg.encode('utf-8')).hexdigest()[:16]
    try:
        if guard.exists() and guard.read_text().strip() == digest:
            sys.exit(0)
        guard.write_text(digest)
    except Exception:
        pass

    lines = ["[出力契約の未充足を検出しました（マニュアル §0-15）]",
             "この応答は送信前に修正が必要です。"]
    lines += [f"  ・【{t}】{m}" for t, m in viol]
    lines.append("修正したうえで、同じ応答を出し直してください。")
    print("\n".join(lines), file=sys.stderr)
    sys.exit(2)

if __name__ == '__main__':
    main()
