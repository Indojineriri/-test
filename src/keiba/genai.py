"""④⑤ 生成AIによる示唆出し + 予想（2段エージェント方式）。

ユーザ要望:
  「過去レースを生成AIで示唆を出した上で、その示唆から勝てそうな馬を推定する」

2段構成:
  Stage1 derive_insights : 過去ダービー（各馬のレース前指標 + 実着順）を Claude に渡し、
      「ダービーでどんな馬が好走するか」の転用可能な示唆(ルール)を導出させる。
  Stage2 apply_insights  : その示唆と、今年の出走馬の指標表を渡し、各馬を評価して
      勝てそうな馬（複勝圏候補）を根拠つきで推定させる。

設計（claude-api スキルのベストプラクティス）:
  - モデルは claude-opus-4-8、adaptive thinking。
  - 過去レースの大きな文脈は安定プレフィックスにして prompt caching（cache_control）。
  - 出力は structured outputs（messages.parse + pydantic）でスキーマ固定。
  - ネットワーク不要の「プロンプト組み立て」関数と、API 呼び出しを分離（テスト可能）。

このモジュールの API 呼び出しには ANTHROPIC_API_KEY が必要。プロンプト組み立てと
データ整形はオフラインでテストできる。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from .ml import build_features_for_context, PIT_FEATURES
from .schema import RaceContext

MODEL = "claude-opus-4-8"

# 人が読んで意味の分かる指標だけをプロンプトに載せる（トークン節約・解釈性）
_PROMPT_FEATURES = [
    ("pit_starts", "出走数"),
    ("pit_win_rate", "勝率"),
    ("pit_show_rate", "複勝率"),
    ("pit_avg_finish", "平均着順"),
    ("pit_avg_finish_last3", "近3走平均着順"),
    ("pit_best_last3f", "最速上り3F"),
    ("pit_max_grade_win", "最高勝鞍格(5=G1..1=条件)"),
    ("pit_total_prize", "総賞金(万)"),
    ("pit_running_style", "脚質"),
    ("pit_days_since_last", "前走間隔(日)"),
]


# --- 構造化出力スキーマ ------------------------------------------------------

class Insight(BaseModel):
    pattern: str = Field(description="ダービーで好走する馬の特徴・傾向（1文）")
    rationale: str = Field(description="過去データのどこからそう言えるかの根拠")
    weight: float = Field(description="重要度 0.0〜1.0", ge=0.0, le=1.0)


class DerbyInsights(BaseModel):
    summary: str = Field(description="過去ダービーから読み取れる全体傾向の要約")
    insights: list[Insight] = Field(description="転用可能な示唆のリスト")
    caveats: list[str] = Field(description="注意点・例外（データが少ない等）")


class HorsePick(BaseModel):
    horse_no: int = Field(description="馬番")
    horse_name: str
    score: float = Field(description="複勝期待度 0.0〜1.0", ge=0.0, le=1.0)
    matched_insights: list[str] = Field(description="この馬に当てはまった示唆")
    reason: str = Field(description="推定理由（1〜2文）")


class Prediction(BaseModel):
    honmei_horse_no: int = Field(description="◎本命の馬番")
    ranking: list[HorsePick] = Field(description="複勝期待度の高い順")
    commentary: str = Field(description="レース全体の見立て（数文）")


# --- データ → テキスト整形 --------------------------------------------------

def _entrants_table(ctx: RaceContext, with_result: pd.DataFrame | None = None) -> str:
    """対象レースの出走馬のレース前指標を、行テキストに整形して返す。

    with_result（horse_id,finish_pos）があれば実着順も付ける（過去レースの学習素材用）。
    """
    feat = build_features_for_context(ctx)
    tgt = feat[feat["is_target"].fillna(False).astype(bool)].copy()
    tgt["horse_id"] = tgt["horse_id"].astype(str)
    if "horse_no" in tgt:
        tgt = tgt.sort_values("horse_no")

    result_map = {}
    if with_result is not None:
        r = with_result.copy()
        r["horse_id"] = r["horse_id"].astype(str)
        result_map = dict(zip(r["horse_id"], r["finish_pos"]))

    lines = []
    for _, row in tgt.iterrows():
        cells = []
        for col, label in _PROMPT_FEATURES:
            val = row.get(col)
            if isinstance(val, float):
                if np.isnan(val):
                    val = "-"
                elif col in ("pit_win_rate", "pit_show_rate"):
                    val = f"{val*100:.0f}%"
                else:
                    val = f"{val:.1f}"
            cells.append(f"{label}={val}")
        head = f"馬番{_to_int(row.get('horse_no'))} {row.get('horse_name','')}"
        if result_map:
            fin = result_map.get(str(row["horse_id"]))
            head += f"  →実着順:{_to_int(fin)}"
        lines.append(head + " | " + " / ".join(cells))
    return "\n".join(lines)


def build_insight_prompt(past_items: list[tuple[RaceContext, pd.DataFrame]]) -> str:
    """過去ダービー群を、示唆導出用のテキストにまとめる（安定プレフィックス）。"""
    parts = ["# 過去の日本ダービー（各馬のレース前時点の指標 → 実際の着順）\n"]
    for ctx, actual in past_items:
        parts.append(f"\n## {ctx.race_name}（{ctx.race.get('date','')}）")
        parts.append(_entrants_table(ctx, with_result=actual))
    return "\n".join(parts)


def build_application_prompt(ctx: RaceContext) -> str:
    """今年の出走馬の指標表を、予想適用用のテキストにまとめる。"""
    return (f"# 予想対象: {ctx.race_name}（{ctx.race.get('date','')}）\n"
            f"## 出走馬のレース前指標（着順は未確定）\n"
            + _entrants_table(ctx))


# --- 生成AI 呼び出し ---------------------------------------------------------

_INSIGHT_SYSTEM = (
    "あなたは競馬データ分析の専門家です。日本ダービー(東京・芝2400m・3歳GI)の"
    "過去結果を、各馬のレース前時点の指標とともに分析し、"
    "「どんな指標を持つ馬が好走しやすいか」という転用可能な示唆を導きます。"
    "データに基づき、断定しすぎず、根拠を明示してください。"
)

_APPLY_SYSTEM = (
    "あなたは競馬予想の専門家です。与えられた『過去傾向の示唆』を、"
    "今年の出走馬のレース前指標に当てはめ、複勝圏(3着内)に入りそうな馬を"
    "根拠つきで推定します。示唆と各馬の指標を照合し、なぜその馬かを説明してください。"
)


def derive_insights(client, past_items: list[tuple[RaceContext, pd.DataFrame]],
                    model: str = MODEL) -> DerbyInsights:
    """Stage1: 過去ダービーから示唆を導出する（structured output）。"""
    context_text = build_insight_prompt(past_items)
    resp = client.messages.parse(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=[{"type": "text", "text": _INSIGHT_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": [
            # 大きい過去データは安定プレフィックス側にしてキャッシュ
            {"type": "text", "text": context_text,
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text":
             "上記の過去ダービーを分析し、好走馬の傾向(示唆)を導いてください。"},
        ]}],
        output_format=DerbyInsights,
    )
    return resp.parsed_output


def apply_insights(client, insights: DerbyInsights, ctx: RaceContext,
                   model: str = MODEL) -> Prediction:
    """Stage2: 示唆を今年の出走馬に当てはめて予想する（structured output）。"""
    insight_text = _format_insights_for_prompt(insights)
    entrants_text = build_application_prompt(ctx)
    resp = client.messages.parse(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=[{"type": "text", "text": _APPLY_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": [
            {"type": "text", "text": insight_text,
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": entrants_text},
            {"type": "text", "text":
             "示唆を各馬に照合し、複勝圏に入りそうな馬を期待度の高い順に並べ、"
             "本命(◎)を1頭選んでください。"},
        ]}],
        output_format=Prediction,
    )
    return resp.parsed_output


def _format_insights_for_prompt(insights: DerbyInsights) -> str:
    lines = ["# 過去ダービーから導いた示唆", f"全体傾向: {insights.summary}", ""]
    for i, ins in enumerate(insights.insights, 1):
        lines.append(f"{i}. [{ins.weight:.1f}] {ins.pattern} （根拠: {ins.rationale}）")
    if insights.caveats:
        lines.append("\n注意点:")
        lines += [f"  - {c}" for c in insights.caveats]
    return "\n".join(lines)


# --- 表示整形 ----------------------------------------------------------------

def format_insights(insights: DerbyInsights) -> str:
    lines = ["=== 【生成AI ①示唆出し】過去ダービーの傾向 ==="]
    lines.append(f"全体傾向: {insights.summary}\n")
    lines.append("◆ 導かれた示唆（重要度順）")
    for ins in sorted(insights.insights, key=lambda x: -x.weight):
        lines.append(f"  [{ins.weight:.1f}] {ins.pattern}")
        lines.append(f"        根拠: {ins.rationale}")
    if insights.caveats:
        lines.append("\n◆ 注意点")
        for c in insights.caveats:
            lines.append(f"  - {c}")
    return "\n".join(lines)


def format_prediction(pred: Prediction, race_name: str = "対象レース") -> str:
    lines = [f"=== 【生成AI ②予想】{race_name} ==="]
    lines.append(f"◎ 本命: 馬番{pred.honmei_horse_no}\n")
    lines.append("◆ 複勝期待度ランキング")
    marks = ["◎", "○", "▲", "△", "△"] + ["・"] * 20
    for mark, p in zip(marks, pred.ranking):
        lines.append(f"  {mark} 馬番{p.horse_no} {p.horse_name}（期待度 {p.score*100:.0f}%）")
        lines.append(f"      {p.reason}")
        if p.matched_insights:
            lines.append(f"      該当示唆: {', '.join(p.matched_insights)}")
    lines.append(f"\n◆ 見立て\n{pred.commentary}")
    return "\n".join(lines)


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return "-"
