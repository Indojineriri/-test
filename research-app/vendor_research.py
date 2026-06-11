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
（と必要に応じて注目したい技術）について、複数の一次情報源から事実を裏取りし、
最終的に submit_vendors ツールで構造化された VendorCaseList を提出します。

# 進め方
1. 各ベンダーの公式ホームページを web_search で特定する。
2. **必ず 2 つ以上の一次情報源**を fetch_url で読み込む。優先度の高い順に：
   (a) 製品スペックページ／データシート／カタログ PDF
   (b) プレスリリース、ニュースルーム、最新の発表
   (c) 公式デモ動画、ホワイトペーパー、技術解説
   (d) 展示会レポート、業界メディアの一次取材記事
3. ユーザーが注目技術を指定している場合、その技術に該当する製品/サービスに絞る。
4. 読んだ情報を必ず**メモとして要約**してから、各事例の項目に落とし込む。
5. 各ベンダーごとに次の項目を埋める：
   - company / product / summary（『{社名}の「{製品名}」は…』に続く 1〜2 文の要約）
   - features（製品の特長、最大 3 個 / 各 40〜80 文字）
   - problems_solved（解決する課題、最大 3 個 / 各 30〜60 文字）
   - use_cases（活用例、最大 3 個、『見出し: 説明』形式が望ましい）
   - url / image_url

# 書き方のルール（守らないと品質が落ちます）
- **必ず数値・固有名詞・技術用語を含める**。抽象表現だけの文は禁止。
  - 良い例: "可搬重量 5〜25kg、リーチ 994mm、再現精度 ±0.04mm"
  - 悪い例: "高性能なロボットアーム"  ← 禁止
- **competitor との差分や、これまでにない点**を 1 文で言い切る。
  - 良い例: "従来のティーチングペンダント不要で、ボタン押下のみで動作記録が可能"
  - 悪い例: "使いやすいインターフェース"  ← 禁止
- **問題提起 → 解決**の構造で『解決する課題』を書く。単に課題名を並べない。
  - 良い例: "ティーチング工程の属人化 — 専門オペレーター不在の現場でも導入可能に"
  - 悪い例: "簡易化"  ← 禁止
- **活用例は「業界 / 工程 / 規模感」を必ず示す**。誰がどんな用途で使っているかを書く。
  - 良い例: "自動車 Tier1 サプライヤー: 樹脂部品のバリ取り工程に CRX-10iA を 8 台導入し、夜間無人稼働"
  - 悪い例: "製造業全般"  ← 禁止
- 推測や創作は絶対にしない。情報がない項目は『(情報なし)』とだけ書く。
- 公式が言っていない『業界初』『最高性能』など断定的キャッチコピーは避ける。
- 各ステップで『次に何をするか』を 1〜2 行で出力する。
- 全ベンダーで上記レベルの具体性が確保できてから submit_vendors を呼ぶ。
"""

INITIAL_PROMPT_TPL = """\
# 調査対象ベンダーリスト
{vendor_list}

# タスク
上記の各ベンダーについて、公式情報を中心に**最低 2 つの一次情報源**を fetch_url で
読み込み、添付フォーマットに沿った VendorCase を作成してください。
注目技術が指定されているベンダーは、その技術に関連する製品/サービスを優先します。

# 必達基準（これを満たさない場合 submit_vendors を呼ばないこと）
- 各 feature に具体的な数値（kg, mm, 秒, 円, % など）または固有名詞（モデル名、技術名、API 名）が
  最低 1 つは含まれている。
- 各 problem は『何が課題で、どう解決したか』の一行になっている。
- 各 use_case は『業界 / 工程 / 規模感』のいずれかを示し、抽象的でない。
- summary は競合と比べて何がユニークかが読み取れる 1〜2 文。

進行中は各ステップで『次に何をするか』を 1〜2 行で出力しながら進めてください。
全ベンダーが基準を満たしてから submit_vendors を呼んでください。
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
