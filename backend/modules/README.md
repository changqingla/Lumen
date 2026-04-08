# Backend Modules

`backend/modules/` 是业务域代码的主目录。

目录约束：

1. 模块优先按业务域组织，而不是新增根级技术层目录
2. 模块内优先就近放置 `router/controller/services/registry` 等域内实现
3. 只有跨多个业务域稳定复用的能力，才进入 `backend/shared/`
4. 基础设施适配放入 `backend/infrastructure/`
