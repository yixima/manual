# 反映マトリクス：更新したマニュアルを「どのClaudeに・どこまで自動で」効かせられるか

> 調査日：2026-08-27 ／ 出典＝Anthropic 公式ドキュメント（本文中にURLを明記）
> 本書の事実主張には確信度ラベルを付す（マニュアル §3-1）。

## 結論（先に）

| 対象 | 自動反映 | 到達手段 | 確信度 |
|---|---|---|---|
| **未来**の Claude Code（ローカル） | ◎ **完全自動＋強制可** | `~/.claude/CLAUDE.md`／`~/.claude/rules/`／**フック** | 【確認済】 |
| **未来**の Claude Code（web・cloud） | ○ 自動 | リポジトリの `CLAUDE.md`＋`.claude/settings.json` のフック | 【確認済】 |
| **未来**の Cowork（デスクトップ） | ○ 自動 | 設定→Cowork→**グローバル指示**／`~/.claude/CLAUDE.md` | 【確認済】 |
| **未来**の Chat（claude.ai） | ○ 自動 | 設定→**Claudeへの指示**（全会話）／**プロジェクト指示**（当該PJ内） | 【確認済】 |
| **進行中**のセッション | △ 部分的 | Code＝フックがあれば次ターンから自動／Chat＝再貼付または発動キーワード | 【確認済】/【未確認・推測】 |
| **過去**のセッション | ✕ **不可能** | 生成済みの応答は遡って変更できない | 【確認済】（原理） |

**要点は3つ。**
1. **未来のセッションは、4箇所に置くだけで全面自動化できる。**
2. **Claude Code だけは「確率的でない強制」が可能**——フックは、モデルの判断と無関係にシェルコマンドとして必ず実行される。マニュアルの発動率を根本から変えられるのはこの層だけである。
3. **過去は不可能。** これは実装の制約ではなく原理である。過去チャットを開いて新しい発言をした場合に現在の設定が効くかは【未確認・推測】。

---

## 1. Claude Code（ローカル／CLI・IDE）

### 1-1. 常時読み込まれるファイル（読み込み順＝広い→狭い）
出典：https://code.claude.com/docs/en/memory

| スコープ | 場所 | 適用範囲 |
|---|---|---|
| 管理ポリシー | Linux/WSL `/etc/claude-code/CLAUDE.md`／macOS `/Library/Application Support/ClaudeCode/CLAUDE.md`／Windows `C:\Program Files\ClaudeCode\CLAUDE.md` | **組織全体・個人設定で除外不可** |
| ユーザー指示 | `~/.claude/CLAUDE.md` | **自分の全プロジェクト・全セッション** |
| プロジェクト指示 | `./CLAUDE.md` または `./.claude/CLAUDE.md` | そのリポジトリ（git で共有） |
| ローカル指示 | `./CLAUDE.local.md` | 自分のみ・当該プロジェクト |
| ルール | `~/.claude/rules/*.md`（個人）／`.claude/rules/*.md`（PJ） | 毎セッション読み込み。`paths:` を付ければ該当ファイル操作時のみ |

【確認済】**すべて「毎セッションの開始時にコンテキストへ読み込まれる」。** 手動操作は不要。

### 1-2. 公式が明記している「長いほど守られない」
出典：同上（Write effective instructions）

> **Size**: target under 200 lines per CLAUDE.md file. Longer files consume more context and **reduce adherence**.
> （200行未満を目標とする。長いファイルはコンテキストを消費し、**遵守率を下げる**。）

> CLAUDE.md content is delivered as a user message after the system prompt, not as part of the system prompt itself. Claude reads it and tries to follow it, but **there's no guarantee of strict compliance**, especially for vague or conflicting instructions.

【確認済】**「本編130KBを常時発動」という設計は、公式仕様の観点から遵守率を下げる方向に働く。** v15 §0-1 が自ら認めた「全項点検は分量的に不可能」は、単なる自己申告ではなく仕様上の裏付けがある。

### 1-3. 唯一の非確率的な強制層＝フック
出典：https://code.claude.com/docs/en/hooks

公式は明確に述べている：

> Both are loaded at the start of every conversation. Claude treats them as **context, not enforced configuration**. **To block an action regardless of what Claude decides, use a hook instead.**
> If the instruction is something that must run at a specific point... write it as a hook instead. **Hooks execute as shell commands at fixed lifecycle events and apply regardless of what Claude decides.**

本件で使える主要イベント：

| イベント | できること | マニュアル上の用途 |
|---|---|---|
| `SessionStart` | セッション開始時に stdout をコンテキストへ注入 | L0 コアカードの投入 |
| **`UserPromptSubmit`** | **毎ターン** stdout をコンテキストへ注入。`permissionDecision: "deny"` でプロンプト自体を拒否可 | **関門7項を毎ターン再注入＝「忘れる」を構造的に潰す** |
| `Stop` | 応答終了をブロック（exit 2 または `permissionDecision: "deny"`） | **出力契約（状態・次の一手・確信度ラベル）の未記載を検出して差し戻す** |
| `PreToolUse` | ツール実行を拒否 | 危険操作・非ASCIIファイル名の共有を機械的に阻止（§7-11・§8-5） |
| `PostToolUse` | 実行後に検査 | 納品ファイル名の正規表現検証（§7-11）・実行結果の検証（§8-6） |
| `InstructionsLoaded` | どの指示ファイルが読まれたかをログ | **発動率の実測ログ**（§0-12 の外部観測化） |

【確認済】設定場所：`~/.claude/settings.json`（全プロジェクト）／`.claude/settings.json`（PJ・git 共有）／管理ポリシー（組織・最優先）。
【確認済】**クラウドセッション（claude.ai/code）はローカルの `~/.claude/settings.json` を読まない。** フックはリポジトリと組織の管理設定から供給される。

### 1-4. 単一ソース化の手段
【確認済】`.claude/rules/` はシンボリックリンクに対応する。共有ディレクトリを各プロジェクトへリンクすれば、**1ファイルの更新が全プロジェクトへ即時反映**される。
```
ln -s ~/manual/dist/rules .claude/rules/manual
```

### 1-5. 圧縮（compact）耐性
【確認済】プロジェクト直下の `CLAUDE.md` は `/compact` 後にディスクから再読込・再注入される。会話中にだけ与えた指示は消える。**＝マニュアルは必ずファイルに置く。会話に貼るだけにしない。**

---

## 2. Cowork

出典：https://academy.claude.com/tutorials/customize-claude-cowork ／ https://code.claude.com/docs/en/memory

- 【確認済】**設定 → Cowork → グローバル指示（デスクトップアプリのみ）**：「すべての Cowork セッションに適用されるルール」。
- 【確認済】プロジェクト右パネルの **Instructions**：当該プロジェクトのみ。
- 【確認済】**デスクトップの Cowork セッションは `~/.claude/CLAUDE.md` を読む。ただし作業ディレクトリ外を指す `@` インポートはスキップされる**（シンボリックリンクの `~/.claude/CLAUDE.md` 自体も無視される）。
  → **設計上の重大な帰結：コアカードを `@import` で外出しすると Cowork では読まれない。コアカードは各配布先に「実体としてインライン」で置く。**
- 【確認済】Cowork・クラウドセッションは、ローカルの `~/.claude/skills/` を読まない。

---

## 3. Chat（claude.ai）

出典：https://support.claude.com/en/articles/10185728-understanding-claude-s-personalization-features

- 【確認済】**設定 →「Claudeへの指示」（account-level）＝すべての会話に適用。**
- 【確認済】**プロジェクト指示 ＝ そのプロジェクト内のチャットのみに適用。**
- 【未確認・推測】プロジェクトナレッジに添付したファイルが、毎ターン全文コンテキストに載るのか、検索で必要箇所のみ取得されるのかは公式に明記されていない。**容量が大きいほど後者になる可能性が高い**（一般的な実装として）。
  → **したがって「130KB の本編をナレッジに置けば常時発動する」とは言えない。** 常時発動を確実にしたいものは**指示欄（文字数上限内）に入れる**のが安全側。
- 【確認済】**過去の会話に遡って適用されるとは、公式ドキュメントに記載がない。**

---

## 4. 「過去」について（正直な記載）

**過去のセッションに遡って反映することはできない。** 既に生成された応答のテキストは確定しており、後から設定を変えても書き換わらない。これは製品の制約ではなく、対話ログの性質である。

できることは次の3つに限られる。
1. **過去チャットを再開したときに、その時点の設定で新しいターンを動かす**（【未確認・推測】：account-level 指示が既存会話の新ターンに適用されるかは未確認。要実測）。
2. **引き継ぎファイル（§10-5）で、過去の文脈を新セッションへ持ち込む。**
3. **失敗記録（§10-4）として、過去の失敗を未来の発動条件へ変換する**——これが実質的に「過去を反映する」唯一の方法である。

---

## 5. この結論が設計に与える指示

1. **配布先は4つ**：`~/.claude/CLAUDE.md`（Code 全PJ＋Cowork デスクトップ）／リポジトリ `CLAUDE.md`（Code web・チーム）／Cowork グローバル指示／claude.ai「Claudeへの指示」＋プロジェクト指示。
2. **4箇所すべてに同一のコアカードを、実体としてインラインで置く**（`@import` は Cowork で落ちる）。
3. **コアカードは200行未満**（公式の遵守率基準）。本編・記録はリポジトリ側に無省略で保持し、参照で開く。
4. **Claude Code にはフック層を追加する**。これがマニュアル史上はじめて「確率的でない強制」を持ち込む層になる。
5. **配布は手作業にしない。** 単一ソース（本リポジトリ）→ 生成スクリプト → 4配布先、という一方向の流れにし、版ずれを構造的に不可能にする（§0-7 の版管理を機械化）。
