"""出走馬どうしの「直接対決（head-to-head）」を集計する。

ダービー出走馬は皐月賞・共同通信杯などの同じステップレースで何度も対戦している。
その同居レースでの着順から「A は B に何勝何敗か」を数え、強さの序列(上下関係)を作る。
予想に反映でき、可視化(対戦表・序列)にも使える。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import RaceContext


def head_to_head(ctx: RaceContext):
    """出走馬どうしの直接対決を集計する。

    Returns:
        dict:
            horses   … [{horse_id, horse_no, horse_name}] 出走馬一覧（馬番順）
            matrix   … 勝敗行列 DataFrame（行=自分, 列=相手, 値=勝ち数 / "-")
            records  … [{a, b, a_wins, b_wins, races}] 対戦ペアの成績
            order    … 直接対決の勝率で並べた強い順の horse_id リスト（序列）
            meta     … 各馬の {wins, losses, win_rate, vs_count}
    """
    entries = ctx.entries.copy()
    entries["horse_id"] = entries["horse_id"].astype(str)
    ids = entries["horse_id"].tolist()
    id2no = dict(zip(entries["horse_id"], entries.get("horse_no", range(1, len(ids)+1))))
    id2name = dict(zip(entries["horse_id"], entries.get("horse_name", ids)))

    hist = ctx.history.copy()
    hist["horse_id"] = hist["horse_id"].astype(str)
    own = hist[hist["horse_id"].isin(set(ids))]

    # 勝敗カウント: wins[a][b] = a が b に勝った回数
    wins = {a: {b: 0 for b in ids} for a in ids}
    races_together = {a: {b: 0 for b in ids} for a in ids}
    for race_id, grp in own.groupby("race_id"):
        g = grp.dropna(subset=["finish_pos"])
        if len(g) < 2:
            continue
        recs = list(zip(g["horse_id"], g["finish_pos"].astype(float)))
        for i in range(len(recs)):
            for j in range(len(recs)):
                if i == j:
                    continue
                a, fa = recs[i]
                b, fb = recs[j]
                races_together[a][b] += 1  # 後で /2 はしない（左右対称に数える）
                if fa < fb:               # 着順が小さい=上位=勝ち
                    wins[a][b] += 1

    # ペア成績
    records = []
    seen = set()
    for a in ids:
        for b in ids:
            if a == b or (b, a) in seen:
                continue
            seen.add((a, b))
            aw, bw = wins[a][b], wins[b][a]
            n = aw + bw
            if n > 0:
                records.append({"a": a, "b": b, "a_wins": aw, "b_wins": bw, "races": n})

    # 各馬の通算（対出走馬）勝敗と勝率
    meta = {}
    for a in ids:
        w = sum(wins[a][b] for b in ids if b != a)
        l = sum(wins[b][a] for b in ids if b != a)
        n = w + l
        meta[a] = {"wins": int(w), "losses": int(l),
                   "win_rate": (w / n) if n else np.nan, "vs_count": int(n)}

    # 序列: 直接対決勝率の高い順（対戦数が同じなら勝率、未対戦は後ろ）
    order = sorted(ids, key=lambda a: (
        -(meta[a]["win_rate"] if not np.isnan(meta[a]["win_rate"]) else -1),
        -meta[a]["vs_count"]))

    # 勝敗行列（表示用）。対角は "-"、未対戦は空。
    mat = pd.DataFrame("", index=ids, columns=ids)
    for a in ids:
        for b in ids:
            if a == b:
                mat.loc[a, b] = "—"
            elif wins[a][b] + wins[b][a] > 0:
                mat.loc[a, b] = f"{wins[a][b]}-{wins[b][a]}"  # a勝-b勝

    horses = [{"horse_id": i, "horse_no": _int(id2no.get(i)),
               "horse_name": id2name.get(i, i)} for i in ids]

    return {
        "horses": horses,
        "matrix": mat,
        "records": records,
        "order": order,
        "meta": meta,
        "id2no": id2no,
        "id2name": id2name,
    }


def h2h_strength(ctx: RaceContext) -> dict:
    """直接対決から各馬の『相対強度スコア』(0〜1)を返す。予想に足す用。

    勝率を基本に、対戦数で信頼度を重み付け（対戦が多いほど確からしい）。
    未対戦の馬は中立 0.5。
    """
    h = head_to_head(ctx)
    out = {}
    for hid, m in h["meta"].items():
        if m["vs_count"] == 0:
            out[hid] = 0.5
        else:
            # 勝率を、対戦数に応じて 0.5 から引っ張る（ベイズ風の縮小推定）
            k = 4.0  # 縮小の強さ
            wr = m["win_rate"]
            n = m["vs_count"]
            out[hid] = (wr * n + 0.5 * k) / (n + k)
    return out


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
