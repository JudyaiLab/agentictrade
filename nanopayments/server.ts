/**
 * AgenticTrade Nanopayments Gateway
 *
 * Express sidecar that protects marketplace API routes with Circle x402
 * micropayments. Buyers pay per-call in USDC (gas-free via Gateway batching).
 *
 * Architecture:
 *   AI Agent → this server (x402 gate) → ACF FastAPI backend → response
 *
 * Supports dual-rail:
 *   - Arc Testnet (chain ID 5042002) — demo/hackathon/dev
 *   - Mainnet — production (when Arc mainnet launches)
 */

import express, { Request, Response, NextFunction } from "express";
import { createGatewayMiddleware } from "@circle-fin/x402-batching/server";

// ── Config ──────────────────────────────────────────────────────────

const PORT = parseInt(process.env.NANO_PORT || "3402", 10);
const ACF_BACKEND = process.env.ACF_BACKEND || "http://localhost:8092";
const SELLER_ADDRESS = process.env.SELLER_ADDRESS;
if (!SELLER_ADDRESS) {
  console.error("SELLER_ADDRESS env var is required");
  process.exit(1);
}

// Dual-rail: restrict to Arc testnet for now; add mainnet chain when ready
const ACCEPTED_NETWORKS = (process.env.ACCEPTED_NETWORKS || "eip155:5042002").split(",");

// ACF backend API key for proxy authentication
const ACF_API_KEY = process.env.ACF_API_KEY || "";

// Default per-call price in USD (overridable per route)
const DEFAULT_PRICE = process.env.DEFAULT_PRICE || "$0.001";

// ── Gateway Middleware ──────────────────────────────────────────────

const gateway = createGatewayMiddleware({
  sellerAddress: SELLER_ADDRESS as `0x${string}`,
  networks: ACCEPTED_NETWORKS,
});

// ── Express App ─────────────────────────────────────────────────────

const app = express();
app.use(express.json());

// Health check (no payment required)
app.get("/health", (_req: Request, res: Response) => {
  res.json({
    status: "ok",
    service: "agentictrade-nanopayments",
    seller: SELLER_ADDRESS,
    networks: ACCEPTED_NETWORKS,
    default_price: DEFAULT_PRICE,
  });
});

// ── Payment-gated proxy routes ──────────────────────────────────────

// Discover services (free — encourages adoption)
app.get("/api/v1/services", async (req: Request, res: Response) => {
  await proxyToBackend(req, res);
});

app.get("/api/v1/discover", async (req: Request, res: Response) => {
  await proxyToBackend(req, res);
});

// Service detail (free)
app.get("/api/v1/services/:id", async (req: Request, res: Response) => {
  await proxyToBackend(req, res);
});

// Proxy call — THIS costs money (per-call micropayment)
app.all(
  "/api/v1/proxy/:serviceId/*",
  gateway.require(DEFAULT_PRICE),
  async (req: Request, res: Response) => {
    // Log nanopayment with explorer link
    const payment = (req as any).payment;
    if (payment) {
      txCount++;
      const amount = parseFloat(payment.amount || "0") / 1_000_000; // USDC 6 decimals
      totalRevenue += amount;
      const agentName = (req.headers["x-agent-name"] as string) || "Buyer-Agent";
      const serviceName = (req.headers["x-service-name"] as string) || req.params.serviceId.slice(0, 8);
      txLog.push({
        id: txCount,
        payer: payment.payer || "unknown",
        agent_name: agentName,
        service: req.params.serviceId,
        service_name: serviceName,
        amount: amount.toFixed(6),
        timestamp: new Date().toISOString(),
        explorer_url: `${ARC_EXPLORER}/address/${payment.payer}`,
      });
      persistTx();
      console.log(
        `[nanopay #${txCount}] ${agentName} (${payment.payer?.slice(0, 10)}...) paid $${amount.toFixed(4)} USDC → ${serviceName}`
      );
    }
    await proxyToBackend(req, res);
  }
);

// Custom price per service — fetch price from backend then gate
app.post(
  "/api/v1/nano/call/:serviceId",
  async (req: Request, res: Response, next: NextFunction) => {
    try {
      // Fetch service price from ACF backend
      const svcRes = await fetch(
        `${ACF_BACKEND}/api/v1/services/${req.params.serviceId}`
      );
      if (!svcRes.ok) {
        res.status(404).json({ error: "Service not found" });
        return;
      }
      const svc = (await svcRes.json()) as any;
      const price = `$${svc.pricing?.price_per_call || "0.001"}`;

      // Apply dynamic price gate
      gateway.require(price)(req, res, next);
    } catch (err) {
      res.status(500).json({ error: "Price lookup failed" });
    }
  },
  async (req: Request, res: Response) => {
    const payment = (req as any).payment;
    if (payment) {
      console.log(
        `[nanopay] ${payment.payer} paid ${payment.amount} USDC → ${req.params.serviceId}`
      );
    }

    // Forward the actual API call to backend proxy
    const backendUrl = `${ACF_BACKEND}/api/v1/proxy/${req.params.serviceId}/call`;
    try {
      const backendRes = await fetch(backendUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(req.headers.authorization
            ? { Authorization: req.headers.authorization }
            : {}),
        },
        body: JSON.stringify(req.body),
      });
      const data = await backendRes.json();
      res.status(backendRes.status).json(data);
    } catch (err) {
      res.status(502).json({ error: "Backend unavailable" });
    }
  }
);

// ── Transaction persistence ─────────────────────────────────────────

import { readFileSync, writeFileSync, existsSync } from "fs";

interface TxRecord {
  id: number;
  payer: string;
  agent_name: string;
  service: string;
  service_name: string;
  amount: string;
  timestamp: string;
  explorer_url: string;
}

const TX_FILE = new URL("../data/nanopayment_transactions.json", import.meta.url).pathname;
let txCount = 0;
let totalRevenue = 0;
let txLog: TxRecord[] = [];

// Load persisted transactions on startup
try {
  if (existsSync(TX_FILE)) {
    txLog = JSON.parse(readFileSync(TX_FILE, "utf-8"));
    txCount = txLog.length;
    totalRevenue = txLog.reduce((sum, tx) => sum + parseFloat(tx.amount || "0"), 0);
    console.log(`[persist] Loaded ${txCount} transactions from disk`);
  }
} catch { /* fresh start */ }

function persistTx(): void {
  try { writeFileSync(TX_FILE, JSON.stringify(txLog, null, 2)); } catch {}
}

// Arc testnet block explorer
const ARC_EXPLORER = "https://testnet.explorer.arc.network";

app.get("/api/v1/nano/stats", (_req: Request, res: Response) => {
  res.json({
    transactions: txCount,
    total_revenue_usdc: totalRevenue,
    seller: SELLER_ADDRESS,
    networks: ACCEPTED_NETWORKS,
    explorer: {
      base_url: ARC_EXPLORER,
      address_url: `${ARC_EXPLORER}/address/${SELLER_ADDRESS}`,
    },
    recent_transactions: txLog.slice(-20),
  });
});

// Full transaction history (paginated)
app.get("/api/v1/nano/transactions", (req: Request, res: Response) => {
  const limit = Math.min(parseInt(req.query.limit as string) || 50, 200);
  const offset = parseInt(req.query.offset as string) || 0;
  const agent = req.query.agent as string;

  let filtered = txLog;
  if (agent) {
    filtered = txLog.filter(t => t.agent_name.toLowerCase().includes(agent.toLowerCase()));
  }

  res.json({
    total: filtered.length,
    limit,
    offset,
    transactions: filtered.slice(offset, offset + limit).reverse(),
  });
});

// Individual transaction explorer link
app.get("/api/v1/nano/tx/:txId", (req: Request, res: Response) => {
  const tx = txLog.find((t) => t.id === parseInt(req.params.txId));
  if (!tx) {
    res.status(404).json({ error: "Transaction not found" });
    return;
  }
  res.json({
    ...tx,
    settlement_note:
      "Nanopayments batch-settle on-chain. Individual payments are gas-free offchain authorizations. Settlement TX appears on explorer when Gateway batches.",
  });
});

// ── Proxy helper ────────────────────────────────────────────────────

async function proxyToBackend(req: Request, res: Response): Promise<void> {
  const url = `${ACF_BACKEND}${req.originalUrl}`;
  try {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    // Use client's auth if provided, otherwise fall back to gateway service key
    if (req.headers.authorization) {
      headers["Authorization"] = req.headers.authorization as string;
    } else if (ACF_API_KEY) {
      headers["Authorization"] = `Bearer ${ACF_API_KEY}`;
    }

    const backendRes = await fetch(url, {
      method: req.method,
      headers,
      ...(req.method !== "GET" && req.method !== "HEAD"
        ? { body: JSON.stringify(req.body) }
        : {}),
    });

    const data = await backendRes.text();
    res.status(backendRes.status);

    // Forward relevant headers
    const ct = backendRes.headers.get("content-type");
    if (ct) res.setHeader("Content-Type", ct);

    res.send(data);
  } catch (err) {
    res.status(502).json({ error: "Backend unavailable" });
  }
}

// ── Start ───────────────────────────────────────────────────────────

app.listen(PORT, () => {
  console.log(`
╔══════════════════════════════════════════════════════╗
║  AgenticTrade Nanopayments Gateway                   ║
║  Port: ${PORT}                                          ║
║  Seller: ${SELLER_ADDRESS.slice(0, 10)}...${SELLER_ADDRESS.slice(-8)}            ║
║  Networks: ${ACCEPTED_NETWORKS.join(", ")}                  ║
║  Backend: ${ACF_BACKEND}                      ║
║  Price: ${DEFAULT_PRICE}/call                              ║
╚══════════════════════════════════════════════════════╝
  `);
});
