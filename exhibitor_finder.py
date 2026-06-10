"""出展企業の関連抽出段（Claude による意味的スコアリング）。

杓子定規なキーワード一致ではなく、技術的な隣接性・応用可能性まで踏まえて
各社が指定テーマにどの程度関連するかを Claude に判定させる。各社×各テーマで
0-100 のスコアと根拠・該当シグナルを返し、ランキング化できるようにする。

クライアント生成・エラー整形は既存の claude_client を再利用する。
"""

from __future__ import annotations

import anthropic
from pydantic import BaseModel, Field

# 既定テーマ。name に加えて desc を与え、Claude が「意味的な隣接」を
# 判断できるよう技術的な射程を明示する。UI 側で増減・自由入力できる。
DEFAULT_THEMES: list[dict[str, str]] = [
    {
        "name": "製薬向けロボティクス",
        "desc": "医薬・バイオ・ライフサイエンス分野でのロボット応用。無菌/アイソレータ、"
        "ラボ自動化、分注・検体ハンドリング、GMP対応、クリーン環境での搬送・組立など。",
    },
    {
        "name": "エンドエフェクタ",
        "desc": "ロボットアーム先端のツール全般。ロボットハンド/グリッパ、吸着・把持機構、"
        "ツールチェンジャ、ばら積みピッキング向けの把持ソリューションなど。",
    },
    {
        "name": "力覚センサ",
        "desc": "力/トルクセンサ、6軸力覚センサ、触覚センシング、力制御・"
        "コンプライアンス制御、嵌合・研磨・組立での力フィードバック。",
    },
    {
        "name": "VLA (Vision-Language-Action)",
        "desc": "視覚・言語・行動を統合する基盤モデル、汎用ロボット学習、"
        "マニピュレーション基盤モデル、自然言語指示でのロボット操作。",
    },
    {
        "name": "Sim2Real",
        "desc": "シミュレーション学習から実機への転移、強化学習、デジタルツイン、"
        "Isaac Sim / Gazebo 等を用いたロボット動作の事前学習・検証。",
    },
]

SYSTEM_PROMPT = (
    "あなたはロボティクス分野に精通したテクノロジースカウトです。展示会の出展企業情報を読み、"
    "指定された技術テーマへの関連度を評価します。重要なのは、単なるキーワードの字面一致ではなく、"
    "その企業の技術・製品が各テーマに技術的にどれだけ隣接し、応用・転用できるかという"
    "『意味的な関連』を見抜くことです。たとえばテーマ語そのものを謳っていなくても、"
    "基盤技術や顧客用途から実質的に関連すると判断できる場合は、その根拠を明示した上で評価します。"
    "ただし関連の薄いものを過大評価せず、根拠は出展情報の記述に基づいて述べてください。"
)


class ThemeScore(BaseModel):
    theme: str = Field(description="評価対象テーマ名（入力のテーマ名と一致させる）")
    score: int = Field(description="関連度 0-100。字面一致だけでなく技術的隣接・応用可能性も加味")
    rationale: str = Field(description="そのスコアにした根拠を日本語で簡潔に")
    signals: list[str] = Field(
        default_factory=list,
        description="判断の手がかりになった出展情報中の具体的な語・記述（原文寄り）",
    )


class ExhibitorAssessment(BaseModel):
    company: str
    summary: str = Field(description="この企業が何をしているかの1-2文要約")
    top_theme: str = Field(description="最も関連度の高いテーマ名")
    max_score: int = Field(description="全テーマ中の最高スコア")
    theme_scores: list[ThemeScore]


def _themes_block(themes: list[dict[str, str]]) -> str:
    lines = ["# 評価対象テーマ"]
    for i, t in enumerate(themes, 1):
        desc = t.get("desc", "")
        lines.append(f"{i}. {t['name']}" + (f" — {desc}" if desc else ""))
    return "\n".join(lines)


def assess_exhibitor(
    client: anthropic.Anthropic,
    exhibitor: dict,
    themes: list[dict[str, str]],
    model: str,
    max_chars: int = 6000,
) -> tuple[ExhibitorAssessment, object]:
    """1 社を全テーマで評価して構造化結果を返す。

    exhibitor は scraper の出力 dict（name / raw_text / categories / website 等）。
    """
    name = exhibitor.get("name", "(社名不明)")
    cats = exhibitor.get("categories") or []
    body = (exhibitor.get("raw_text") or exhibitor.get("description") or "").strip()
    if len(body) > max_chars:
        body = body[:max_chars] + "\n…(以下省略)"

    instruction = (
        f"{_themes_block(themes)}\n\n"
        "# 出展企業情報\n"
        f"企業名: {name}\n"
        + (f"製品分類: {', '.join(cats)}\n" if cats else "")
        + (f"会社サイト: {exhibitor['website']}\n" if exhibitor.get("website") else "")
        + f"出展内容・説明:\n{body or '(説明テキストなし)'}\n\n"
        "上記企業について、各テーマへの関連度を 0-100 で評価してください。"
        "theme_scores には入力した全テーマ分を必ず含め、theme 名は入力と一致させてください。"
        "max_score / top_theme は theme_scores の中で最大のものに合わせてください。"
    )
    response = client.messages.parse(
        model=model,
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": instruction}],
        output_format=ExhibitorAssessment,
    )
    return response.parsed_output, response.usage


def assess_many(
    client: anthropic.Anthropic,
    exhibitors: list[dict],
    themes: list[dict[str, str]],
    model: str,
    progress=None,
):
    """複数社を順に評価。progress(done, total, name) で進捗通知可能。

    1 社の失敗で全体を止めないよう、例外は (exhibitor, error) として収集する。
    戻り値: (results: list[ExhibitorAssessment], errors: list[tuple[dict, str]])
    """
    results: list[ExhibitorAssessment] = []
    errors: list[tuple[dict, str]] = []
    total = len(exhibitors)
    for i, ex in enumerate(exhibitors, 1):
        try:
            assessment, _ = assess_exhibitor(client, ex, themes, model)
            results.append(assessment)
        except Exception as e:  # noqa: BLE001
            errors.append((ex, str(e)))
        if progress:
            progress(i, total, ex.get("name", "?"))
    results.sort(key=lambda a: a.max_score, reverse=True)
    return results, errors
