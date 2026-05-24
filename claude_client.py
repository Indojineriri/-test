from __future__ import annotations

from typing import Iterator

import anthropic
from pydantic import BaseModel

from ppt_parser import Slide

SYSTEM_PROMPT = (
    "あなたは企業の定例会議の資料作成を支援する優秀なアシスタントです。"
    "提供されたプレゼン資料(各スライドのテキストと画像)と、任意で提供される議事録を"
    "正確に読み取り、日本語で簡潔かつ論理的に回答します。事実に基づき、"
    "資料に存在しない情報を断定的に作り出さないでください。"
)

ANALYZE_INSTRUCTION = (
    "上記のプレゼン資料(および議事録があればそれ)の内容を理解し、"
    "次の構成で日本語のMarkdownにまとめてください。\n\n"
    "## 全体の要旨\n3〜5行で要約。\n\n"
    "## 各スライドの要点\nスライド番号ごとに箇条書き。\n\n"
    "## 想定される論点\n会議で議論すべきポイントの候補を箇条書き。\n\n"
    "冗長な前置きは不要です。"
)


class AgendaItem(BaseModel):
    title: str
    objective: str
    talking_points: list[str]
    duration_min: int


class MeetingMaterial(BaseModel):
    meeting_title: str
    overall_objective: str
    agenda: list[AgendaItem]
    key_messages: list[str]


def get_client(api_key: str | None = None) -> anthropic.Anthropic:
    # When api_key is None the SDK reads ANTHROPIC_API_KEY from the environment.
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def _deck_blocks(slides: list[Slide], minutes_text: str | None) -> list[dict]:
    """Stable, cacheable content blocks shared across analyze and generate."""
    blocks: list[dict] = []
    for s in slides:
        body = s.text.strip() or "(テキストなし)"
        blocks.append({"type": "text", "text": f"=== スライド {s.index} ===\n{body}"})
        if s.image_b64:
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": s.image_media_type,
                        "data": s.image_b64,
                    },
                }
            )
    if minutes_text and minutes_text.strip():
        blocks.append({"type": "text", "text": f"=== 議事録 ===\n{minutes_text.strip()}"})
    # Cache everything up to here (system + deck): the generate step reuses it.
    blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


def stream_analysis(
    client: anthropic.Anthropic,
    slides: list[Slide],
    minutes_text: str | None,
    model: str,
    usage_sink: dict,
) -> Iterator[str]:
    content = _deck_blocks(slides, minutes_text)
    content.append({"type": "text", "text": ANALYZE_INSTRUCTION})
    with client.messages.stream(
        model=model,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    ) as stream:
        for text in stream.text_stream:
            yield text
        usage_sink["usage"] = stream.get_final_message().usage


def generate_material(
    client: anthropic.Anthropic,
    slides: list[Slide],
    minutes_text: str | None,
    understanding: str,
    discussion_points: str,
    model: str,
) -> tuple[MeetingMaterial, object]:
    content = _deck_blocks(slides, minutes_text)
    instruction = (
        "定例会議用の資料を作成します。\n\n"
        "# ユーザーが入力した論点\n"
        f"{discussion_points}\n\n"
        "# これまでの資料理解\n"
        f"{understanding}\n\n"
        "上記のプレゼン資料・議事録、ユーザーの論点、資料理解を踏まえ、"
        "定例会議のアジェンダと、各アジェンダで伝えるべきメッセージを日本語で作成してください。"
        "talking_points は各アジェンダで話すべき要点や根拠、key_messages は会議全体で"
        "特に強調すべきメッセージを記載してください。"
    )
    content.append({"type": "text", "text": instruction})
    response = client.messages.parse(
        model=model,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        output_format=MeetingMaterial,
    )
    return response.parsed_output, response.usage


def material_to_markdown(m: MeetingMaterial) -> str:
    lines = [f"# {m.meeting_title}", "", f"**会議の目的**: {m.overall_objective}", "", "## アジェンダ"]
    for i, item in enumerate(m.agenda, 1):
        lines.append(f"### {i}. {item.title}（{item.duration_min}分）")
        lines.append(f"- **ねらい**: {item.objective}")
        for tp in item.talking_points:
            lines.append(f"  - {tp}")
    lines.append("")
    lines.append("## 強調すべきメッセージ")
    for km in m.key_messages:
        lines.append(f"- {km}")
    return "\n".join(lines)
