"""wimipet/1 边界校验，仅实现 C 的消费职责。"""
import copy
import json
import math

PROTOCOL = 'wimipet/1'
IMPLEMENTATION = 'interaction/0.2.0'
MAX_IMAGE = 2 * 1048576
STATES = {'standing', 'lying', 'clinging', 'hanging', 'falling', 'dragging'}
ACTION_TRANSITIONS = {
    'requested': {'queued', 'accepted', 'rejected'},
    'queued': {'accepted', 'rejected', 'interrupted', 'failed'},
    'accepted': {'completed', 'interrupted', 'failed'},
    'completed': set(), 'rejected': set(), 'interrupted': set(), 'failed': set(),
}
ERROR_CODES = {'invalid-input','unsupported-version','unsupported-capability','not-found',
               'conflict','stale','cancelled','timeout','unavailable','internal'}


class ProtocolError(Exception):
    def __init__(self, code, message, retryable=False):
        self.error = dict(code=code, message=message, retryable=retryable)
        super().__init__(message)


def require(condition, message, code='invalid-input'):
    if not condition:
        raise ProtocolError(code, message)


def identifier(value):
    return isinstance(value, str) and bool(value.strip())


def integer(value):
    return type(value) is int


def fingerprint(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ProtocolError('invalid-input', '请求必须是有效 JSON 数据')


def envelope(request):
    require(isinstance(request, dict), '请求需要 JSON 对象')
    require(request.get('protocol') == PROTOCOL, '不支持的协议版本', 'unsupported-version')
    for key in ('sessionId', 'requestId', 'type'):
        require(identifier(request.get(key)), f'{key} 必须为非空字符串')
    require(integer(request.get('expiresAtMs')), 'expiresAtMs 必须为 epoch 整数毫秒')
    require(isinstance(request.get('payload'), dict), 'payload 必须为对象')


def upstream_error(value):
    if isinstance(value,dict) and 'error' in value:
        error=value['error']
        require(isinstance(error,dict) and error.get('code') in ERROR_CODES and
                isinstance(error.get('message'),str) and type(error.get('retryable')) is bool, '上游错误格式不符')
        raise ProtocolError(error['code'],error['message'],error['retryable'])


def rectangle(rect):
    require(isinstance(rect,dict) and all(type(rect.get(k)) in (int,float) and math.isfinite(rect[k])
            for k in ('x','y','width','height')), '物理像素矩形无效')
    require(rect['width']>0 and rect['height']>0, '矩形尺寸必须为正')


def character_snapshot(package, loaded):
    require(isinstance(package, dict) and isinstance(loaded, dict), '需要角色包和已加载能力')
    require(package.get('protocol') == PROTOCOL, '角色包版本不支持', 'unsupported-version')
    require(not package.get('requiredCapabilities'), '角色包要求未知扩展能力', 'unsupported-capability')
    require(identifier(package.get('packageId')) and integer(package.get('revision')) and package['revision'] > 0, '角色包身份无效')
    instance = loaded.get('instanceId')
    require(identifier(instance) and instance != 'all', 'instanceId 无效或为保留值')
    require(loaded.get('packageId') == package['packageId'] and loaded.get('revision') == package['revision'], '已加载能力与包版本不一致', 'conflict')
    character = package.get('character', {})
    require(isinstance(character, dict) and identifier(character.get('name')), '缺少角色名称')
    require(isinstance(character.get('persona'), str) and len(character['persona']) <= 20000, '人设最多 20000 Unicode 码点')
    actions, supported, postures = package.get('actions'), loaded.get('supportedActions'), loaded.get('supportedPostures')
    require(isinstance(actions, list) and isinstance(supported, list) and isinstance(postures, list), '缺少动作或已加载能力列表')
    require(all(s in STATES for s in postures), '未知已加载姿态')
    by_id = {a.get('actionId'): a for a in actions if isinstance(a, dict) and identifier(a.get('actionId'))}
    require(len(by_id) == len(actions), '角色包动作 ID 无效或重复')
    seen, capabilities = set(), []
    for item in supported:
        require(isinstance(item, dict), '已加载动作描述无效')
        action_id = item.get('actionId')
        require(identifier(action_id) and action_id not in seen and action_id in by_id, '已加载能力包含未知或重复动作', 'unsupported-capability')
        action, allowed = by_id[action_id], item.get('allowedStates')
        require(identifier(item.get('meaning')) and isinstance(allowed, list) and all(s in action.get('allowedStates', []) for s in allowed), '已加载动作语义或状态无效')
        require(type(action.get('loop')) is bool, '动作缺少循环语义')
        seen.add(action_id)
        capabilities.append(dict(actionId=action_id, meaning=item['meaning'], allowedStates=allowed, loop=action['loop']))
    return copy.deepcopy(dict(instanceId=instance, packageId=package['packageId'], revision=package['revision'],
                              name=character['name'], persona=character['persona'], supportedActions=capabilities,
                              supportedPostures=postures))


def policy(value):
    require(integer(value.get('policyRevision')) and value['policyRevision'] > 0, '策略版本必须为正整数')
    require(type(value.get('paused')) is bool and value.get('scope') in ('active-window', 'desktop'), '观察暂停/范围无效')
    require(integer(value.get('cooldownMs')) and 0 <= value['cooldownMs'] <= 300000, '冷却必须为 0–300000 整数毫秒')
    regions = value.get('excludedRegions')
    require(isinstance(regions, list), 'excludedRegions 必须为数组')
    for rect in regions:
        rectangle(rect)
    fingerprint(value)
    return copy.deepcopy(value)
