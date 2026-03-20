"""
本地 PostgreSQL 数据导入脚本
目标：localhost:5432/postgres → schema chat_qms → complaints 表
数据源：docs/客诉问题整理清单.xlsx
"""

import logging
import sys
from pathlib import Path

import pandas as pd
import numpy as np
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============ 从 .env 加载配置 ============
import os
from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

DB_CONFIG = {
    "host": os.environ.get("PG_HOST", "127.0.0.1"),
    "port": int(os.environ.get("PG_PORT", "5432")),
    "dbname": os.environ.get("PG_DBNAME", "postgres"),
    "user": os.environ.get("PG_USER", "root"),
    "password": os.environ.get("PG_PASSWORD", ""),
}
SCHEMA = "chat_qms"

# ============ Excel 字段映射 ============
FIELD_MAPPING = {
    "工厂": "factory",
    "流程编号": "id",
    "客户名称": "customer",
    "物料图号": "material_code",
    "客诉类型": "complaint_type",
    "客户反馈时间": "report_date",
    "投诉类别": "complaint_category",
    "产品类别": "product_category",
    "产品类型": "product_type",
    "责任方": "responsibility_type",
    "质量因素": "quality_factor",
    "不良现象": "defect_description",
    "智领复判": "internal_review",
    "临时措施详细内容": "temporary_measures",
    "发生原因": "occurrence_cause",
    "流出原因": "outflow_cause",
    "系统原因": "system_cause",
    "发生对策": "occurrence_countermeasure",
    "流出对策": "outflow_countermeasure",
    "系统对策": "system_countermeasure",
    "验证情况": "verification_status",
    "供应商": "supplier",
    "是否重复发生": "is_repeated",
    "内部客户代码": "internal_customer_code",
    "是否NTF数据": "is_ntf",
    "FA失效点确认": "fa_failure_point",
    "内部责任单位": "responsibility_unit",
    "具体原因": "specific_cause",
    "客户复判": "customer_review",
}

# 数据库列的固定顺序（与 INSERT 语句对应）
DB_COLUMNS = [
    "id", "factory", "customer", "material_code", "product_category",
    "product_type", "complaint_type", "complaint_category",
    "responsibility_type", "responsibility_unit", "quality_factor",
    "defect_description", "internal_review", "customer_review",
    "occurrence_cause", "outflow_cause", "system_cause", "specific_cause",
    "temporary_measures", "occurrence_countermeasure",
    "outflow_countermeasure", "system_countermeasure", "verification_status",
    "supplier", "is_repeated", "is_ntf", "internal_customer_code",
    "fa_failure_point", "report_date", "year", "quarter", "month",
]


def get_connection():
    """获取数据库连接"""
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    return conn


def init_table(conn):
    """创建 schema + complaints 表 + 索引"""
    cur = conn.cursor()

    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    cur.execute(f"SET search_path TO {SCHEMA}")

    # 建表（幂等：先 DROP 再 CREATE）
    cur.execute("DROP TABLE IF EXISTS complaints CASCADE")

    cur.execute("""
    CREATE TABLE complaints (
        id VARCHAR(64) PRIMARY KEY,
        factory VARCHAR(64),
        customer VARCHAR(256),
        material_code VARCHAR(128),
        product_category VARCHAR(128),
        product_type VARCHAR(128),
        complaint_type VARCHAR(64),
        complaint_category VARCHAR(64),
        responsibility_type VARCHAR(64),
        responsibility_unit VARCHAR(128),
        quality_factor VARCHAR(128),
        defect_description TEXT,
        internal_review TEXT,
        customer_review TEXT,
        occurrence_cause TEXT,
        outflow_cause TEXT,
        system_cause TEXT,
        specific_cause TEXT,
        temporary_measures TEXT,
        occurrence_countermeasure TEXT,
        outflow_countermeasure TEXT,
        system_countermeasure TEXT,
        verification_status TEXT,
        supplier VARCHAR(256),
        is_repeated VARCHAR(16),
        is_ntf VARCHAR(16),
        internal_customer_code VARCHAR(64),
        fa_failure_point TEXT,
        report_date TIMESTAMP,
        year INT,
        quarter VARCHAR(4),
        month INT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 单列索引
    indexes = [
        ("idx_factory", "factory"),
        ("idx_customer", "customer"),
        ("idx_supplier", "supplier"),
        ("idx_complaint_type", "complaint_type"),
        ("idx_complaint_category", "complaint_category"),
        ("idx_responsibility_type", "responsibility_type"),
        ("idx_responsibility_unit", "responsibility_unit"),
        ("idx_product_category", "product_category"),
        ("idx_product_type", "product_type"),
        ("idx_year", "year"),
        ("idx_quarter", "quarter"),
        ("idx_month", "month"),
        ("idx_report_date", "report_date"),
    ]
    for idx_name, col in indexes:
        cur.execute(f"CREATE INDEX {idx_name} ON complaints({col})")

    # 复合索引
    composites = [
        ("idx_customer_year", "customer, year"),
        ("idx_factory_year", "factory, year"),
        ("idx_resp_type_year", "responsibility_type, year"),
    ]
    for idx_name, cols in composites:
        cur.execute(f"CREATE INDEX {idx_name} ON complaints({cols})")

    # 全文索引
    for col in ["defect_description", "occurrence_cause", "outflow_cause", "system_cause", "specific_cause"]:
        cur.execute(f"""
            CREATE INDEX idx_{col}_ft ON complaints
            USING gin(to_tsvector('simple', COALESCE({col}, '')))
        """)

    # updated_at 触发器
    cur.execute("""
    CREATE OR REPLACE FUNCTION update_updated_at_column()
    RETURNS TRIGGER AS $$
    BEGIN
        NEW.updated_at = CURRENT_TIMESTAMP;
        RETURN NEW;
    END;
    $$ language 'plpgsql';

    CREATE TRIGGER trg_complaints_updated_at
    BEFORE UPDATE ON complaints
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    """)

    # 字段注释
    column_comments = {
        "id": "流程编号（主键）",
        "factory": "工厂代码",
        "customer": "客户名称",
        "material_code": "物料图号",
        "product_category": "产品类别",
        "product_type": "产品类型",
        "complaint_type": "客诉类型：售后/制造过程",
        "complaint_category": "投诉类别：功能/外观/尺寸等",
        "responsibility_type": "责任方：供应商原因/我司原因/客户原因",
        "responsibility_unit": "内部责任单位",
        "quality_factor": "质量因素",
        "defect_description": "不良现象描述",
        "internal_review": "智领复判结果",
        "customer_review": "客户复判结果",
        "occurrence_cause": "发生原因",
        "outflow_cause": "流出原因",
        "system_cause": "系统原因",
        "specific_cause": "具体原因",
        "temporary_measures": "临时措施详细内容",
        "occurrence_countermeasure": "发生对策",
        "outflow_countermeasure": "流出对策",
        "system_countermeasure": "系统对策",
        "verification_status": "验证情况",
        "supplier": "供应商名称",
        "is_repeated": "是否重复发生：是/否",
        "is_ntf": "是否NTF数据：是/否",
        "internal_customer_code": "内部客户代码",
        "fa_failure_point": "FA失效点确认",
        "report_date": "客户反馈时间",
        "year": "年份（从report_date提取）",
        "quarter": "季度（Q1/Q2/Q3/Q4）",
        "month": "月份（从report_date提取）",
        "created_at": "记录创建时间",
        "updated_at": "记录更新时间",
    }
    for col, comment in column_comments.items():
        cur.execute(f"COMMENT ON COLUMN complaints.{col} IS %s", (comment,))

    conn.commit()
    logger.info("表 complaints 创建完成（含索引 + 触发器 + 字段注释）")


def clean_excel(df: pd.DataFrame) -> pd.DataFrame:
    """清洗 Excel 数据"""
    logger.info(f"原始数据: {len(df)} 行, {len(df.columns)} 列")

    # 重命名列
    df = df.rename(columns=FIELD_MAPPING)

    # 时间字段 + 衍生字段
    df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
    df["year"] = df["report_date"].dt.year.astype("Int64")
    df["month"] = df["report_date"].dt.month.astype("Int64")
    df["quarter"] = "Q" + df["report_date"].dt.quarter.astype(str)
    # quarter 中 NaT 行会变成 'Qnan'，修正为 None
    df.loc[df["report_date"].isna(), "quarter"] = None

    # 空值统一处理：NaN / 空字符串 → None
    df = df.where(df.notna(), None)
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].apply(lambda x: None if isinstance(x, str) and x.strip() == "" else x)

    # 去重
    before = len(df)
    df = df.drop_duplicates(subset=["id"], keep="first")
    if len(df) < before:
        logger.warning(f"去重: {before} → {len(df)}")

    # 检查主键空值
    null_ids = df["id"].isna().sum()
    if null_ids > 0:
        logger.warning(f"id 为空的行: {null_ids} 条，将被跳过")
        df = df.dropna(subset=["id"])

    logger.info(f"清洗后: {len(df)} 行")
    return df


def import_data(conn, df: pd.DataFrame):
    """批量导入数据"""
    cur = conn.cursor()
    cur.execute(f"SET search_path TO {SCHEMA}")

    columns_str = ", ".join(DB_COLUMNS)
    template = "(" + ", ".join(["%s"] * len(DB_COLUMNS)) + ")"

    # 构造 ON CONFLICT 更新子句（排除 id）
    update_cols = [c for c in DB_COLUMNS if c != "id"]
    conflict_str = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols])

    sql = f"""
    INSERT INTO complaints ({columns_str})
    VALUES %s
    ON CONFLICT (id) DO UPDATE SET {conflict_str}
    """

    # 转为元组列表
    rows = []
    for _, row in df.iterrows():
        values = []
        for col in DB_COLUMNS:
            val = row.get(col)
            # pandas Int64 NA → None
            if pd.isna(val) if not isinstance(val, str) else False:
                val = None
            values.append(val)
        rows.append(tuple(values))

    batch_size = 500
    total = len(rows)

    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        execute_values(cur, sql, batch, template=template)
        done = min(i + batch_size, total)
        logger.info(f"导入进度: {done}/{total} ({done / total * 100:.1f}%)")

    conn.commit()
    logger.info(f"导入完成: {total} 条记录")


def verify(conn):
    """验证导入结果"""
    cur = conn.cursor()
    cur.execute(f"SET search_path TO {SCHEMA}")

    cur.execute("SELECT COUNT(*) FROM complaints")
    count = cur.fetchone()[0]
    logger.info(f"complaints 表总记录数: {count}")

    cur.execute("SELECT MIN(report_date), MAX(report_date) FROM complaints")
    min_date, max_date = cur.fetchone()
    logger.info(f"时间范围: {min_date} ~ {max_date}")

    cur.execute("""
        SELECT year, COUNT(*) as cnt
        FROM complaints
        WHERE year IS NOT NULL
        GROUP BY year ORDER BY year
    """)
    for row in cur.fetchall():
        logger.info(f"  {row[0]}年: {row[1]} 条")


def main():
    excel_path = Path(__file__).parent.parent / "docs" / "客诉问题整理清单.xlsx"
    if not excel_path.exists():
        logger.error(f"Excel 文件不存在: {excel_path}")
        sys.exit(1)

    logger.info(f"Excel: {excel_path}")
    logger.info(f"目标: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']} → schema {SCHEMA}")

    # 1. 读取 Excel
    df = pd.read_excel(excel_path)

    # 2. 清洗
    df = clean_excel(df)

    # 3. 连接数据库
    conn = get_connection()
    try:
        # 4. 建表
        init_table(conn)

        # 5. 导入
        import_data(conn, df)

        # 6. 验证
        verify(conn)
    except Exception as e:
        conn.rollback()
        logger.error(f"导入失败: {e}", exc_info=True)
        sys.exit(1)
    finally:
        conn.close()

    logger.info("全部完成")


if __name__ == "__main__":
    main()
