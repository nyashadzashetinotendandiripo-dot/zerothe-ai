# ZerotheBot - AI Secretary App

## Architecture
- `core/` - Python engine (agent, browser, approvals, swarm, triggers, engine)
- `plugins/` - Plugin connectors (whatsapp, teams, salesforce, jobhunt, etc.)
- `service/server.py` - FastAPI backend on port 8765
- `rn-app/` - React Native phone app (Expo)
- `web/` - Web PWA frontend

## Key files
- `core/agent.py` - Turn lifecycle, tool execution, screen lock
- `core/browser.py` - Playwright browser controller (BrowserHost.call requires coroutines)
- `plugins/whatsapp/plugin.py` - WhatsApp Web automation (async page helpers via br.call)
- `core/plugins.py` - Document renderers (pdf_create, docx_create), build_python
- `service/server.py` - API routes including /api/computer/preview
- `rn-app/src/Chat.js` - Phone chat UI (light theme, browser takeover)

## Testing
- `python -m pytest tests/test_whatsapp.py -v` - WhatsApp plugin tests
- `python -m py_compile core/plugins.py` - Compile check

## Common tasks
- WhatsApp: wa_status -> wa_open_chat -> wa_send
- PDF: pdf_create with markdown-lite text
- Browser takeover: /api/bots/{id}/takeover + /screen/input

## Instabox VM features
- Public URL: `instabox-port publish 8765 --name zerethebot`
- Headed desktop: `instabox-desktop status` (watch WhatsApp Web live on Computer tab)
- Computer Use MCP: `open-computer-use mcp` (AI-driven desktop control)
- Terminal sessions: `instabox-term ls` / `instabox-term attach`
- AI coding: `claude` / `codex` pre-installed on VM
- Container: `docker-compose up -d`
- Fast Python installs: `uv pip install -r requirements-service.txt`
