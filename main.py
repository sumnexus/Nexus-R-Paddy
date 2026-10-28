import json
import os
import sys
import requests

from collections import Counter
from dotenv import load_dotenv
# --- settings ----

load_dotenv()
api_key = os.environ.get("api")