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

def load_cfg(cwd):
    p = pathlib.Path(cwd) / '.claude' / 'manual-hooks.json'
    cfg = {"enforce": True, "rules": {"declaration_without_action": True,
                                      "missing_state_line": True,
                                      "unsourced_verified_label": True}}
    try:
        cfg.update(json.loads(p.read_text(encoding='utf-8')))
    except Exception:
        pass
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

def evaluate(msg, cfg):
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
    contract = {
        "has_label": bool(re.search(r'【(確認済|未確認・推測|不明)】', msg)),
        "has_state_line": bool(RE_STATE.search(msg)),
        "has_backcheck": bool(re.search(r'(要裏取り|要・裏取り)', msg)),
        "length": len(msg),
    }
    return viol, contract

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
    viol, contract = evaluate(msg, cfg)

    # ① 測定：常に記録する
    try:
        mdir = pathlib.Path(cwd) / 'metrics'
        mdir.mkdir(exist_ok=True)
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
    guard = pathlib.Path(cwd) / 'metrics' / f'.stopguard-{sid}'
    digest = hashlib.sha256(msg.encode('utf-8')).hexdigest()[:16]
    try:
        if guard.exists() and guard.read_text().strip() == digest:
            sys.exit(0)
        guard.write_text(digest)
    except Exception:
        pass

    lines = ["[出力契約の未充足を検出しました（マニュアル v16 §0-15）]",
             "この応答は送信前に修正が必要です。"]
    lines += [f"  ・【{t}】{m}" for t, m in viol]
    lines.append("修正したうえで、同じ応答を出し直してください。")
    print("\n".join(lines), file=sys.stderr)
    sys.exit(2)

if __name__ == '__main__':
    main()
