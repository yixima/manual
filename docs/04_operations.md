# 運用手順：この仕組みをどう回すか

## 平時（毎日・何もしなくてよい）

配布が済んでいれば、次は自動で動く。

- **`[Code]`**：フックが毎ターン関門を注入し、出力契約を検査し、`metrics/compliance.jsonl` に記録する。
- **`[Chat]` `[Cowork]`**：コアカードが毎ターンのコンテキストに入る。

**平時にユーザーがすることは無い。** 唯一あるとすれば、応答が期待と違ったときに「マニュアル確認」と打つこと（§0-3）。

## 週次（15分）

```bash
python3 tools/score_session.py          # ① 出力契約の充足率を見る
```

充足率が 95% を下回っている、または違反の型が偏っているなら、盲検採点へ進む。

```bash
python3 tools/make_audit_package.py --transcript <トランスクリプト.jsonl> -n 20 -o audit_samples.md
```

→ `chatgpt/prompt_02_blind_grader.md` ＋ `chatgpt/rubric.md` ＋ `audit_samples.md` を ChatGPT に貼る。
→ 返ってきた JSON の `single_change_with_highest_impact` を読む。**これが次の改訂の起点になる。**

## 失敗が起きたとき（その場で）

1. **記録する**：`dist/L2_records_v16.md` に、必須5項目（①何が起きたか ②被害 ③直接原因 ④拡大原因 ⑤再発防止＝発動すべきだった条項）で追記する。
2. **⑤に既存条項の番号を書けるか確かめる**。
   - **書ける** → 条項は足さない。**その条項の発動経路を強化する**（関門・自動発動表・フックのどれか）。これが §0-14 の定員制である。
   - **書けない** → そのときだけ新条項を検討する。
3. **なぜ関門をすり抜けたかを1行残す**（どの言い訳で省いたか）。すり抜けの型を残すことが、次の取りこぼしを構造的に減らす（§0-10⑦）。

## 改訂するとき（v17 以降）

```
① 失敗記録と週次の採点結果を集める
② ChatGPT に赤チームをさせる           chatgpt/prompt_03_red_team.md
③ ChatGPT に発動テストを作らせる       chatgpt/prompt_04_examiner.md → evals/cases.yaml
④ 改訂案を作る（§0-14 の定員制を必ず通す）
⑤ ChatGPT に対案を出させる             chatgpt/prompt_05_counter_proposal.md
⑥ 採否を決める。不採用は理由を chatgpt/decisions.md に残す
⑦ tools/build_v16.py を v17 向けに更新して生成する
⑧ python3 tools/audit_activation.py dist/L1_manual_v17.md --records dist/L2_records_v17.md
      → 到達率 100% / 捕捉率 100% でなければ発行しない（§0-12 の合格基準）
⑨ ChatGPT に独立検査させ、数値を突き合わせる  chatgpt/prompt_01_independent_check.md
      → 一致しなければ、どちらかの基準が誤っている。原因を特定するまで発行しない
⑩ python3 tools/build_dist.py            → 版・関門・表の一致を機械照合
⑪ 配布（dist/DISTRIBUTION.md の6箇所）
```

**⑧⑨⑩のいずれかが不合格なら発行しない。** これが §0-7「発行前の照合」の機械化である。

## 配布（初回・および改訂のたび）

`dist/DISTRIBUTION.md` の表に従い、**6箇所**へ配る。

- **配布は一方向である。** リポジトリ → dist/ → 各配布先。
- **配布先で直接編集しない。** 編集はリポジトリで行い、再生成して再配布する。これで版ずれが構造的に起きなくなる。

初回に限り、`[Code]` では次も行う。

```bash
cp -r .claude/hooks .claude/settings.json <対象リポジトリ>/.claude/
```

## よくある詰まり

| 症状 | 原因 | 対処 |
|---|---|---|
| フックが動かない | 設定が読まれていない | セッションで `/context` を実行し、フックが載っているか確認する |
| クラウドセッションでフックが動かない | 【確認済】ローカルの `~/.claude/settings.json` は読まれない | リポジトリ側の `.claude/settings.json` に置く |
| Cowork でコアカードが効かない | `@` インポートは作業ディレクトリ外だとスキップされる | **実体として貼る**（設定→Cowork→グローバル指示） |
| 差し戻しが多すぎて作業が進まない | 誤検知 | `.claude/manual-hooks.json` の該当ルールを false にし、**その事実を L2 に記録する**（黙って無効化しない） |
| 過去のチャットに反映されない | 原理的に不可能 | 引き継ぎファイル（§10-5）で文脈を新セッションへ持ち込む |
