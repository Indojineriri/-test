from __future__ import annotations

import html as html_mod
import re
import urllib.request
from dataclasses import dataclass
from typing import Callable

import anthropic

import arxiv_search
from models import CaseList

DEFAULT_MODEL = "claude-opus-4-7"

DEEP_SYSTEM = """\
あなたは技術リサーチを行うエージェントです。ユーザーが指定したテーマについて、
複数のツールを使って多段的にリサーチし、最終的に submit_findings ツールで
構造化された事例リストを提出します。

# 進め方
1. テーマを分析し、何を調べるべきか短く計画を書く。
2. web_search で広く事例を集める（複数の検索クエリを試す）。
3. arxiv_search で学術論文を探す。
4. 有望な候補について fetch_url でページ本文を取得し、詳細を読み込む。
5. 各事例について最低 2 つの一次情報を当たり、内容を裏取りする。
6. 十分な情報が集まったら submit_findings で最終結果を提出する。

# ルール
- 推測や創作は絶対にしない。情報がないなら『（情報なし）』と書く。
- 各事例の overview / challenges / solutions は **最大 4 個** の箇条書き、
  各項目 **40〜60 文字** で 1 行に収まる長さにする（スライドの枠に収めるため）。
  具体的な数値・固有名詞・技術用語を必ず含める。
- 抽象的な決まり文句（『高性能を実現』『画期的』『難しい』だけ等）は禁止。
- `image_url` は **論文のプロジェクトページ / ブログ記事 / GitHub README** の URL を優先する
  （arXiv abstract ページは og:image を持たないので、可能なら project page を探す）。
  見つからなければ null（PPT 生成側で PDF から自動抽出する）。
- ステップごとに『次に何をするか』を 1〜2 行で出力しながら進める。
- submit_findings を呼ぶ前に『これで十分か』を一度自問し、足りなければ追加調査する。
"""

INITIAL_PROMPT_TPL = """\
# テーマ
{theme}

# タスク
上記テーマについて、事例を {n} 件ピックアップして深くリサーチしてください。
過去 1〜2 年の事例を中心に、各事例ごとに複数の一次情報源を当たってください。

各事例について次の項目を埋めます（submit_findings スキーマ参照）：
- title / subtitle / organization / year / url / link_text / image_url
- overview（**最大 4 個 / 各 40〜60 文字**）: 何をするシステムか、入出力、対象タスク、規模、性能
- challenges（**最大 4 個 / 各 40〜60 文字**）: 既存技術の構造的限界、データ・物理・計算の制約
- solutions（**最大 4 個 / 各 40〜60 文字**）: 提案手法のキモ、技術要素、新規性

進行中は各ステップで『次に何をするか』を 1〜2 行で出力しながら進めてください。
"""


# ---------------------------------------------------------------------------
# Client-side tool implementations
# ---------------------------------------------------------------------------

def _fetch_url_impl(url: str, max_chars: int = 12000) -> str:
    """Fetch a URL and return cleaned text content."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; research-app/1.0)",
                "Accept": "text/html,application/xhtml+xml,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            ct = resp.headers.get("Content-Type", "").lower()
            data = resp.read(3_000_000)
    except Exception as e:
        return f"[fetch_url error] {type(e).__name__}: {e}"

    text = data.decode("utf-8", errors="ignore")

    if "html" in ct or "<html" in text[:1000].lower():
        text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html_mod.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n…[truncated; total {len(text)} chars]"
    return text or "(本文を抽出できませんでした)"


def _arxiv_search_impl(query: str, max_results: int = 10) -> str:
    try:
        papers = arxiv_search.search(query, max_results=max_results)
    except Exception as e:
        return f"[arxiv_search error] {type(e).__name__}: {e}"
    if not papers:
        return "(該当論文なし)"
    return "\n\n".join(p.to_context() for p in papers)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

def _tools(case_list_schema: dict, max_web_uses: int) -> list[dict]:
    return [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": max_web_uses,
        },
        {
            "name": "fetch_url",
            "description": (
                "指定 URL を取得し、本文テキスト（最大 12000 文字）を返す。"
                "検索結果の中で有望なページの内容を実際に読みたいときに使う。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "取得対象の URL"},
                },
                "required": ["url"],
            },
        },
        {
            "name": "arxiv_search",
            "description": (
                "arXiv API を直接検索し、関連論文の abstract / 著者 / URL を返す。"
                "web_search だけでは漏れる学術論文を狙うときに使う。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "検索クエリ（英語推奨）"},
                    "max_results": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        },
        {
            "name": "submit_findings",
            "description": (
                "リサーチを完了し、構造化された事例リスト (CaseList) を提出する。"
                "これを呼ぶとリサーチは終了する。情報が十分集まってから呼ぶこと。"
            ),
            "input_schema": case_list_schema,
        },
    ]


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

@dataclass
class Progress:
    """Single step in the agent loop, surfaced to the UI."""

    iteration: int
    kind: str  # "think" / "web_search" / "fetch" / "arxiv" / "submit" / "nudge"
    detail: str


ProgressCb = Callable[[Progress], None]


def deep_research(
    client: anthropic.Anthropic,
    theme: str,
    n_cases: int = 5,
    model: str = DEFAULT_MODEL,
    max_iters: int = 15,
    max_web_uses: int = 25,
    on_progress: ProgressCb | None = None,
) -> tuple[CaseList, list]:
    """Run a multi-turn agent loop that does deep research on `theme`.

    Returns (CaseList, list_of_usage_per_turn).
    """
    schema = CaseList.model_json_schema()
    tools = _tools(schema, max_web_uses)

    messages: list[dict] = [
        {"role": "user", "content": INITIAL_PROMPT_TPL.format(theme=theme, n=n_cases)}
    ]
    usages: list = []

    def emit(p: Progress) -> None:
        if on_progress is not None:
            try:
                on_progress(p)
            except Exception:
                pass

    for i in range(max_iters):
        resp = client.messages.create(
            model=model,
            max_tokens=16000,
            system=DEEP_SYSTEM,
            tools=tools,
            messages=messages,
        )
        usages.append(resp.usage)
        messages.append({"role": "assistant", "content": resp.content})

        text_chunks: list[str] = []
        client_tool_uses: list = []
        web_search_count = 0

        for block in resp.content:
            t = getattr(block, "type", "")
            if t == "text":
                if block.text.strip():
                    text_chunks.append(block.text.strip())
            elif t == "tool_use":
                if block.name in ("fetch_url", "arxiv_search", "submit_findings"):
                    client_tool_uses.append(block)
            elif t == "server_tool_use":
                if getattr(block, "name", "") == "web_search":
                    web_search_count += 1
                    q = (getattr(block, "input", {}) or {}).get("query", "")
                    emit(Progress(i, "web_search", q))

        if text_chunks:
            emit(Progress(i, "think", " ".join(text_chunks)[:400]))

        # Final submission?
        for tu in client_tool_uses:
            if tu.name == "submit_findings":
                emit(Progress(i, "submit", f"事例 {len(tu.input.get('cases', []))} 件を提出"))
                return CaseList.model_validate(tu.input), usages

        # Other client-side tool calls
        tool_results: list[dict] = []
        for tu in client_tool_uses:
            if tu.name == "fetch_url":
                url = (tu.input or {}).get("url", "")
                emit(Progress(i, "fetch", url))
                content = _fetch_url_impl(url)
            elif tu.name == "arxiv_search":
                q = (tu.input or {}).get("query", "")
                mr = (tu.input or {}).get("max_results", 10)
                emit(Progress(i, "arxiv", q))
                content = _arxiv_search_impl(q, mr)
            else:
                continue
            tool_results.append(
                {"type": "tool_result", "tool_use_id": tu.id, "content": content}
            )

        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        elif resp.stop_reason == "end_turn":
            # Model finished without submitting; nudge once.
            emit(Progress(i, "nudge", "submit_findings の呼び出しを促進"))
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "情報が十分なら submit_findings を呼んで完了してください。"
                        "不足があれば追加で web_search / fetch_url / arxiv_search を使ってください。"
                    ),
                }
            )
        else:
            # Tool use without client-side tools (e.g. web_search only) — continue.
            messages.append(
                {"role": "user", "content": "次のステップを実行してください。"}
            )

    # Max iterations reached without submission — force a final submit.
    emit(Progress(max_iters, "submit", "強制提出（max_iters 到達）"))
    messages.append(
        {
            "role": "user",
            "content": (
                "リサーチを終了します。ここまでに集めた情報のみを使い、"
                "submit_findings を必ず呼んで結果を提出してください。"
            ),
        }
    )
    resp = client.messages.create(
        model=model,
        max_tokens=16000,
        system=DEEP_SYSTEM,
        tools=tools,
        tool_choice={"type": "tool", "name": "submit_findings"},
        messages=messages,
    )
    usages.append(resp.usage)
    for block in resp.content:
        if getattr(block, "type", "") == "tool_use" and block.name == "submit_findings":
            return CaseList.model_validate(block.input), usages
    raise RuntimeError("submit_findings が呼ばれませんでした（強制提出も失敗）。")
