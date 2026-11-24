def find_user(cursor, uid):
    cursor.execute("SELECT id, name FROM users WHERE id = ?", (uid,))
    return cursor.fetchone()
