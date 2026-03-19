---
name: hive-doris-sql-transformation
description: 将 Hive HQL 文件转换为 Apache Doris SQL 文件。支持单文件和目录批量处理。触发场景：用户提供 .hql 文件路径或目录路径，要求转换/迁移 Hive SQL 到 Doris；或用户说"把这个 HQL 转成 Doris SQL"、"迁移 Hive 表到 Doris"、"批量转换 HQL 文件"等。
---

# Hive → Doris SQL 转换

## 工作流程

### Step 1：运行转换脚本

使用 `scripts/hive_to_doris.py` 完成基础语法自动转换：

```bash
# 单文件
python3 <skill_dir>/scripts/hive_to_doris.py <file.hql>
# 指定输出
python3 <skill_dir>/scripts/hive_to_doris.py <file.hql> --output <file.sql>

# 批量目录（输出到 doris_output/ 子目录）
python3 <skill_dir>/scripts/hive_to_doris.py <directory/>
# 指定输出目录
python3 <skill_dir>/scripts/hive_to_doris.py <directory/> --output-dir <out_dir/>
```

脚本自动处理：
- 数据类型映射（STRING → VARCHAR, TIMESTAMP → DATETIME 等）
- 移除 Hive 存储子句（STORED AS、ROW FORMAT、LOCATION、TBLPROPERTIES）
- CLUSTERED BY → DISTRIBUTED BY HASH
- INSERT OVERWRITE PARTITION → INSERT OVERWRITE（移除分区规格）
- 函数替换（NVL → IFNULL, COLLECT_LIST → GROUP_CONCAT 等）
- 标注需要手动处理的 `-- [WARN]` 和 `-- TODO` 注释

### Step 2：处理 WARN / TODO 注释

转换后文件头部和内联注释会标出需要手动介入的项目：

| 标注 | 含义 | 操作 |
|------|------|------|
| `-- TODO: Add data model` | 缺少 DUPLICATE/UNIQUE/AGGREGATE KEY | 根据业务语义选择并添加 |
| `-- TODO: PARTITION BY RANGE/LIST` | 分区需手动重写 | 参考 conversion_rules.md §2.3 |
| `-- [WARN] collect_list` | 函数语义有差异 | 验证 GROUP_CONCAT 是否满足需求 |
| `-- [WARN] EXTERNAL TABLE` | 外部表需配置 Catalog | 参考 conversion_rules.md §2.5 |
| `-- [WARN] UDF` | UDF 需重写 | 按 Doris Java UDF API 重实现 |

### Step 3：验证和补全

转换完成后逐项确认：

1. **数据模型**：每张表都有 `DUPLICATE KEY` / `UNIQUE KEY` / `AGGREGATE KEY`
2. **分布键**：`DISTRIBUTED BY HASH(合适字段) BUCKETS N`，N 根据数据量调整
3. **分区**：动态分区属性或显式分区定义
4. **函数语义**：重点检查 `GROUP_CONCAT`（原 collect_list）、`DATE_TRUNC`（参数顺序与 TRUNC 相反）
5. **UDF**：标记为 `-- [WARN] UDF` 的需另行实现

详细规则参考 `references/conversion_rules.md`：
- **数据类型映射** → §1
- **DDL 差异（分区/分桶/外部表）** → §2
- **DML 差异（INSERT/LOAD）** → §3
- **函数映射完整表** → §4
- **窗口函数** → §5
- **不支持特性列表** → §7

### Step 4：输出结果

向用户展示：
1. 转换后的 SQL 内容（单文件时直接展示；批量时列出文件清单和 warning 汇总）
2. 需要手动处理的事项列表（从 WARN 注释汇总）
3. 建议的后续验证步骤

## 注意事项

- 脚本做**尽力而为**的转换，不能保证 100% 正确，复杂嵌套查询需人工审查
- `BINARY` 类型转为 `VARCHAR(65533)`，如有业务含义需评估
- Multi-insert（`FROM t INSERT INTO a ... INSERT INTO b`）需手动拆成多条语句
- Hive `TRUNC(date, 'MM')` → Doris `DATE_TRUNC('month', date)`，**参数顺序相反**
