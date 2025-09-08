
Bank of Anthos — AI Agentic Extension on GKE

Table of Contents

	•	Overview
	•	Objectives
	•	Quickstart
	•	Directory Structure
	•	Scope & Deliverables
	•	Baseline vs Extension
	•	Architecture
	•	Learnings

Overview

This project extends Bank of Anthos, Google’s microservices banking demo, with AI-powered agentic components for fraud detection and risk analysis.
Following hackathon rules, the core microservices remain unchanged. Instead, we introduced external containerized agents deployed alongside the app on Google Kubernetes Engine (GKE Autopilot).

Objectives

	•	✅ Deploy Bank of Anthos unchanged on GKE Autopilot.
	•	✅ Build external AI agents consuming BoA APIs via Model Context Protocol (MCP).
	•	✅ Integrate Google AI (Gemini via AI Studio) for fraud scoring & decisions.
	•	✅ Demonstrate containerized agents running independently of the baseline.
	•	⚡ Optional (prepared): Vertex AI path and Agent2Agent orchestration.

Quickstart

Run the following commands to reproduce a fraud scoring call:

# 0. Clone repo
git clone https://github.com/M10vir/boa-agent-hackathon.git
cd boa-agent-hackathon

# 1. Port-forward the AI agent gateway (agents namespace → local:8082)
kubectl -n agents port-forward svc/adk-gateway 8082:8080

# 2. Health check
curl -sS http://localhost:8082/healthz | jq .

# 3. Test a low-risk transaction
curl -sS -H 'accept: application/json' \
  -X POST "http://localhost:8082/fraud/score?user_id=TESTUSER&txn_id=txn-allow&amount=1200&merchant=Coffee&geo=US" \
  | jq '{risk_score,decision,ai_backend,reasons}'

# 4. Test a higher-risk transaction
curl -sS -H 'accept: application/json' \
  -X POST "http://localhost:8082/fraud/score?user_id=TESTUSER&txn_id=txn-review&amount=6200&merchant=Electronics&geo=US" \
  | jq '{risk_score,decision,ai_backend,reasons}'

Expected:
	•	Low amount → ALLOW with sensible reasons.
	•	High amount → REVIEW with reasons from Gemini (Studio).
	•	ai_backend → "studio" confirms AI Studio path is active.

Directory Structure

Validated repo layout (focus on hackathon contributions):

.
├── agents/                      # New AI agents (hackathon contribution)
│   ├── adk-python/              # Fraud Sentinel Agent (Gemini integration)
│   │   ├── app/                 # FastAPI app (fraud scoring endpoint)
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── mcp-server/              # MCP server exposing BoA APIs as tools
│       ├── main.py
│       ├── Dockerfile
│       └── requirements.txt
├── bank-of-anthos/              # Baseline microservices (unchanged)
│   ├── kubernetes-manifests/    # Core services YAML
│   ├── src/                     # Core service code
│   └── docs/, extras/, iac/     # Docs and infra from baseline
├── infra/k8s/                   # Custom manifests for agents
│   ├── agents-namespace.yaml
│   ├── adk-gateway.yaml
│   ├── boa-apis-configmap.yaml
│   └── mcp-server.yaml
├── docs/                        # Supplementary hackathon docs
│   ├── deployment-log.md
│   └── infra-bootstrap.md
├── screenshots.sh               # Script for consistent demo captures
├── README.md
└── requirements.txt             # Root Python deps for dev tooling

Scope & Deliverables

Scope
Enhance Bank of Anthos with AI agents without touching core code.

Deliverables
	•	Running project on GKE Autopilot.
	•	Public GitHub repo with code, manifests, and logs.
	•	Architecture diagram (docs/architecture-diagram.png).
	•	60–90s demo video.
	•	Blog/social post tagged #GKEHackathon.

Baseline vs Extension

Baseline (unchanged, provided by Google):
	•	Repo: GoogleCloudPlatform/bank-of-anthos
	•	Services: frontend, userservice, transactionhistory, balancereader, ledgerwriter, contacts, loadgenerator

Extension (Hackathon work in this repo):
	•	agents/adk-python/ → Fraud Sentinel (AI-powered, Studio backend)
	•	agents/mcp-server/ → MCP server wrapping BoA APIs
	•	infra/k8s/ → Kubernetes manifests for agents
	•	docs/ → Logs, architecture, deployment proof

Architecture

### High-Level Diagram

```mermaid
flowchart LR
  subgraph GKE["Google Kubernetes Engine (GKE) Cluster"]
    direction LR

    subgraph BoA["Bank of Anthos (unchanged core services)"]
      FE[Frontend (LB)]
      USERSVC[userservice]
      TXN[transactionhistory]
      LEDGER[ledger-writer/reader]
      CONTACTS[contacts]
    end

    subgraph AgentsNS["Agents Namespace"]
      ADK[ADK Agent Gateway (Fraud Agent)\nFastAPI /fraud/score]
      MCP[MCP Server\n(BoA API Tools via MCP)]
    end
  end

  subgraph GoogleAI["Google AI"]
    style GoogleAI stroke-dasharray: 3 3
    STUDIO[Gemini via AI Studio\n(API Key)]
    VERTEX[Gemini via Vertex AI\n(Service Account)]
  end

  %% Primary runtime flow
  FE -->|User actions create/echo transactions| USERSVC
  USERSVC --> TXN
  FE -. demo call .->|curl/Swagger| ADK

  ADK -->|MCP Tools: getUserProfile/getTransactions| MCP
  MCP -->|REST calls| TXN
  MCP -->|REST calls| USERSVC

  ADK -->|Score Prompt| STUDIO
  ADK -. optional .->|Preferred path| VERTEX

  ADK -->|JSON Decision\n(risk_score, decision, reasons)| FE

  %% Optional A2A
  ADK -. A2A signal .-> A2A[Creditworthiness Co-Pilot\n(optional)]
  A2A -. influence .-> ADK

  %% kubectl-ai path
  KAI[kubectl-ai\n(optional)] -. intent->|“Restart adk-gateway”| GKE

  %% Labels
  classDef primary fill:#0ea5e9,stroke:#0369a1,color:#fff;

	•	Baseline Bank of Anthos services run in default namespace.
	•	AI Agents (MCP + Fraud Sentinel) run in agents namespace.
	•	Fraud Sentinel agent calls Gemini (AI Studio) for scoring.
	•	Clean separation: core app unchanged, AI agents modular and external.

Learnings

	•	GKE Autopilot provisions nodes only when workloads are scheduled → cost-efficient.
	•	Docker Buildx with --platform linux/amd64 ensures Mac M1 compatibility.
	•	MCP provides a clean layer between legacy APIs and AI agent extensions.
	•	Gemini (Studio) yields explainable outputs suitable for demos; Vertex path optional but prepared.
