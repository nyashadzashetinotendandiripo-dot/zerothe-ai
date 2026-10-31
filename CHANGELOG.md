# Changelog

## Unreleased

- **Command sandbox (bubblewrap).** On Linux (the cloud app, Docker, remote mode) every terminal command now runs inside a real OS sandbox: the host root is replaced by a tmpfs that mounts only read-only system directories plus your workspace (read-write), in a private PID namespace with a throwaway $HOME and /tmp — host files, credentials and other processes are simply not there. Network stays allowed by default (Settings > Computer > Sandbox network to cut it). Settings > Computer > Command sandbox: Auto (default — uses bwrap when installed, plain when not), Always (refuse to run unsandboxed), Off. Terminal results and the phone's Computer page show "sandboxed" so you can see it working. Windows desktops keep the old behaviour (no OS sandbox there). Also fixes the phone's Settings page not showing saved values (it read dotted keys against nested data, and selects never pre-selected), and the phone's Command policy select wrote values the backend ignored — it now offers the real Workspace-only / Ask-every-time modes, plus a Run box in the phone's Terminal.

- **Renamed to ZerotheBot.** Window, tray, installer (`ZerotheBot-Setup-<version>.exe`), data folder (`%APPDATA%\ZerotheBot`), database, backups, and credential-store entries. First run moves the old `OpenGrokBot` data folder into place and carries saved keys forward, so Bots, memory, settings and logins survive; old `OpenGrokBot` backups still restore. Uninstall the old OpenGrokBot app once you have confirmed everything moved.
- **Google plugins share Gmail's sign-in.** Docs/Sheets/Slides (and Calendar) no longer demand their own client ID: leaving their fields blank uses Gmail's OAuth client, like the help text always said. This fixes Docs showing "not configured" after signing in with Google.
- **WhatsApp Web plugin.** Read chats, open chats, read messages and send (approval first) through the Bot's browser tab — one tool call per task instead of dozens of screenshot steps. Pair once by scanning the QR code.
- **New app plugins: Asana, HubSpot, X, Chrome, Zoom, Salesforce.** Asana (tasks), HubSpot (contacts/companies/deals) and X (profiles/posts/search/post) are key-only declarative plugins; Chrome reads this PC's bookmarks and history; Zoom (Server-to-Server OAuth) and Salesforce (Connected App, no browser dance) refresh their own tokens.
- **Accounting + Teams plugins: QuickBooks, Xero, Sage, Microsoft Teams.** QuickBooks (company, queries, customers, invoices with sandbox mode), Xero (tenants, contacts, invoices, chart of accounts with automatic refresh-token rotation), Sage (contacts, sales invoices, ledgers, tax rates) — paste one refresh token each or press Connect; they renew themselves. Teams pairs with a browser code (no Azure dance for you): chats, channel posts and online meetings.
- **CAD + circuit plugins: Onshape, Autodesk Fusion, CircuitLab skill.** Onshape (documents, workspaces, parts/assemblies, create) via API key; Fusion (hubs, projects, files, versions) via one Connect click; CircuitLab has no public API, so a starter skill drives it in the browser tab instead.
- **Token diet: lazy tool loading.** The model APIs resend every tool's full definition on every call (the "Tools Tax" — thousands of tokens per turn). Each step now sends full schemas only for relevant tools (recently used, pinned, keyword-matched, capped); everything else is a one-line catalog plus a find_tools escape hatch, and tools the model reaches for are promoted automatically. Small toolsets are untouched. Toggle: Settings > context.lazy_tools.
- **Hard history cap + cheaper defaults.** No thread may send more than ~12,000 history tokens per request (context.max_history_tokens): old blobs are trimmed and oldest pairs dropped first, pairs kept intact, stored history untouched. Replies cap at 4,096 output tokens (context.max_tokens, "continue" still works), new bots get 25 steps (was 40), congestion retries 5→3, and daily-quota errors fail fast with a "resets daily" message instead of burning retries.
- **One-click sign-in for X, QuickBooks, Xero and Sage.** A generic OAuth Connect (PKCE, per-vendor endpoints, Basic or body secrets) drives the Plugins Connect button; X also keeps its pasted-token path and gains posting via sign-in. QuickBooks/Xero refresh with the Basic auth those vendors require.
- **Automatic provider failover.** When a Bot's provider fails (quota, key, outage), the same step retries on the next provider in your chain instead of dying. Set the order in Settings > Models > Failover chain. Failed providers rest 10 minutes, then rejoin; the chat shows one line per switch. Size errors only move to a model with room.
- **Use installed Google Chrome for browsing.** Settings > Computer > Browser engine: bundled Chromium (now the full build, not the stripped headless shell that blanked some pages) or the PC's installed Chrome — same version sites expect, same saved profile and logins. Falls back to bundled with a notice if Chrome won't start.

## 1.6.0

- **Web search.** Bots can now search the live web (`web_search` tool, DuckDuckGo — free, no API key) and answer with cited markdown links: current events, news, docs, anything after the model's training cut-off. Supports `site:` operators (e.g. `site:x.com` for X/Twitter posts). Read-only, so no approval prompt.
- **xAI (Grok) provider.** Settings > Providers now includes an xAI preset (`https://api.x.ai/v1`, default model `grok-4.7`); add a key from console.x.ai to run Grok models.
- **Approval escalations.** Unanswered approvals no longer die silently: you get an urgent nudge at 75% of the approval timeout and again if something sits unanswered for three days (each escalation fires once).
- **Routine safety limits.** Every routine can set a ceiling (max items per run), a tripwire (stop if more than X% of items look unusual) and a kill condition (an event that pauses the routine and notifies you). The limits are injected into every unattended run and shown in the routine list.
- **Marketplace.** New Marketplace page with categorized Bot templates (category, creator, blurb on every template) plus four new templates: Social Media, Design Reviewer, Life Admin and Product Scout.
- **Logins from chat.** New `login_fill` tool: a Bot that hits a login page shows an approval card right in the chat, you type (or paste from any password manager) the username and password, and it goes straight into the form. The password never reaches the model, chat history, or logs. Optional "Remember on this PC" stores it in the Windows Credential Manager so the next visit fills silently. Works on desktop and the phone PWA. Keep `request_takeover` for 2FA codes and CAPTCHAs.
- **Google Docs, Sheets & Slides.** New built-in connector (`gdocs`): find, create, read and append Google Docs; read/create/append Sheets; create text-based Slide decks. Same Google sign-in as Gmail; files it creates can be attached to email.
- **Email attachments, both ways.** `gmail_read` now lists attachments and `gmail_get_attachment` saves one into `shared/attachments/`; `gmail_create_draft` and `gmail_send` accept `attachments:` paths from the workspace.
- **Provider Guard: catch misconfiguration at Test time, not chat time.** Settings > Providers → Test now checks all three things that can be wrong — the key (model list), the chosen model ("isn't on this provider — did you mean …"), and a **real mini chat call** that surfaces the actual failure (out of credit, wrong model id, unreachable endpoint) in plain language right where you configured it.
- **Plain-language provider errors.** Every chat failure now says what's wrong and what to do: 402 → "Out of credit — add credit at your provider's billing page", 404 → names the exact model id to fix, request-too-large → "Prompt too big: needs ~N tokens, model allows ~M", image rejections → "Images not supported". Rate limits still say to wait; size errors never do.
- **Size self-healing.** The app learns each model's real per-request token ceiling from its own "Limit: N" error, counts tool schemas in its size estimate, compacts earlier (with the summarising call capped under the limit) and fails fast with exact numbers when a Bot genuinely can't fit — no more mystery 413s. Stale limits raise themselves once a bigger call succeeds.
- **Vision auto-fix.** If a text-only model rejects screenshots ("content must be a string"), the request retries with images stripped once, then "This model can see images" is turned off for that provider automatically and announced in the thread.

## 1.5.0

Five big features and five small ones. (There is no 1.4.)

**Major**
- **Files.** A new page that shows everything your Bots have saved in the shared workspace: recent files, folders, search, previews for text and images, Save a copy, Delete, and Open folder. Paths can never leave the workspace.
- **Daily digest.** What every Bot did today, yesterday, in the last 24 hours or this week: tasks, files written, sites visited, approvals, tokens. It is built from the action log (no model call, so it is instant and free). Home shows a one-line summary with a Full digest button, `/digest` works in any chat, and an optional daily notification can arrive at a time you choose.
- **Estimated cost.** Enter what your provider charges per million tokens and Usage shows the estimated spend for the week and today, per Bot and per model. Local models and `:free` models count as free, and models without a price are flagged instead of guessed. `/cost` in chat.
- **Quick Ask.** `Ctrl+J` (or `Ctrl+Alt+Space` from anywhere on your PC, even with the window closed to the tray) opens a small box: pick a Bot, type, press Enter. It goes to that Bot's chat. Slash commands work too. The global shortcut can be turned off in Settings > App.
- **Backup and restore.** Settings > App > Back up… saves your Bots, chats, memory, routines, settings and skills (optionally the workspace files) as one zip. API keys and tokens are never included. Restore… checks the file, keeps your current data as a before-restore copy, restarts the service and applies it.

**Minor**
- **Duplicate Bot.** Same job, instructions, model and limits, but a clean start: no memory, chats or access grants.
- **Update notice.** Home shows a banner when a newer release exists (one plain request to GitHub a day, nothing about you is sent; switch it off in Settings > App, or check on demand).
- **Pause all / Resume all.** From the command palette, the tray menu, or `/pauseall` and `/resumeall`.
- **Pinned Bots.** Pin a Bot to the top of the sidebar (chat menu or palette). `Ctrl+1` to `Ctrl+9` follow the order you see.
- **Match Windows theme.** A third theme choice that follows Windows' light/dark setting, live.

## 1.3.0

- **Search everything.** `Ctrl+K`, then type: besides Bots, pages and actions, it now finds words in any conversation (Bots and group chats) and in every Bot's memory, and opens the chat. Also `/search <words>` in a chat.
- **Quiet hours and Do Not Disturb.** Settings > Notifications has a quiet-hours schedule (it can cross midnight) and one-click Do Not Disturb for 1 hour, 4 hours or until morning. While it is on nothing pops up, beeps or is pushed to your phone, but every notification still lands in the Inbox. Also `/dnd 2h` and `/dnd off`, and a palette action. The sidebar shows when it is on.
- **Daily token budget per Bot.** Edit Bot > Daily budget (or `/budget 50k`). When a Bot has used its budget since midnight it stops, even in the middle of a task, and works again the next day. Routines and proactive work are skipped while a Bot is over budget. Home and `/usage` show how much each Bot has used today.
- **Export and retry.** Export any chat as a Markdown transcript (chat menu, palette, or `/export`, which saves it in the shared workspace). `/retry` sends your last message again.
- Upgrading is automatic: the database gets the new budget column the first time 1.3 starts.

## 1.2.0

A UI and UX revamp of the desktop app.

- **Home.** A new landing page: what needs your approval, who is working, how much of the week's token budget is used, your whole team at a glance, and a live feed of recent Bot actions. The app opens on it, and `Ctrl+0` brings you back.
- **Appearance.** Settings > App now has an accent colour (six choices, tuned for both themes), a Comfortable or Compact density, and a text size. Buttons, selections and your own message bubbles all follow the accent.
- **Command palette, rebuilt.** `Ctrl+K` shows recents, groups results into Bots, Groups, Pages and Actions, matches loosely ("ibx" finds Inbox) and shows shortcut hints. New actions: switch theme, stop all running tasks, keyboard shortcuts.
- **Shortcuts.** `Ctrl+1` to `Ctrl+9` jump to a Bot, `Ctrl+0` opens Home, `Ctrl+/` lists every shortcut. Switching theme from the palette keeps you on the page you were on.
- Fixed: button text on a light-theme accent was dark on blue; contrast is now picked per accent.

## 1.1.0

- **Slash commands.** Type `/` in any chat (desktop or phone) for a list of commands: `/status`, `/model`, `/mode`, `/approve`, `/deny`, `/approvals`, `/memory`, `/remember`, `/forget`, `/skills`, `/skill`, `/routines`, `/new`, `/rename`, `/stop`, `/pause`, `/resume`, `/usage`, `/version`, `/help`. They run in the service, are never sent to the model, and answer in the chat.
- Phone setup fixes: the Mobile tab refreshes after applying, and the QR code uses your Wi-Fi address instead of a virtual adapter.

## 1.0.0

First public release.
