Bank of Anthos — AI Agentic Extension on GKE

📑 Table of Contents

	•	Overview
	•	Objectives
	•	Prerequisites
	•	Scope & Deliverables
	•	Baseline vs Extension
	•	Architecture
	•	Deployment Steps
	•	Demo
	•	Learnings

Overview

This project extends Bank of Anthos, Google’s microservices demo banking app, with AI-powered agentic components for fraud detection and risk analysis.
We followed the hackathon rule: no modification to the core code — instead, we introduced external containerized agents deployed alongside the app on GKE Autopilot.

Objectives

	•	Deploy Bank of Anthos unchanged to GKE Autopilot.
	•	Build external AI agents that consume existing BoA APIs via MCP.
	•	Integrate Gemini (Vertex AI) for fraud scoring & decision-making.
	•	Showcase agent-to-agent orchestration (ADK/A2A).

Prerequisites

	•	Google Cloud project with billing enabled.
	•	gcloud SDK, kubectl, and Docker Buildx on local machine (MacBook M1 in our setup).
	•	Enabled APIs: container.googleapis.com, aiplatform.googleapis.com, artifactregistry.googleapis.com, iamcredentials.googleapis.com.
	•	Artifact Registry (Docker format) for pushing custom agent images.

Scope & Deliverables

	•	Scope: Enhance microservices with AI agents without touching core BoA code.
	•	Deliverables:
	•	Hosted project on GKE Autopilot.
	•	Public GitHub repo with code, manifests, and logs.
	•	Architecture diagram (docs/architecture-diagram.png).
	•	~3-minute demo video.
	•	Blog/social post with #GKEHackathon hashtag.

Baseline vs Extension

✅ Baseline (Unchanged, provided by Google)

	•	Repo: GoogleCloudPlatform/bank-of-anthos
	•	Deployment: Applied as-is using kubernetes-manifests/
	•	Components: frontend, userservice, transactionhistory, balancereader, ledgerwriter, contacts, loadgenerator.

🚀 Extension (Hackathon Contribution)

This repository contains all original work:
	•	agents/mcp-server/ → MCP server exposing BoA APIs as tools.
	•	agents/adk-python/ → Fraud Agent (ADK) integrating Gemini for fraud scoring.
	•	infra/k8s/ → K8s manifests for agents (namespace, ConfigMap, Deployments, Services).
	•	docs/ → Infra bootstrap, deployment logs, architecture diagram.

Architecture

	•	Baseline BoA services run in default namespace.
	•	Agents (MCP + Fraud) run in agents namespace, communicate with BoA APIs via MCP.
	•	Fraud scoring uses Gemini (Vertex AI).

Deployment Steps

	1.	Step 1 — Local baseline (venv, repo skeleton).
	2.	Step 2 — GCP baseline (APIs, Artifact Registry, GKE cluster).
	3.	Step 3 — Deploy Bank of Anthos baseline (frontend reachable).
	4.	Step 4 — Build/push MCP server + Fraud Agent images.
	5.	Step 5 — Deploy AI agents to GKE.
	6.	Step 6 — Integrate Gemini fraud scoring.
	7.	Step 7 — Record demo video + blog/social post.

Demo

👉 Live frontend: http://<EXTERNAL_IP>
👉 Agent endpoints: /fraud/score, /tools/getUserProfile, etc.

Learnings

	•	GKE Autopilot provisions nodes only when workloads are scheduled (no idle nodes).
	•	Buildx with --platform linux/amd64 ensures compatibility from Mac M1 → GKE.
	•	MCP provides a clean separation between legacy APIs and AI agent extensions.

## Bank of Anthos Baseline
This project builds on [GoogleCloudPlatform/bank-of-anthos](https://github.com/GoogleCloudPlatform/bank-of-anthos).
The base application is deployed unchanged to GKE Autopilot as the hackathon requires.
Our contributions are in:
- `agents/` — MCP server + Fraud Agent (AI-powered)
- `infra/k8s/` — K8s manifests for new agents
- `docs/` — Deployment logs & architecture
