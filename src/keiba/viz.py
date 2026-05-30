"""③可視化: 出走馬の分析を matplotlib でグラフ化し PNG バイト列で返す。

Cloud Run / CLI から使う。日本語フォントは IPAGothic / Noto Sans CJK を優先。
画像は PNG バイト列で返すので、web 層がそのまま image/png として配信できる。
"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")  # GUI 不要のバックエンド
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .analyze import analyze_entrants  # noqa: E402
from .schema import RaceContext  # noqa: E402

# 日本語フォントの設定（あるものを使う）
for _f in ["Noto Sans CJK JP", "IPAGothic", "IPAPGothic", "Hiragino Sans",
           "Yu Gothic", "Meiryo", "TakaoGothic"]:
    try:
        import matplotlib.font_manager as _fm
        if any(_f == f.name for f in _fm.fontManager.ttflist):
            matplotlib.rcParams["font.family"] = _f
            break
    except Exception:
        pass
matplotlib.rcParams["axes.unicode_minus"] = False


# 描けるチャートの種類
CHART_KINDS = ["show_rate", "prize", "last3f", "style"]


def render_chart(ctx: RaceContext, kind: str = "show_rate") -> bytes:
    """出走馬分析から指定種類のグラフを描き、PNG バイト列で返す。"""
    view = analyze_entrants(ctx)
    if view.empty:
        return _placeholder("データがありません")

    if kind == "show_rate":
        return _barh(view, "pit_show_rate", "複勝率 [%]",
                     f"{ctx.race_name}：出走馬の複勝率", color="#3b7dd8",
                     ascending=True)
    if kind == "prize":
        return _barh(view, "pit_total_prize", "総獲得賞金 [万円]",
                     f"{ctx.race_name}：総賞金", color="#d8a13b", ascending=True)
    if kind == "last3f":
        return _barh(view, "pit_best_last3f", "最速上がり3F [秒]（小さいほど良）",
                     f"{ctx.race_name}：決め手（最速上がり）", color="#46a06b",
                     ascending=False, invert=True)
    if kind == "style":
        return _style_pie(view, ctx.race_name)
    raise ValueError(f"未知のチャート種類: {kind}")


def _label(view):
    """馬名（無ければ馬番）のラベル列。"""
    if "horse_name" in view and view["horse_name"].notna().any():
        return view["horse_name"].fillna(view.get("horse_no", "").astype(str))
    return view.get("horse_no", view.index).astype(str)


def _barh(view, col, xlabel, title, color, ascending, invert=False) -> bytes:
    d = view.dropna(subset=[col]).copy()
    if d.empty:
        return _placeholder(f"{xlabel} のデータがありません")
    d = d.sort_values(col, ascending=ascending)
    labels = _label(d)
    fig, ax = plt.subplots(figsize=(8, max(3, 0.45 * len(d))))
    ax.barh(labels, d[col], color=color)
    ax.set_xlabel(xlabel)
    ax.set_title(title)
    if invert:
        ax.invert_xaxis()
    fig.tight_layout()
    return _to_png(fig)


def _style_pie(view, race_name) -> bytes:
    col = "pit_running_style"
    if col not in view:
        return _placeholder("脚質データがありません")
    counts = view[col].fillna("不明").value_counts()
    order = ["逃げ", "先行", "差し", "追込", "不明"]
    counts = counts.reindex([o for o in order if o in counts.index])
    colors = {"逃げ": "#d85b5b", "先行": "#d8a13b", "差し": "#3b7dd8",
              "追込": "#6b46a0", "不明": "#aaaaaa"}
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.pie(counts.values, labels=[f"{k}（{v}頭）" for k, v in counts.items()],
           colors=[colors.get(k, "#999") for k in counts.index],
           autopct="%1.0f%%", startangle=90)
    ax.set_title(f"{race_name}：脚質の分布")
    fig.tight_layout()
    return _to_png(fig)


def _placeholder(msg: str) -> bytes:
    fig, ax = plt.subplots(figsize=(6, 2))
    ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=14)
    ax.axis("off")
    return _to_png(fig)


def _to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()
