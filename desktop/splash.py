"""
RAAH Desktop Splash & Diagnostic View Provider
==============================================

Generates self-contained, offline HTML views for:
1. Initializing / Loading Screen: Sleek tactical radar/telemetry animation,
   system status messages, and branding.
2. Diagnostic Error Screen: Clear error reporting when backend startup fails,
   displaying exit code, log excerpt, and action buttons.
"""

import html
from typing import Optional


def get_splash_html(
    status_text: str = "Initializing tactical dispatch engine...",
    details: str = "Checking clinical ML model, simulation state, and persistence bridge...",
) -> str:
    """Return responsive dark-theme tactical loading screen HTML."""
    safe_status = html.escape(status_text)
    safe_details = html.escape(details)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>RAAH Command Center</title>
  <style>
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      user-select: none;
    }}
    body {{
      background: radial-gradient(circle at center, #0f172a 0%, #080c14 100%);
      color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }}
    .container {{
      text-align: center;
      max-width: 520px;
      padding: 40px 24px;
      display: flex;
      flex-direction: column;
      align-items: center;
    }}
    .radar-wrapper {{
      position: relative;
      width: 140px;
      height: 140px;
      margin-bottom: 32px;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .radar-ring {{
      position: absolute;
      border-radius: 50%;
      border: 1px solid rgba(14, 165, 233, 0.25);
    }}
    .ring-1 {{
      width: 140px;
      height: 140px;
      border-color: rgba(0, 242, 254, 0.3);
      animation: pulse-ring 3s cubic-bezier(0.215, 0.61, 0.355, 1) infinite;
    }}
    .ring-2 {{
      width: 100px;
      height: 100px;
      border-color: rgba(16, 185, 129, 0.3);
    }}
    .ring-3 {{
      width: 60px;
      height: 60px;
      border-color: rgba(56, 189, 248, 0.4);
    }}
    .radar-sweep {{
      position: absolute;
      width: 140px;
      height: 140px;
      border-radius: 50%;
      background: conic-gradient(from 0deg, transparent 0deg 280deg, rgba(0, 242, 254, 0.25) 360deg);
      animation: sweep 2s linear infinite;
    }}
    .emblem {{
      position: relative;
      width: 72px;
      height: 72px;
      z-index: 10;
      filter: drop-shadow(0 0 16px rgba(0, 242, 254, 0.6));
    }}
    .title {{
      font-size: 32px;
      font-weight: 900;
      letter-spacing: 8px;
      color: #38bdf8;
      margin-bottom: 6px;
      text-shadow: 0 0 24px rgba(56, 189, 248, 0.4);
    }}
    .subtitle {{
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 3px;
      text-transform: uppercase;
      color: #94a3b8;
      margin-bottom: 28px;
    }}
    .status-box {{
      background: rgba(15, 23, 42, 0.8);
      border: 1px solid rgba(56, 189, 248, 0.2);
      border-radius: 8px;
      padding: 14px 20px;
      width: 100%;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
    }}
    .status-text {{
      font-size: 13px;
      font-weight: 600;
      color: #e2e8f0;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      margin-bottom: 6px;
    }}
    .status-indicator {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background-color: #10b981;
      box-shadow: 0 0 10px #10b981;
      animation: blink 1.2s ease-in-out infinite;
    }}
    .details-text {{
      font-size: 11px;
      color: #64748b;
      line-height: 1.4;
    }}
    .footer {{
      margin-top: 36px;
      font-size: 10px;
      letter-spacing: 1.5px;
      color: #475569;
      text-transform: uppercase;
    }}

    @keyframes sweep {{
      from {{ transform: rotate(0deg); }}
      to {{ transform: rotate(360deg); }}
    }}
    @keyframes pulse-ring {{
      0% {{ transform: scale(0.8); opacity: 0.8; }}
      50% {{ transform: scale(1.1); opacity: 0.3; }}
      100% {{ transform: scale(0.8); opacity: 0.8; }}
    }}
    @keyframes blink {{
      0%, 100% {{ opacity: 1; transform: scale(1); }}
      50% {{ opacity: 0.4; transform: scale(0.8); }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="radar-wrapper">
      <div class="radar-ring ring-1"></div>
      <div class="radar-ring ring-2"></div>
      <div class="radar-ring ring-3"></div>
      <div class="radar-sweep"></div>
      <svg class="emblem" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect x="42" y="15" width="16" height="70" rx="6" fill="#00f2fe"/>
        <rect x="15" y="42" width="70" height="16" rx="6" fill="#00f2fe"/>
        <polyline points="22,50 36,50 42,38 48,62 54,28 60,68 66,50 78,50" stroke="#ffffff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
    </div>

    <div class="title">RAAH</div>
    <div class="subtitle">Tactical Emergency Coordination</div>

    <div class="status-box">
      <div class="status-text">
        <span class="status-indicator"></span>
        <span id="status-label">{safe_status}</span>
      </div>
      <div class="details-text" id="details-label">{safe_details}</div>
    </div>

    <div class="footer">Autonomous Emergency Medical Dispatch Engine</div>
  </div>
</body>
</html>
"""


def get_error_html(
    title: str = "Backend Initialization Failed",
    error_message: str = "The RAAH backend service failed to start or did not become ready in time.",
    details: Optional[str] = None,
    log_file_path: Optional[str] = None,
) -> str:
    """Return responsive dark-theme diagnostic error screen HTML."""
    safe_title = html.escape(title)
    safe_message = html.escape(error_message)
    safe_details = html.escape(details or "No diagnostic details captured.")
    safe_log_path = html.escape(log_file_path or "data/logs/desktop_backend.log")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>RAAH Command Center - Error</title>
  <style>
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}
    body {{
      background: radial-gradient(circle at center, #180808 0%, #080303 100%);
      color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}
    .card {{
      background: #0f1117;
      border: 1px solid #ef4444;
      border-radius: 12px;
      box-shadow: 0 10px 40px rgba(239, 68, 68, 0.2);
      max-width: 680px;
      width: 100%;
      padding: 32px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }}
    .header {{
      display: flex;
      align-items: center;
      gap: 14px;
    }}
    .icon {{
      width: 44px;
      height: 44px;
      border-radius: 10px;
      background: rgba(239, 68, 68, 0.15);
      border: 1px solid rgba(239, 68, 68, 0.4);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #ef4444;
      font-size: 24px;
      font-weight: 900;
    }}
    .title-group h1 {{
      font-size: 20px;
      font-weight: 700;
      color: #f87171;
    }}
    .title-group p {{
      font-size: 12px;
      color: #94a3b8;
      margin-top: 2px;
    }}
    .message {{
      font-size: 14px;
      color: #cbd5e1;
      line-height: 1.5;
    }}
    .terminal {{
      background: #040508;
      border: 1px solid #1e293b;
      border-radius: 8px;
      padding: 14px;
      font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
      font-size: 12px;
      color: #fca5a5;
      max-height: 200px;
      overflow-y: auto;
      white-space: pre-wrap;
      word-break: break-all;
    }}
    .log-info {{
      font-size: 11px;
      color: #64748b;
    }}
    .log-info code {{
      background: #1e293b;
      padding: 2px 6px;
      border-radius: 4px;
      color: #93c5fd;
    }}
    .actions {{
      display: flex;
      gap: 12px;
      margin-top: 6px;
    }}
    button {{
      cursor: pointer;
      font-size: 13px;
      font-weight: 600;
      padding: 10px 18px;
      border-radius: 6px;
      border: none;
      transition: all 0.2s ease;
    }}
    .btn-retry {{
      background: #ef4444;
      color: white;
    }}
    .btn-retry:hover {{
      background: #dc2626;
    }}
    .btn-secondary {{
      background: #1e293b;
      color: #cbd5e1;
    }}
    .btn-secondary:hover {{
      background: #334155;
      color: white;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <div class="icon">!</div>
      <div class="title-group">
        <h1>{safe_title}</h1>
        <p>RAAH Mission Critical Operations Halt</p>
      </div>
    </div>

    <div class="message">{safe_message}</div>

    <div class="terminal">{safe_details}</div>

    <div class="log-info">
      Full diagnostic log available at: <code>{safe_log_path}</code>
    </div>

    <div class="actions">
      <button class="btn-retry" onclick="window.location.reload()">Retry Connection</button>
      <button class="btn-secondary" onclick="window.close()">Exit Application</button>
    </div>
  </div>
</body>
</html>
"""
