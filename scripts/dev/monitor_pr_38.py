import subprocess
import time
import sys

PR_ID = "38"
CHECK_INTERVAL = 3600  # 1 hour

def check_pr():
    print(f"[{time.ctime()}] Checking PR #{PR_ID} status...")
    try:
        # Check if PR has new commits or state changed
        result = subprocess.run(
            ["gh", "pr", "view", PR_ID, "--json", "commits,reviewDecision"],
            capture_output=True,
            text=True,
            check=True
        )
        import json
        data = json.loads(result.stdout)
        
        last_commit_msg = data["commits"][-1]["message"]
        decision = data["reviewDecision"]
        
        print(f"  Decision: {decision}")
        print(f"  Last Commit: {last_commit_msg}")
        
        if "Signed-off-by" in last_commit_msg:
             print("  [!] DCO likely fixed in last commit.")
        
    except Exception as e:
        print(f"  Error: {e}")

if __name__ == "__main__":
    print(f"Starting PR #{PR_ID} monitor (Interval: {CHECK_INTERVAL}s)...")
    while True:
        check_pr()
        time.sleep(CHECK_INTERVAL)
