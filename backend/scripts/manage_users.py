import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import User, get_engine


def main():
    parser = argparse.ArgumentParser(description="Approve or revoke premium access for an existing signed-in user.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--tier", choices=("standard", "premium"), required=True)
    args = parser.parse_args()
    with Session(get_engine()) as session:
        users = session.scalars(select(User).where(User.email == args.email.strip().lower())).all()
        if not users:
            parser.error("Account not found. Ask the user to sign in once, or use PREMIUM_EMAILS before their first login.")
        for user in users:
            user.account_tier = args.tier
        session.commit()
    print(f"Set {args.email} to {args.tier}. Changes apply to the next authenticated request.")
    if args.tier == "standard":
        print("Also remove this email from PREMIUM_EMAILS / ADMIN_EMAILS if listed there.")


if __name__ == "__main__":
    main()
