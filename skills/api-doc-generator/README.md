# api-doc-generator v2（Claude Code Skill）

**从真实代码生成接口文档，强制提供请求体、成功响应体和失败响应体示例。** 识别实际使用的 HTTP/REST、WebSocket、GraphQL、gRPC 等协议，由 Claude Code 分析代码并输出 v2 JSON 补丁；Python 合并/校验脚本确保单文件 `docs/api.md` 具有结构化示例，并维护人工内容与增量更新。

## 安装

将 `api-doc-generator` 文件夹完整复制到项目的 `.claude/skills/api-doc-generator/`，或到用户级 `~/.claude/skills/api-doc-generator/`。本项目下至少应含：

```text
.claude/skills/api-doc-generator/
├── SKILL.md
├── README.md
├── references/
│   ├── analysis-workflow.md
│   ├── documentation-spec.md
│   └── patch-contract.md
├── scripts/
│   └── merge_api_docs.py
├── examples/
│   ├── patch-example.json
│   └── websocket-patch-example.json
└── tests/
    ├── test_merge_api_docs.py
    └── test_smoke_prefix.py
```

需要 Python 3.9+；无需第三方 Python 依赖。若此前安装 v1，请先备份旧 Skill 目录再覆盖同名目录；已维护的 `docs/api.md` 不需要直接删除。

## 使用

在仓库根目录启动 Claude Code：

```text
/api-doc-generator 全项目
/api-doc-generator 模块 用户
/api-doc-generator 接口 POST /api/user/login
```

或明确自然语言提出“请生成/更新接口文档”。普通代码修改不会自动触发 Skill。

## v2 带来的改变

- **每条接口**都有独立的 `请求体示例`、`成功响应体示例`、`失败响应体示例` 章节；确实没有对应 Body 或无法确定时也会显式说明，不能隐去。
- JSON 示例由结构化 JSON 数据生成，保证 fenced code block 可被解析。拒绝省略号、TODO、缺失案例等典型伪示例。
- 每条示例需引用 Agent 实际审阅过的源码位置。脚本只校验声明和格式，不负责源码语义验证。
- WebSocket 消息/事件不再仅列表概括，应逐事件输出完整载荷示例，支持不同形态的多案例。
- 新增 `audit` 命令，对每条接口报告 `pass / review / fail`，识别旧版缺例子章节。
- 旧版安全增量合并、按模块/接口更新、删除校验、人工内容原样保留均继续支持。

## 本地自测

```bash
python3 -m unittest discover -s .claude/skills/api-doc-generator/tests -v
python3 .claude/skills/api-doc-generator/scripts/merge_api_docs.py --help
python3 .claude/skills/api-doc-generator/scripts/merge_api_docs.py audit --doc docs/api.md
```

`audit` 在有错误时返回 3；`--fail-on-review` 会将待确认也视为返回 3。`apply` 在语法或补丁约束错误时返回 2。

## v1 升级和已有 API.md 迁移

1. 替换 Skill 目录，保留现有 `docs/api.md` 备份。
2. 如已有 `API-DOC-GENERATOR` 受管标记，先 `inspect`，再 `audit` 查找缺少请求/响应示例的旧条目，按模块或全量重新生成。**v2 脚本兼容读取原受管文档，但不接受 v1 补丁**。
3. 如旧文档（例如人工编写的 `API.md`）不含管理标记，工具将拒绝自动覆盖。请先人工比对、备份并明确迁移方案，不能为了通过更新而直接覆盖。
4. 先执行 `apply --dry-run --diff` 检查涉及接口，确认后正式应用，最后运行 `audit`。

注意：此压缩包没有目标业务项目的源码。示例补丁仅展示输入格式，不代表你的真实 API。只有在有仓库访问权限的 Claude Code 中运行 Skill，才能分析真实接口并产生基于实现的示例。
