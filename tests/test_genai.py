"""生成AI 示唆出し+予想の検証。

API 呼び出しは行わず、(1) プロンプト組み立てがオフラインで正しく動くこと、
(2) フェイク Anthropic クライアントで2段フロー(derive→apply)が通ること、
(3) 整形出力が出ること、を確認する。

    python3 tests/test_genai.py
"""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from keiba import genai  # noqa: E402
from test_dataset import _make_context  # noqa: E402


def _item(seed):
    ctx, strength, _ = _make_context(n_entrants=10, career=6, seed=seed)
    order = sorted(ctx.entries["horse_id"], key=lambda h: -strength[h])
    actual = pd.DataFrame({"horse_id": order,
                           "finish_pos": list(range(1, len(order) + 1))})
    return ctx, actual


# --- オフライン: プロンプト組み立て -----------------------------------------

def test_build_insight_prompt_has_results():
    items = [_item(1), _item(2)]
    text = genai.build_insight_prompt(items)
    assert "過去の日本ダービー" in text
    assert "実着順" in text          # 過去レースは着順つき
    assert "複勝率" in text and "脚質" in text
    # 馬の行が出走頭数 × レース数ぶんある
    assert text.count("馬番") >= 10 * 2


def test_build_application_prompt_no_results():
    ctx, _ = _item(3)
    text = genai.build_application_prompt(ctx)
    assert "予想対象" in text
    assert "着順は未確定" in text
    assert "実着順" not in text      # 対象レースは着順なし


# --- フェイククライアントで2段フロー ----------------------------------------

class _FakeParsed:
    def __init__(self, obj):
        self.parsed_output = obj


class FakeMessages:
    def __init__(self, insights, prediction):
        self._insights, self._prediction = insights, prediction
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        # output_format で何を返すか分岐
        fmt = kwargs.get("output_format")
        if fmt is genai.DerbyInsights:
            return _FakeParsed(self._insights)
        if fmt is genai.Prediction:
            return _FakeParsed(self._prediction)
        raise AssertionError("unexpected output_format")


class FakeClient:
    def __init__(self, insights, prediction):
        self.messages = FakeMessages(insights, prediction)


def _sample_insights():
    return genai.DerbyInsights(
        summary="複勝率と賞金が高く、上がりが速い差し馬が好走。",
        insights=[
            genai.Insight(pattern="重賞勝ち(最高勝鞍格が高い)馬は堅実",
                          rationale="過去の上位馬はG2/G3勝ちが多い", weight=0.8),
            genai.Insight(pattern="上がり3Fが速い馬は東京2400向き",
                          rationale="差し決着が多い", weight=0.6),
        ],
        caveats=["3歳春で2400m未経験が多く不確実"],
    )


def _sample_prediction():
    return genai.Prediction(
        honmei_horse_no=1,
        ranking=[
            genai.HorsePick(horse_no=1, horse_name="馬A", score=0.8,
                            matched_insights=["重賞勝ち馬は堅実"], reason="賞金最多で複勝率高い"),
            genai.HorsePick(horse_no=2, horse_name="馬B", score=0.6,
                            matched_insights=["上がりが速い"], reason="決め手がある"),
        ],
        commentary="堅めの決着を予想。",
    )


def test_two_stage_flow():
    insights, pred = _sample_insights(), _sample_prediction()
    client = FakeClient(insights, pred)
    past = [_item(s) for s in (10, 11, 12)]
    ctx, _ = _item(99)

    got_insights = genai.derive_insights(client, past)
    assert got_insights.summary.startswith("複勝率")
    assert len(got_insights.insights) == 2

    got_pred = genai.apply_insights(client, got_insights, ctx)
    assert got_pred.honmei_horse_no == 1

    # 2回 parse が呼ばれ、それぞれ adaptive thinking + opus + cache_control を使う
    calls = client.messages.calls
    assert len(calls) == 2
    for c in calls:
        assert c["model"] == genai.MODEL
        assert c["thinking"] == {"type": "adaptive"}
        # system か user に cache_control が付いている（プロンプトキャッシュ）
        sys_cc = any(b.get("cache_control") for b in c["system"])
        usr_cc = any(b.get("cache_control") for b in c["messages"][0]["content"])
        assert sys_cc and usr_cc


def test_format_outputs():
    txt_i = genai.format_insights(_sample_insights())
    assert "示唆出し" in txt_i and "重賞勝ち" in txt_i
    txt_p = genai.format_prediction(_sample_prediction(), race_name="ダービー")
    assert "本命" in txt_p and "◎" in txt_p and "馬A" in txt_p


if __name__ == "__main__":
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for name, fn in fns:
        fn()
        print(f"  ok: {name}")
    print(f"OK: {len(fns)} tests passed")
