# Markdown 文档规范 v2

## 唯一文档与层级

唯一主文件为仓库根目录 `docs/api.md`。合并脚本自动组织：文档标题、目录、模块标题（`##`）、接口标题（`###`）、人工补充区和已移除接口的人工留存区。Agent 只生成每个接口独立的 `content`（`####` 小节）和机器可验证的 `examples`，**不要在 content 手写示例章节**。

每个接口的正文包括：

1. `#### 基本信息`：方法/路径/消息方向/协议/用途/版本/认证要求。
2. `#### 请求信息`：HTTP Header、Content-Type、Path/Query/Body，或协议专属的连接条件、事件触发条件等。
3. `#### 请求参数`：字段、类型、必填条件、默认值、说明、校验规则；嵌套字段使用完整路径，例如 `items[].text`。
4. `#### 响应结构`：最终序列化后的数据形态、状态码、字段和枚举，统一包装器、可空性与可选条件。
5. `#### 业务规则`、`#### 错误码`、`#### 注意事项`：有来源的内容才写入。
6. **脚本额外自动渲染三个接口级章节：`#### 请求体示例`、`#### 成功响应体示例`、`#### 失败响应体示例`**。每个章节即使不适用或待确认，也必须呈现明确的状态与原因。

## 每条接口强制示例

JSON 补丁中 `interfaces[i].examples` 必含 `request/success/error` 三组。每组必有：

- `state`: `present`、`none`、`not_applicable`、`unknown`。
- `reason`: `present` 时空字符串；其他状态写具体原因。
- `cases`: `present` 至少一条；其他状态为空数组。

每条 `case` 必含 `title`、`format`（`json`/`http`/`bash`/`graphql`/`protobuf`/`text`）、`body`、`evidence`（至少一个实际源文件及定位说明）。`format=json` 的 `body` 是 JSON 数据值，**不是包含转义 JSON 的字符串**：例如直接写 `"body": {"id": "demo-id"}`；脚本用 `json.dumps` 输出合法 JSON 代码块。

### 正确处理不适用和不确定

- HTTP GET / DELETE / HEAD 不意味着一定没有 Body：**按实际代码判断**。确认无请求体才用 `request.state=none`，同时在正文记录实际 Path/Query/Header 入参。
- HTTP 204、gRPC 空消息、WS 单向事件等，只有按实际协议可确认时才使用 `none` 或 `not_applicable`。
- **不能从源码确认的响应**用 `unknown`，给出具体查证结果与下一步排查路径；输出标记“待确认”，审计为 `needs_review`。不得为了通过审计伪造 JSON。
- `error.state=none` 只能用于**确认不存在该类失败消息/负载**的接口，不意味着不存在网络或框架层故障。否则可用 `unknown` 并说明未能确定。
- JSON 示例不得使用 `...`、`…`、`{…}`、`<api_key>`、`TODO`、`TBD` 或缺失括号等无效/省略式内容；模拟值应以真实字段类型为准并使用脱敏数据。

### HTTP 示例覆盖

- 每个 JSON Body 接口至少一个完整请求体和一个成功响应体；真实可能出现多个重要状态码时分别列出典型情形。
- 请求只有 Query/Path 的接口请求体区明确“无请求体”，同时在请求参数表中写明 Query/Path、必填、枚举和默认值。
- 响应为数组、分页、统一包装、文件、流式输出时使用实际外部形态，不能只写内部 Service 的返回类型。
- 对可确认的典型错误使用完整错误响应；没有完整错误映射不能随意编造 HTTP 400/500 或业务码。

### WebSocket 示例覆盖

- 一条 WebSocket 连接可能包含多类客户端消息与服务端事件。**要按消息类型或事件类型建立可检索的条目**，也可以在一个业务操作下用多 `cases` 明确每种受支持的事件。
- 每条客户端发送消息提供完整消息 JSON，例如 `session.start`、`session.update`；对应的确认/业务成功事件提供完整响应消息结构。
- `session.started`、`asr.final`、`sv.final` 等**服务端事件应当分别提供完整 JSON 消息示例**，并注明连接范围、事件方向、必需元数据、何时触发、可选字段及事件之间的顺序。
- 音频二进制帧无法直接作为 JSON 请求体，应描述帧格式、时序及样本构造方法，并用 `none/not_applicable` 的适当状态表达方向或非 JSON 帧结构，不能虚构 base64 音频数据。

### GraphQL / gRPC

- GraphQL Query/Mutation：给出完整 query 文本及变量 JSON；对于 Subscription 列出服务端 payload 示例。必要时用多 `cases`。
- gRPC：基于 proto 的请求/响应 message 给出可确认的 JSON 映射或 textproto 示例；区分一元和流式。不得将 proto 的 `oneof`/`optional`/默认值写错。

## 文档格式与人工维护

统一中文说明、原生字段命名、Markdown 表格，不写无根据的内部实现细节。不泄漏真实凭据或用户隐私。示例验证属于代码事实溯源，不是简单模仿此前 API.md 的格式。

`<!-- MANUAL:START -->` 与 `<!-- MANUAL:END -->` 之间内容由用户维护，必须保持原样。没有受管区标记的既有文档不得直接覆盖；先迁移或征得明确同意后采取安全处理。
