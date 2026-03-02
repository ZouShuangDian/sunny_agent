import bcrypt, uuid
from uuid6 import uuid7

usernumb = 'test_user'
password = 'test123'
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

print(f"""
INSERT INTO sunny_agent.users (id, usernumb, username, hashed_pwd, role_id, department, is_active, data_scope)
SELECT
  '{uuid7()}',
  '{usernumb}',
  '测试用户',
  '{hashed}',
  id,
  '技术部',
  TRUE,
  '{{}}'
FROM sunny_agent.roles WHERE name = 'admin';
""")
