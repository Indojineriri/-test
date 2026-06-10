"""出展企業の関連抽出段（Claude による意味的スコアリング）。

ユーザーが自由記述で書いた「探しているもの」に、各出展企業がどれだけ近いかを
Claude に判定させる。杓子定規なキーワード一致ではなく、ユーザーの意図（用途・
技術・応用領域）への意味的・技術的な近さで 0-100 のスコアと根拠・手がかりを返す。

クライアント生成・エラー整形は既存の claude_client を再利用する。
"""

from __future__ import annotations

import random
import time

import anthropic
from pydantic import BaseModel, Field

SYSTEM_PROMPT = (
    "あなたはロボティクス分野に精通したテクノロジースカウトです。展示会の出展企業情報を読み、"
    "ユーザーが自由記述で書いた『探しているもの』に、その企業がどれだけ近いかを評価します。"
    "重要なのは単なるキーワードの字面一致ではなく、ユーザーの意図（用途・技術・応用領域・狙い）に"
    "意味的・技術的にどれだけ合致するか、隣接・応用できるかを見抜くことです。"
    "ユーザーの記述語そのものを謳っていなくても、基盤技術や顧客用途から実質的に近いと"
    "判断できる場合は、その根拠を明示した上で評価します。"
    "ただし関連の薄いものを過大評価せず、根拠は出展情報の記述に基づいて述べてください。"
)


class ExhibitorMatch(BaseModel):
    company: str
    summary: str = Field(description="この企業が何をしているかの1-2文要約")
    score: int = Field(
        description="ユーザーが探しているものへの近さ 0-100。字面一致ではなく意図への合致度"
    )
    rationale: str = Field(description="なぜ近い/遠いかの根拠を日本語で簡潔に")
    signals: list[str] = Field(
        default_factory=list,
        description="判断の手がかりになった出展情報中の具体的な語・記述（原文寄り）",
    )


def _is_transient(e: Exception) -> bool:
    """一時的（再試行で回復しうる）エラーかどうか。"""
    if isinstance(
        e,
        (
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        ),
    ):
        return True
    if isinstance(e, anthropic.APIStatusError):
        return e.status_code in (408, 409, 425, 429, 500, 502, 503, 504, 529)
    return False


def _call_with_retries(fn, attempts: int = 5, base: float = 1.5, cap: float = 30.0):
    """一時的エラーに対し指数バックオフで再試行。最終失敗時は例外を送出。"""
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if i == attempts - 1 or not _is_transient(e):
                raise
            time.sleep(min(cap, base * (2 ** i)) + random.uniform(0, 0.5))


def exhibitor_key(ex: dict) -> str:
    """出展企業を一意に識別するキー（再開・キャッシュ照合用）。"""
    if ex.get("charge_no") is not None:
        return f"charge:{ex['charge_no']}"
    return f"name:{ex.get('name', '')}"


def assessment_to_record(key: str, a: ExhibitorMatch) -> dict:
    """キャッシュ行（JSON 1 行）に変換。"""
    return {"key": key, "data": a.model_dump()}


def assessment_from_record(rec: dict) -> tuple[str, "ExhibitorMatch"]:
    return rec["key"], ExhibitorMatch.model_validate(rec["data"])


def assess_exhibitor(
    client: anthropic.Anthropic,
    exhibitor: dict,
    query: str,
    model: str,
    max_chars: int = 6000,
) -> tuple[ExhibitorMatch, object]:
    """1 社が、ユーザーの自由記述（探しているもの）にどれだけ近いかを評価する。

    exhibitor は scraper の出力 dict（name / raw_text / categories / website 等）。
    """
    name = exhibitor.get("name", "(社名不明)")
    cats = exhibitor.get("categories") or []
    body = (exhibitor.get("raw_text") or exhibitor.get("description") or "").strip()
    if len(body) > max_chars:
        body = body[:max_chars] + "\n…(以下省略)"

    instruction = (
        "# ユーザーが探しているもの（自由記述）\n"
        f"{query.strip()}\n\n"
        "# 出展企業情報\n"
        f"企業名: {name}\n"
        + (f"製品分類: {', '.join(cats)}\n" if cats else "")
        + (f"会社サイト: {exhibitor['website']}\n" if exhibitor.get("website") else "")
        + f"出展内容・説明:\n{body or '(説明テキストなし)'}\n\n"
        "この企業が上記『探しているもの』にどれだけ近いかを score(0-100) で評価してください。"
        "字面一致ではなく、ユーザーの意図（用途・技術・応用領域）への意味的な合致で判断し、"
        "rationale に根拠、signals に出展情報中の手がかりとなった語・記述を入れてください。"
    )
    response = _call_with_retries(
        lambda: client.messages.parse(
            model=model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": instruction}],
            output_format=ExhibitorMatch,
        )
    )
    return response.parsed_output, response.usage


def assess_many(
    client: anthropic.Anthropic,
    exhibitors: list[dict],
    query: str,
    model: str,
    on_result=None,
    on_error=None,
    progress=None,
):
    """複数社を順に評価。1 社完了ごとに on_result(key, exhibitor, match) を呼ぶので、
    呼び出し側はそこで逐次保存できる（途中で中断されても完了分は残る）。

    - on_error(exhibitor, error_str): 1 社失敗時。失敗しても全体は止めない。
    - progress(done, total, name): 進捗通知。
    戻り値: (results, errors)
    """
    results: list[ExhibitorMatch] = []
    errors: list[tuple[dict, str]] = []
    total = len(exhibitors)
    for i, ex in enumerate(exhibitors, 1):
        try:
            match, _ = assess_exhibitor(client, ex, query, model)
            results.append(match)
            if on_result:
                on_result(exhibitor_key(ex), ex, match)
        except Exception as e:  # noqa: BLE001
            errors.append((ex, str(e)))
            if on_error:
                on_error(ex, str(e))
        if progress:
            progress(i, total, ex.get("name", "?"))
    results.sort(key=lambda a: a.score, reverse=True)
    return results, errors
