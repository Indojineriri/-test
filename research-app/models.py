from __future__ import annotations

from pydantic import BaseModel, Field


class Case(BaseModel):
    """One research case row, used for both CSV and PPT export."""

    title: str = Field(description="事例の短いタイトル。例: '巨大動画モデル/世界モデル： Cosmos Predict'")
    subtitle: str = Field(description="1〜2文の要約。スライド上部の帯に入る一文。")
    organization: str = Field(description="発表元の組織名。例: '1X Technologies / NVIDIA'")
    year: int | None = Field(default=None, description="発表年（西暦）。不明なら None。")
    overview: list[str] = Field(description="『概要』欄の箇条書き。3〜5項目。")
    challenges: list[str] = Field(description="『なぜ難しいのか』欄の箇条書き。3〜5項目。")
    solutions: list[str] = Field(description="『解決した技術的課題』欄の箇条書き。3〜5項目。")
    url: str = Field(description="一次情報源の URL。論文、公式ブログ、デモ動画など。")
    link_text: str = Field(description="スライド下部に表示するリンクの表示文字列。")
    image_url: str | None = Field(
        default=None,
        description="事例ページの代表画像 URL（og:image 等）。取得できなければ None。",
    )


class CaseList(BaseModel):
    cases: list[Case]
