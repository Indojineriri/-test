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
1. 必要に応じて Web 検索ツールを使い、一次情報（公式ブログ、論文、企業発表、デモ動画）を集める。
2. 下の『arXiv 候補リスト』も参考にし、関連性の高いものを取り上げる。
3. 各事例について、次の項目を整理する：
   - タイトル / 1〜2 文の要約 / 発表元 / 発表年 / URL
   - 概要（3〜5 個の箇条書き）
   - なぜ難しいのか（既存技術の課題、3〜5 個）
   - 解決した技術的課題（提案手法のキモ、3〜5 個）
4. すべて日本語で記述する（固有名詞は原語のままで可）。

# arXiv 候補リスト
{arxiv_context}

最後に、各事例ごとに「---」で区切って、上記項目を見出し付きで列挙してください。
"""

STRUCTURE_INSTRUCTION = """\
直前のリサーチ結果を、`submit_cases` ツールを用いて構造化された JSON に変換してください。
各事例の本文を読み取り、CaseList スキーマに沿って忠実にフィールドへ割り当ててください。
情報がない項目は空文字列 / 空配列 / null を使用し、内容を創作しないでください。
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
