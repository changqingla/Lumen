# Custom Skills

Gateway 将通过 `/api/skills/install` 安装的技能写入此目录。Docker Compose
仅向 Gateway 提供写权限；LangGraph 与沙箱只读使用这些技能。

每个技能必须位于独立子目录，并包含符合项目约定的 `SKILL.md`。
仓库自带技能应放在相邻的 `public/` 目录，不应写入这里。

此目录中的安装产物属于部署态并由 `.gitignore` 排除，只保留本说明文件。
需要随源码发布的技能应经过审查后移入 `public/`。
