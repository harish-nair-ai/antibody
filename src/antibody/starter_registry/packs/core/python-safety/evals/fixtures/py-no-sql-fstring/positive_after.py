def find_user(cursor, user_id):
    cursor.execute(f"SELECT id, name FROM users WHERE id = {user_id}")
    return cursor.fetchone()
