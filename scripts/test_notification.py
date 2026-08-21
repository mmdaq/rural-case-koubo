import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import render_notification, _select_candidates
from utils.dedup import SeenStore
from utils.state import RunState
import tempfile

# 测试 render_notification（不打印内容，避免编码问题）
md = render_notification(2, "2026-08-22")
assert "无新增案例" in md, "通知应包含'无新增案例'"
assert "连续第 2 天" in md, "通知应包含连续天数"
assert "检查关键词" in md, "通知应包含建议操作"
print("PASS: render_notification 内容正确")

# 测试 RunState
tmp = os.path.join(tempfile.mkdtemp(), "test_state.json")
state = RunState(tmp)
assert state.consecutive_no_new == 0, f"初始应为0, 实际{state.consecutive_no_new}"
assert state.stopped == False, "初始不应停止"
print("PASS: RunState 初始状态正确")

state.record_no_new()
assert state.consecutive_no_new == 1, f"应为1, 实际{state.consecutive_no_new}"
print("PASS: RunState record_no_new 正常")

state.record_no_new()
state.record_no_new()
assert state.consecutive_no_new == 3, f"应为3, 实际{state.consecutive_no_new}"
print("PASS: RunState 累计计数正确")

state.stop()
assert state.stopped == True, "应已停止"
print("PASS: RunState stop 正确")

# 重新加载验证持久化
state2 = RunState(tmp)
assert state2.consecutive_no_new == 3, f"重载后应为3, 实际{state2.consecutive_no_new}"
assert state2.stopped == True, "重载后应仍停止"
print("PASS: RunState 持久化正确")

state2.reset()
assert state2.consecutive_no_new == 0, f"reset后应为0, 实际{state2.consecutive_no_new}"
print("PASS: RunState reset 正确")

# 测试 _select_candidates 只返回未推送案例
store = SeenStore(os.path.join(tempfile.mkdtemp(), "test_seen.json"))
store.mark_seen("2024-07-2-044-004", "蔡某珠案")

cases = [
    {"rule_code": "2024-07-2-044-004", "title": "蔡某珠案"},  # 已推送
    {"rule_code": "2024-07-2-044-005", "title": "张某梅案"},  # 未推送
    {"rule_code": "2024-07-2-044-003", "title": "蒋某某案"},  # 未推送
]

result = _select_candidates(cases, store, 5)
codes = [d["rule_code"] for d in result]
assert len(result) == 2, f"期望2个未推送, 实际{len(result)}"
assert "2024-07-2-044-004" not in codes, "不应包含已推送案例"
print(f"PASS: _select_candidates 只返回未推送案例: {codes}")

# 测试全部已推送时返回空
store.mark_seen("2024-07-2-044-005", "张某梅案")
store.mark_seen("2024-07-2-044-003", "蒋某某案")
result2 = _select_candidates(cases, store, 5)
assert len(result2) == 0, f"期望空列表, 实际{len(result2)}"
print("PASS: 全部已推送时返回空列表")

print("\n所有测试通过!")