import sqlite3

# 数据库文件路径
db_path = '/home/loading/nlp/open-alpha-arena-bench/backend/data.db'

# 要删除的指定 ID
id_to_delete = 5  # 请替换为你想删除的 ID

def delete_account_by_id(db_path, account_id):
    try:
        # 连接到 SQLite 数据库
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 执行删除操作
        cursor.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        
        # 提交事务
        conn.commit()

        # 获取删除的行数
        rows_deleted = cursor.rowcount
        if rows_deleted > 0:
            print(f"成功删除 {rows_deleted} 行数据.")
        else:
            print(f"没有找到 ID 为 {account_id} 的记录.")
    
    except sqlite3.Error as e:
        print(f"数据库错误: {e}")
    finally:
        # 关闭数据库连接
        conn.close()

# 调用删除函数
delete_account_by_id(db_path, id_to_delete)
