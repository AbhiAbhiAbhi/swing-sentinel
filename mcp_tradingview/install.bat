@echo off
echo Installing TradingView MCP dependencies...
pip install "mcp[cli]>=1.3.0" "httpx>=0.27.0" "ta>=0.11.0" "playwright>=1.44.0"
playwright install chromium
echo.
echo Done! Next steps:
echo   1. Copy .env.example to ..\.env and set TV_SESSION (get it from browser DevTools)
echo   2. Restart Claude Code to load the MCP
echo   3. Try: "Screen NSE stocks where RSI is below 30"
