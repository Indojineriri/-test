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
                     ascending=False, invert=True, xlim=(30, 37))
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
    # 上がり3F は 33〜35秒台に集中するので、軸を 30〜37 に固定して差を見やすく。
    # データが範囲外なら自動調整にフォールバック。
    if y.between(29, 38).all():
        ax.set_ylim(37, 30)  # 反転（下=速い）
    else:
        ax.invert_yaxis()
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


def render_past_result(ctx, actual, indicator: str = "pit_total_prize") -> bytes:
    """過去レースの各馬を『指標』でプロットし、3着以内(複勝圏)を色分けする。

    着順そのものではなく、レース前の指標（賞金・複勝率・上がり等）を縦軸に取り、
    実際に 3 着以内に来た馬を強調色にする。これにより
    「どんな指標を持つ馬が好走したか（傾向）」が見える。

    Args:
        ctx: 過去レースの RaceContext（出走馬の指標を計算するのに使う）
        actual: 実結果（horse_id, finish_pos）
        indicator: 縦軸にする指標カラム（pit_total_prize / pit_show_rate /
                   pit_best_last3f / pit_avg_corner_pos など）
    """
    import numpy as np
    import pandas as pd
    from .ml import build_features_for_context

    if actual is None or actual.empty:
        return _placeholder("結果データがありません")

    feat = build_features_for_context(ctx)
    tgt = feat[feat["is_target"].fillna(False).astype(bool)].copy()
    tgt["horse_id"] = tgt["horse_id"].astype(str)
    # 対象行(entries)の finish_pos は未確定なので落とし、実結果を結合する
    tgt = tgt.drop(columns=["finish_pos"], errors="ignore")
    if indicator not in tgt.columns:
        return _placeholder(f"{indicator} のデータがありません")
    a = actual.copy()
    a["horse_id"] = a["horse_id"].astype(str)
    d = tgt.merge(a[["horse_id", "finish_pos"]], on="horse_id", how="inner")
    d = d.dropna(subset=["finish_pos", indicator])
    if d.empty:
        return _placeholder(f"{indicator} のデータがありません")
    d["finish_pos"] = d["finish_pos"].astype(float)
    d = d.sort_values("finish_pos")
    # 率系(0〜1)は % に直して見やすく
    if indicator in ("pit_show_rate", "pit_win_rate"):
        d[indicator] = d[indicator] * 100

    in3 = d["finish_pos"] <= 3
    label = CHART_LABELS_INDICATOR.get(indicator, indicator)

    fig, ax = plt.subplots(figsize=(8, 5.5))
    # X = 実着順、Y = 指標。3着内を色分け（金赤）・圏外を灰
    ax.scatter(d.loc[~in3, "finish_pos"], d.loc[~in3, indicator],
               s=70, color="#c9d2e0", edgecolor="#999", label="着外", zorder=2)
    ax.scatter(d.loc[in3, "finish_pos"], d.loc[in3, indicator],
               s=140, color="#d8703b", edgecolor="#7a3a16", label="3着以内（複勝圏）",
               zorder=3)
    # 馬名ラベル
    name_col = "horse_name" if "horse_name" in d else "horse_id"
    for _, r in d.iterrows():
        ax.annotate(str(r.get(name_col, "")), (r["finish_pos"], r[indicator]),
                    fontsize=7, xytext=(4, 2), textcoords="offset points")
    ax.axvline(3.5, color="#888", ls="--", lw=1)  # 3着と4着の境
    ax.set_xlabel("実際の着順（左=上位）")
    ax.set_ylabel(label)
    ax.set_title(f"{ctx.race_name}：{label} と好走の関係（橙＝複勝圏）")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    if indicator in _LOWER_BETTER:
        ax.invert_yaxis()  # 上がり等は小さいほど良い
    fig.tight_layout()
    return _to_png(fig)


# 指標→日本語ラベル（過去結果プロットの縦軸用）
CHART_LABELS_INDICATOR = {
    "pit_total_prize": "総獲得賞金 [万円]",
    "pit_show_rate": "複勝率 [%]",
    "pit_win_rate": "勝率 [%]",
    "pit_best_last3f": "最速上がり3F [秒]（小=速い）",
    "pit_avg_corner_pos": "平均通過順位（小=前）",
    "pit_max_grade_win": "最高勝鞍格（5=G1）",
    "pit_starts": "キャリア（出走数）",
}
_LOWER_BETTER = {"pit_best_last3f", "pit_avg_corner_pos"}
# 過去結果プロットで選べる指標
PAST_INDICATORS = ["pit_total_prize", "pit_show_rate", "pit_best_last3f",
                   "pit_avg_corner_pos", "pit_max_grade_win"]


def _label(view):
    """馬名（無ければ馬番）のラベル列。"""
    if "horse_name" in view and view["horse_name"].notna().any():
        return view["horse_name"].fillna(view.get("horse_no", "").astype(str))
    return view.get("horse_no", view.index).astype(str)


def _barh(view, col, xlabel, title, color, ascending, invert=False,
          xlim=None) -> bytes:
    d = view.dropna(subset=[col]).copy()
    if d.empty:
        return _placeholder(f"{xlabel} のデータがありません")
    d = d.sort_values(col, ascending=ascending)
    labels = _label(d)
    fig, ax = plt.subplots(figsize=(8, max(3, 0.45 * len(d))))
    ax.barh(labels, d[col], color=color)
    ax.set_xlabel(xlabel)
    ax.set_title(title)
    if xlim is not None:
        # 軸範囲を固定して差を見やすく（上がり3F などゼロ始まりだと潰れる値向け）
        lo, hi = xlim
        ax.set_xlim(hi, lo) if invert else ax.set_xlim(lo, hi)
    elif invert:
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


def render_past_trend_all(items, indicator="pit_total_prize"):
    """過去全年を1枚に集約して、指標 × 好走(3着内) の傾向を見る。

    年ごとに分けないだけで、見せ方は render_past_result と同じ。
    横軸=実着順(左=上位)、縦軸=指標。実際に3着以内に来た馬を橙(複勝圏)、
    着外を灰で示す。全年の馬を重ねて描くので、複数年に共通する傾向が見える。
    """
    import numpy as np
    import pandas as pd
    from .ml import build_features_for_context

    recs = []
    for ctx, actual in items:
        if actual is None or len(actual) == 0:
            continue
        feat = build_features_for_context(ctx)
        tgt = feat[feat["is_target"].fillna(False).astype(bool)].copy()
        tgt["horse_id"] = tgt["horse_id"].astype(str)
        tgt = tgt.drop(columns=["finish_pos"], errors="ignore")
        if indicator not in tgt.columns:
            continue
        a = actual.copy()
        a["horse_id"] = a["horse_id"].astype(str)
        d = tgt.merge(a[["horse_id", "finish_pos"]], on="horse_id", how="inner")
        d = d.dropna(subset=["finish_pos", indicator])
        for _, r in d.iterrows():
            recs.append({"pos": float(r["finish_pos"]),
                         "val": float(r[indicator])})
    if not recs:
        return _placeholder("過去レースのデータがありません")

    df = pd.DataFrame(recs)
    if indicator in ("pit_show_rate", "pit_win_rate"):
        df["val"] = df["val"] * 100.0
    label = CHART_LABELS_INDICATOR.get(indicator, indicator)
    in3 = df["pos"] <= 3
    # 年が重なって同じ着順が並ぶので、横にわずかに散らして潰れを防ぐ
    rng = np.random.default_rng(0)
    jx = df["pos"].values + rng.uniform(-0.18, 0.18, len(df))

    fig, ax = plt.subplots(figsize=(8, 5.5))
    ax.scatter(jx[~in3.values], df["val"].values[~in3.values],
               s=70, color="#c9d2e0", edgecolor="#999", label="着外", zorder=2)
    ax.scatter(jx[in3.values], df["val"].values[in3.values],
               s=140, color="#d8703b", edgecolor="#7a3a16",
               label="3着以内（複勝圏）", zorder=3)
    ax.axvline(3.5, color="#888", ls="--", lw=1)  # 3着と4着の境
    ax.set_xlabel("実際の着順（左=上位）")
    ax.set_ylabel(label)
    ax.set_title("過去" + str(len(items)) + "年まとめ：" + label + " と好走の傾向（橙＝複勝圏）")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    # 縦軸はダービーのデータ可視化と同じ尺度に揃える
    if indicator == "pit_best_last3f":
        ax.set_ylim(37, 30)  # 上がり3F は 30〜37秒・下ほど速い
    elif indicator in _LOWER_BETTER:
        ax.invert_yaxis()    # 通過順位など：下ほど良い（前）
    fig.tight_layout()
    return _to_png(fig)
