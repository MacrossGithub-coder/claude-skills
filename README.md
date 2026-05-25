# macrossz-skills

A personal collection of Claude Code skills.

## Installation

Add to your `~/.claude/settings.json`:

```json
"extraKnownMarketplaces": {
  "macrossz-skills": {
    "source": {
      "source": "github",
      "repo": "MacrossGithub-coder/claude-skills"
    }
  }
}
```

Then install skills via `/plugins` in Claude Code.

## Available Skills

| Skill | Description |
|-------|-------------|
| `hive-doris-sql-transformation` | 将 Hive HQL 文件转换为 Apache Doris SQL 文件，支持单文件和批量处理 |
| `backtrader-multi` | 对单只股票运行多策略量化回测，并生成中英双语的可分享分析报告 |
