import app.config
from app import db
import app.cli as cli
db.init_engine(app.config.Config.DATABASE_URL)
with db.tx() as conn:
    cli.run_schema(conn)
    cli.seed_base(conn)
    cli.seed_demo(conn)
    role = db.scalar(conn, "SELECT id FROM app_role WHERE name='Admin'")
    if not db.scalar(conn, "SELECT COUNT(*) FROM app_user WHERE username='admin'"):
        db.insert(conn, "app_user", {"username": "admin", "full_name": "Administrator", "password_hash": "Admin@12345", "role_id": role})
