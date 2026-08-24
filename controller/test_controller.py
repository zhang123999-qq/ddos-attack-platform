"""模型序列化冒烟测试 — 在 controller/ 目录下运行: python test_controller.py"""
from app.models import AttackType, TargetSpec
print('AttackType:', AttackType)
print('TargetSpec:', TargetSpec)

# Test serialization
from app.models import NodeInfo, NodeHeartbeat, AttackResult, AttackStatus, AttackCommand, AttackParams, Scenario, ScenarioStep
from datetime import datetime

node = NodeInfo(node_id='test', node_type='http', ip='127.0.0.1', hostname='test', cpu_cores=4, memory_gb=8.0)
print('NodeInfo:', node.model_dump(mode='json'))

hb = NodeHeartbeat(node_id='test', cpu_percent=50.0, memory_percent=30.0, network_mbps=100.0, active_connections=10)
print('NodeHeartbeat:', hb.model_dump(mode='json'))

result = AttackResult(attack_id='test', node_id='test', status=AttackStatus.RUNNING)
print('AttackResult:', result.model_dump(mode='json'))

cmd = AttackCommand(
    attack_id='test',
    attack_type=AttackType.HTTP_FLOOD,
    params=AttackParams(target=TargetSpec(ip='127.0.0.1', port=80), duration=60, rps=1000, concurrency=100),
)
print('AttackCommand:', cmd.model_dump(mode='json'))

scenario = Scenario(
    scenario_id='test', name='Test', description='Test scenario',
    steps=[ScenarioStep(attack_type=AttackType.HTTP_FLOOD, params=AttackParams(target=TargetSpec(ip='127.0.0.1', port=80), duration=60, rps=1000, concurrency=100))],
)
print('Scenario:', scenario.model_dump(mode='json'))

print('All tests passed')