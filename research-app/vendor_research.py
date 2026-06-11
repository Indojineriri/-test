from __future__ import annotations

import html as html_mod
import re
import urllib.request
from dataclasses import dataclass
from typing import Callable

import anthropic

from models import VendorCaseList

DEFAULT_MODEL = "claude-opus-4-7"

VENDOR_SYSTEM = """\
あなたは企業ベンダーをリサーチするエージェントです。ユーザーが指定したベンダー
（と、必要に応じて注目したい技術）について、公式サイト・プレスリリース・製品ページを
中心に多段リサーチし、最終的に submit_vendors ツールで構造化された VendorCaseList を提出します。

# 進め方
1. 各ベンダーの公式ホームページを web_search で探す。
2. fetch_url で公式の製品ページ・プレスリリースを読み込み、製品の特長・解決する課題・活用例を整理する。
3. ユーザーが注目技術を指定している場合、その技術に該当する製品/サービスに絞る。
4. 各ベンダーごとに次の項目を埋める：
   - company / product / summary（『{社名}の「{製品名}」は…』に続く 1〜2 文の要約）
   - features（製品の特長、最大 3 個 / 各 40〜70 文字）
   - problems_solved（解決する課題、最大 3 個 / 各 30〜50 文字）
   - use_cases（活用例、最大 3 個、『見出し: 説明』形式が望ましい）
   - url（公式情報の URL）
   - image_url（製品画像があれば URL、なければ null）

# ルール
- 推測や創作は絶対にしない。情報がないなら『（情報なし）』と書く。
- 抽象的な決まり文句は禁止。具体的な数値・技術名・固有名詞を含める。
- 各ステップで『次に何をするか』を 1〜2 行で出力する。
- 十分集まったら submit_vendors を呼んで完了する。
"""

INITIAL_PROMPT_TPL = """\
# 調査対象ベンダーリスト
{vendor_list}

# タスク
上記の各ベンダーについて公式情報を中心にリサーチし、添付フォーマットに沿った
VendorCase を作成してください。注目技術が指定されているベンダーは、その技術に
関連する製品/サービスを優先して取り上げます。

進行中は各ステップで『次に何をするか』を 1〜2 行で出力しながら進めてください。
最後に submit_vendors ツールを呼んで完了します。
"""


def _fetch_url_impl(url: str, max_chars: int = 12000) -> str:
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


def _tools(schema: dict, max_web_uses: int) -> list[dict]:
    return [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": max_web_uses},
        {
            "name": "fetch_url",
            "description": "URL を取得し本文テキストを返す。公式ページの詳細を読むのに使う。",
            "input_schema": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
        {
            "name": "submit_vendors",
            "description": "リサーチを完了し VendorCaseList を提出する。これを呼ぶと終了する。",
            "input_schema": schema,
        },
    ]


@dataclass
class Progress:
    iteration: int
    kind: str
    detail: str


ProgressCb = Callable[[Progress], None]


def get_client(api_key: str | None = None) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def research_vendors(
    client: anthropic.Anthropic,
    vendors: list[tuple[str, str | None]],
    model: str = DEFAULT_MODEL,
    max_iters: int = 20,
    max_web_uses: int = 30,
    on_progress: ProgressCb | None = None,
) -> tuple[VendorCaseList, list]:
    """Run the vendor research agent loop.

    `vendors` is a list of (vendor_name, focus_tech_or_None).
    """
    schema = VendorCaseList.model_json_schema()
    tools = _tools(schema, max_web_uses)

    vendor_lines = "\n".join(
        f"- {name}" + (f" / 注目技術: {tech}" if tech else "")
        for name, tech in vendors
    )
    messages: list[dict] = [
        {"role": "user", "content": INITIAL_PROMPT_TPL.format(vendor_list=vendor_lines)}
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
            system=VENDOR_SYSTEM,
            tools=tools,
            messages=messages,
        )
        usages.append(resp.usage)
        messages.append({"role": "assistant", "content": resp.content})

        text_chunks: list[str] = []
        client_tool_uses: list = []
        for block in resp.content:
            t = getattr(block, "type", "")
            if t == "text" and block.text.strip():
                text_chunks.append(block.text.strip())
            elif t == "tool_use" and block.name in ("fetch_url", "submit_vendors"):
                client_tool_uses.append(block)
            elif t == "server_tool_use" and getattr(block, "name", "") == "web_search":
                q = (getattr(block, "input", {}) or {}).get("query", "")
                emit(Progress(i, "web_search", q))

        if text_chunks:
            emit(Progress(i, "think", " ".join(text_chunks)[:400]))

        for tu in client_tool_uses:
            if tu.name == "submit_vendors":
                emit(Progress(i, "submit", f"ベンダー {len(tu.input.get('vendors', []))} 件を提出"))
                return VendorCaseList.model_validate(tu.input), usages

        tool_results: list[dict] = []
        for tu in client_tool_uses:
            if tu.name == "fetch_url":
                url = (tu.input or {}).get("url", "")
                emit(Progress(i, "fetch", url))
                content = _fetch_url_impl(url)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": content}
                )

        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        elif resp.stop_reason == "end_turn":
            emit(Progress(i, "nudge", "submit_vendors の呼び出しを促進"))
            messages.append(
                {
                    "role": "user",
                    "content": "情報が十分なら submit_vendors を呼んで完了してください。",
                }
            )
        else:
            messages.append({"role": "user", "content": "次のステップを実行してください。"})

    # Force final submit.
    emit(Progress(max_iters, "submit", "強制提出（max_iters 到達）"))
    messages.append(
        {
            "role": "user",
            "content": "リサーチを終了します。submit_vendors を必ず呼んで結果を提出してください。",
        }
    )
    resp = client.messages.create(
        model=model,
        max_tokens=16000,
        system=VENDOR_SYSTEM,
        tools=tools,
        tool_choice={"type": "tool", "name": "submit_vendors"},
        messages=messages,
    )
    usages.append(resp.usage)
    for block in resp.content:
        if getattr(block, "type", "") == "tool_use" and block.name == "submit_vendors":
            return VendorCaseList.model_validate(block.input), usages
    raise RuntimeError("submit_vendors が呼ばれませんでした（強制提出も失敗）。")
