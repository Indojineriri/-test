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

    # 序列: 「A が B に勝っている → A が上」という推移的な強さで並べる。
    # 単純な勝率だと『強い相手としか戦っていない馬』が下に沈むので、
    # 直接対決を伝播させるレーティング(Massey/Colley 風の反復)で序列を決める。
    rating = _transitive_rating(ids, wins)
    for a in ids:
        meta[a]["rating"] = float(rating.get(a, 0.0))
    # rating の高い順。同点は対戦勝ち数で割る。
    order = sorted(ids, key=lambda a: (-meta[a]["rating"], -meta[a]["wins"]))

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

    推移的レーティング（A>B>C を反映）を 0〜1 に正規化する。
    対戦が全く無い馬は中立 0.5。
    """
    h = head_to_head(ctx)
    ratings = {hid: m["rating"] for hid, m in h["meta"].items()}
    vals = [v for hid, v in ratings.items() if h["meta"][hid]["vs_count"] > 0]
    if not vals:
        return {hid: 0.5 for hid in ratings}
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    out = {}
    for hid, m in h["meta"].items():
        if m["vs_count"] == 0:
            out[hid] = 0.5
        else:
            out[hid] = 0.1 + 0.8 * (m["rating"] - lo) / span  # 0.1〜0.9 に収める
    return out


def _transitive_rating(ids, wins, iters: int = 50, damping: float = 0.85) -> dict:
    """直接対決を伝播させて推移的な強さレーティングを返す。

    考え方（簡易 Massey/Colley + PageRank 風）:
      - 「A が B に勝った」= B から A へ強さが流れる有向辺。
      - 各馬のレーティングを、勝った相手の強さの和で反復更新する。
        → 強い相手に勝つほど、また『勝った相手がさらに上位に勝っている』ほど高くなる。
      - これにより A>B>C のとき、A と C が未対戦でも A が C の上に来る。
    返り値は平均0付近の実数（高いほど強い）。
    """
    n = len(ids)
    if n == 0:
        return {}
    idx = {h: i for i, h in enumerate(ids)}
    # 勝敗の重み行列 W[i][j] = i が j に勝った回数
    W = np.zeros((n, n))
    for a in ids:
        for b in ids:
            if a != b:
                W[idx[a]][idx[b]] = wins[a][b]

    # 反復: r_i ← (1-d)/n + d * Σ_j (W[i][j] / outdeg_j) * r_j
    #   j が i に負けている分だけ、j の強さが i に流れる（負け側 j の総負け数で正規化）
    losses_total = W.sum(axis=0)  # j 列の合計 = j が負けた総数
    r = np.ones(n) / n
    for _ in range(iters):
        new = np.full(n, (1 - damping) / n)
        for j in range(n):
            if losses_total[j] > 0:
                share = r[j] * damping / losses_total[j]
                new += W[:, j] * share
        s = new.sum()
        r = new / s if s > 0 else new
    # 中心化して見やすく（平均0）
    r = r - r.mean()
    return {ids[i]: float(r[i]) for i in range(n)}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
