# Frontend Features

该目录用于承载按业务域组织的前端代码。

当前结构已以业务域为主组织：

- 每个 feature 自己维护 `pages/`、`components/`、`hooks/`、`api/`、`lib/`
- 仅在跨两个及以上业务域稳定复用时，才提升到 `src/shared/`
- 新代码不得再回落到根级 `pages/`、`hooks/`、`utils/`、`lib/`
