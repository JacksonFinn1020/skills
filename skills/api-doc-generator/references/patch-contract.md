# 合并补丁协议 v2（schema_version = 2）

## CLI 用法

```bash
python3 .claude/skills/api-doc-generator/scripts/merge_api_docs.py inspect --doc docs/api.md
python3 .claude/skills/api-doc-generator/scripts/merge_api_docs.py apply --patch /tmp/api-doc-patch.json --doc docs/api.md --dry-run --diff
python3 .claude/skills/api-doc-generator/scripts/merge_api_docs.py apply --patch /tmp/api-doc-patch.json --doc docs/api.md
python3 .claude/skills/api-doc-generator/scripts/merge_api_docs.py audit --doc docs/api.md
python3 .claude/skills/api-doc-generator/scripts/merge_api_docs.py audit --doc docs/api.md --fail-on-review
```

纯 Python 3.9+ 标准库，不需要安装第三方依赖。脚本只**合并/检查文件**，不负责扫描代码、发现接口或验证源码语义。

## v2 补丁示例

真实完整 JSON 参考 `examples/patch-example.json` 和 `examples/websocket-patch-example.json`。

```json
{
  "schema_version": 2,
  "project_key": "sample-app",
  "mode": "interface",
  "scope_key": "backend:http:post:/api/user/login",
  "scan_complete": true,
  "scan_evidence": ["检查了 src/routes/user.py 中的注册和处理逻辑"],
  "interfaces": [{
    "key": "backend:http:post:/api/user/login",
    "module_key": "user", "module_title": "用户模块",
    "title": "用户登录", "protocol": "HTTP",
    "source_paths": ["src/routes/user.py"],
    "content": "#### 基本信息\n\nPOST `/api/user/login`。\n\n#### 请求参数\n\n| 字段 | 类型 | 必填 | 说明 |\n|---|---|---|---|\n| `account` | string | 是 | 账号 |",
    "examples": {
      "request": {"state": "present", "reason": "", "cases": [
        {"title": "JSON 请求体", "format": "json", "body": {"account": "demo-account", "password": "demo-password"}, "evidence": ["src/routes/user.py:login request schema"]}
      ]},
      "success": {"state": "present", "reason": "", "cases": [
        {"title": "HTTP 200", "format": "json", "body": {"ok": true}, "evidence": ["src/routes/user.py:success response"]}
      ]},
      "error": {"state": "unknown", "reason": "尚未核实该演示接口的异常处理器", "cases": []}
    }
  }],
  "deleted": []
}
```

**上方为补丁结构示意，不代表任何真实项目，字段值必须在实际运行时依据源码重新生成。**

## 字段约束

- `schema_version`：**只接受 2**。此前 v1 补丁应补 `examples` 后转换为 v2，不可直接发送。
- `project_key`：跨次运行稳定的项目标识，需匹配受管文档。避免通过临时目录名推断。
- `mode`：`interface`、`module`、`project`。
- `scope_key`：单接口时等于接口 key；模块时为 module_key；全项目为 `all`。
- `scan_complete`：布尔值；有完整扫描时 `scan_evidence` 必须非空。扫描不完整时严禁任何删除。
- `interfaces`：每项必须恰好含 `key, module_key, module_title, title, protocol, source_paths, content, examples`。
- `content`：正文 Markdown，不能带 `#` 到 `###` 标题、受管注释或三类示例章节。示例从 `examples` 自动生成。
- `examples`：必须恰好有 `request, success, error`。每组 `{state, reason, cases}`。`present` 至少一条案例，`reason=""`；其他状态必给具体单行 `reason`，`cases=[]`。
- 每个示例案例必须恰好有 `title, format, body, evidence`；`evidence` 为非空路径/代码定位数组。`format=json` 时 `body` 是原生 JSON 值而非 JSON 字符串；其他格式 `body` 为非空文本。
- `deleted`：每项 `{key, evidence}`，只删除已存在、且完整扫描范围内核实不存在的 API。新增/更新与删除不能重叠。

### 状态值与质量报告

| state | 何时使用 | 生成文档 | 审计状态 |
|---|---|---|---|
| `present` | 有源码证实的示例 | 显示完整 fenced code 示例与证据 | `pass`（格式检查通过时） |
| `none` | 源码/协议证实无请求体或响应体 | 明确说明无对应 Body | `pass` |
| `not_applicable` | 消息方向/接口形态不适用 | 明确说明不适用 | `pass` |
| `unknown` | 深度排查后仍无法确定 | 标注待确认和具体原因 | `review` |

`audit` JSON 输出包括 `total, passed, needs_review, failed, details`。`failed > 0` 的质量问题必须修复；`needs_review > 0` 不可宣称完整，必须向用户报告。`--fail-on-review` 把 `review` 也视为非零退出码 3。

脚本能确定 JSON *语法*、示例结构、占位符及章节存在，但**无法自动确认字段是否真实由源码返回**，此责任在调用 Agent 的追踪和核验工作。

## 增量、旧文档与删除保护

- 第一次写入创建 `<!-- API-DOC-GENERATOR:START project="..." -->` / `<!-- API-DOC-GENERATOR:END -->` 管理区；管理区外所有内容保持原样。
- 已有非空文档但无管理标记时拒绝覆盖；先人工迁移，或另行确认备份并处理。
- 旧 v1 格式的受管文档可由 v2 脚本读取；尚未通过新规范的旧条目将在 `audit` 中标记 `fail`，可逐接口或全项目更新。全量完整扫描时旧 key 必须重列或逐项经证实删除。
- 单接口只能更新/删除目标 key；模块模式不能越界；全量扫描缺少旧 key 但未标注删除时直接拒绝。即便全量也**绝不自行以差集删除**。
- 人工区 `<!-- MANUAL:START -->` / `<!-- MANUAL:END -->` 永远原样保留；确认删除接口后将人工文字迁入 `已移除接口人工说明留存`，不把旧接口当作活跃接口。
- 先 dry run/diff 再写入；实际写入使用同目录临时文件和原子替换，失败时不改变目标文档。
