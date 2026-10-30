"""Tests that documentation is isolated to its managed region."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'merge_api_docs.py'
spec = importlib.util.spec_from_file_location('merge_api_docs_smoke', SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class ManagedRegionTest(unittest.TestCase):
    def test_document_unmanaged_prefix_suffix_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc_path = Path(tmp) / 'api.md'
            item = dict(key='svc:http:get:/ping', module_key='system',
                        module_title='系统模块', title='健康检查', protocol='HTTP',
                        source_paths=['src/routes.ts'], content='#### 基本信息\n\nGET `/ping`',
                        examples={
                            'request': {'state':'none','reason':'GET ping 不接收请求体','cases':[]},
                            'success': {'state':'present','reason':'','cases':[
                                {'title':'成功响应','format':'json','body':{'status':'ok'},
                                 'evidence':['src/routes.ts']} ]},
                            'error': {'state':'unknown','reason':'未在源码找到失败响应契约','cases':[]}
                        })
            value = dict(schema_version=2, project_key='repo', mode='project',
                         scope_key='all', scan_complete=True, scan_evidence=['all routers'],
                         interfaces=[item], deleted=[])
            first, _ = module.merge(module.read_document(doc_path), value)
            doc_path.write_text('手工前言\n\n' + first + '\n\n手工后记\n', encoding='utf-8')
            second, _ = module.merge(module.read_document(doc_path), value)
            self.assertEqual(second, doc_path.read_text(encoding='utf-8'))
            self.assertTrue(second.startswith('手工前言\n\n'))
            self.assertTrue(second.endswith('\n\n手工后记\n'))


if __name__ == '__main__':
    unittest.main()
