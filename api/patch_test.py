import re
path = "/app/tests/test_consolidate.py"
with open(path, encoding="utf-8") as f:
    content = f.read()
content2 = content.replace(
    '    assert outcome.action == "contradiction"\n    assert outcome.memory_id != memory.id\n    await db_session.refresh(memory)',
    '    assert outcome.action == "contradiction"\n    assert outcome.memory_id != memory.id\n    await db_session.flush()\n    print("DIRTY OBJECTS:", db_session.dirty)\n    await db_session.refresh(memory)'
)
assert content2 != content, "replacement did not match"
with open(path, "w", encoding="utf-8") as f:
    f.write(content2)
