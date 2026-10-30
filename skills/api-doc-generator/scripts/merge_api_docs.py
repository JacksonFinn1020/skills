#!/usr/bin/env python3
"""Safe deterministic merger for Claude Code's api-doc-generator Skill.

This tool NEVER analyzes source code. The invoking agent must establish source
facts and supply a fully evidenced JSON patch first.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any

DOC_START = re.compile(r'^<!-- API-DOC-GENERATOR:START project="([^"]+)" -->$', re.M)
DOC_END = re.compile(r'^<!-- API-DOC-GENERATOR:END -->$', re.M)
ITEM_RE = re.compile(
    r'^<!-- API-DOC:ITEM (?P<attrs>[^\n]*) -->\n(?P<body>.*?)^<!-- API-DOC:END -->\n?',
    re.M | re.S,
)
ARCHIVE_RE = re.compile(
    r'^<!-- API-DOC:ARCHIVE:START -->\n(?P<body>.*?)^<!-- API-DOC:ARCHIVE:END -->\n?',
    re.M | re.S,
)
MANUAL_RE = re.compile(
    r'^<!-- MANUAL:START -->\n(?P<text>.*?)^<!-- MANUAL:END -->$',
    re.M | re.S,
)
ATTR_RE = re.compile(r'([a-z_]+)="([^"]*)"')
SAFE_KEY = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:/@#~+%-]{0,240}$')


# Schema v2: each endpoint carries machine-checkable, source-supported examples.
EXAMPLE_STATUSES = {'present', 'none', 'not_applicable', 'unknown'}
EXAMPLE_GROUPS = ('request', 'success', 'error')
EXAMPLE_FORMATS = {'json', 'bash', 'http', 'graphql', 'protobuf', 'text'}
EXAMPLE_SECTION_TITLES = {
    'request': '请求体示例',
    'success': '成功响应体示例',
    'error': '失败响应体示例',
}
EXAMPLE_META = re.compile(
    r'^<!-- API-DOC:EXAMPLE-STATUS request="(present|none|not_applicable|unknown)" '
    r'success="(present|none|not_applicable|unknown)" '
    r'error="(present|none|not_applicable|unknown)" -->$', re.M)
EXAMPLE_HEADINGS = re.compile(r'^#### (请求体示例|成功响应体示例|失败响应体示例)\s*$', re.M)
INVALID_EXAMPLE_WORDS = re.compile(
    r'(^\s*(?:\.{3,}|…|TODO|TBD|待确认|待填写|省略|xxx)\s*$|<[^<>\n]{1,80}>|\{…\}|\[…\])',
    re.I)


def validate_example_body(value: Any, label: str) -> None:
    """Forbid obvious fake/ellipsized JSON while accepting valid JSON scalars."""
    if isinstance(value, dict):
        for key, child in value.items():
            require(isinstance(key, str), f'{label} 的 JSON 对象键必须是字符串')
            require(not INVALID_EXAMPLE_WORDS.search(key), f'{label} 含省略式字段名')
            validate_example_body(child, f'{label}.{key}')
    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate_example_body(child, f'{label}[{index}]')
    elif isinstance(value, str):
        require(not INVALID_EXAMPLE_WORDS.search(value.strip()),
                f'{label} 包含省略号、TODO 或未替换的占位符')
        require('<!-- API-DOC:' not in value and '<!-- MANUAL:' not in value,
                f'{label} 包含保留文档标记')
    elif value is not None:
        require(type(value) in (int, float, bool), f'{label} 包含非 JSON 值')
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise PatchError(f'{label} 不是标准 JSON: {exc}') from exc


def validate_examples(item: dict[str, Any], label: str) -> None:
    examples = item['examples']
    require(isinstance(examples, dict) and set(examples) == set(EXAMPLE_GROUPS),
            f'{label}.examples 必须包含 request、success、error')
    for group in EXAMPLE_GROUPS:
        group_value = examples[group]
        field = f'{label}.examples.{group}'
        require(isinstance(group_value, dict) and set(group_value) == {'state', 'reason', 'cases'},
                f'{field} 必须包含 state、reason、cases')
        state = group_value['state']
        require(state in EXAMPLE_STATUSES, f'{field}.state 非法')
        require(isinstance(group_value['reason'], str), f'{field}.reason 必须是字符串')
        require(isinstance(group_value['cases'], list), f'{field}.cases 必须是数组')
        if state == 'present':
            require(len(group_value['cases']) > 0, f'{field} 声称有示例却未提供 cases')
            require(not group_value['reason'].strip(), f'{field} 存在示例时 reason 应为空字符串')
        else:
            require(not group_value['cases'], f'{field} state={state} 时不能包含示例')
            reason = safe_text(group_value['reason'], f'{field}.reason')
            require('\n' not in reason and '<!--' not in reason and not INVALID_EXAMPLE_WORDS.search(reason),
                    f'{field}.reason 必须是具体的单行原因，不能是占位符')
        seen_titles = set()
        for index, example in enumerate(group_value['cases']):
            case_field = f'{field}.cases[{index}]'
            require(isinstance(example, dict) and
                    set(example) == {'title', 'format', 'body', 'evidence'},
                    f'{case_field} 必须包含 title、format、body、evidence')
            title = safe_text(example['title'], f'{case_field}.title')
            require('\n' not in title and '<!--' not in title and '```' not in title,
                    f'{case_field}.title 不允许换行或结构标记')
            require(title not in seen_titles, f'{case_field} 示例标题重复')
            seen_titles.add(title)
            fmt = example['format']
            require(fmt in EXAMPLE_FORMATS, f'{case_field}.format 不受支持')
            validate_lines(example['evidence'], f'{case_field}.evidence')
            if fmt == 'json':
                validate_example_body(example['body'], f'{case_field}.body')
            else:
                body = safe_text(example['body'], f'{case_field}.body')
                require('```' not in body and '<!-- API-DOC:' not in body and
                        '<!-- MANUAL:' not in body and not INVALID_EXAMPLE_WORDS.search(body) and
                        (fmt == 'graphql' or '...' not in body) and '…' not in body,
                        f'{case_field}.body 含省略占位符或文档结构标记')
    # Success response is not optional without an explicit explanation.
    # A confirmed 204/one-way action may legitimately have no response body.


def render_examples(examples: dict[str, Any]) -> str:
    states = {key: examples[key]['state'] for key in EXAMPLE_GROUPS}
    lines = [
        f'<!-- API-DOC:EXAMPLE-STATUS request="{states["request"]}" '
        f'success="{states["success"]}" error="{states["error"]}" -->',
    ]
    for key in EXAMPLE_GROUPS:
        group = examples[key]
        state = group['state']
        lines.extend(['', f'#### {EXAMPLE_SECTION_TITLES[key]}', ''])
        if state != 'present':
            target = '请求体' if key == 'request' else '响应体'
            status = {'none': f'无{target}', 'not_applicable': '此消息方向不适用',
                      'unknown': f'待确认：无法从当前代码确定{target}示例'}[state]
            lines.append(f'**{status}。** {group["reason"]}')
            continue
        for example in group['cases']:
            lines.append(f'**{example["title"]}**')
            fmt = example['format']
            body = (json.dumps(example['body'], ensure_ascii=False, indent=2,
                               allow_nan=False) if fmt == 'json' else example['body'].strip())
            lines.extend(['', f'```{fmt}', body, '```', ''])
            sources = '、'.join(f'`{src}`' for src in example['evidence'])
            lines.append(f'代码依据：{sources}')
            lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def audit_item(raw: str, key: str) -> dict[str, Any]:
    """Audit rendered interfaces including legacy sections from schema v1."""
    problems: list[str] = []
    review: list[str] = []
    raw = raw.split('<!-- MANUAL:START -->', 1)[0]  # Human notes are not machine-audited.
    matches = list(EXAMPLE_META.finditer(raw))
    if len(matches) != 1:
        problems.append('缺少唯一的示例状态元数据；可能是未升级的旧版接口章节')
    states: dict[str, str] = {}
    if len(matches) == 1:
        states = dict(zip(EXAMPLE_GROUPS, matches[0].groups()))
    headings = list(EXAMPLE_HEADINGS.finditer(raw))
    for group in EXAMPLE_GROUPS:
        heading = EXAMPLE_SECTION_TITLES[group]
        found = [match for match in headings if match.group(1) == heading]
        if len(found) != 1:
            problems.append(f'{heading} 缺失或重复')
            continue
        start = found[0].end()
        # End at the next fourth-level heading, including the human section.
        next_heading = re.search(r'^####\s+', raw[start:], re.M)
        section = raw[start:start + next_heading.start()] if next_heading else raw[start:]
        state = states.get(group)
        blocks = list(re.finditer(r'^```(\w+)\n(.*?)^```\s*$', section, re.M | re.S))
        if state == 'present':
            if not blocks:
                problems.append(f'{heading} 标记 present 但没有代码示例')
            for block in blocks:
                fmt, body = block.group(1), block.group(2)
                if fmt == 'json':
                    try:
                        parsed = json.loads(body, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                        validate_example_body(parsed, f'{key}/{heading}')
                    except (json.JSONDecodeError, ValueError, PatchError) as exc:
                        problems.append(f'{heading} 包含非法或省略式 JSON: {exc}')
        elif state == 'unknown':
            review.append(f'{heading} 待确认，缺少已验证示例')
            if blocks:
                problems.append(f'{heading} 待确认状态不应有代码示例')
        elif state in ('none', 'not_applicable'):
            if blocks:
                problems.append(f'{heading} 声称无请求/响应体但存在代码示例')
        else:
            problems.append(f'{heading} 缺少明确的示例状态')
    return {'key': key, 'status': 'fail' if problems else ('review' if review else 'pass'),
            'errors': problems, 'reviews': review}


def audit_items(doc: Document) -> dict[str, Any]:
    details = [audit_item(item.raw, item.key)
               for item in sorted(doc.items.values(), key=lambda v: v.key)]
    return {'total': len(details), 'passed': sum(x['status'] == 'pass' for x in details),
            'needs_review': sum(x['status'] == 'review' for x in details),
            'failed': sum(x['status'] == 'fail' for x in details),
            'details': details}


class PatchError(Exception):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise PatchError(reason)


def key_ok(key: Any) -> bool:
    return isinstance(key, str) and bool(SAFE_KEY.fullmatch(key)) and '--' not in key


def safe_text(value: Any, label: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), f'{label} 必须是非空字符串')
    require('\x00' not in value, f'{label} 不能包含 NUL')
    return value.strip()


def validate_lines(value: Any, label: str, must_exist: bool = True) -> list[str]:
    require(isinstance(value, list), f'{label} 必须是数组')
    if must_exist:
        require(bool(value), f'{label} 不得为空')
    result = []
    for index, v in enumerate(value):
        result.append(safe_text(v, f'{label}[{index}]'))
    return result


def attrs_from_marker(text: str) -> dict[str, str]:
    pieces = list(ATTR_RE.finditer(text))
    require(bool(pieces), '接口标记不含元数据')
    rest = ATTR_RE.sub('', text).strip()
    require(not rest, '接口标记属性格式损坏')
    attrs = {p.group(1): html.unescape(p.group(2)) for p in pieces}
    require(len(attrs) == len(pieces), '接口标记包含重复属性')
    require(set(attrs) == {'key', 'module', 'module_title', 'title', 'protocol'},
            '接口标记元数据字段缺失或多余')
    require(key_ok(attrs['key']) and key_ok(attrs['module']), '旧文档接口或模块 key 非法')
    return attrs


@dataclass
class ExistingItem:
    key: str
    module: str
    module_title: str
    title: str
    protocol: str
    manual: str
    raw: str


@dataclass
class Document:
    project_key: str | None = None
    prefix: str = ''
    suffix: str = ''
    items: dict[str, ExistingItem] = field(default_factory=dict)
    archive: str = ''
    exists: bool = False


def validate_manual_content(text: str) -> None:
    require('<!-- API-DOC:' not in text and '<!-- API-DOC-GENERATOR:' not in text,
            '人工维护区包含保留的 API-DOC 结构标记，请先人工修复')
    require('<!-- MANUAL:' not in text, '人工维护区含嵌套 MANUAL 标记，请先人工修复')


def read_document(path: Path) -> Document:
    doc = Document()
    if not path.exists():
        return doc
    require(path.is_file(), f'文档路径不是普通文件: {path}')
    original = path.read_text(encoding='utf-8')
    doc.exists = True
    if not original:
        return doc

    starts = list(DOC_START.finditer(original))
    ends = list(DOC_END.finditer(original))
    require(len(starts) == 1 and len(ends) == 1,
            '已有文档缺少唯一的 API-DOC-GENERATOR 受管区域；拒绝覆盖，请人工迁移')
    start, end = starts[0], ends[0]
    require(start.end() < end.start(), '文档受管区域起止标记顺序错误')
    doc.project_key = html.unescape(start.group(1))
    doc.prefix = original[:start.start()]
    doc.suffix = original[end.end():]
    managed = original[start.end():end.start()]

    items = list(ITEM_RE.finditer(managed))
    archives = list(ARCHIVE_RE.finditer(managed))
    require(len(archives) <= 1, '人工留存区重复')
    remainder = managed
    for match in reversed(sorted(items + archives, key=lambda m: m.start())):
        remainder = remainder[:match.start()] + '\n' + remainder[match.end():]
    require('<!-- API-DOC:' not in remainder and '<!-- MANUAL:' not in remainder,
            '受管区域存在结构标记损坏或嵌套，拒绝更新')

    for match in items:
        meta = attrs_from_marker(match.group('attrs'))
        manual_matches = list(MANUAL_RE.finditer(match.group('body')))
        require(len(manual_matches) == 1, f"接口 {meta['key']} 缺少唯一的人工区标记")
        manual = manual_matches[0].group('text')
        validate_manual_content(manual)
        require(meta['key'] not in doc.items, f"旧文档出现重复接口 key: {meta['key']}")
        doc.items[meta['key']] = ExistingItem(
            key=meta['key'], module=meta['module'],
            module_title=meta['module_title'], title=meta['title'],
            protocol=meta['protocol'], manual=manual, raw=match.group(0),
        )

    if archives:
        doc.archive = archives[0].group('body')
        # Archived descriptions can themselves contain protected MANUAL blocks.
        # Do not edit them and do not interpret them as active endpoints.
        require('<!-- API-DOC:ITEM' not in doc.archive,
                '留存区包含活动接口标记，请人工检查')
    return doc


def parse_patch(path: Path) -> dict[str, Any]:
    try:
        patch = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PatchError(f'补丁 JSON 不可读取: {exc}') from exc
    require(isinstance(patch, dict), '补丁根节点必须是对象')
    required = {'schema_version', 'project_key', 'mode', 'scope_key', 'scan_complete',
                'scan_evidence', 'interfaces', 'deleted'}
    require(set(patch) == required, f'补丁字段必须恰好为: {", ".join(sorted(required))}')
    require(type(patch['schema_version']) is int and patch['schema_version'] == 2,
            '仅支持 schema_version=2；旧版 v1 补丁需增加 examples 字段')
    require(key_ok(patch['project_key']), 'project_key 必须是稳定安全的 ASCII 标识')
    require(patch['mode'] in ('interface', 'module', 'project'), '未知 mode')
    require(key_ok(patch['scope_key']), 'scope_key 非法')
    if patch['mode'] == 'project':
        require(patch['scope_key'] == 'all', 'project 模式 scope_key 必须是 all')
    require(type(patch['scan_complete']) is bool, 'scan_complete 必须是布尔值')
    validate_lines(patch['scan_evidence'], 'scan_evidence', patch['scan_complete'])
    require(isinstance(patch['interfaces'], list), 'interfaces 必须是数组')
    require(isinstance(patch['deleted'], list), 'deleted 必须是数组')
    if patch['deleted']:
        require(patch['scan_complete'], '扫描不完整时严禁删除')
    seen = set()
    for index, item in enumerate(patch['interfaces']):
        label = f'interfaces[{index}]'
        require(isinstance(item, dict), f'{label} 必须是对象')
        fields = {'key', 'module_key', 'module_title', 'title',
                  'protocol', 'source_paths', 'content', 'examples'}
        require(set(item) == fields, f'{label} 字段必须为 {sorted(fields)}')
        for k in ('key', 'module_key'):
            require(key_ok(item[k]), f'{label}.{k} 非法')
        for k in ('module_title', 'title', 'protocol', 'content'):
            safe_text(item[k], f'{label}.{k}')
        for k in ('module_title', 'title', 'protocol'):
            require('\n' not in item[k] and '\r' not in item[k], f'{label}.{k} 不得换行')
        require(not re.search(r'(?m)^#{1,3}\s', item['content']),
                f'{label}.content 不得包含 1–3 级标题（由工具生成）')
        require('<!-- API-DOC:' not in item['content'] and
                '<!-- API-DOC-GENERATOR:' not in item['content'] and
                '<!-- MANUAL:' not in item['content'],
                f'{label}.content 包含保留标记')
        validate_lines(item['source_paths'], f'{label}.source_paths')
        require(not re.search(r'(?m)^#### (?:请求体示例|成功响应体示例|失败响应体示例|请求示例|响应示例|成功响应示例|错误响应示例)\s*$', item['content']),
                f'{label}.content 不允许手写示例章节；请改用 examples 结构字段')
        validate_examples(item, label)
        require(item['key'] not in seen, f'补丁包含重复接口 key: {item["key"]}')
        seen.add(item['key'])
        if patch['mode'] == 'module':
            require(item['module_key'] == patch['scope_key'], f'{label} 越过当前模块范围')
        if patch['mode'] == 'interface':
            require(item['key'] == patch['scope_key'], f'{label} 不属于目标接口')
    deleted_keys = set()
    for index, removed in enumerate(patch['deleted']):
        label = f'deleted[{index}]'
        require(isinstance(removed, dict) and set(removed) == {'key', 'evidence'},
                f'{label} 必须包含 key 和 evidence')
        require(key_ok(removed['key']), f'{label}.key 非法')
        validate_lines(removed['evidence'], f'{label}.evidence')
        require(removed['key'] not in seen, f'{label} 同时出现在新增/更新和删除列表')
        require(removed['key'] not in deleted_keys, f'{label} 重复删除')
        deleted_keys.add(removed['key'])
        if patch['mode'] == 'interface':
            require(removed['key'] == patch['scope_key'], f'{label} 不属于目标接口')
    if patch['mode'] == 'interface':
        require(len(patch['interfaces']) + len(patch['deleted']) == 1,
                'interface 模式必须恰好指定一个生成或删除目标')
    return patch


def anchor(prefix: str, key: str) -> str:
    return 'api-' + prefix + '-' + hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]


def escape_attr(text: str) -> str:
    return html.escape(text, quote=True)


def render_item(item: dict[str, Any], manual: str) -> str:
    validate_manual_content(manual)
    marker = (
        '<!-- API-DOC:ITEM '
        f'key="{escape_attr(item["key"])}" '
        f'module="{escape_attr(item["module_key"])}" '
        f'module_title="{escape_attr(item["module_title"])}" '
        f'title="{escape_attr(item["title"])}" '
        f'protocol="{escape_attr(item["protocol"])}" -->'
    )
    body = item['content'].strip() + '\n\n' + render_examples(item['examples'])
    return (f'{marker}\n<a id="{anchor("interface", item["key"])}"></a>\n'
            f'### {item["title"]}\n\n{body}\n\n'
            f'#### 人工补充说明\n\n<!-- MANUAL:START -->\n'
            f'{manual}<!-- MANUAL:END -->\n<!-- API-DOC:END -->\n')


def old_as_item(old: ExistingItem) -> dict[str, Any]:
    return {'key': old.key, 'module_key': old.module, 'module_title': old.module_title,
            'title': old.title, 'protocol': old.protocol}


def format_managed(project_key: str, items: dict[str, tuple[dict[str, Any], str]],
                   archived: str) -> str:
    by_module: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for item, block in items.values():
        by_module.setdefault(item['module_key'], []).append((item, block))
    for module, members in by_module.items():
        names = {item['module_title'] for item, _ in members}
        require(len(names) == 1,
                f'模块 {module} 的 module_title 不一致，请修正补丁或原文档')
    parts = [f'<!-- API-DOC-GENERATOR:START project="{escape_attr(project_key)}" -->\n',
             '# 接口文档\n\n## 目录\n\n']
    for module in sorted(by_module):
        members = sorted(by_module[module], key=lambda x: x[0]['key'])
        module_title = members[0][0]['module_title']
        parts.append(f'- [{module_title}](#{anchor("module", module)})\n')
        for item, _ in members:
            parts.append(f'  - [{item["title"]}](#{anchor("interface", item["key"])})\n')
    if archived:
        parts.append('- [已移除接口人工说明留存](#api-archived-manual)\n')
    parts.append('\n')
    for module in sorted(by_module):
        members = sorted(by_module[module], key=lambda x: x[0]['key'])
        module_title = members[0][0]['module_title']
        parts.append(f'<a id="{anchor("module", module)}"></a>\n'
                     f'## {module_title}\n\n')
        for _, block in members:
            parts.append(block.rstrip('\n') + '\n\n')
    if archived:
        parts.append('<a id="api-archived-manual"></a>\n'
                     '## 已移除接口人工说明留存\n\n'
                     '<!-- API-DOC:ARCHIVE:START -->\n'
                     + archived.rstrip('\n') + '\n'
                     '<!-- API-DOC:ARCHIVE:END -->\n\n')
    parts.append('<!-- API-DOC-GENERATOR:END -->')
    return ''.join(parts)


def archived_note(old: ExistingItem) -> str:
    if not old.manual.strip():
        return ''
    return (f'### {old.title}（已移除）\n\n'
            f'- 原接口标识：`{old.key}`\n'
            f'- 原模块：{old.module_title}\n\n'
            f'<!-- MANUAL:START -->\n{old.manual}'
            '<!-- MANUAL:END -->\n\n')


def merge(doc: Document, patch: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    require(doc.project_key is None or doc.project_key == patch['project_key'],
            '现有文档的 project_key 与本次项目不符，拒绝覆盖')
    items: dict[str, tuple[dict[str, Any], str]] = {}
    for key, old in doc.items.items():
        items[key] = (old_as_item(old), old.raw)
    summary: dict[str, Any] = {'mode': patch['mode'], 'scope_key': patch['scope_key'],
        'added': 0, 'updated': 0, 'unchanged': 0, 'deleted': 0,
        'archived_manual': 0, 'retained': 0, 'document_changed': False}
    for item in patch['interfaces']:
        key = item['key']
        old = doc.items.get(key)
        if old and patch['mode'] == 'module':
            require(old.module == patch['scope_key'],
                    f'禁止在模块模式下迁移其他模块的接口: {key}')
        manual = old.manual if old else ''
        block = render_item(item, manual)
        current = old.raw if old else None
        if current is None:
            summary['added'] += 1
        elif current == block:
            summary['unchanged'] += 1
        else:
            summary['updated'] += 1
        items[key] = (item, block)
    archived = doc.archive
    for removed in patch['deleted']:
        key = removed['key']
        old = doc.items.get(key)
        if not old:
            continue  # Repeating the same verified deletion remains idempotent.
        if patch['mode'] == 'module':
            require(old.module == patch['scope_key'],
                    f'禁止删除其他模块接口: {key}')
        items.pop(key)
        summary['deleted'] += 1
        note = archived_note(old)
        if note:
            archived += note
            summary['archived_manual'] += 1

    if patch['scan_complete']:
        if patch['mode'] == 'project':
            scope_keys = set(doc.items)
        elif patch['mode'] == 'module':
            scope_keys = {key for key, old in doc.items.items()
                          if old.module == patch['scope_key']}
        else:
            scope_keys = {patch['scope_key']} & set(doc.items)
        leftover = scope_keys - {x['key'] for x in patch['interfaces']} - {
            x['key'] for x in patch['deleted']}
        require(not leftover,
                f'完整扫描范围内存在未重新列出、也未逐项确认删除的旧接口: {sorted(leftover)}')
    summary['retained'] = len(items)
    result = format_managed(patch['project_key'], items, archived)
    if doc.project_key is None:
        require(not doc.exists or not (doc.prefix or doc.suffix),
                '无法自动迁移现有无标记内容')
        return result + '\n', summary
    return doc.prefix + result + doc.suffix, summary


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.api-doc-', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('inspect', help='读取已受管文档中的接口清单')
    inspect.add_argument('--doc', type=Path, default=Path('docs/api.md'))
    audit = commands.add_parser('audit', help='逐接口审计请求体、成功和失败响应示例')
    audit.add_argument('--doc', type=Path, default=Path('docs/api.md'))
    audit.add_argument('--fail-on-review', action='store_true')
    apply = commands.add_parser('apply', help='使用有证据的 JSON 补丁增量维护文档')
    apply.add_argument('--doc', type=Path, default=Path('docs/api.md'))
    apply.add_argument('--patch', type=Path, required=True)
    apply.add_argument('--dry-run', action='store_true')
    apply.add_argument('--diff', action='store_true')
    args = parser.parse_args(argv)
    try:
        doc = read_document(args.doc)
        if args.command == 'inspect':
            print(json.dumps({'exists': doc.exists, 'managed': doc.project_key is not None,
                              'project_key': doc.project_key,
                              'interfaces': [
                                  {'key': x.key, 'module_key': x.module,
                                   'module_title': x.module_title, 'title': x.title,
                                   'protocol': x.protocol, 'has_manual': bool(x.manual.strip())}
                                  for x in sorted(doc.items.values(), key=lambda item: item.key)],
                              'has_archived_manual': bool(doc.archive.strip())},
                             ensure_ascii=False, indent=2))
            return 0
        if args.command == 'audit':
            require(doc.project_key is not None, '当前文件无受管接口文档，无法审计')
            results = audit_items(doc)
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return 3 if results['failed'] or (args.fail_on_review and results['needs_review']) else 0
        patch = parse_patch(args.patch)
        new_content, summary = merge(doc, patch)
        old_content = args.doc.read_text(encoding='utf-8') if args.doc.exists() else ''
        summary['document_changed'] = new_content != old_content
        # The auditor covers all retained items; unchanged legacy items may be flagged.
        with tempfile.TemporaryDirectory() as audit_tmp:
            audit_path = Path(audit_tmp) / 'api.md'
            audit_path.write_text(new_content, encoding='utf-8')
            quality = audit_items(read_document(audit_path))
        summary['quality'] = {k: v for k, v in quality.items() if k != 'details'}
        summary['quality_issues'] = [x for x in quality['details'] if x['status'] != 'pass']
        updated_keys = {x['key'] for x in patch['interfaces']}
        require(not any(x['status'] == 'fail' and x['key'] in updated_keys
                        for x in quality['details']),
                '本次生成接口未通过质量审计，拒绝写入')
        summary['dry_run'] = args.dry_run
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if args.diff:
            for line in difflib.unified_diff(old_content.splitlines(keepends=True),
                    new_content.splitlines(keepends=True),
                    fromfile=str(args.doc) + ' (before)',
                    tofile=str(args.doc) + ' (after)'):
                sys.stdout.write(line)
        if not args.dry_run and summary['document_changed']:
            atomic_write(args.doc, new_content)
        return 0
    except (PatchError, OSError, UnicodeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(run())
