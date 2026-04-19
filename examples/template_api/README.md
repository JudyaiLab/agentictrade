# Template API for AgenticTrade

A minimal FastAPI service you can fork, customize, and list on the [AgenticTrade](https://agentictrade.io) marketplace.

## Quick Start

```bash
# 1. Clone / fork this template
git clone https://github.com/JudyaiLab/agentictrade.git
cd agent-commerce-framework/examples/template_api

# 2. Install
pip install -r requirements.txt

# 3. Run locally
uvicorn main:app --host 0.0.0.0 --port 8080

# 4. Test
curl http://localhost:8080/health
curl -X POST http://localhost:8080/predict \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world"}'
```

## Customize

1. Edit `main.py` — replace the `/predict` endpoint with your AI logic
2. Update the request/response models to match your API
3. Deploy to any cloud (Railway, Render, Fly.io, AWS, etc.)
4. List on AgenticTrade:
   - Log in at https://agentictrade.io/portal/dashboard
   - Fill in the onboarding wizard with your deployed URL
   - Set your price per call and free tier

## Deploy Options

| Platform | One-click | Free tier |
|----------|-----------|-----------|
| [Railway](https://railway.app) | Yes | 500h/month |
| [Render](https://render.com) | Yes | Free web service |
| [Fly.io](https://fly.io) | CLI deploy | 3 shared VMs |
| [Replit](https://replit.com) | Yes | Free with limits |

## Structure

```
template_api/
├── main.py              # FastAPI app with /predict endpoint
├── requirements.txt     # Dependencies
└── README.md           # This file
```

## License

MIT — use freely for any purpose.
