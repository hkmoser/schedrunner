#!/usr/bin/env python3

import os
import subprocess

current_dir = os.path.dirname(os.path.abspath(__file__))

scripts_to_run = [
    "get_budgets.py",
    "get_balances.py",
    "get_balances_widget.py",
    "get_transactions.py"
]

for script in scripts_to_run:
    script_path = os.path.join(current_dir, script)
    if os.path.isfile(script_path):
        print(f"Running {script}...")
        subprocess.run(["python3", script_path], cwd=current_dir)
    else:
        print(f"Script {script} not found.")
