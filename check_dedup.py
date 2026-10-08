import sqlite3
conn = sqlite3.connect('scenarios/boss/output/requirements.db')

print('=== 最近 20 条 greetings ===')
rows = conn.execute(
    "SELECT g.id, jd.dedup_key, g.keyword, g.sent_at FROM greetings g JOIN job_details jd ON g.job_details_id = jd.id ORDER BY g.id DESC LIMIT 20"
).fetchall()
for r in rows:
    print(r)

print()
print('=== 王雪华 在 job_visits 的所有记录 ===')
rows2 = conn.execute(
    "SELECT id, title, company, hr_name, keyword, greeted, visited_at FROM job_visits WHERE hr_name LIKE '%王雪华%' ORDER BY id"
).fetchall()
for r in rows2:
    print(r)

print()
print('=== 苏州纳芯微 在 job_visits 的所有记录 ===')
rows3 = conn.execute(
    "SELECT id, title, company, hr_name, keyword, greeted, visited_at FROM job_visits WHERE company LIKE '%纳芯%' ORDER BY id"
).fetchall()
for r in rows3:
    print(r)

conn.close()
