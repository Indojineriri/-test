"""Optional AI-assisted rule generation.

Hand-authoring accurate rules for hundreds of games is impractical, so this
helper lets you type a game name and have Claude fill in the structured rule
fields. It is entirely optional: the app works without an API key, this is
only used by the "AIでルールを生成" button on the add-game form.
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
    "あなたはボードゲームに精通した専門家です。指定されたボードゲームについて、"
    "事実に基づき正確に、日本語で構造化された解説を作成します。"
    "知らないゲームや不確かな点は推測で断定せず、その旨を記してください。"
)

INSTRUCTION = (
    "次のボードゲームについて、構造化データを作成してください。\n"
    "ゲーム名: {name}\n\n"
    "- game_type: 主要なゲームメカニクス/ジャンル（例: ワーカープレイスメント, デッキ構築, "
    "正体隠匿, タイル配置, 競り, 協力 など）\n"
    "- objective: このゲームの目的（何を目指すか）\n"
    "- characters: 登場人物・役職・コマなど（無ければ「特になし」）\n"
    "- procedure: 1手番/1ラウンドの大まかな流れを1行1ステップの箇条書き（改行区切り、簡潔に）\n"
    "- detailed_rules: セットアップ・各アクションの詳細・特殊ケース・例外処理などを含む"
    "詳しい解説（段落や箇条書きを使った長めの文章。procedureの要点を肉付けする）\n"
    "- end_condition: 終了条件と勝敗の決め方\n"
    "- min_players / max_players: 推奨プレイ人数\n"
    "- play_time_min: 標準的なプレイ時間（分）\n"
    "- difficulty: 難易度 1(軽い)〜5(重い)\n"
    "- description: 1〜2文の概要"
)


class GeneratedRules(BaseModel):
    game_type: str
    objective: str
    characters: str
    procedure: str
    detailed_rules: str
    end_condition: str
    min_players: int
    max_players: int
    play_time_min: int
    difficulty: int
    description: str


def is_available() -> bool:
    return anthropic is not None and bool(os.getenv("ANTHROPIC_API_KEY"))


def generate(name: str) -> dict:
    """Return a dict of rule fields for the named game.

    Raises RuntimeError with a readable message if generation is unavailable
    or fails, so the caller can show it to the user.
    """
    if anthropic is None:
        raise RuntimeError("anthropic ライブラリが未インストールです。")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY が未設定です。")

    client = anthropic.Anthropic()
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM,
        messages=[{"role": "user", "content": INSTRUCTION.format(name=name)}],
        output_format=GeneratedRules,
    )
    out = resp.parsed_output
    return out.model_dump()
