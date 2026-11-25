# InboxWarmup social — GitHub Actions edition

This is a **private-repository, scheduled Python job**, based on the Windows Organic Studio. No laptop needs to remain online. It is **not a continuously hosted web dashboard**. Its automated publishing targets are LinkedIn (Postpone) and X (Buffer); Reddit is draft-only until you manually complete Postpone's notification workflow. Instagram is excluded.

## Upload and secure it

1. Create a **PRIVATE** GitHub repository, e.g. `inboxwarmup-social`. Do not upload this code into the live website repository.
2. Extract the ZIP, and upload **the contents** of this folder to the **repository root**, not the ZIP itself. Preserve `.github/workflows/daily-social.yml` (upload hidden `.github` folder too). If the browser won't include directories, use GitHub Desktop or git instead.
3. Settings → Secrets and variables → Actions → **Repository secrets** → **New repository secret**: create `GROQ_API_KEY`, `POSTPONE_API_KEY`, `BUFFER_API_KEY`. These are not included in the ZIP. Rotate previously shared keys before using them.
4. Repository → Actions → **InboxWarmup Daily Social** → **Run workflow** to perform a test. This test MAY actually queue content on connected LinkedIn/X accounts. Inspect logs and report artifact.
5. Scheduled runs are at 03:30 UTC = **09:00 IST**. GitHub may delay or occasionally skip cron jobs; check the Actions history daily at first.

## Expected output

- The workflow generates 3 posts (Reddit draft, LinkedIn draft, X draft) through Groq.
- It queues LinkedIn through Postpone and X through Buffer if your API plan, permissions, and connected-account discovery support it. Reddit drafts are **not published**.
- Post statuses are in Actions → run → summary and downloadable `social-report-*` artifacts. `queued` means the provider accepted it, not that the post is live.
- `data/posts.sqlite3` is **committed to the private repository** after runs so today's drafts/history survive between runner instances. This is necessary because hosted GitHub runners are ephemeral. It contains generated post content, public provider post IDs and error messages, **not API keys**.
- If one provider rejects API publishing or account discovery fails, its post becomes `verify`. Review provider logs before retrying; duplicate submissions are avoided on uncertain errors.

## Limitations and production notes

- Postpone may require a paid API plan, and its Reddit integration may only schedule notifications. Nothing here bypasses provider or Reddit limits.
- Groq free limits and Buffer plan limits can pause publishing.
- No dashboard is served by GitHub Actions; use downloaded CSV and GitHub summary for reports. Actual post views/replies generally require providers' analytics APIs and may be missing.
- GitHub's daily schedule is best-effort; it is not a guaranteed precision timer.
- `POSTPONE_LINKEDIN_USERNAME` and `BUFFER_X_CHANNEL_ID` can be added as repository **Variables** if the provider API exposes more than one account and automatic selection is ambiguous.
- Protect the private repository: the database holds post history and a bot is given `contents: write` for state persistence. Use GitHub branch rules sparingly; rules that block Actions pushes will also block database persistence.
- **Never upload `.env`, backups, generated logs with keys, or `venv/.venv`**. A sample `.env.example` is included only as documentation.

## Testing

Run `python -m unittest discover -s tests -v` locally or through CI. Tests mock external providers; **no live API publish has been performed by this package**. You must run the workflow to validate your connected accounts and available API features.

## November 2026 setup refresh — login and reporting

The desktop helper now calls `gh auth setup-git` after the GitHub browser login so Git pushes can authenticate. If GitHub requests a device login, **press Enter and complete the browser authorization**; do not press Ctrl+C. The browser may need a moment to return control to the command prompt. Authentication interruption cannot be automatically repaired.

- `RUN-SETUP.bat` deploys the private repo, uses your local `.env`, saves the three required values to encrypted GitHub Actions Secrets, and starts a workflow. It **does not claim that a still-running workflow has succeeded**.
- `VERIFY-DEPLOYMENT.bat` queries the GitHub repository, checks only secret *names* (never displays secret values), and prints recent workflow statuses and links. Run it after the initial workflow finishes or when you want to audit deployment.
- GitHub → Actions → latest InboxWarmup Daily Social run → **Artifacts** → `social-report-*` → download `dashboard.html` and `post-history.csv` for the latest stored local activity. It includes historical queue statuses and errors. It does **not** invent likes, views, replies or actual conversions.
- If Postpone has API restrictions or connected account discovery is ambiguous, the workflow reports **verify** and does not blindly retry an uncertain submission. Buffer queueing requires an authorized connected X channel.
- Reddit is **draft only** and Instagram is **excluded**.

**Private local configuration:** A private package may include `.env` so that `RUN-SETUP.bat` can reuse settings you already supplied, but you must NEVER upload that file or the ZIP to GitHub directly. Only allow the launcher to stage its explicit allowlist of source files. Rotate any API keys and dashboard passwords previously shared in chat. If you no longer have `.env`, the setup helper searches the previous Windows folder or asks you to select a local copy.
