"""
Agent Commerce Framework — Team Management Example

Demonstrates setting up an AI agent team:
1. Create team owner and member API keys
2. Create a team
3. Add members with roles
4. Configure routing rules
5. Set up quality gates

Run: python examples/team_setup.py
Requires: ACF server running on http://localhost:8000
"""
import json
import os
import sys

import httpx

BASE_URL = os.getenv("ACF_BASE_URL", "http://localhost:8000")


def main():
    client = httpx.Client(base_url=BASE_URL, timeout=30)

    # --- 1. Create Owner API Key ---
    print("1. Creating team owner API key...")
    # Provider keys require auth — bootstrap with a buyer key first
    resp = client.post("/api/v1/keys", json={
        "owner_id": "bootstrap-team-setup",
        "role": "buyer",
    })
    resp.raise_for_status()
    boot = resp.json()
    boot_auth = {"Authorization": f"Bearer {boot['key_id']}:{boot['secret']}"}

    resp = client.post("/api/v1/keys", json={
        "owner_id": "team-owner",
        "role": "provider",
    }, headers=boot_auth)
    resp.raise_for_status()
    owner_key = resp.json()
    auth = f"{owner_key['key_id']}:{owner_key['secret']}"
    headers = {"Authorization": f"Bearer {auth}"}

    # --- 2. Create Team ---
    print("2. Creating development team...")
    resp = client.post("/api/v1/teams", json={
        "name": "AI Development Squad",
        "description": "Full-stack AI agent team with coding, review, and QA capabilities",
    }, headers=headers)
    resp.raise_for_status()
    team = resp.json()
    team_id = team["id"]
    print(f"   Team ID: {team_id}")

    # --- 3. Add Members ---
    members = [
        {"agent_id": "leader-agent", "role": "leader", "skills": ["management", "architecture", "review"]},
        {"agent_id": "coder-agent", "role": "worker", "skills": ["python", "javascript", "sql"]},
        {"agent_id": "reviewer-agent", "role": "reviewer", "skills": ["code-review", "security", "testing"]},
        {"agent_id": "qa-agent", "role": "worker", "skills": ["testing", "qa", "documentation"]},
    ]

    print("3. Adding team members...")
    for m in members:
        resp = client.post(f"/api/v1/teams/{team_id}/members", json=m, headers=headers)
        resp.raise_for_status()
        print(f"   Added {m['agent_id']} as {m['role']}")

    # --- 4. Configure Routing Rules ---
    rules = [
        {
            "name": "code-tasks",
            "keywords": ["implement", "code", "build", "fix", "debug", "refactor"],
            "target_agent_id": "coder-agent",
            "priority": 10,
        },
        {
            "name": "review-tasks",
            "keywords": ["review", "audit", "check", "security", "inspect"],
            "target_agent_id": "reviewer-agent",
            "priority": 20,
        },
        {
            "name": "qa-tasks",
            "keywords": ["test", "qa", "verify", "validate", "document"],
            "target_agent_id": "qa-agent",
            "priority": 15,
        },
    ]

    print("4. Setting up routing rules...")
    for r in rules:
        resp = client.post(f"/api/v1/teams/{team_id}/rules", json=r, headers=headers)
        resp.raise_for_status()
        print(f"   Rule '{r['name']}' -> {r['target_agent_id']}")

    # --- 5. Set Up Quality Gates ---
    gates = [
        {"gate_type": "quality_score", "threshold": 8.0, "gate_order": 1},
        {"gate_type": "coverage", "threshold": 8.0, "gate_order": 2},
        {"gate_type": "error_rate", "threshold": 9.5, "gate_order": 3},
    ]

    print("5. Configuring quality gates...")
    for g in gates:
        resp = client.post(f"/api/v1/teams/{team_id}/gates", json=g, headers=headers)
        resp.raise_for_status()
        print(f"   Gate '{g['gate_type']}' threshold={g['threshold']}")

    # --- 6. View Team ---
    print("\n6. Final team configuration:")
    resp = client.get(f"/api/v1/teams/{team_id}", headers=headers)
    resp.raise_for_status()
    team_data = resp.json()
    print(f"   Name: {team_data['name']}")
    print(f"   Members: {len(team_data.get('members', []))}")
    print(f"   Routing rules: {len(team_data.get('routing_rules', []))}")
    print(f"   Quality gates: {len(team_data.get('quality_gates', []))}")

    print("\nTeam setup complete!")
    print(f"Team ID: {team_id}")
    print("Tasks matching keywords will auto-route to the right agent.")
    print("All outputs must pass quality gates before delivery.")


if __name__ == "__main__":
    try:
        main()
    except httpx.ConnectError:
        print(f"Error: Cannot connect to {BASE_URL}")
        print("Make sure the ACF server is running:")
        print("  uvicorn api.main:app --port 8000")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"Error: {e.response.status_code} — {e.response.text}")
        sys.exit(1)
