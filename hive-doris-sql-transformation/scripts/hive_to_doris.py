#!/usr/bin/env python3
"""
Hive HQL → Apache Doris SQL Converter
Usage:
  python hive_to_doris.py <file.hql>
  python hive_to_doris.py <directory/>
  python hive_to_doris.py <file.hql> --output <out.sql>
  python hive_to_doris.py <directory/> --output-dir <out_dir/>
"""

import re
import sys
import os
import argparse
from pathlib import Path
from typing import Optional

# ─── Warning collector ────────────────────────────────────────────────────────
warnings: list[str] = []

def warn(msg: str):
    warnings.append(f"-- [WARN] {msg}")


# ─── Data type mapping ────────────────────────────────────────────────────────
TYPE_MAP = [
    (r'\bSTRING\b',    'VARCHAR(65533)'),
    (r'\bTIMESTAMP\b', 'DATETIME'),
    (r'\bBINARY\b',    'VARCHAR(65533)'),   # no direct equivalent
    (r'\bINTEGER\b',   'INT'),
    # Keep: TINYINT, SMALLINT, INT, BIGINT, FLOAT, DOUBLE, BOOLEAN, DATE,
    #        CHAR(n), VARCHAR(n), DECIMAL(p,s), ARRAY<>, MAP<>, STRUCT<>
]

def convert_types(sql: str) -> str:
    for pattern, replacement in TYPE_MAP:
        sql = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)
    return sql


# ─── Function mapping ─────────────────────────────────────────────────────────
# Each entry: (hive_pattern, doris_replacement, warn_message_or_None)
FUNC_MAP = [
    # Null helpers
    (r'\bNVL\s*\(',              'IFNULL(',   None),
    (r'\bISNULL\s*\(',           'ISNULL(',   None),
    (r'\bISNOTNULL\s*\(',        'ISNOTNULL(', None),

    # Aggregation
    (r'\bCOLLECT_LIST\s*\(',     'GROUP_CONCAT(',         'collect_list → group_concat; order not guaranteed'),
    (r'\bCOLLECT_SET\s*\(',      'GROUP_CONCAT(DISTINCT ', 'collect_set → GROUP_CONCAT(DISTINCT …); verify parentheses'),

    # Date/time
    (r'\bTO_DATE\s*\(',          'DATE(',        None),
    (r'\bWEEKOFYEAR\s*\(',       'WEEK(',        None),
    (r'\bUNIX_TIMESTAMP\s*\(',   'UNIX_TIMESTAMP(',  None),
    (r'\bFROM_UNIXTIME\s*\(',    'FROM_UNIXTIME(',   None),
    (r'\bDATE_FORMAT\s*\(',      'DATE_FORMAT(',     None),
    (r'\bADD_MONTHS\s*\(',       "DATE_ADD(DATE_FORMAT(",  "ADD_MONTHS not in Doris; rewrite as DATE_ADD manually"),
    (r'\bLAST_DAY\s*\(',         'LAST_DAY(',    None),
    (r'\bMONTHS_BETWEEN\s*\(',   'DATEDIFF(',    'months_between → use DATEDIFF/30.0 manually'),
    (r'\bTRUNC\s*\(',            'DATE_TRUNC(',  'trunc(date,fmt) → DATE_TRUNC(fmt,date), swap args!'),
    (r'\bNEXT_DAY\s*\(',         'NEXT_DAY(',    'NEXT_DAY may not be supported; verify Doris version'),

    # String
    (r'\bBASE64\s*\(',           'TO_BASE64(',   None),
    (r'\bUNBASE64\s*\(',         'FROM_BASE64(', None),
    (r'\bSPACE\s*\(',            'SPACE(',       None),
    (r'\bINSTR\s*\(',            'INSTR(',       None),
    (r'\bQUOTE\s*\(',            'QUOTE(',       None),
    (r'\bFIELD\s*\(',            'FIELD(',       None),
    (r'\bFIND_IN_SET\s*\(',      'FIND_IN_SET(', None),
    (r'\bGET_JSON_OBJECT\s*\(',  'JSON_EXTRACT(', "get_json_object → JSON_EXTRACT; path syntax may differ"),

    # Array
    (r'\bSIZE\s*\(',             'ARRAY_SIZE(',  "size() → ARRAY_SIZE(); for map use MAP_SIZE()"),
    (r'\bARRAY_CONTAINS\s*\(',   'ARRAY_CONTAINS(', None),
    (r'\bSORT_ARRAY\s*\(',       'ARRAY_SORT(',  None),

    # Math
    (r'\bPMOD\s*\(',             'MOD(',         None),
    (r'\bPOW\s*\(',              'POW(',         None),

    # Conditional
    (r'\bIF\s*\(',               'IF(',          None),
    (r'\bNVL2\s*\(',             'IF(', 'nvl2(x,a,b) → IF(x IS NOT NULL, a, b); manual rewrite needed'),
]

def convert_functions(sql: str) -> str:
    for pattern, replacement, warning in FUNC_MAP:
        if re.search(pattern, sql, flags=re.IGNORECASE):
            if warning:
                warn(warning)
            sql = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)
    return sql


# ─── DDL transformations ──────────────────────────────────────────────────────

def remove_hive_storage_clauses(sql: str) -> str:
    """Remove STORED AS, ROW FORMAT, FIELDS TERMINATED, LINES TERMINATED, SERDE."""
    patterns = [
        r"STORED\s+AS\s+\w+",
        r"ROW\s+FORMAT\s+(?:DELIMITED|SERDE\s+'[^']*'(?:\s+WITH\s+SERDEPROPERTIES\s*\([^)]*\))?)",
        r"FIELDS\s+TERMINATED\s+BY\s+'[^']*'",
        r"COLLECTION\s+ITEMS\s+TERMINATED\s+BY\s+'[^']*'",
        r"MAP\s+KEYS\s+TERMINATED\s+BY\s+'[^']*'",
        r"LINES\s+TERMINATED\s+BY\s+'[^']*'",
        r"NULL\s+DEFINED\s+AS\s+'[^']*'",
        r"ESCAPED\s+BY\s+'[^']*'",
    ]
    for p in patterns:
        sql = re.sub(p, '', sql, flags=re.IGNORECASE | re.DOTALL)
    return sql


def remove_location(sql: str) -> str:
    """Remove LOCATION clause (for managed tables)."""
    sql = re.sub(r"LOCATION\s+'[^']*'", '', sql, flags=re.IGNORECASE)
    return sql


def remove_tblproperties(sql: str) -> str:
    """Remove TBLPROPERTIES clause."""
    sql = re.sub(r"TBLPROPERTIES\s*\([^)]*\)", '', sql, flags=re.IGNORECASE | re.DOTALL)
    return sql


def convert_partitioned_by(sql: str) -> str:
    """
    Hive: PARTITIONED BY (dt STRING, region STRING)
    Doris: add a comment noting manual partition config is needed.
    Doris partition syntax is much more explicit (RANGE/LIST), so we flag it.
    """
    match = re.search(r'PARTITIONED\s+BY\s*\(([^)]+)\)', sql, re.IGNORECASE | re.DOTALL)
    if match:
        cols = match.group(1).strip()
        warn(f"PARTITIONED BY ({cols}) detected. Doris requires explicit PARTITION BY RANGE/LIST definition. See conversion_rules.md.")
        sql = re.sub(r'PARTITIONED\s+BY\s*\([^)]+\)', f'-- TODO: PARTITION BY RANGE/LIST ({cols})', sql, flags=re.IGNORECASE | re.DOTALL)
    return sql


def convert_clustered_by(sql: str) -> str:
    """
    Hive: CLUSTERED BY (col1, col2) INTO N BUCKETS
    Doris: DISTRIBUTED BY HASH(col1, col2) BUCKETS N
    """
    pattern = r'CLUSTERED\s+BY\s*\(([^)]+)\)\s*(?:SORTED\s+BY\s*\([^)]+\)\s*)?INTO\s+(\d+)\s+BUCKETS'
    match = re.search(pattern, sql, re.IGNORECASE | re.DOTALL)
    if match:
        cols = match.group(1).strip()
        n    = match.group(2)
        replacement = f'DISTRIBUTED BY HASH({cols}) BUCKETS {n}'
        sql = re.sub(pattern, replacement, sql, flags=re.IGNORECASE | re.DOTALL)
    else:
        # No CLUSTERED BY — add default distribution hint
        if re.search(r'\bCREATE\s+TABLE\b', sql, re.IGNORECASE):
            warn("No CLUSTERED BY found. Add DISTRIBUTED BY HASH(<key_col>) BUCKETS N manually.")
    return sql


def add_duplicate_key_hint(sql: str) -> str:
    """
    Doris tables require a data model key (DUPLICATE KEY / UNIQUE KEY / AGGREGATE KEY).
    If none present, insert a TODO comment after the closing ')' of column definitions.
    """
    if re.search(r'\bCREATE\s+(?:EXTERNAL\s+)?TABLE\b', sql, re.IGNORECASE):
        if not re.search(r'\b(DUPLICATE|UNIQUE|AGGREGATE)\s+KEY\b', sql, re.IGNORECASE):
            # Insert after the last ')' that closes column list, before DISTRIBUTED/COMMENT/etc.
            sql = re.sub(
                r'(\))\s*(DISTRIBUTED|COMMENT|PARTITION|;|$)',
                r'\1\n-- TODO: Add data model: DUPLICATE KEY(col1[,col2]) or UNIQUE KEY(...) or AGGREGATE KEY(...)\n\2',
                sql, count=1, flags=re.IGNORECASE
            )
    return sql


def convert_insert_overwrite(sql: str) -> str:
    """
    Hive: INSERT OVERWRITE TABLE t PARTITION (dt='2024-01-01') SELECT ...
    Doris: INSERT OVERWRITE TABLE t SELECT ...   (dynamic partition)
    """
    # Static partition spec
    sql = re.sub(
        r'(INSERT\s+OVERWRITE\s+TABLE\s+\S+)\s+PARTITION\s*\([^)]+\)',
        r'\1  -- PARTITION clause removed; Doris uses dynamic partitioning',
        sql, flags=re.IGNORECASE
    )
    # INTO TABLE with partition
    sql = re.sub(
        r'(INSERT\s+INTO\s+TABLE\s+\S+)\s+PARTITION\s*\([^)]+\)',
        r'\1  -- PARTITION clause removed',
        sql, flags=re.IGNORECASE
    )
    # Hive allows "INSERT INTO TABLE t" — normalise to "INSERT INTO t"
    sql = re.sub(r'INSERT\s+INTO\s+TABLE\s+', 'INSERT INTO ', sql, flags=re.IGNORECASE)
    sql = re.sub(r'INSERT\s+OVERWRITE\s+TABLE\s+', 'INSERT OVERWRITE TABLE ', sql, flags=re.IGNORECASE)
    return sql


def handle_external_table(sql: str) -> str:
    """Flag EXTERNAL TABLE for manual review."""
    if re.search(r'\bCREATE\s+EXTERNAL\s+TABLE\b', sql, re.IGNORECASE):
        warn("EXTERNAL TABLE detected. Doris supports external tables via Catalog; requires manual configuration.")
    return sql


def handle_udf(sql: str) -> str:
    """Flag CREATE FUNCTION / ADD JAR for manual handling."""
    if re.search(r'\bCREATE\s+(?:TEMPORARY\s+)?FUNCTION\b', sql, re.IGNORECASE):
        warn("CREATE FUNCTION (UDF) detected. Doris UDF must be reimplemented using Doris Java UDF API.")
    if re.search(r'\bADD\s+JAR\b', sql, re.IGNORECASE):
        warn("ADD JAR detected. Remove this line; Doris UDFs are registered differently.")
        sql = re.sub(r'ADD\s+JAR\s+\S+\s*;?', '-- ADD JAR removed (Doris UDF registration differs)', sql, flags=re.IGNORECASE)
    return sql


def handle_set_commands(sql: str) -> str:
    """Flag/remove unsupported Hive SET commands."""
    unsupported = [
        r'SET\s+hive\.\S+\s*=',
        r'SET\s+mapred\.\S+\s*=',
        r'SET\s+mapreduce\.\S+\s*=',
        r'SET\s+tez\.\S+\s*=',
    ]
    for p in unsupported:
        if re.search(p, sql, re.IGNORECASE):
            warn(f"Hive/MapReduce SET property detected; removed. Review Doris session variables.")
            sql = re.sub(p + r'[^\n]*', '-- SET removed (Hive/MR specific)', sql, flags=re.IGNORECASE)
    return sql


def handle_msck_repair(sql: str) -> str:
    if re.search(r'\bMSCK\s+REPAIR\b', sql, re.IGNORECASE):
        warn("MSCK REPAIR TABLE not applicable in Doris; removed.")
        sql = re.sub(r'MSCK\s+REPAIR\s+TABLE\s+\S+\s*;?',
                     '-- MSCK REPAIR TABLE removed (not applicable in Doris)', sql, flags=re.IGNORECASE)
    return sql


def handle_analyze(sql: str) -> str:
    if re.search(r'\bANALYZE\s+TABLE\b', sql, re.IGNORECASE):
        warn("ANALYZE TABLE detected. Doris collects stats automatically; consider ANALYZE TABLE in Doris 2.0+.")
    return sql


def clean_whitespace(sql: str) -> str:
    """Collapse multiple blank lines into one."""
    sql = re.sub(r'\n{3,}', '\n\n', sql)
    return sql.strip()


# ─── Main conversion pipeline ─────────────────────────────────────────────────

def convert(sql: str) -> tuple[str, list[str]]:
    global warnings
    warnings = []

    steps = [
        handle_set_commands,
        handle_external_table,
        handle_udf,
        handle_msck_repair,
        handle_analyze,
        remove_hive_storage_clauses,
        remove_tblproperties,
        convert_partitioned_by,
        convert_clustered_by,
        add_duplicate_key_hint,
        remove_location,
        convert_insert_overwrite,
        convert_types,
        convert_functions,
        clean_whitespace,
    ]

    for step in steps:
        sql = step(sql)

    return sql, list(warnings)


def convert_file(src: Path, dst: Path):
    raw = src.read_text(encoding='utf-8')
    converted, w = convert(raw)

    header = [
        f"-- ============================================================",
        f"-- Converted from: {src}",
        f"-- Converter: hive_to_doris.py",
        f"-- ============================================================",
    ]
    if w:
        header += ["", "-- ⚠️  Items requiring manual review:"] + w
    header += ["", ""]

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text('\n'.join(header) + converted + '\n', encoding='utf-8')
    return len(w)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Convert Hive HQL to Apache Doris SQL")
    parser.add_argument('input', help='HQL file or directory')
    parser.add_argument('--output', '-o', help='Output SQL file (single file mode)')
    parser.add_argument('--output-dir', '-d', help='Output directory (batch mode)')
    parser.add_argument('--suffix', default='.sql', help='Output file suffix (default: .sql)')
    args = parser.parse_args()

    src = Path(args.input)

    if not src.exists():
        print(f"[ERROR] Path not found: {src}", file=sys.stderr)
        sys.exit(1)

    # ── Single file mode ──
    if src.is_file():
        if args.output:
            dst = Path(args.output)
        else:
            dst = src.with_suffix(args.suffix)
        n = convert_file(src, dst)
        print(f"✓ {src} → {dst}  ({n} warning(s))")
        return

    # ── Batch directory mode ──
    hql_files = list(src.rglob('*.hql')) + list(src.rglob('*.HQL')) + list(src.rglob('*.sql'))
    # Exclude already-converted files if suffix is .sql
    if args.suffix == '.sql':
        hql_files = [f for f in hql_files if f.suffix.lower() == '.hql']

    if not hql_files:
        print(f"[WARN] No .hql files found under {src}")
        return

    out_root = Path(args.output_dir) if args.output_dir else src / 'doris_output'
    total_warns = 0
    for f in sorted(hql_files):
        rel = f.relative_to(src)
        dst = (out_root / rel).with_suffix(args.suffix)
        n = convert_file(f, dst)
        total_warns += n
        print(f"✓ {f} → {dst}  ({n} warning(s))")

    print(f"\nDone. {len(hql_files)} file(s) converted, {total_warns} total warning(s).")
    print(f"Output directory: {out_root}")


if __name__ == '__main__':
    main()
