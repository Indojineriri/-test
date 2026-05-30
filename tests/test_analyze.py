"""分析機能（診断・出走馬分析）のオフライン検証。

合成 RaceContext を保存→ロードし、診断と分析が筋の通った結果を返すか確認する。

    python3 tests/test_analyze.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from keiba import analyze as A  # noqa: E402
from keiba import service  # noqa: E402
from keiba.storage import LocalStorage  # noqa: E402
from test_dataset import _make_context  # 合成 RaceContext を再利用  # noqa: E402


# --- 診断 -------------------------------------------------------------------

def test_diagnose_coverage_counts():
    ctx, _, _ = _make_context(n_entrants=8, career=6, seed=1)
    s = A.diagnose_coverage(ctx)
    assert s["n_entrants"] == 8
    assert s["n_result_rows"] == len(ctx.history)
    assert s["n_unique_past_races"] == len(ctx.races)
    # per_horse は出走 8 頭ぶん、過去出走数は正の整数
    ph = s["per_horse"]
    assert len(ph) == 8
    assert (ph["past_starts"] >= 1).all()
    # フォーマットが落ちずに文字列を返す
    txt = A.format_coverage(s)
    assert "データ健全性診断" in txt and "各出走馬の過去出走数" in txt


def test_diagnose_flags_no_history():
    """過去成績ゼロの馬を警告する。"""
    # entries に居るが results に登場しない馬を1頭混ぜる
    ctx, _, _ = _make_context(n_entrants=4, career=4, seed=2)
    ghost = ctx.entries.iloc[[0]].copy()
    ghost["horse_id"] = "GHOST"
    ghost["horse_name"] = "ゴースト"
    ctx.entries = pd.concat([ctx.entries, ghost], ignore_index=True)
    s = A.diagnose_coverage(ctx)
    assert s["horses_with_no_history"] >= 1
    assert any("1走も取れていない" in w for w in s["warnings"])


# --- 出走馬分析 -------------------------------------------------------------

def test_analyze_entrants_shape_and_leakage_free():
    ctx, strength, _ = _make_context(n_entrants=8, career=6, seed=3)
    view = A.analyze_entrants(ctx)
    assert len(view) == 8
    # 必須の人間可読列が出ている
    for c in ["horse_name", "pit_starts", "pit_show_rate", "pit_avg_finish"]:
        assert c in view.columns
    # 率は 0..100（％化済み）
    assert view["pit_show_rate"].between(0, 100).all()
    # 強い馬ほど複勝率が高い傾向（健全性。リーク無しでも信号は出る）
    sview = view.assign(strength=view["horse_name"].map(
        {f"馬{h}": strength[h] for h in strength}))
    corr = sview[["pit_show_rate", "strength"]].corr().iloc[0, 1]
    assert corr > 0.3, f"強さと複勝率の相関が低い: {corr}"


def test_analyze_entrants_via_save_load(tmp=None):
    """fetch 相当の保存 → load_context → 分析、の一連が通る。"""
    import tempfile
    d = tmp or Path(tempfile.mkdtemp())
    store = LocalStorage(d)
    ctx, _, target = _make_context(n_entrants=6, career=5, seed=4)

    # service の保存関数を直接使う（取得は使わず ctx を CSV 化）
    prefix = f"races/{target}"
    store.write_text(f"{prefix}/entries.csv", ctx.entries.to_csv(index=False))
    store.write_text(f"{prefix}/results.csv", ctx.history.to_csv(index=False))
    store.write_text(f"{prefix}/races.csv", ctx.races.to_csv(index=False))
    import json
    store.write_text(f"{prefix}/meta.json", json.dumps(
        {"race_id": target, "race_meta": ctx.race}, ensure_ascii=False, default=str))

    # ロードして分析
    ctx2 = service.load_context(target, store)
    assert len(ctx2.entries) == 6
    s = A.diagnose_coverage(ctx2)
    assert s["n_entrants"] == 6
    view = A.analyze_entrants(ctx2)
    assert len(view) == 6


def test_prize_and_running_style_present():
    """賞金・脚質の特徴量が分析結果に出ること。"""
    ctx, _, _ = _make_context(n_entrants=8, career=6, seed=6)
    view = A.analyze_entrants(ctx)
    assert "pit_total_prize" in view.columns
    assert "pit_running_style" in view.columns
    # 脚質ラベルは想定の語彙
    styles = set(view["pit_running_style"].unique())
    assert styles <= {"逃げ", "先行", "差し", "追込", "不明"}
    # 賞金は非負
    assert (view["pit_total_prize"].fillna(0) >= 0).all()


def test_evaluate_past_race():
    """過去レースの答え合わせ: 強い馬を上位に予想できるか。"""
    ctx, strength, target = _make_context(n_entrants=10, career=6, seed=8)
    # 対象レースの「実結果」を強さ順で合成（強い馬ほど上位着順）
    ent = ctx.entries.copy()
    order = sorted(ent["horse_id"], key=lambda h: -strength[h])
    actual = pd.DataFrame({
        "horse_id": order,
        "finish_pos": list(range(1, len(order) + 1)),
    })
    ev = A.evaluate_past_race(ctx, actual, rank_by="pit_show_rate")
    # 予想上位3頭のうち、実3着内をある程度当てられる（リーク無しでも信号は出る）
    assert ev["top3_hits"] >= 1
    txt = A.format_evaluation(ev)
    assert "答え合わせ" in txt and "実着順" in txt


def test_horse_form():
    ctx, _, _ = _make_context(n_entrants=5, career=5, seed=5)
    hid = ctx.entries["horse_id"].iloc[0]
    form = A.horse_form(ctx, hid)
    assert not form.empty
    assert "finish_pos" in form.columns
    # 時系列に並んでいる（date 昇順）
    if "date" in form.columns:
        dates = pd.to_datetime(form["date"], errors="coerce").dropna()
        assert (dates.values == np.sort(dates.values)).all()


if __name__ == "__main__":
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for name, fn in fns:
        fn()
        print(f"  ok: {name}")
        passed += 1
    print(f"OK: {passed} tests passed")
