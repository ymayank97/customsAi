# Officer reviews and future training data

The workflow is **upload a Bill of Entry → inspect an item → record an officer decision → export verified training examples**. Premium membership authorizes review entry; approval should be given to officers qualified to verify product facts and classification references.

## Approve accounts

Enable the existing Google login with `GOOGLE_OAUTH_CLIENT_ID` and `JWT_SECRET`. Google identities must have a verified email. Standard accounts can upload, inspect and download their own audits. Premium reviewers can additionally save decisions and export their own labels. Administrators can view and export labels from all reviewers; audit files remain restricted to their owners.

Set these backend environment variables and restart the API:

```dotenv
PREMIUM_EMAILS=reviewer@example.com,another-reviewer@example.com
ADMIN_EMAILS=owner@example.com
```

Administrators have premium review capabilities. Comma-separated email lists are case-insensitive. Account privileges are checked against the database and server configuration on every authenticated request. A login screen, browser payload or JWT cannot choose a premium role. This release uses administrator approval; it does not process membership payments.

Alternatively, grant or revoke premium membership for an account that has signed in at least once:

```powershell
.venv/Scripts/python.exe -m backend.scripts.manage_users --email reviewer@example.com --tier premium
.venv/Scripts/python.exe -m backend.scripts.manage_users --email reviewer@example.com --tier standard
```

Revocation also requires removing the email from `PREMIUM_EMAILS` / `ADMIN_EMAILS` if listed there. The browser shows the revised account tier after a reload; the API enforces it immediately. Local deployments without authentication can inspect audits but cannot create officer labels.

## Apply the schema

SQLite automatically creates `training_labels` and adds the `users.account_tier` column at first database access. Existing users remain Standard. For PostgreSQL, apply the migration before deploying the new API:

```powershell
.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
```

Alternatively use the existing `AUTO_MIGRATE=1` startup option. Existing records in `training_feedback` stay historical and are not promoted into verified training data.

## Record a review

Open a completed audit and choose **Review item**:

- **Confirm the suggestion**: record the original suggested code after independently checking it.
- **Correct the code**: enter a different eight-digit code. The server verifies it exists in the tariff master.
- **More details needed**: describe the missing information; no training code is assigned.

Confirmed and corrected reviews require a document or tariff reference and a short explanation. Product details such as material, composition and intended use are optional and help distinguish otherwise identical descriptions. Leave the duty rate blank unless independently verified; zero is a valid rate.

The server reads the description, declaration and original suggestion from the user's persisted audit, checks job ownership and saves the reviewer identity and timestamps. It accepts labels only for completed audits and real rows. The unique key `(user_id, job_id, row_idx)` makes repeat saves update the current review instead of creating duplicates. The original prediction remains separate from the officer's decision. Saved reviews appear in results after reload.

`training_labels` holds the current decision for each reviewed row, not an immutable history of every edit. Back up this table with the application database.

## Prepare the next model

Use **Reviewed items → Export training labels (CSV)**. Premium reviewers export their own labels; an administrator exports the combined dataset. The export contains `item_description`, `gold_cth`, `label_source`, raw description, extra product details, original suggested and declared codes, verified BCD, explanation, reviewer ID, review ID, job ID and timestamps.

- Requests for more details are excluded.
- Matching descriptions and product details are normalized using Unicode normalization, case folding and whitespace normalization, then deduplicated.
- Different accepted codes for the same normalized description and details are marked as conflicts and excluded. Resolve the codes, provide evidence-backed distinguishing details, or mark unresolved items as needing information.
- Additional verified product details are appended to `item_description`; `raw_description` preserves the imported text.

The export is a labeled dataset for offline training. Saving or exporting a review does not retrain or replace the live model. When building a training split, group matching product descriptions across all files so a repeated item cannot land in both training and evaluation. If using extra product details during training, also test against the original sparse descriptions that the deployed model will receive.

Keep a separate, unseen officer-reviewed evaluation set. Report chapter, heading, six-digit subheading and exact eight-digit accuracy separately. Before deploying a new model, check invalid-code rate, review routing and performance on sparse descriptions in addition to exact accuracy. The existing evaluator accepts the exported `gold_cth` / `label_source` columns:

```powershell
.venv/Scripts/python.exe -m backend.scripts.evaluate_retrieval --help
```

Consult [the data and accuracy notes](accuracy-and-data.md) for leakage checks and the distinction between declaration agreement and verified accuracy.

## API

`POST /api/reviews` accepts `job_id`, `row_idx`, `decision`, optional `corrected_cth` / `corrected_bcd`, `reference`, `reasoning` and optional `product_details`. Source description, original prediction and reviewer identity are server-controlled. `GET /api/reviews` supports `job_id`, `q`, `page` and `page_size`; `GET /api/reviews/export` returns deduplicated eligible CSV data. All three require premium reviewer access.
