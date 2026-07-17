# Chat Feature

该目录承载聊天相关页面、组件、hooks、API 适配与工具函数。

当前职责边界：

- `pages/` 放聊天页面
- `components/` 放聊天私有组件
- `hooks/` 放聊天状态与交互逻辑
- `api/` 放聊天流式接口与运行时协议适配
- `lib/` 放聊天域私有纯逻辑与展示辅助

`useRAGChat` 只负责 React 生命周期、会话运行时注册和流式编排。消息协议规范化、历史与实时状态合并、断流恢复以及未完成工具调用收尾集中在 `lib/runtime-message.ts`，这些规则不依赖 React，可独立测试。
