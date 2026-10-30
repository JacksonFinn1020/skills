"""Standard-library regression tests for deterministic Markdown merging."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'merge_api_docs.py'
spec = importlib.util.spec_from_file_location('merge_api_docs', SCRIPT)
merge_module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = merge_module
spec.loader.exec_module(merge_module)


def example_cases():
    return {
        'request': {'state': 'present', 'reason': '', 'cases': [
            {'title': '登录请求', 'format': 'json', 'body': {'account': 'demo-user', 'password': 'example-password'},
             'evidence': ['src/routes.py:login request schema']}]},
        'success': {'state': 'present', 'reason': '', 'cases': [
            {'title': 'HTTP 200 成功', 'format': 'json', 'body': {'ok': True, 'sessionId': 'session-demo'},
             'evidence': ['src/routes.py:login success']} ]},
        'error': {'state': 'present', 'reason': '', 'cases': [
            {'title': 'HTTP 401 错误', 'format': 'json', 'body': {'error': {'code': 'unauthorized'}},
             'evidence': ['src/routes.py:login failure']} ]},
    }


def api(key='backend:http:post:/login', module='user', title='登录', content=None):
    return dict(key=key, module_key=module, module_title={'user':'用户模块','order':'订单模块'}[module],
                title=title, protocol='HTTP', source_paths=['src/routes.py'],
                content=content or '#### 基本信息\n\nPOST `/login`', examples=example_cases())


def patch(mode='project', scope='all', interfaces=None, deleted=None, complete=True):
    return dict(schema_version=2, project_key='demo', mode=mode, scope_key=scope,
                scan_complete=complete, scan_evidence=['src/routes.py'] if complete else [],
                interfaces=interfaces if interfaces is not None else [api()],
                deleted=deleted if deleted is not None else [])


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.doc = Path(self.tmp.name) / 'docs' / 'api.md'

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, value):
        payload_path = Path(self.tmp.name) / 'patch.json'
        payload_path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        code = merge_module.run(['apply', '--patch', str(payload_path), '--doc', str(self.doc)])
        self.assertEqual(code, 0)
        return self.doc.read_text(encoding='utf-8')



    def test_missing_error_example_section_rejected_during_audit(self):
        original = self.apply(patch())
        start = original.index('#### 失败响应体示例')
        end = original.index('#### 人工补充说明', start)
        self.doc.write_text(original[:start] + original[end:], encoding='utf-8')
        result = merge_module.audit_items(merge_module.read_document(self.doc))
        self.assertEqual(result['failed'], 1)
        self.assertIn('失败响应体示例 缺失', result['details'][0]['errors'][0])

    def test_manual_notes_headings_do_not_affect_audit(self):
        original = self.apply(patch())
        text = original.replace('<!-- MANUAL:START -->\n',
                '<!-- MANUAL:START -->\n#### 请求体示例\n手工说明，与自动生成无关\n', 1)
        self.doc.write_text(text, encoding='utf-8')
        result = merge_module.audit_items(merge_module.read_document(self.doc))
        self.assertEqual(result['passed'], 1)

    def test_not_applicable_server_only_response(self):
        item = api()
        item['protocol'] = 'WebSocket'
        item['examples']['request'] = {
            'state': 'not_applicable', 'reason': '该事件为服务器主动推送', 'cases': []}
        self.apply(patch(interfaces=[item]))
        self.assertIn('此消息方向不适用', self.doc.read_text(encoding='utf-8'))
        self.assertEqual(merge_module.audit_items(merge_module.read_document(self.doc))['passed'], 1)

    def test_claiming_none_with_body_is_rejected(self):
        item = api()
        item['examples']['request']['state'] = 'none'
        item['examples']['request']['reason'] = '接口没有请求体'
        with self.assertRaises(merge_module.PatchError):
            merge_module.parse_patch(self._write_patch(patch(interfaces=[item])))

    def test_legacy_managed_item_can_be_upgraded_in_place(self):
        original = self.apply(patch())
        start = original.index('<!-- API-DOC:EXAMPLE-STATUS')
        end = original.index('#### 人工补充说明', start)
        text = original[:start] + original[end:]
        self.doc.write_text(text, encoding='utf-8')
        self.assertEqual(merge_module.audit_items(merge_module.read_document(self.doc))['failed'], 1)
        updated = self.apply(patch('interface', api()['key'], [api()], complete=False))
        self.assertIn('#### 成功响应体示例', updated)
        self.assertEqual(merge_module.audit_items(merge_module.read_document(self.doc))['passed'], 1)

    def test_audit_full_pass_and_valid_json(self):
        self.apply(patch())
        result = merge_module.audit_items(merge_module.read_document(self.doc))
        self.assertEqual(result['passed'], 1)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(result['needs_review'], 0)
        self.assertEqual(merge_module.run(['audit', '--doc', str(self.doc)]), 0)

    def test_missing_examples_fails_patch(self):
        item = api()
        del item['examples']
        with self.assertRaises(merge_module.PatchError):
            merge_module.parse_patch(self._write_patch(patch(interfaces=[item])))

    def test_missing_success_example_cannot_claim_present(self):
        item = api()
        item['examples']['success']['cases'] = []
        with self.assertRaises(merge_module.PatchError):
            merge_module.parse_patch(self._write_patch(patch(interfaces=[item])))

    def test_ellipsis_placeholder_rejected(self):
        item = api()
        item['examples']['success']['cases'][0]['body'] = {'modules': {'…': '…'}}
        with self.assertRaises(merge_module.PatchError):
            merge_module.parse_patch(self._write_patch(patch(interfaces=[item])))

    def test_invalid_non_json_example_placeholder_rejected(self):
        item = api()
        item['examples']['request']['cases'][0]['format'] = 'text'
        item['examples']['request']['cases'][0]['body'] = '{"session": ...}'
        with self.assertRaises(merge_module.PatchError):
            merge_module.parse_patch(self._write_patch(patch(interfaces=[item])))

    def test_no_request_body_explicitly_documented(self):
        item = api()
        item['examples']['request'] = {
            'state': 'none', 'reason': 'GET 查询只有路径参数，不接收请求体', 'cases': []}
        doc = self.apply(patch(interfaces=[item]))
        self.assertIn('无请求体', doc)
        self.assertIn('GET 查询只有路径参数', doc)
        self.assertEqual(merge_module.audit_items(merge_module.read_document(self.doc))['passed'], 1)

    def test_unknown_response_is_reported(self):
        item = api()
        item['examples']['success'] = {
            'state': 'unknown', 'reason': '运行时插件生成响应，静态代码中没有完整契约', 'cases': []}
        doc = self.apply(patch(interfaces=[item]))
        self.assertIn('待确认：无法从当前代码确定响应体示例', doc)
        quality = merge_module.audit_items(merge_module.read_document(self.doc))
        self.assertEqual(quality['needs_review'], 1)
        self.assertEqual(merge_module.run(['audit', '--doc', str(self.doc), '--fail-on-review']), 3)

    def test_websocket_multiple_event_examples(self):
        item = api()
        item['protocol'] = 'WebSocket'
        item['examples']['success']['cases'] = [
            {'title': name, 'format': 'json', 'body': {'type': name, 'session_id': 'demo'},
             'evidence': ['src/ws/handler.py']} for name in ('session.started', 'asr.final')]
        self.apply(patch(interfaces=[item]))
        doc = self.doc.read_text(encoding='utf-8')
        self.assertIn('session.started', doc)
        self.assertIn('asr.final', doc)
        self.assertEqual(merge_module.audit_items(merge_module.read_document(self.doc))['passed'], 1)

    def test_audit_detects_removed_code_example(self):
        original = self.apply(patch())
        mutated = original.replace('```json\n{', '```json\n{INVALID', 1)
        self.doc.write_text(mutated, encoding='utf-8')
        quality = merge_module.audit_items(merge_module.read_document(self.doc))
        self.assertEqual(quality['failed'], 1)
        self.assertEqual(merge_module.run(['audit', '--doc', str(self.doc)]), 3)

    def test_non_json_text_example(self):
        item = api()
        item['protocol'] = 'GraphQL'
        item['examples']['request'] = {
            'state': 'present', 'reason': '', 'cases': [
            {'title': 'GraphQL 操作', 'format': 'graphql',
             'body': 'query User($id: ID!) { user(id: $id) { id name } }',
             'evidence': ['schema.graphql:User']}]}
        self.apply(patch(interfaces=[item]))
        self.assertIn('```graphql', self.doc.read_text(encoding='utf-8'))
        self.assertEqual(merge_module.audit_items(merge_module.read_document(self.doc))['passed'], 1)

    def test_legacy_section_is_flagged_for_upgrade(self):
        self.apply(patch())
        text = self.doc.read_text(encoding='utf-8')
        text = text.replace('<!-- API-DOC:EXAMPLE-STATUS request="present" success="present" error="present" -->', '')
        self.doc.write_text(text, encoding='utf-8')
        self.assertEqual(merge_module.audit_items(merge_module.read_document(self.doc))['failed'], 1)

    def test_schema_v1_patches_rejected(self):
        legacy = patch()
        legacy['schema_version'] = 1
        with self.assertRaises(merge_module.PatchError):
            merge_module.parse_patch(self._write_patch(legacy))

    def test_new_and_idempotent(self):
        first = self.apply(patch())
        second = self.apply(patch())
        self.assertEqual(first, second)
        self.assertEqual(first.count('### 登录'), 1)
        self.assertIn('## 用户模块', first)
        self.assertIn('## 目录', first)
        self.assertIn('#### 请求体示例', first)
        self.assertIn('#### 成功响应体示例', first)
        self.assertIn('#### 失败响应体示例', first)
        self.assertIn('\"sessionId\": \"session-demo\"', first)

    def test_incremental_single_interface_preserves_unrelated(self):
        original = [api(), api('backend:http:get:/orders', 'order', '查询订单')]
        first = self.apply(patch(interfaces=original))
        updated = api(content='#### 基本信息\n\nPOST `/login`\n\n#### 业务规则\n\n仅限注册用户')
        second = self.apply(patch('interface', updated['key'], [updated], complete=False))
        self.assertIn('仅限注册用户', second)
        self.assertEqual(first.count('### 查询订单'), second.count('### 查询订单'))
        self.assertIn('## 订单模块', second)

    def test_module_update_does_not_delete_other_module(self):
        first = [api(), api('backend:http:get:/orders', 'order', '查询订单')]
        self.apply(patch(interfaces=first))
        second = self.apply(patch('module', 'user', [api(title='用户登录')]))
        self.assertIn('### 查询订单', second)
        self.assertIn('### 用户登录', second)

    def test_module_cannot_cross_module_move(self):
        item = api()
        self.apply(patch())
        item['module_key'] = 'order'
        item['module_title'] = '订单模块'
        with self.assertRaises(merge_module.PatchError):
            merge_module.merge(merge_module.read_document(self.doc),
                 merge_module.parse_patch(self._write_patch(patch('module', 'order', [item]))))

    def _write_patch(self, data):
        p = Path(self.tmp.name) / 'temporary.json'
        p.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
        return p

    def test_manual_preserved_byte_for_byte(self):
        original = self.apply(patch())
        manual = '\n联调账号由测试人员提供。\n\n- **勿覆盖**\n'
        self.doc.write_text(original.replace('<!-- MANUAL:START -->\n',
                                '<!-- MANUAL:START -->\n' + manual), encoding='utf-8')
        changed = api(content='#### 基本信息\n\n接口有变更')
        result = self.apply(patch('interface', changed['key'], [changed], complete=False))
        self.assertIn(manual, result)
        self.assertIn('接口有变更', result)
        self.assertNotIn('POST `/login`', result)

    def test_deletion_retains_manual_archive(self):
        original = self.apply(patch())
        content = original.replace('<!-- MANUAL:START -->\n',
                    '<!-- MANUAL:START -->\n人工联调注意：保留这段。\n', 1)
        self.doc.write_text(content, encoding='utf-8')
        removed = [{'key': api()['key'], 'evidence': ['已经检查 src/routes.py 和全局注册点，路由不存在']}]
        updated = self.apply(patch(interfaces=[], deleted=removed))
        self.assertNotIn('### 登录\n', updated)
        self.assertIn('## 已移除接口人工说明留存', updated)
        self.assertIn('人工联调注意：保留这段。', updated)
        self.assertEqual(updated, self.apply(patch(interfaces=[], deleted=removed)))

    def test_deletion_requires_explicit_per_item_evidence(self):
        self.apply(patch())
        with self.assertRaises(merge_module.PatchError):
            merge_module.merge(merge_module.read_document(self.doc),
                    merge_module.parse_patch(self._write_patch(patch(interfaces=[]))))

    def test_incomplete_scan_cannot_delete(self):
        changed = patch(interfaces=[], deleted=[{'key':api()['key'],'evidence':['deleted']}], complete=False)
        with self.assertRaises(merge_module.PatchError):
            merge_module.parse_patch(self._write_patch(changed))

    def test_unmanaged_document_fails_without_modifying(self):
        self.doc.parent.mkdir(parents=True)
        self.doc.write_text('# Existing hand-maintained docs\n', encoding='utf-8')
        self.assertEqual(merge_module.run(['apply', '--patch', str(self._write_patch(patch())),
                            '--doc', str(self.doc)]), 2)
        self.assertEqual(self.doc.read_text(encoding='utf8'), '# Existing hand-maintained docs\n')

    def test_different_project_is_rejected(self):
        self.apply(patch())
        incompatible = patch()
        incompatible['project_key'] = 'other'
        with self.assertRaises(merge_module.PatchError):
            merge_module.merge(merge_module.read_document(self.doc),
                 merge_module.parse_patch(self._write_patch(incompatible)))

    def test_dry_run_no_write(self):
        path = self._write_patch(patch())
        self.assertEqual(merge_module.run(['apply', '--patch', str(path), '--doc',
                          str(self.doc), '--dry-run']), 0)
        self.assertFalse(self.doc.exists())

    def test_malformed_protection_marker_refuses(self):
        document = self.apply(patch())
        self.doc.write_text(document.replace('<!-- MANUAL:END -->', ''), encoding='utf-8')
        with self.assertRaises(merge_module.PatchError):
            merge_module.read_document(self.doc)

    def test_inspect_inventory(self):
        self.apply(patch())
        doc = merge_module.read_document(self.doc)
        self.assertEqual(list(doc.items), [api()['key']])

    def test_module_stale_record_requires_confirmation(self):
        self.apply(patch(interfaces=[api(), api('backend:http:get:/orders', 'order', '查询订单')]))
        with self.assertRaises(merge_module.PatchError):
            merge_module.merge(merge_module.read_document(self.doc),
                merge_module.parse_patch(self._write_patch(patch('module', 'user', []))))


if __name__ == '__main__':
    unittest.main()
