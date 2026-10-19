from backend.app.config import settings
from backend.app.db import User


def capabilities(user: User) -> dict:
    email = user.email.strip().lower()
    admin = email in settings.admin_emails
    premium = admin or email in settings.premium_emails or user.account_tier == "premium"
    return {"account_tier": "premium" if premium else "standard", "can_label_rows": premium, "is_admin": admin}


def user_payload(user: User) -> dict:
    return {
        "id": user.id, "email": user.email, "name": user.name or "", "picture": user.picture or "",
        **capabilities(user),
    }
