# Hive → Doris 转换规则参考

## 1. 数据类型映射

| Hive 类型 | Doris 类型 | 备注 |
|-----------|-----------|------|
| `STRING` | `VARCHAR(65533)` 或 `STRING` | Doris 2.0+ 支持 STRING |
| `TIMESTAMP` | `DATETIME` | |
| `BINARY` | `VARCHAR(65533)` | 无直接对应，需评估 |
| `INTEGER` | `INT` | |
| `TINYINT` | `TINYINT` | ✅ 兼容 |
| `SMALLINT` | `SMALLINT` | ✅ 兼容 |
| `BIGINT` | `BIGINT` | ✅ 兼容 |
| `FLOAT` | `FLOAT` | ✅ 兼容 |
| `DOUBLE` | `DOUBLE` | ✅ 兼容 |
| `BOOLEAN` | `BOOLEAN` | ✅ 兼容 |
| `DATE` | `DATE` | ✅ 兼容 |
| `CHAR(n)` | `CHAR(n)` | ✅ 兼容 |
| `VARCHAR(n)` | `VARCHAR(n)` | ✅ 兼容 |
| `DECIMAL(p,s)` | `DECIMAL(p,s)` | ✅ 兼容 |
| `ARRAY<T>` | `ARRAY<T>` | Doris 1.2+ 支持 |
| `MAP<K,V>` | `MAP<K,V>` | Doris 1.2+ 支持 |
| `STRUCT<...>` | `STRUCT<...>` | Doris 1.2+ 支持 |

---

## 2. DDL 语法差异

### 2.1 表存储格式（移除）

Hive 中以下子句在 Doris 中不适用，需删除：
```sql
-- 移除以下子句:
STORED AS ORC
STORED AS PARQUET
STORED AS TEXTFILE
ROW FORMAT DELIMITED
FIELDS TERMINATED BY '\t'
COLLECTION ITEMS TERMINATED BY ','
MAP KEYS TERMINATED BY ':'
LINES TERMINATED BY '\n'
SERDE 'org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe'
LOCATION 'hdfs://namenode/path/to/table'
TBLPROPERTIES ('orc.compress'='SNAPPY')
```

### 2.2 数据模型（必须添加）

Doris 表必须指定数据模型，根据业务语义选择：

```sql
-- 明细模型（保留所有数据，适合日志/行为数据）
CREATE TABLE t (
  id BIGINT,
  event_time DATETIME,
  value DOUBLE
) DUPLICATE KEY(id, event_time)
DISTRIBUTED BY HASH(id) BUCKETS 16;

-- 唯一键模型（按主键去重，适合维度表/upsert场景）
CREATE TABLE t (
  user_id BIGINT,
  name VARCHAR(128),
  updated_at DATETIME
) UNIQUE KEY(user_id)
DISTRIBUTED BY HASH(user_id) BUCKETS 16;

-- 聚合模型（预聚合，适合指标汇总表）
CREATE TABLE t (
  dt DATE,
  user_id BIGINT,
  pv BIGINT SUM,
  uv BIGINT SUM
) AGGREGATE KEY(dt, user_id)
DISTRIBUTED BY HASH(user_id) BUCKETS 16;
```

### 2.3 分区（手动重写）

Hive 动态分区 → Doris 显式分区定义：

```sql
-- Hive
PARTITIONED BY (dt STRING, region STRING)

-- Doris - 按范围分区（日期常用）
PARTITION BY RANGE(dt) (
  PARTITION p20240101 VALUES LESS THAN ('2024-01-02'),
  PARTITION p20240102 VALUES LESS THAN ('2024-01-03')
)
-- 或使用动态分区（推荐）
PROPERTIES (
  "dynamic_partition.enable" = "true",
  "dynamic_partition.time_unit" = "DAY",
  "dynamic_partition.start" = "-30",
  "dynamic_partition.end" = "3",
  "dynamic_partition.prefix" = "p",
  "dynamic_partition.buckets" = "16"
)

-- Doris - 列表分区
PARTITION BY LIST(region) (
  PARTITION p_cn VALUES IN ('CN'),
  PARTITION p_us VALUES IN ('US')
)
```

### 2.4 分桶

```sql
-- Hive
CLUSTERED BY (user_id) SORTED BY (event_time) INTO 32 BUCKETS

-- Doris（SORTED BY 不支持，忽略）
DISTRIBUTED BY HASH(user_id) BUCKETS 32
```

分桶数建议：
- 每个 Bucket 数据量 100MB～1GB
- 分桶字段选高基数的 JOIN/过滤字段

### 2.5 外部表

```sql
-- Hive 外部表
CREATE EXTERNAL TABLE t (...) LOCATION 'hdfs://...';

-- Doris 通过 Catalog 访问外部数据源
CREATE CATALOG hive_catalog PROPERTIES (
  'type' = 'hms',
  'hive.metastore.uris' = 'thrift://hms-host:9083'
);
-- 或使用 External Table（仅部分格式支持）
```

---

## 3. DML 语法差异

### 3.1 INSERT OVERWRITE with PARTITION

```sql
-- Hive（静态分区）
INSERT OVERWRITE TABLE sales PARTITION (dt='2024-01-01')
SELECT * FROM stg_sales WHERE dt = '2024-01-01';

-- Doris（直接覆盖，配合动态分区）
INSERT OVERWRITE TABLE sales
SELECT * FROM stg_sales WHERE dt = '2024-01-01';
```

### 3.2 Multi-insert（需拆分）

```sql
-- Hive（多表 INSERT，一次扫描）
FROM source_table
INSERT INTO table_a SELECT col1, col2 WHERE type = 'A'
INSERT INTO table_b SELECT col1, col3 WHERE type = 'B';

-- Doris（需拆成两条独立语句）
INSERT INTO table_a SELECT col1, col2 FROM source_table WHERE type = 'A';
INSERT INTO table_b SELECT col1, col3 FROM source_table WHERE type = 'B';
```

### 3.3 LOAD DATA

```sql
-- Hive
LOAD DATA INPATH 'hdfs://...' INTO TABLE t;

-- Doris 使用 Broker Load 或 Stream Load
LOAD LABEL db.label (
  DATA INFILE('hdfs://namenode/path/*')
  INTO TABLE t
  FORMAT AS "orc"
  (col1, col2, col3)
) WITH BROKER 'broker_name';
```

---

## 4. 函数映射

### 4.1 聚合函数

| Hive | Doris | 注意 |
|------|-------|------|
| `collect_list(x)` | `GROUP_CONCAT(x)` | 顺序不保证 |
| `collect_set(x)` | `GROUP_CONCAT(DISTINCT x)` | 需验证括号 |
| `percentile(x, p)` | `PERCENTILE(x, p)` | ✅ |
| `percentile_approx(x, p)` | `PERCENTILE_APPROX(x, p)` | ✅ |
| `corr(x, y)` | `CORR(x, y)` | ✅ |
| `covar_pop(x, y)` | `COVAR_POP(x, y)` | ✅ |
| `covar_samp(x, y)` | `COVAR_SAMP(x, y)` | ✅ |

### 4.2 日期函数

| Hive | Doris | 注意 |
|------|-------|------|
| `to_date(ts)` | `DATE(ts)` | |
| `weekofyear(d)` | `WEEK(d)` | |
| `trunc(d, 'MM')` | `DATE_TRUNC('month', d)` | **参数顺序相反！** |
| `months_between(d1, d2)` | `DATEDIFF(d1, d2) / 30.0` | 近似值 |
| `add_months(d, n)` | `DATE_ADD(d, INTERVAL n MONTH)` | |
| `next_day(d, 'MO')` | 手动实现 | Doris 不支持 |
| `last_day(d)` | `LAST_DAY(d)` | ✅ |

### 4.3 字符串函数

| Hive | Doris | |
|------|-------|---|
| `nvl(x, y)` | `IFNULL(x, y)` | |
| `base64(x)` | `TO_BASE64(x)` | |
| `unbase64(x)` | `FROM_BASE64(x)` | |
| `get_json_object(j, '$.k')` | `JSON_EXTRACT(j, '$.k')` | 路径语法基本兼容 |
| `parse_url(url, 'HOST')` | `PARSE_URL(url, 'HOST')` | ✅ |
| `regexp_extract(s, p, idx)` | `REGEXP_EXTRACT(s, p, idx)` | ✅ |

### 4.4 数组函数

| Hive | Doris | |
|------|-------|---|
| `size(arr)` | `ARRAY_SIZE(arr)` | |
| `array_contains(arr, v)` | `ARRAY_CONTAINS(arr, v)` | ✅ |
| `sort_array(arr)` | `ARRAY_SORT(arr)` | |
| `array(v1, v2)` | `ARRAY(v1, v2)` | ✅ |
| `posexplode(arr)` | 不支持 | 手动改写 |
| `inline(array_of_struct)` | 不支持 | 手动改写 |

---

## 5. 窗口函数

Doris 窗口函数与 Hive 基本一致，以下需注意：

```sql
-- Hive: ROWS/RANGE BETWEEN 语法相同
-- Doris 不支持 WINDOW 子句别名：
-- Hive 中可以 WINDOW w AS (PARTITION BY ...) 然后引用 OVER w
-- Doris 需要每个窗口函数内联写完整的 OVER(...)
```

---

## 6. 配置项清单（迁移后）

Doris 表创建后需确认：
1. `DUPLICATE KEY` / `UNIQUE KEY` / `AGGREGATE KEY` 已选择
2. `DISTRIBUTED BY HASH(...)` 字段合理
3. 动态分区属性已配置（如需要）
4. Colocation 组（如有大量 JOIN 同一分桶字段的表）
5. `PROPERTIES("replication_num" = "3")` 按集群配置副本数

---

## 7. 不支持的 Hive 特性

| 特性 | 处理方式 |
|------|---------|
| Multi-insert FROM ... INSERT | 拆成多条 INSERT |
| TABLESAMPLE | 不支持，改用 LIMIT 或随机抽样 |
| LATERAL VIEW OUTER | 仅支持 LATERAL VIEW |
| TRANSFORM ... USING | 不支持，改写为 UDF |
| CONNECT BY / 层次查询 | 不支持，改写为递归 CTE（Doris 2.0+）|
| GROUPING SETS 部分语法 | Doris 支持 GROUPING SETS、ROLLUP、CUBE |
| ADD JAR / CREATE TEMPORARY FUNCTION | 需通过 Doris UDF 注册机制重新实现 |
