import os

import psycopg


def connect():
    return psycopg.connect(os.environ["SUPABASE_DB_URL"])
