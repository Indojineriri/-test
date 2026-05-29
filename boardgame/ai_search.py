"""AI-powered natural-language search over the catalog.

Lets users type free-form queries like "カタンと似た交渉ゲーム" or
"初心者向けの短時間で遊べる協力ゲーム". We hand Claude a compact catalog
(id + key attributes) and ask it to return the most relevant game ids with a
one-line reason for each.

Optional: requires ANTHROPIC_API_KEY. When unavailable the caller falls back to
the normal keyword/attribute filtering.
"""
from __future__ import annotations

import os

try:
    import anthropic
    from pydantic import BaseModel
except Exception:  # pragma: no cover - anthropic is optional
    anthropic = None
    BaseModel = object  # type: ignore


MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7")

SYSTEM = (
    "あなたはボードゲームに精通したレコメンド担当です。ユーザーの自由記述の要望を読み取り、"
    "与えられたゲーム一覧の中から最も合致するものを選びます。"
    "一覧に存在するゲームのidだけを返し、存在しないゲームを創作してはいけません。"
    "『○○と似た』という要望では、基準のゲーム自身は除外し、メカニクス・人数・"
    "テーマ・重さが近いものを選んでください。関連が薄い場合は無理に多く選ばず、"
    "本当に合うものだけを返してください。"
)


class Hit(BaseModel):
    id: int
    reason: str  # 日本語で1行、なぜ合うか


class SearchResult(BaseModel):
    interpretation: str  # クエリをどう解釈したかの一言
    hits: list[Hit]


def is_available() -> bool:
    return anthropic is not None and bool(os.getenv("ANTHROPIC_API_KEY"))


def _catalog_lines(games) -> str:
    """One compact line per game so the whole catalog fits cheaply in context."""
    lines = []
    for g in games:
        lines.append(
            f"id={g.id} | {g.name}({g.name_en}) | type={g.game_type} | "
            f"players={g.min_players}-{g.max_players} | time={g.play_time_min}min | "
            f"difficulty={g.difficulty}/5 | tags={g.tags} | {g.description}"
        )
    return "\n".join(lines)


def search(query: str, games, limit: int = 12) -> SearchResult:
    """Return AI-ranked hits for a natural-language query.

    `games` is the full list of Game rows. Raises RuntimeError with a readable
    message when AI search is unavailable so the caller can fall back.
    """
    if anthropic is None:
        raise RuntimeError("anthropic ライブラリが未インストールです。")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY が未設定です。")

    catalog = _catalog_lines(games)
    instruction = (
        "# ゲーム一覧（この中からのみ選ぶこと）\n"
        f"{catalog}\n\n"
        "# ユーザーの要望\n"
        f"{query}\n\n"
        f"上記の要望に最も合うゲームを、関連度の高い順に最大{limit}件まで選び、"
        "それぞれに日本語で簡潔な推薦理由を付けてください。"
    )
    client = anthropic.Anthropic()
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=1500,
        system=SYSTEM,
        messages=[{"role": "user", "content": instruction}],
        output_format=SearchResult,
    )
    return resp.parsed_output
