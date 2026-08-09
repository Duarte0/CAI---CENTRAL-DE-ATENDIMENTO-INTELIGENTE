# test_webhook_local.py
import requests
import json
import time

# URL do seu webhook
WEBHOOK_URL = "http://localhost:8000/webhook/digisac"

# Payload igual ao que o Digisac envia
payload = {
    "event": "message.created",
    "conversation_id": "conv-123",
    "message_id": f"msg-{int(time.time())}",
    "content": "Olá, não consigo fazer login no sistema",
    "sender_id": "customer-456",
    "timestamp": "2026-07-17T14:30:00Z"
}

# Envia como JSON (sem assinatura primeiro)
response = requests.post(
    WEBHOOK_URL,
    json=payload,
    headers={"Content-Type": "application/json"}
)

print(f"Status: {response.status_code}")
print(f"Resposta: {response.json()}")