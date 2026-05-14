#!/bin/bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
echo "Now fill .env file and run: python scripts/vibe_scout.py"
