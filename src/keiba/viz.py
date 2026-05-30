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


# 描けるチャートの種類（ラベルは UI 表示用）
CHART_KINDS = ["show_rate", "prize", "last3f", "style", "corner",
               "starts", "style_last3f", "h2h"]
CHART_LABELS = {
    "show_rate": "複勝率",
    "prize": "総賞金",
    "last3f": "決め手（最速上がり3F）",
    "style": "脚質の分布",
    "corner": "脚質（平均通過順位）",
    "starts": "キャリア数（出走数）",
    "style_last3f": "脚質 × 上がり3F（2軸）",
    "h2h": "直接対決の序列",
}


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
    if kind == "corner":
        return _barh(view, "pit_avg_corner_pos", "平均通過順位（小さいほど前）",
                     f"{ctx.race_name}：脚質（平均通過順位）", color="#9a6ad8",
                     ascending=False, invert=True)
    if kind == "starts":
        return _barh(view, "pit_starts", "出走数（キャリア）",
                     f"{ctx.race_name}：キャリア数", color="#5b8fb0", ascending=True)
    if kind == "style_last3f":
        return _scatter_style_last3f(view, ctx.race_name)
    if kind == "h2h":
        return _h2h_chart(ctx)
    raise ValueError(f"未知のチャート種類: {kind}")


def _scatter_style_last3f(view, race_name) -> bytes:
    """横軸=平均通過順位(脚質)、縦軸=最速上がり3F(決め手) の2軸散布図。"""
    d = view.dropna(subset=["pit_avg_corner_pos", "pit_best_last3f"]).copy()
    if d.empty:
        return _placeholder("脚質×上がりのデータがありません")
    fig, ax = plt.subplots(figsize=(8, 6))
    x = d["pit_avg_corner_pos"].astype(float)
    y = d["pit_best_last3f"].astype(float)
    ax.scatter(x, y, s=80, color="#3b7dd8", alpha=0.8)
    for _, r in d.iterrows():
        ax.annotate(str(r.get("horse_name") or r.get("horse_no") or ""),
                    (r["pit_avg_corner_pos"], r["pit_best_last3f"]),
                    fontsize=8, xytext=(4, 2), textcoords="offset points")
    ax.set_xlabel("平均通過順位（左=前に行く／逃げ・先行）")
    ax.set_ylabel("最速上がり3F [秒]（下=速い＝決め手あり）")
    ax.set_title(f"{race_name}：脚質 × 決め手")
    ax.invert_yaxis()  # 上がりは小さいほど良いので下が優秀
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return _to_png(fig)


def _h2h_chart(ctx) -> bytes:
    """直接対決の勝率で並べた序列を横棒で表示（出走馬どうしの上下関係）。"""
    from .h2h import head_to_head
    h = head_to_head(ctx)
    rows = [(h["id2name"].get(hid, hid), h["meta"][hid]) for hid in h["order"]]
    rows = [(name, m) for name, m in rows if m["vs_count"] > 0]
    if not rows:
        return _placeholder("出走馬どうしの対戦データがありません")
    rows.reverse()  # 強い順を上に
    names = [r[0] for r in rows]
    wr = [r[1]["win_rate"] * 100 for r in rows]
    labels = [f'{n}（{m["wins"]}勝{m["losses"]}敗）' for n, m in rows]
    fig, ax = plt.subplots(figsize=(8, max(3, 0.5 * len(rows))))
    ax.barh(range(len(rows)), wr, color="#d8703b")
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(labels)
    ax.set_xlabel("直接対決の勝率 [%]")
    ax.set_xlim(0, 100)
    ax.axvline(50, color="#888", ls="--", lw=1)
    ax.set_title(f"{ctx.race_name}：出走馬どうしの直接対決（上ほど強い）")
    fig.tight_layout()
    return _to_png(fig)


def render_past_result(race_name: str, actual, highlight_top=3) -> bytes:
    """過去レースの着順を縦棒で表示し、3着以内(複勝圏)を色分けする。

    Args:
        race_name: レース名
        actual: 実結果 DataFrame（finish_pos, horse_name/horse_no を含む）
        highlight_top: 何着までを強調色にするか（既定 3 = 複勝圏）
    """
    import pandas as pd
    if actual is None or actual.empty or "finish_pos" not in actual:
        return _placeholder("結果データがありません")
    d = actual.dropna(subset=["finish_pos"]).copy()
    d["finish_pos"] = d["finish_pos"].astype(float)
    d = d.sort_values("finish_pos")
    name_col = "horse_name" if "horse_name" in d else "horse_id"
    labels = d[name_col].astype(str) if name_col in d else d.index.astype(str)
    pos = d["finish_pos"].values
    # 3着以内=金/銀/銅系、それ以外=灰
    medal = {1: "#e3b203", 2: "#9aa0a6", 3: "#b5651d"}
    colors = [medal.get(int(p), "#c9d2e0") if p <= highlight_top else "#dfe3ea"
              for p in pos]
    fig, ax = plt.subplots(figsize=(max(7, 0.55 * len(d)), 5))
    ax.bar(range(len(d)), pos, color=colors, edgecolor="#888")
    ax.set_xticks(range(len(d)))
    ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=8)
    ax.set_ylabel("着順（低いほど上位）")
    ax.invert_yaxis()  # 1着を上に
    ax.set_title(f"{race_name}：結果（金銀銅＝複勝圏 3着以内）")
    fig.tight_layout()
    return _to_png(fig)


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
