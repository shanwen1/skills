# 浏览器传输适配：优先复用已连接能力

浏览器只是向已有 ChatGPT 对话收发消息的传输层，不负责独立验收。此文不承诺任意 Codex/Chrome 都有相同 API。

## 接入顺序

1. 检查当前 Agent 已有 Chrome 插件、浏览器 Skill、系统工具。优先使用已经实际跑通的**Chrome 原生 UI 操作**或经用户授权的扩展；不为“标准化”而替换工作通道。
2. 用户给出**完整当前对话 URL**；核对页面 URL 中 conversation ID，看到已登录对话的内容与输入框。拒绝只按页面标题模糊搜索；不得自动新建会话。
3. 只读确认可以获取 ChatGPT 输入框和最新消息。若扩展接口超时，而原生 UI 可操作，可以在用户已有授权范围内用原生 UI；不要称扩展 API 已恢复。
4. 浏览器操作按**读取 → 输入 → 发送 → 验证可见 → 等待回复完整 → 读取 → 校验 ID**顺序串行执行。每次发送前复核当前 URL，防止标签切换。
5. 等待回复时不以页面中“出现第一个字”作为完成；检查界面完成标志和内容稳定性，遇错误提示、需要人工登录、配额限制则停止。
6. 只有最近一次**完整**且 `run_id + turn_id + reviewed_sha` 匹配的 GPT 回复能驱动下一轮；旧回复不得复用。
7. 不在调试日志、截图路径或项目仓库里记录 Cookies、访问令牌、用户隐私资料。浏览器状态只保留在用户原有 Chrome Profile；绝不复制个人 Chrome Profile。

## 工具选择

- 已有的 Codex Chrome/Browser 能力可以读写指定页面：优先复用。
- 需要其他方案时，可评估 Browser Harness、Playwright Extension 等工具；**先说明安装、权限扩大和隐私风险并取得用户同意**。Whole-browser CDP 可能暴露同一 Profile 内其他标签页。
- 若只能读取、不能发送，或跨站权限被拒绝：如实报告阻断。不要通过修改 Chrome 安全设置、抓取会话 Cookie 或绕过网页授权来规避。

## 建议探针顺序

`BIND_OK → READ_OK → PING_SENT (已在正确对话看见) → PONG_RECEIVED (完整、匹配) → HANDOFF_SENT → REVIEW_RECEIVED`。

同一时刻一个浏览器控制者操作目标对话；多 Agent 并发时对浏览器使用单执行队列。若发送后工具报超时，必须先只读确认该消息是否已经出现，避免重复发送。DOM 结构变化时先重新检查页面；不要硬编码未经验证的选择器。

## 可迁移能力说明

已有一次用户确认的成功案例：Codex/Luna 通过 Chrome 原生 UI 与 ChatGPT 现有对话双向交接。**本仓库没有复制那次环境的私有脚本或浏览器凭据**；新环境仍需确认本地实际工具能力，不能因为安装了 Skill 就宣布连接成功。

## 不要误认浏览器外壳为 bridge runner

看到 ChatGPT 页面、Electron 窗口或本地 CDP 端口，只能证明浏览器界面存在；不能证明 `gpt-codex-bridge` 正在持续监听。必须同时核对 bridge 状态文件、`bridgectl`/`bridge_runtime` 进程和最新消息 ID。像 Codex Web GPT 这类 launcher 可能提供 ChatGPT Web 访问或 CDP，但不是本 Skill 的调度器；不要为“让它常驻”而停止或重建用户正在使用的程序。
