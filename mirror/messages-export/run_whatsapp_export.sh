#!/bin/bash
# run_whatsapp_export.sh — scheduler entry point for the WhatsApp export.
# Same wrapper as the Messages export; separate lock and its own log file.
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run_export.sh" whatsapp_export.py
