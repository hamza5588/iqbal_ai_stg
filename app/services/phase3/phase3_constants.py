import os

# Count of same canonical question on a lesson in window -> flag high-frequency
CRITICAL_FREQ_THRESHOLD = int(os.getenv("PHASE3_CRITICAL_FREQ_THRESHOLD", "4"))

# Days to look back for frequency
CRITICAL_FREQ_WINDOW_DAYS = int(os.getenv("PHASE3_CRITICAL_FREQ_WINDOW_DAYS", "14"))
