#!/usr/bin/env python3
"""
screener_auth.py
Launches a Chromium browser via Playwright so the user can log in to Screener.in.
Automatically captures the `sessionid` cookie and updates the .env file.
"""
import os
from playwright.sync_api import sync_playwright

def update_env_file(sessionid: str):
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')
    env_lines = []
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            env_lines = f.readlines()
    
    # Remove existing SCREENER_SESSION_ID if present
    env_lines = [line for line in env_lines if not line.startswith('SCREENER_SESSION_ID=')]
    
    # Append the new sessionid
    env_lines.append(f'SCREENER_SESSION_ID="{sessionid}"\n')
    
    with open(env_path, 'w') as f:
        f.writelines(env_lines)
    print(f"\n[+] Successfully saved SCREENER_SESSION_ID to {env_path}")

def main():
    print("Launching browser... Please log in to Screener.in when the window appears.")
    with sync_playwright() as p:
        # Launch browser in non-headless mode so user can interact
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        # Go to login page
        page.goto("https://www.screener.in/login/")
        
        print("\n=======================================================")
        print("Waiting for you to log in...")
        print("Please log into Screener.in in the Chromium window.")
        print("Once you are on the Dashboard (/dash), come back here")
        input("and PRESS ENTER to continue and capture the cookie...")
        print("=======================================================\n")
        
        # Extract the cookies
        cookies = context.cookies()
        sessionid = next((c['value'] for c in cookies if c['name'] == 'sessionid'), None)
        
        if sessionid:
            print(f"\n[+] Captured sessionid: {sessionid[:5]}...{sessionid[-5:]}")
            update_env_file(sessionid)
        else:
            print("\n[-] Error: Could not find 'sessionid' cookie after login.")
            
        browser.close()

if __name__ == "__main__":
    main()
