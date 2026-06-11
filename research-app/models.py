from __future__ import annotations

from pydantic import BaseModel, Field


class Case(BaseModel):
    """One research case row, used for both CSV and PPT export."""

    title: str = Field(description="事例の短いタイトル。例: '巨大動画モデル/世界モデル： Cosmos Predict'")
    subtitle: str = Field(description="1〜2文の要約。スライド上部の帯に入る一文。")
    headline: str = Field(
        default="",
        description=(
            "スライド中央の見出しバナーに入る短い文字列。"
            "『〇〇 × 技術名』形式が望ましい。例: '1X Technologies NEO × Cosmos Predict'。"
            "空なら organization + title から自動生成される。"
        ),
    )
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


class VendorCase(BaseModel):
    """One vendor research case row for vendor-focused slides."""

    company: str = Field(description="出展者・企業名。例: '株式会社モーションリブ'")
    product: str = Field(description="製品名・サービス名。例: 'SPX4(仮称)'")
    summary: str = Field(
        description=(
            "スライド上部の 1〜2 文の要約。"
            "『{社名}の「{製品名}」は…』の文に続く本文として使われる。"
        ),
    )
    features: list[str] = Field(
        description="製品の特長。最大 3 個、各 40〜70 文字の箇条書き。"
    )
    problems_solved: list[str] = Field(
        description="解決する課題。最大 3 個、各 30〜50 文字の箇条書き。"
    )
    use_cases: list[str] = Field(
        description=(
            "活用例。最大 3 個。各項目は『見出し: 説明』形式が望ましい。"
            "例: '品質・耐久試験: ボタンの押し込みや扉の開閉など、実機を操作するような繰り返しテスト'"
        ),
    )
    url: str = Field(description="ベンダーの一次情報源 URL（公式ページ、製品ページ、プレスリリース等）。")
    image_url: str | None = Field(default=None, description="製品画像の URL。og:image を優先。")
    references: list[str] = Field(
        default_factory=list,
        description=(
            "スライド下部に『参考リンク』として表示する補足 URL のリスト。"
            "最大 3 件。製品ページ、技術ホワイトペーパー、デモ動画、"
            "プレスリリース等から最も有益なものを選ぶ。"
        ),
    )
    focus_tech: str | None = Field(
        default=None,
        description="ユーザーが指定した注目技術（あれば）。リサーチの焦点として記録。",
    )


class VendorCaseList(BaseModel):
    vendors: list[VendorCase]
