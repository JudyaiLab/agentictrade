/**
 * AgenticTrade Nanopayments Demo — Multi-Agent Buyer
 *
 * Simulates multiple AI agents discovering and paying for services
 * via Circle Nanopayments (x402) on Arc testnet.
 *
 * Usage:
 *   PRIVATE_KEY=0x... npx tsx demo-buyer.ts           # random agent
 *   AGENT=mimi PRIVATE_KEY=0x... npx tsx demo-buyer.ts  # specific agent
 */

import { GatewayClient } from "@circle-fin/x402-batching/client";

// ── Config ──────────────────────────────────────────────────────────

const NANO_GATEWAY = process.env.NANO_GATEWAY || "http://localhost:3402";
const CHAIN = "arcTestnet";
const TARGET_TX_COUNT = parseInt(process.env.TX_COUNT || "5");

// Agent identities — each has different preferred services
const AGENTS = [
  { name: "Judy-Buyer", role: "Platform Admin", services: ["CoinSifter Pro API", "SEO/AEO Smart Optimizer", "AI Result Validator"] },
  { name: "Mimi-Research", role: "Marketing Research", services: ["SEO/AEO Smart Optimizer", "Shorts Script Factory", "SignalFuse"] },
  { name: "Moongg-QA", role: "QA & Testing", services: ["SkillScan Security Analyzer", "AI Result Validator", "CoinSifter Pro API"] },
];

function pickAgent(): typeof AGENTS[0] {
  const env = process.env.AGENT?.toLowerCase();
  if (env === "mimi") return AGENTS[1];
  if (env === "moongg") return AGENTS[2];
  if (env === "judy") return AGENTS[0];
  return AGENTS[Math.floor(Math.random() * AGENTS.length)];
}

// ── Main ────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  const privateKey = process.env.PRIVATE_KEY;
  if (!privateKey) {
    console.error("ERROR: Set PRIVATE_KEY in .env (Arc testnet wallet)");
    process.exit(1);
  }

  const agent = pickAgent();
  console.log(`=== AgenticTrade Nanopayments — ${agent.name} (${agent.role}) ===\n`);

  const client = new GatewayClient({
    chain: CHAIN,
    privateKey: privateKey as `0x${string}`,
  });

  const balances = await client.getBalances();
  console.log(`Wallet USDC: ${balances.wallet.formatted}`);
  console.log(`Gateway USDC: ${balances.gateway.formattedAvailable}`);

  if (balances.gateway.available < 1_000_000n) {
    console.log("\nDepositing 5 USDC to Gateway...");
    const deposit = await client.deposit("5");
    console.log(`Deposit tx: ${deposit.depositTxHash}`);
  }

  console.log("\n--- Discover Services ---");
  const discoverRes = await fetch(`${NANO_GATEWAY}/api/v1/services`);
  const services = await discoverRes.json() as any;
  console.log(`Found ${Array.isArray(services) ? services.length : "?"} services`);

  console.log(`\n--- ${agent.name}: ${TARGET_TX_COUNT} Paid Calls ---\n`);

  const serviceId = process.env.DEMO_SERVICE_ID || "758c1057-191e-405e-a352-7f52bcd97a82";
  let successCount = 0;
  let failCount = 0;
  const startTime = Date.now();

  for (let i = 1; i <= TARGET_TX_COUNT; i++) {
    try {
      const callUrl = `${NANO_GATEWAY}/api/v1/proxy/${serviceId}/api/demo`;
      const supported = await client.supports(callUrl);

      if (!supported.supported) {
        const res = await fetch(callUrl);
        if (res.ok) successCount++;
        else failCount++;
        continue;
      }

      const svcName = agent.services[Math.floor(Math.random() * agent.services.length)];

      const { data, status } = await client.pay(callUrl, {
        headers: {
          "X-Agent-Name": agent.name,
          "X-Service-Name": svcName,
        },
      });

      if (status >= 200 && status < 300) {
        successCount++;
        if (i % 10 === 0 || i <= 3) {
          console.log(`  [${i}/${TARGET_TX_COUNT}] ✓ ${agent.name} → ${svcName} ($0.001)`);
        }
      } else {
        failCount++;
        console.log(`  [${i}/${TARGET_TX_COUNT}] ✗ status ${status}`);
      }
    } catch (err: any) {
      failCount++;
      if (i <= 3) {
        console.log(`  [${i}/${TARGET_TX_COUNT}] ✗ ${err.message?.slice(0, 60)}`);
      }
    }

    if (i % 10 === 0) await sleep(500);
  }

  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
  const finalBalances = await client.getBalances();

  console.log(`\n╔══════════════════════════════════════╗`);
  console.log(`║  ${agent.name} — ${agent.role}`);
  console.log(`╠══════════════════════════════════════╣`);
  console.log(`║  Success: ${successCount}/${TARGET_TX_COUNT} | Time: ${elapsed}s`);
  console.log(`║  Cost: ~$${(successCount * 0.001).toFixed(3)} USDC | Gas: $0`);
  console.log(`║  Gateway: ${finalBalances.gateway.formattedAvailable} USDC`);
  console.log(`╚══════════════════════════════════════╝`);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch(console.error);
