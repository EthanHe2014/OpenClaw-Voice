#!/bin/zsh
cd /Users/Ethan/.openclaw/workspace/spark
TOKEN=$(cat .hook_token)
curl -s -w '\nHTTP=%{http_code}\n' --max-time 120 \
  -X POST http://127.0.0.1:18789/hooks/voice/agent \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"Reply with exactly: DELIVERY_TEST_7","agentId":"spark","sessionKey":"agent:spark:voice","sessionMode":"persistent","waitForCompletion":true}'
