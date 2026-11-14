#!/bin/bash

# Check for config file
if [ -z "$1" ]; then
  echo "Usage: ./rest_config.sh <config.json>"
  exit 1
fi

CONFIG_FILE=$1

if [ ! -f "$CONFIG_FILE" ]; then
  echo "Error: Config file not found: $CONFIG_FILE"
  exit 1
fi

# Display the configuration (without apiKey)
echo "--- Configuration to be sent (excluding apiKey) ---"
jq 'del(.credentials.apiKey)' "$CONFIG_FILE"
echo "-------------------------------------------------"

# Send the configuration to the server
echo "Sending configuration to the server..."
# /api/v1/configure requires authentication: export TDA_TOKEN (a JWT from
# /api/v1/auth/login or a tda_ access token — see create_access_token.sh).
if [ -z "$TDA_TOKEN" ]; then
    echo "Error: set TDA_TOKEN to a JWT or access token first." >&2
    exit 1
fi
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST http://127.0.0.1:5050/api/v1/configure \
-H "Authorization: Bearer $TDA_TOKEN" \
-H "Content-Type: application/json" \
-d @"$CONFIG_FILE")

HTTP_STATUS=$(tail -n1 <<< "$RESPONSE")
HTTP_BODY=$(sed '$ d' <<< "$RESPONSE")

echo "--- Server Response (HTTP Status: $HTTP_STATUS) ---"
echo "$HTTP_BODY" | jq .
echo "-------------------------------------------------"

if [ "$HTTP_STATUS" -ne 200 ]; then
  echo "Error: Configuration failed."
  exit 1
fi
