def find_user(cursor, user_id):
    cursor.execute("SELECT id, name FROM users WHERE id = ?", (user_id,))
    return cursor.fetchone()
