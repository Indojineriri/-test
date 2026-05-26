from __future__ import annotations

import json
import os

import anthropic

from models import Case, CaseList
import arxiv_search

DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7")

SYSTEM_PROMPT = (
    "あなたは技術リサーチを支援する優秀なアシスタントです。"
    "ユーザーが指定したテーマに関する事例を、Web 検索と提供された arXiv 論文要旨をもとに"
    "正確に調査し、出典 URL を明示しながら日本語でまとめます。"
    "事実を捏造せず、不確かな情報は『不明』と記載してください。"
)

RESEARCH_INSTRUCTION = """\
以下のテーマに関する直近（過去 1〜2 年を中心）の事例を、最大 {n} 件ピックアップしてください。

# テーマ
{theme}

# 調査の進め方
1. Web 検索ツールを積極的に使い、一次情報（公式ブログ、論文、企業発表、デモ動画）を集める。
   各事例について最低 2 つの一次情報に当たり、本文中の具体的記述を読み込むこと。
2. 下の『arXiv 候補リスト』も参考にし、関連性の高い論文は abstract を要約に取り込む。
3. 各事例について、次の項目を**具体的な数値・固有名詞・技術用語を盛り込んで**整理する：

   ## タイトル / サマリ
   - タイトル（短く）
   - 1〜2 文の要約
   - 発表元、発表年、URL

   ## 概要（4〜6 個の箇条書き）
   - 何をするシステムなのか
   - 入力 / 出力モダリティ（音声、テキスト、画像、関節角度 等）
   - 対象タスクや実演デモ（具体名で）
   - 規模感（パラメータ数、データセットサイズ、訓練時間 等、判れば）
   - 性能・成果（成功率、ベースライン比較 等、判れば）

   ## なぜ難しいのか（4〜6 個の箇条書き）
   - 既存技術の構造的な限界（単に『難しい』ではなく、何が原因で何ができないか）
   - 利用可能なデータの制約（量、ラベル、ドメインギャップ 等）
   - 物理 / センサ / 計算資源 の制約
   - 関連分野（VLA, VLM, RL 等）の通説的な問題点とのつながり

   ## 解決した技術的課題（4〜6 個の箇条書き）
   - 提案手法のキモ（アーキテクチャ、損失関数、データ収集方法など、具体に踏み込む）
   - どんな技術要素を組み合わせたか
   - 他手法と比べて何が新規か
   - その結果としてどう難しさを乗り越えたか

4. すべて日本語で記述する（モデル名・技術用語は原語のままで可）。
5. 抽象的な決まり文句（『高性能を実現』『画期的』など）は避け、必ず具体に踏み込む。
6. 不明な点は推測せず『（情報なし）』と明記する。

# arXiv 候補リスト
{arxiv_context}

最後に、各事例ごとに「---」で区切って、上記項目を見出し付きで列挙してください。
"""

STRUCTURE_INSTRUCTION = """\
直前のリサーチ結果を、`submit_cases` ツールを用いて構造化された JSON に変換してください。

# 重要な指示
- 各事例の `overview`, `challenges`, `solutions` は **4〜6 個** の箇条書きにする。
  情報が乏しい場合でも、リサーチ本文の記述を分割・展開して 4 個以上に膨らませること。
- 各箇条書きは **1 行 30〜80 文字** を目安に具体的な情報を含める。
  『高い性能』『難しい』のような抽象表現で終わらせず、技術名・数値・固有名詞を残す。
- リサーチ本文に書かれていない事実は決して創作しないこと。
  どうしても情報が見つからない場合のみ、最小限の項目数で良い。
- `subtitle` はスライド帯に入る 1〜2 文の要約。事例の特徴がひと目で分かる文にする。
- `link_text` はリンクのアンカーテキスト。論文タイトル、ブログタイトル、デモ動画のタイトル等。
"""


def _arxiv_context(theme: str, n: int) -> tuple[str, list[arxiv_search.ArxivPaper]]:
    try:
        papers = arxiv_search.search(theme, max_results=n)
    except Exception as e:
        return f"(arXiv 取得に失敗: {e})", []
    if not papers:
        return "(該当論文なし)", []
    return "\n".join(p.to_context() for p in papers), papers


def get_client(api_key: str | None = None) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def research(
    client: anthropic.Anthropic,
    theme: str,
    n_cases: int = 5,
    model: str = DEFAULT_MODEL,
    arxiv_n: int = 10,
    max_web_uses: int = 8,
) -> tuple[str, str, object]:
    """Stage 1: Free-form research with web_search + arXiv context.

    Returns (findings_markdown, arxiv_context_used, usage).
    """
    arxiv_text, _ = _arxiv_context(theme, arxiv_n)
    prompt = RESEARCH_INSTRUCTION.format(theme=theme, n=n_cases, arxiv_context=arxiv_text)
    resp = client.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": max_web_uses,
            }
        ],
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return text, arxiv_text, resp.usage


def structure(
    client: anthropic.Anthropic,
    findings_markdown: str,
    model: str = DEFAULT_MODEL,
) -> tuple[CaseList, object]:
    """Stage 2: turn the free-form findings into a CaseList via forced tool use."""
    schema = CaseList.model_json_schema()
    resp = client.messages.create(
        model=model,
        max_tokens=12000,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "name": "submit_cases",
                "description": "構造化された事例リストを送信する。",
                "input_schema": schema,
            }
        ],
        tool_choice={"type": "tool", "name": "submit_cases"},
        messages=[
            {"role": "user", "content": f"# リサーチ結果\n\n{findings_markdown}\n\n{STRUCTURE_INSTRUCTION}"}
        ],
    )
    for block in resp.content:
        if getattr(block, "type", "") == "tool_use" and block.name == "submit_cases":
            return CaseList.model_validate(block.input), resp.usage
    raise RuntimeError("submit_cases ツール呼び出しが見つかりませんでした。")


def format_api_error(e: Exception) -> str:
    if isinstance(e, anthropic.APIStatusError):
        body = getattr(e, "body", None)
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict) and err.get("message"):
                return f"HTTP {e.status_code} — {err['message']}"
        try:
            e.response.read()
            data = e.response.json()
            err = data.get("error", {}) if isinstance(data, dict) else {}
            return f"HTTP {e.status_code} — {err.get('message') or json.dumps(data)[:300]}"
        except Exception:
            pass
        return f"HTTP {e.status_code} — {getattr(e, 'message', '') or '(本文なし)'}"
    return f"{type(e).__name__}: {e}"
