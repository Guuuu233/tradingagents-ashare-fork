"""DAV-1789 处理臂：经理给出方向时须引用 ≥1 条同侧已采纳论点，否则判中性。

纯提示词层契约（单因素改动，不叠加自洽硬门、不叠加 M1）。
"""
from tradingagents.prompts.en import PROMPTS as EN_PROMPTS
from tradingagents.prompts.zh import PROMPTS as ZH_PROMPTS


def test_zh_manager_prompt_requires_same_side_adopted_claim_for_directional_call():
    prompt = ZH_PROMPTS["research_manager_prompt"]
    assert "方向—同侧已采纳论点约束" in prompt
    assert "adopted_claim_ids" in prompt
    # 无同侧已采纳论点时的强制回退口径
    assert "winner=tie" in prompt
    assert "无同侧已采纳论点可依据" in prompt
    # 部分采纳与已驳回 claim 不得单独充当方向依据
    assert "partially_adopted_claims" in prompt
    assert "rejected_claim_ids" in prompt
    # 机读块旁同步给出前置条件，避免正文与 MANAGER_VERDICT 口径不一致
    assert "方向性裁决前置条件" in prompt


def test_en_manager_prompt_requires_same_side_adopted_claim_for_directional_call():
    prompt = EN_PROMPTS["research_manager_prompt"]
    assert "Direction-vs-adopted-claim constraint" in prompt
    assert "adopted_claim_ids" in prompt
    assert 'winner="tie"' in prompt
    assert "no same-side adopted claim to rely on" in prompt
    assert "rejected_claim_ids" in prompt
